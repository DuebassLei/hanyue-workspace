from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Response
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.types import String as SaString

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
            # 排期到点：queued → approved（提升仅表示可以提交，绝不自动触发平台发布）
            publish_state.apply_transition(
                row, publish_state.APPROVED, actor="system", note="排期到点自动提升"
            )
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
            status_history=publish_state.initial_history(
                publish_state.QUEUED if payload.scheduled_at else publish_state.APPROVED,
                actor="user",
                note="创建排期任务（手动模式，发布后回填结果）"
                if payload.scheduled_at
                else "创建任务（手动模式，立即待发）",
            ),
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
        status_history=publish_state.initial_history(
            publish_state.APPROVED, actor="user", note="创建自动发布任务（已人工审核）"
        ),
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
    # 手动回填是策略允许的直接写终态（MANUAL_RECORDABLE），不走 assert_transition，
    # 但必须在历史里留痕，区分于系统自动发布成功
    job.status = publish_state.SUCCEEDED
    publish_state.record_history(job, publish_state.SUCCEEDED, actor="user", note="手动回填发布结果")
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


def _job_filters(
    db,
    stmt,
    *,
    status: str | None,
    platform: str | None,
    account_id: str | None,
    content_item_id: str | None,
    q: str | None,
):
    """列表筛选：status 支持逗号分隔多值（如 failed,needs_attention）。"""
    if status:
        wanted = [item.strip() for item in status.split(",") if item.strip()]
        unknown = [item for item in wanted if item not in publish_state.ALL_STATUSES]
        if unknown:
            raise ApiError(
                "invalid_status_filter",
                f"未知任务状态：{', '.join(unknown)}",
                status_code=400,
            )
        if wanted:
            stmt = stmt.where(models.PublishJob.status.in_(wanted))
    if platform:
        stmt = stmt.where(models.PublishJob.platform == platform)
    if account_id:
        stmt = stmt.where(models.PublishJob.connected_account_id == account_id)
    if content_item_id:
        stmt = stmt.where(models.PublishJob.content_item_id == content_item_id)
    if q:
        keyword = q.strip().lower()
        if keyword:
            pattern = f"%{keyword}%"
            # 搜索范围：请求号、平台代码、payload 里的标题。
            # 标题在 JSON 列里，SQLite 用 json_extract，PostgreSQL 用 ->>
            is_sqlite = db.get_bind().dialect.name == "sqlite"
            title_expr = (
                func.json_extract(models.PublishJob.payload_snapshot, "$.title")
                if is_sqlite
                else models.PublishJob.payload_snapshot.op("->>")("title")
            )
            stmt = stmt.where(
                or_(
                    func.lower(models.PublishJob.request_id).like(pattern),
                    func.lower(models.PublishJob.platform).like(pattern),
                    func.lower(func.cast(title_expr, SaString)).like(pattern),
                )
            )
    return stmt


@router.get("/publishing/jobs", response_model=list[schemas.PublishJobOut])
def list_jobs(
    user: CurrentUser,
    db: DbSession,
    response: Response,
    limit: int = Query(default=100, le=200),
    cursor: str | None = Query(default=None),
    status: str | None = Query(default=None),
    platform: str | None = Query(default=None),
    account_id: str | None = Query(default=None, alias="accountId"),
    content_item_id: str | None = Query(default=None, alias="contentItemId"),
    q: str | None = Query(default=None),
) -> list[schemas.PublishJobOut]:
    _promote_due_jobs(db, user.id)
    stmt = select(models.PublishJob).where(models.PublishJob.owner_user_id == user.id)
    stmt = _job_filters(
        db,
        stmt,
        status=status,
        platform=platform,
        account_id=account_id,
        content_item_id=content_item_id,
        q=q,
    )
    rows, next_cursor = pagination.paginate(
        db, stmt, models.PublishJob,
        sort_column=models.PublishJob.created_at, cursor=cursor, limit=limit,
    )
    pagination.set_cursor_header(response, next_cursor)
    return [_job_out(row) for row in rows]


@router.get("/publishing/jobs/summary", response_model=schemas.PublishJobSummaryOut)
def jobs_summary(user: CurrentUser, db: DbSession) -> schemas.PublishJobSummaryOut:
    """按状态聚合的发布任务计数；供筛选 chips 展示，避免前端拉全量自算。"""
    _promote_due_jobs(db, user.id)
    rows = db.execute(
        select(models.PublishJob.status, func.count())
        .where(models.PublishJob.owner_user_id == user.id)
        .group_by(models.PublishJob.status)
    ).all()
    by_status = {status: count for status, count in rows}
    return schemas.PublishJobSummaryOut(total=sum(by_status.values()), by_status=by_status)


@router.get("/publishing/jobs/{job_id}", response_model=schemas.PublishJobDetailOut)
def job_detail(job_id: str, user: CurrentUser, db: DbSession) -> schemas.PublishJobDetailOut:
    job = db.get(models.PublishJob, job_id)
    if not job or job.owner_user_id != user.id:
        raise not_found("发布任务")
    records = db.scalars(
        select(models.PublishRecord)
        .where(models.PublishRecord.publish_job_id == job.id)
        .order_by(models.PublishRecord.created_at.desc(), models.PublishRecord.id.desc())
    ).all()
    return _job_detail_out(job, records)


@router.patch("/publishing/jobs/{job_id}", response_model=schemas.PublishJobOut)
def patch_job(
    job_id: str, payload: schemas.PublishJobPatchIn, user: CurrentUser, db: DbSession
) -> schemas.PublishJobOut:
    """改期 / 修改标题正文：仅 approved / queued 且未开始执行的任务可编辑。"""
    job = db.get(models.PublishJob, job_id)
    if not job or job.owner_user_id != user.id:
        raise not_found("发布任务")
    if job.status not in {publish_state.APPROVED, publish_state.QUEUED}:
        raise ApiError(
            "job_not_editable",
            f"当前状态 {job.status} 不可编辑，仅待发布或排队中的任务可改期",
            status_code=409,
        )

    provided = payload.model_fields_set  # 注意：这里存的是字段名（scheduled_at），不是别名
    notes: list[str] = []
    snapshot = dict(job.payload_snapshot or {})

    if "scheduled_at" in provided:
        # queued 任务依赖 scheduled_at 到点提升；显式清空会让任务永久滞留，明确拒绝
        when = payload.scheduled_at
        if when is not None and when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if payload.scheduled_at is None and job.status == publish_state.QUEUED:
            raise ApiError(
                "scheduled_at_required",
                "排队中的任务必须保留计划时间；如需立即待发，请先取消后重新创建",
                status_code=422,
            )
        if when is not None and when <= _utcnow():
            raise ApiError("scheduled_at_past", "计划时间必须晚于当前时间", status_code=422)
        job.scheduled_at = when
        notes.append("修改计划时间")

    if "title" in provided and payload.title is not None and payload.title.strip():
        snapshot["title"] = payload.title.strip()
        notes.append("修改标题")
    if "body" in provided and payload.body is not None:
        snapshot["body"] = payload.body[:2000]
        notes.append("修改正文")

    if notes:
        snapshot["editedAt"] = _utcnow().isoformat()
        job.payload_snapshot = snapshot
        # 状态不变但留痕：时间线上能看到「谁在什么时候改过排期」
        publish_state.record_history(
            job, job.status, actor="user", note="；".join(notes)
        )
    db.commit()
    return _job_out(job)


@router.post("/publishing/jobs/{job_id}/approve", response_model=schemas.PublishJobOut)
def approve_job(job_id: str, user: CurrentUser, db: DbSession) -> schemas.PublishJobOut:
    """人工审核通过：pending_review → approved。

    自动发布五条件中的「已人工审核」在此闭环；拒绝走既有 cancel。
    """
    job = db.get(models.PublishJob, job_id)
    if not job or job.owner_user_id != user.id:
        raise not_found("发布任务")
    try:
        publish_state.apply_transition(
            job, publish_state.APPROVED, actor="user", note="人工审核通过"
        )
    except publish_state.TransitionError as exc:
        raise ApiError("invalid_transition", str(exc), status_code=409) from exc
    job.reviewed_by = user.id
    db.commit()
    return _job_out(job)


@router.post("/publishing/jobs/{job_id}/cancel", response_model=schemas.PublishJobOut)
def cancel_job(job_id: str, user: CurrentUser, db: DbSession) -> schemas.PublishJobOut:
    job = db.get(models.PublishJob, job_id)
    if not job or job.owner_user_id != user.id:
        raise not_found("发布任务")
    try:
        publish_state.apply_transition(job, publish_state.CANCELLED, actor="user", note="用户取消任务")
    except publish_state.TransitionError as exc:
        raise ApiError("invalid_transition", str(exc), status_code=409) from exc
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
    if job.status == publish_state.QUEUED:
        # 排队中的任务重试只是再计一次尝试，状态机不允许自环流转
        job.attempt_count += 1
        publish_state.record_history(job, publish_state.QUEUED, actor="user", note="手动重试（保持排队）")
    else:
        job.attempt_count += 1
        publish_state.apply_transition(
            job, publish_state.QUEUED, actor="user", note="手动重试，重新排队"
        )
        _requeue_real_platform_job(db, job)
    db.commit()
    return _job_out(job)


def _requeue_real_platform_job(db, job: models.PublishJob) -> None:
    """重试真实平台任务时重新投递后台队列。

    此前 retry 只改状态不重新入队，草稿箱/自动任务重试后会永久滞留在 queued——
    这里按创建时的门槛重新投递：草稿箱直接入队（Worker 会再校验凭据），
    自动发布保持「能力已核验 + 官方 OAuth」双门槛；manual 模式由用户手动执行，不入队。
    """
    mode = (job.payload_snapshot or {}).get("mode", "")
    if mode == "draft":
        queue.enqueue(
            db,
            owner_user_id=job.owner_user_id,
            queue="platform-publish",
            kind="wechat_draft",
            payload={
                "publishJobId": job.id,
                "accountId": job.connected_account_id,
                "target": "draft",
            },
        )
        return
    if mode == "auto":
        account = db.get(models.ConnectedAccount, job.connected_account_id)
        cap = connectors.get_capability(job.platform)
        if account and cap.confirmed and account.auth_source == "oauth":
            queue.enqueue(
                db,
                owner_user_id=job.owner_user_id,
                queue="platform-publish",
                kind="publish",
                payload={"publishJobId": job.id, "accountId": job.connected_account_id},
            )


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
    snapshot = job.payload_snapshot or {}
    return schemas.PublishJobOut(
        id=job.id,
        platform=job.platform,
        platform_label=cap.label,
        account_id=job.connected_account_id or "",
        account_name="",
        content_item_id=job.content_item_id or "",
        content_title=snapshot.get("title", ""),
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
        mode=snapshot.get("mode") or "",
        permalink=snapshot.get("permalink") or "",
        updated_at=job.updated_at,
    )


def _job_detail_out(job: models.PublishJob, records: list[models.PublishRecord]) -> schemas.PublishJobDetailOut:
    """详情 = 列表字段 + 状态流转历史 + 每次尝试的结果记录。

    升级前创建的任务没有历史，用当前状态兜底一条，保证时间线不出现空档。
    """
    history = list(job.status_history or [])
    if not history:
        history = publish_state.initial_history(
            job.status,
            actor="system",
            note="历史初始化（升级前无流转记录）",
        )
    return schemas.PublishJobDetailOut(
        **_job_out(job).model_dump(),
        status_history=history,
        records=[
            schemas.PublishRecordOut(
                id=record.id,
                platform=record.platform,
                remote_content_id=record.remote_content_id,
                permalink=record.permalink,
                published_at=record.published_at,
                result_status=record.result_status,
                created_at=record.created_at,
            )
            for record in records
        ],
    )
