from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Response
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from .. import models, schemas
from ..connectors import base as connectors
from ..core import pagination, queue
from ..core.deps import ApiError, CurrentUser, DbSession, default_workspace, not_found
from ..core.logging import get_logger
from ..services import publish_state

router = APIRouter(tags=["publishing"])
logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sweep_legacy_platform_accounts() -> int:
    """启动时清扫失效平台账号，防止历史脏数据在界面上冒充可用能力。

    清理对象：
    - 已下线平台（不在 connectors.PLATFORMS 内）的账号，链路已彻底移除
    - 演示账号（auth_source=demo），从未持有平台凭据，验证与发布链路均会拒绝
    """
    from sqlalchemy import or_

    from ..core.db import SessionLocal

    removed = 0
    with SessionLocal() as db:
        rows = db.scalars(
            select(models.ConnectedAccount).where(
                or_(
                    models.ConnectedAccount.platform.not_in(connectors.PLATFORMS),
                    models.ConnectedAccount.auth_source == "demo",
                )
            )
        ).all()
        for row in rows:
            db.delete(row)
        if rows:
            db.commit()
            removed = len(rows)
            logger.info("启动清扫：移除 %s 个失效平台账号（已下线平台/演示账号）", removed)
    return removed


def _commit_idempotent(db, user_id: str, key: str, job: models.PublishJob) -> models.PublishJob:
    """依赖唯一约束做幂等：并发同键提交时回查原任务，而不是抛 500。"""
    try:
        db.commit()
        return job
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(models.PublishJob).where(
                models.PublishJob.owner_user_id == user_id,
                models.PublishJob.idempotency_key == key,
            )
        )
        if existing:
            return existing
        raise


def _promote_due_jobs(db, user_id: str) -> int:
    """排期到点的任务从 queued 提升为待提交。

    没有独立调度器进程时，用「写入 + 查询」两条路径的到期扫描保证任务不会永久僵尸；
    提升仅表示可以人工提交，绝不自动触发平台发布。
    """
    rows = db.scalars(
        select(models.PublishJob).where(
            models.PublishJob.owner_user_id == user_id,
            models.PublishJob.status == publish_state.QUEUED,
            models.PublishJob.scheduled_at.is_not(None),
        )
    ).all()
    now = _utcnow()
    changed = 0
    for row in rows:
        when = row.scheduled_at
        if when and when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when and when <= now:
            row.status = publish_state.APPROVED
            row.payload_snapshot = {
                **(row.payload_snapshot or {}),
                "scheduledPromotedAt": now.isoformat(),
            }
            changed += 1
    if changed:
        db.commit()
    return changed


@router.get("/platforms/capabilities")
def platform_capabilities() -> dict:
    report = connectors.evidence_report()
    return {
        "platforms": connectors.catalog_payload(),
        "verifiedAt": report["verifiedAt"],
        "verifiedCount": report["verifiedCount"],
        "warnings": report["warnings"],
        "note": "当前仅接入微信公众号：草稿箱推送为真实能力；正式群发未接入，自动发布入口保持关闭",
    }


@router.get("/platform-accounts", response_model=list[schemas.AccountOut])
def list_accounts(
    user: CurrentUser,
    db: DbSession,
    response: Response,
    limit: int = Query(default=100, le=200),
    cursor: str | None = Query(default=None),
) -> list[schemas.AccountOut]:
    stmt = select(models.ConnectedAccount).where(
        models.ConnectedAccount.owner_user_id == user.id
    )
    rows, next_cursor = pagination.paginate(
        db, stmt, models.ConnectedAccount,
        sort_column=models.ConnectedAccount.created_at,
        cursor=cursor, limit=limit, descending=False,
    )
    pagination.set_cursor_header(response, next_cursor)
    return [_account_out(row) for row in rows]


@router.post("/platform-accounts", response_model=schemas.AccountOut, status_code=201)
def connect_account(
    payload: schemas.AccountConnectIn, user: CurrentUser, db: DbSession
) -> schemas.AccountOut:
    """开发态占位授权：只记录 `pending_authorization`，不伪造 connected。

    真实接入时替换为平台 OAuth 跳转与回调校验（state/nonce/PKCE）。
    """
    if payload.platform not in connectors.PLATFORMS:
        raise ApiError("unknown_platform", "未知平台")
    workspace = default_workspace(db, user)
    cap = connectors.get_capability(payload.platform)
    account = models.ConnectedAccount(
        owner_user_id=user.id,
        workspace_id=workspace.id,
        platform=payload.platform,
        display_name=payload.display_name or f"{cap.label}账号",
        status="pending_authorization",
        scopes=[],
    )
    db.add(account)
    db.commit()
    return _account_out(account)


@router.delete("/platform-accounts/{account_id}", status_code=204)
def unbind_account(account_id: str, user: CurrentUser, db: DbSession) -> None:
    account = db.get(models.ConnectedAccount, account_id)
    if not account or account.owner_user_id != user.id:
        raise not_found("平台账号")
    account.status = "unbound"
    account.access_token_ciphertext = ""
    account.refresh_token_ciphertext = ""
    db.commit()


@router.post("/platform-accounts/{account_id}/toggle-auto-publish", response_model=schemas.AccountOut)
def toggle_auto_publish(
    account_id: str, enabled: bool, user: CurrentUser, db: DbSession
) -> schemas.AccountOut:
    account = db.get(models.ConnectedAccount, account_id)
    if not account or account.owner_user_id != user.id:
        raise not_found("平台账号")
    cap = connectors.get_capability(account.platform)
    if enabled and not (cap.confirmed and cap.publish):
        raise ApiError(
            "capability_not_confirmed",
            f"{cap.label} 自动发布能力尚未核实，无法开启",
            status_code=409,
        )
    account.auto_publish_enabled = enabled
    db.commit()
    return _account_out(account)


@router.post("/publishing/preview", response_model=schemas.PreviewOut)
def preview(payload: schemas.PublishPreviewIn, user: CurrentUser, db: DbSession) -> schemas.PreviewOut:
    account = db.get(models.ConnectedAccount, payload.account_id)
    if not account or account.owner_user_id != user.id:
        raise not_found("平台账号")
    cap = connectors.get_capability(account.platform)
    issues = connectors.validate_draft(
        account.platform,
        title=payload.title,
        body=payload.body,
        asset_count=len(payload.asset_ids),
    )
    ok, reason = publish_state.can_submit_auto_publish(
        account_status=account.status,
        capability_confirmed=cap.confirmed,
        capability_supports_publish=cap.publish,
        auto_publish_enabled=account.auto_publish_enabled,
        has_review=True,
    )
    return schemas.PreviewOut(
        platform=account.platform,
        platform_label=cap.label,
        can_auto_publish=ok,
        block_reason=reason,
        issues=issues,
        requires_confirmation=True,
        scheduled_at=payload.scheduled_at,
        asset_count=len(payload.asset_ids),
    )


@router.post("/publishing/jobs", response_model=schemas.PublishJobOut, status_code=201)
def create_job(
    payload: schemas.PublishJobIn, user: CurrentUser, db: DbSession
) -> schemas.PublishJobOut:
    existing = db.scalar(
        select(models.PublishJob).where(
            models.PublishJob.owner_user_id == user.id,
            models.PublishJob.idempotency_key == payload.idempotency_key,
        )
    )
    if existing:
        # 幂等：同键重复提交返回原任务，不新建
        return _job_out(existing)

    account = db.get(models.ConnectedAccount, payload.account_id)
    if not account or account.owner_user_id != user.id:
        raise not_found("平台账号")
    if not payload.reviewed:
        raise ApiError("review_required", "发布前必须完成人工确认", status_code=409)

    workspace = default_workspace(db, user)

    # 关联创作空间稿件时，用稿件内容补齐标题与正文，避免用户再次手动输入
    content_item = None
    if payload.content_item_id:
        content_item = db.get(models.ContentItem, payload.content_item_id)
        if not content_item or content_item.owner_user_id != user.id:
            raise not_found("稿件")

    title = payload.title
    body = payload.body
    if content_item:
        if not title:
            title = content_item.draft_title or content_item.title
        if not body:
            if content_item.draft_body is not None:
                body = content_item.draft_body
            elif content_item.current_version_id:
                version = db.get(models.ContentVersion, content_item.current_version_id)
                body = version.body if version else ""

    snapshot = {
        "title": title,
        "body": body[:2000],
        "assetIds": payload.asset_ids,
        "contentItemId": payload.content_item_id or "",
    }

    if payload.mode == "manual":
        # 平台能力未核验时的降级路径：只做排期与结果记录，绝不调用平台接口
        issues = connectors.validate_draft(
            account.platform,
            title=title,
            body=body,
            asset_count=len(payload.asset_ids),
        )
        blocking = [i["message"] for i in issues if i.get("level") == "error"]
        if blocking:
            raise ApiError("draft_incomplete", "；".join(blocking), status_code=422)
        job = models.PublishJob(
            owner_user_id=user.id,
            workspace_id=workspace.id,
            platform_variant_id=payload.variant_id,
            content_item_id=content_item.id if content_item else None,
            connected_account_id=account.id,
            platform=account.platform,
            scheduled_at=payload.scheduled_at,
            idempotency_key=payload.idempotency_key,
            status=publish_state.QUEUED if payload.scheduled_at else publish_state.APPROVED,
            request_id=uuid.uuid4().hex,
            reviewed_by=user.id,
            payload_snapshot={**snapshot, "mode": "manual"},
        )
        db.add(job)
        job = _commit_idempotent(db, user.id, payload.idempotency_key, job)
        _promote_due_jobs(db, user.id)
        return _job_out(job)

    cap = connectors.get_capability(account.platform)
    ok, reason = publish_state.can_submit_auto_publish(
        account_status=account.status,
        capability_confirmed=cap.confirmed,
        capability_supports_publish=cap.publish,
        auto_publish_enabled=account.auto_publish_enabled,
        has_review=payload.reviewed,
    )
    if not ok:
        raise ApiError("auto_publish_unavailable", reason, status_code=409)

    job = models.PublishJob(
        owner_user_id=user.id,
        workspace_id=workspace.id,
        platform_variant_id=payload.variant_id,
        content_item_id=content_item.id if content_item else None,
        connected_account_id=account.id,
        platform=account.platform,
        scheduled_at=payload.scheduled_at,
        idempotency_key=payload.idempotency_key,
        status=publish_state.APPROVED,
        request_id=uuid.uuid4().hex,
        reviewed_by=user.id,
        payload_snapshot={
            **snapshot,
            "mode": "auto",
            "authSource": account.auth_source or "none",
            "externalCall": False,
        },
    )
    db.add(job)
    saved = _commit_idempotent(db, user.id, payload.idempotency_key, job)
    # 并发同键命中已有任务时不得重复投递发布（避免对平台产生两次副作用）
    is_new = saved is job
    # 只有「官方 OAuth 授权 + Phase 0 能力已核验」才真正提交到平台；
    # 演示账号 / 未核验平台只落任务，绝不产生外部副作用，也不伪装成功。
    if is_new and cap.confirmed and account.auth_source == "oauth":
        queue.enqueue(
            db,
            owner_user_id=user.id,
            queue="platform-publish",
            kind="publish",
            payload={"publishJobId": saved.id, "accountId": account.id},
        )
    return _job_out(saved)


@router.post("/publishing/jobs/{job_id}/record-manual-result", response_model=schemas.PublishJobOut)
def record_manual_result(
    job_id: str, payload: schemas.ManualResultIn, user: CurrentUser, db: DbSession
) -> schemas.PublishJobOut:
    """用户在平台侧手动发布后回填结果；明确标注来源为手动，不伪造平台返回。"""
    job = db.get(models.PublishJob, job_id)
    if not job or job.owner_user_id != user.id:
        raise not_found("发布任务")
    if not publish_state.can_record_manual_result(job.status):
        raise ApiError("invalid_transition", f"当前状态 {job.status} 不能回填发布结果", status_code=409)
    job.status = publish_state.SUCCEEDED
    job.remote_content_id = payload.remote_content_id
    job.payload_snapshot = {
        **(job.payload_snapshot or {}),
        "manualPublish": True,
        "permalink": payload.permalink,
    }
    db.add(
        models.PublishRecord(
            publish_job_id=job.id,
            platform=job.platform,
            remote_content_id=payload.remote_content_id,
            published_at=payload.published_at or _utcnow(),
            permalink=payload.permalink,
            result_status="manual_recorded",
        )
    )
    db.commit()
    return _job_out(job)


@router.get("/publishing/jobs", response_model=list[schemas.PublishJobOut])
def list_jobs(
    user: CurrentUser,
    db: DbSession,
    response: Response,
    limit: int = Query(default=100, le=200),
    cursor: str | None = Query(default=None),
) -> list[schemas.PublishJobOut]:
    _promote_due_jobs(db, user.id)
    stmt = select(models.PublishJob).where(models.PublishJob.owner_user_id == user.id)
    rows, next_cursor = pagination.paginate(
        db, stmt, models.PublishJob,
        sort_column=models.PublishJob.created_at, cursor=cursor, limit=limit,
    )
    pagination.set_cursor_header(response, next_cursor)
    return [_job_out(row) for row in rows]


@router.post("/publishing/jobs/{job_id}/cancel", response_model=schemas.PublishJobOut)
def cancel_job(job_id: str, user: CurrentUser, db: DbSession) -> schemas.PublishJobOut:
    job = db.get(models.PublishJob, job_id)
    if not job or job.owner_user_id != user.id:
        raise not_found("发布任务")
    try:
        publish_state.assert_transition(job.status, publish_state.CANCELLED)
    except publish_state.TransitionError as exc:
        raise ApiError("invalid_transition", str(exc), status_code=409) from exc
    job.status = publish_state.CANCELLED
    db.commit()
    return _job_out(job)


@router.delete("/publishing/jobs/{job_id}", status_code=204)
def delete_job(job_id: str, user: CurrentUser, db: DbSession) -> None:
    job = db.get(models.PublishJob, job_id)
    if not job or job.owner_user_id != user.id:
        raise not_found("发布任务")
    if job.status not in publish_state.TERMINAL | {publish_state.FAILED, publish_state.NEEDS_ATTENTION}:
        raise ApiError("job_not_deletable", "进行中的发布任务不能删除，请先取消任务", status_code=409)
    db.execute(delete(models.PublishRecord).where(models.PublishRecord.publish_job_id == job.id))
    db.delete(job)
    db.commit()


@router.post("/publishing/jobs/{job_id}/retry", response_model=schemas.PublishJobOut)
def retry_job(job_id: str, user: CurrentUser, db: DbSession) -> schemas.PublishJobOut:
    job = db.get(models.PublishJob, job_id)
    if not job or job.owner_user_id != user.id:
        raise not_found("发布任务")
    if not publish_state.retry_allowed(job.status, job.error_code, job.remote_content_id):
        raise ApiError(
            "retry_not_allowed",
            "当前状态不允许重试；如远端可能已成功，请先核实发布结果",
            status_code=409,
        )
    job.status = publish_state.QUEUED
    job.attempt_count += 1
    db.commit()
    return _job_out(job)


def _account_out(account: models.ConnectedAccount) -> schemas.AccountOut:
    cap = connectors.get_capability(account.platform)
    has_own = bool(
        str(account.app_id or "").strip() and str(account.app_secret_ciphertext or "").strip()
    )
    app_id = str(account.app_id or "").strip()
    masked = ""
    if has_own:
        masked = (
            f"{app_id[:5]}***{app_id[-2:]}" if len(app_id) > 7 else app_id
        )
    return schemas.AccountOut(
        id=account.id,
        platform=account.platform,
        platform_label=cap.label,
        display_name=account.display_name,
        status=account.status,
        scopes=account.scopes or [],
        auto_publish_enabled=account.auto_publish_enabled,
        last_synced_at=account.last_synced_at,
        expires_at=account.expires_at,
        auth_source=account.auth_source or "none",
        has_own_credential=has_own,
        app_id_masked=masked,
    )


def _job_out(job: models.PublishJob) -> schemas.PublishJobOut:
    cap = connectors.get_capability(job.platform)
    return schemas.PublishJobOut(
        id=job.id,
        platform=job.platform,
        platform_label=cap.label,
        account_id=job.connected_account_id or "",
        account_name="",
        content_item_id=job.content_item_id or "",
        content_title=(job.payload_snapshot or {}).get("title", ""),
        target=job.target or "publish",
        status=job.status,
        scheduled_at=job.scheduled_at,
        attempt_count=job.attempt_count,
        remote_content_id=job.remote_content_id,
        error_code=job.error_code,
        error_message=job.error_message,
        request_id=job.request_id,
        created_at=job.created_at,
        theme_id=job.theme_id or "",
    )
