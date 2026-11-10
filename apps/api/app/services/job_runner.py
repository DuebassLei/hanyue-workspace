"""异步任务执行器：把 queued 任务真实推进到终态（设计 §6.6 / §11.1）。

所有失败都带明确 errorCode，外部能力缺失时绝不写入 0 或占位数据。
"""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import update

from .. import models
from ..connectors import registry
from ..connectors.wechat_mp import WeChatMPConnector, WeChatMPError
from ..core import queue
from ..core.config import get_settings
from ..core.db import SessionLocal
from ..core.deps import default_workspace
from ..core.security import decrypt_secret, sign_asset_url
from . import image_provider, image_service, publish_state, wechat_article

RETRYABLE_CODES = {"provider_unreachable", "provider_error", "provider_timeout", "rate_limited"}
MAX_ATTEMPTS = queue.MAX_ATTEMPTS


class JobFailure(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ext(mime: str) -> str:
    return {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(mime, ".bin")


def _current_status(job_id: str) -> str | None:
    """用独立会话读最新状态，用于协作式取消，不干扰当前执行会话。"""
    session = SessionLocal()
    try:
        row = session.get(models.BackgroundJob, job_id)
        return row.status if row else None
    finally:
        session.close()


def _write_terminal(db, job_id: str, **values) -> bool:
    """条件写回终态：cancelled 的任务不允许被成功/失败覆盖。"""
    changed = db.execute(
        update(models.BackgroundJob)
        .where(
            models.BackgroundJob.id == job_id,
            models.BackgroundJob.status.notin_(["cancelled", "succeeded", "failed"]),
        )
        .values(**values)
    )
    db.commit()
    return changed.rowcount == 1


def mark_failed(job_id: str, code: str, message: str) -> None:
    db = SessionLocal()
    try:
        _write_terminal(
            db,
            job_id,
            status="failed",
            error_code=code,
            error_message=message[:1000],
            completed_at=_utcnow(),
        )
    finally:
        db.close()


def run_job(job_id: str) -> None:
    db = SessionLocal()
    try:
        # 原子领取（CAS）：并发消费者里只有 rowcount==1 的那个能进入执行体，
        # 避免重复发布 / 重复生图入库。
        claimed = db.execute(
            update(models.BackgroundJob)
            .where(
                models.BackgroundJob.id == job_id,
                models.BackgroundJob.status == "queued",
            )
            .values(
                status="running",
                started_at=_utcnow(),
                attempt_count=models.BackgroundJob.attempt_count + 1,
            )
        )
        db.commit()
        if claimed.rowcount != 1:
            return
        queue.mark_dispatched_done(job_id)

        job = db.get(models.BackgroundJob, job_id)
        for attempt in range(job.attempt_count, MAX_ATTEMPTS + 1):
            if _current_status(job_id) == "cancelled":
                return
            try:
                result = _execute(db, job)
                if _current_status(job_id) == "cancelled":
                    return
                if _write_terminal(
                    db,
                    job_id,
                    status="succeeded",
                    progress=100,
                    result=result,
                    completed_at=_utcnow(),
                ):
                    db.refresh(job)
                return
            except JobFailure as exc:
                if exc.retryable and attempt < MAX_ATTEMPTS:
                    db.execute(
                        update(models.BackgroundJob)
                        .where(models.BackgroundJob.id == job_id)
                        .values(attempt_count=attempt + 1, progress=min(90, 30 * attempt))
                    )
                    db.commit()
                    time.sleep(0.4 * (2 ** (attempt - 1)))  # 指数退避
                    if _current_status(job_id) == "cancelled":
                        return
                    continue
                _write_terminal(
                    db,
                    job_id,
                    status="failed",
                    error_code=exc.code,
                    error_message=exc.message[:1000],
                    completed_at=_utcnow(),
                )
                db.refresh(job)
                _on_failure(db, job)
                return
            except Exception as exc:  # 兜底，保证任务不会永远 queued/running
                _write_terminal(
                    db,
                    job_id,
                    status="failed",
                    error_code="internal_error",
                    error_message=f"{type(exc).__name__}: {exc}"[:1000],
                    completed_at=_utcnow(),
                )
                db.refresh(job)
                _on_failure(db, job)
                return
    finally:
        db.close()


def _on_failure(db, job: models.BackgroundJob) -> None:
    """把关联的旧式任务表（同步任务/发布任务）一并推进到终态，避免状态悬空。"""
    payload = job.payload or {}
    if job.queue == "platform-sync":
        sync_job = db.get(models.PlatformSyncJob, payload.get("syncJobId"))
        if sync_job:
            sync_job.status = "failed"
            sync_job.error_code = job.error_code
            sync_job.started_at = sync_job.started_at or job.started_at
            sync_job.completed_at = job.completed_at
            db.commit()
    if job.queue == "platform-publish":
        publish_job = db.get(models.PublishJob, payload.get("publishJobId"))
        if publish_job:
            publish_job.error_code = job.error_code
            publish_job.error_message = job.error_message
            db.commit()


def _execute(db, job: models.BackgroundJob) -> dict:
    handlers = {
        "ai-image": _handle_ai_image,
        "image-processing": _handle_image_processing,
        "platform-sync": _handle_platform_sync,
        "platform-publish": _handle_platform_publish,
    }
    handler = handlers.get(job.queue)
    if not handler:
        raise JobFailure("unknown_queue", f"未注册的任务队列：{job.queue}")
    return handler(db, job)


def _store_asset(db, user, workspace_id: str, raw: bytes, mime: str, name: str, **extra) -> models.Asset:
    settings = get_settings()
    key = f"{user.id}/{uuid.uuid4().hex}{_ext(mime)}"
    path = image_service.storage_path(settings.storage_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    from PIL import Image
    import io

    with Image.open(io.BytesIO(raw)) as image:
        width, height = image.size
    asset = models.Asset(
        owner_user_id=user.id,
        workspace_id=workspace_id,
        storage_key=key,
        original_name=name,
        mime_type=mime,
        size_bytes=len(raw),
        width=width,
        height=height,
        checksum=hashlib.sha256(raw).hexdigest(),
        **extra,
    )
    db.add(asset)
    db.flush()
    return asset


def _handle_ai_image(db, job: models.BackgroundJob) -> dict:
    payload = job.payload or {}
    user = db.get(models.User, job.owner_user_id)
    workspace = default_workspace(db, user)
    try:
        images = image_provider.generate_images(
            db,
            job.owner_user_id,
            prompt=payload.get("prompt") or "",
            aspect_ratio=payload.get("aspectRatio") or "3:4",
            count=int(payload.get("count") or 1),
            model_id=payload.get("modelId"),
        )
    except image_provider.ImageProviderUnavailable as exc:
        raise JobFailure(exc.code, exc.message, exc.code in RETRYABLE_CODES) from exc
    asset_ids: list[str] = []
    for index, (raw, mime) in enumerate(images, start=1):
        asset = _store_asset(
            db,
            user,
            workspace.id,
            raw,
            mime,
            f"ai-{index}-{uuid.uuid4().hex[:6]}",
            source_type="generated",
            prompt=payload.get("prompt") or "",
            license_note="AI 生成，发布前请确认平台内容规范与商用授权",
        )
        asset_ids.append(asset.id)
    db.commit()
    return {
        "assetIds": asset_ids,
        "count": len(asset_ids),
        # 前端要把结果直接写进正文，这里顺带给出可匿名访问的签名直链
        "assetUrls": [_signed_asset_url(aid) for aid in asset_ids],
    }


def _signed_asset_url(asset_id: str) -> str:
    """素材的签名直链（相对路径）。<img src> 带不了 Authorization，只能靠签名。"""
    return f"{get_settings().api_prefix}/assets/{asset_id}/raw?{sign_asset_url(asset_id)}"


def _handle_image_processing(db, job: models.BackgroundJob) -> dict:
    payload = job.payload or {}
    source = db.get(models.Asset, payload.get("assetId"))
    if not source or source.owner_user_id != job.owner_user_id:
        raise JobFailure("asset_not_found", "指定的素材不存在")
    settings = get_settings()
    path = image_service.storage_path(settings.storage_dir, source.storage_key)
    if not path.is_file():
        raise JobFailure("asset_file_missing", "素材原文件不存在")
    operation = payload.get("operation")
    if operation not in image_service.OPERATIONS:
        raise JobFailure("unsupported_operation", f"不支持的操作：{operation}")
    try:
        outputs = image_service.apply_operation(
            path.read_bytes(), operation, payload.get("options") or {}
        )
    except image_service.ImageValidationError as exc:
        raise JobFailure("invalid_image", str(exc)) from exc
    user = db.get(models.User, job.owner_user_id)
    asset_ids: list[str] = []
    for index, (output, mime, _size) in enumerate(outputs, start=1):
        suffix = f"-{index}" if len(outputs) > 1 else ""
        asset = _store_asset(
            db,
            user,
            source.workspace_id,
            output,
            mime,
            f"{source.original_name}-{operation}{suffix}",
            source_type="derived",
            parent_asset_id=source.id,
        )
        asset_ids.append(asset.id)
    image_job = db.get(models.ImageJob, payload.get("imageJobId"))
    if image_job:
        image_job.status = "succeeded"
        image_job.progress = 100
        image_job.output_asset_ids = asset_ids
        image_job.completed_at = _utcnow()
    db.commit()
    return {
        "assetIds": asset_ids,
        "operation": operation,
        "panelCount": len(asset_ids),
        "assetUrls": [_signed_asset_url(aid) for aid in asset_ids],
    }


def _handle_platform_sync(db, job: models.BackgroundJob) -> dict:
    payload = job.payload or {}
    account = db.get(models.ConnectedAccount, payload.get("accountId"))
    if not account:
        raise JobFailure("account_not_found", "平台账号不存在")
    sync_job = db.get(models.PlatformSyncJob, payload.get("syncJobId"))
    if sync_job:
        sync_job.status = "running"
        sync_job.started_at = _utcnow()
        db.commit()

    connector = registry.get_connector(account.platform, db)
    access_token = decrypt_secret(account.access_token_ciphertext)
    if not getattr(connector, "configured", False):
        raise JobFailure("connector_unavailable", "该平台尚未配置官方应用凭据，无法采集数据")
    if not access_token:
        raise JobFailure("authorization_required", "账号未完成官方 OAuth 授权，无法采集数据")

    try:
        if (payload.get("syncType") or "account") == "account":
            records = connector.sync_account_metrics(
                access_token=access_token, cursor=(sync_job.cursor if sync_job else None)
            )
        else:
            records = connector.sync_content_metrics(
                access_token=access_token, cursor=(sync_job.cursor if sync_job else None)
            )
    except registry.ConnectorUnavailable as exc:
        raise JobFailure("connector_unavailable", str(exc)) from exc
    if not isinstance(records, list):
        raise JobFailure("invalid_platform_response", "平台返回的数据格式不符合适配器契约")

    written = 0
    for record in records:
        metrics = record.get("metrics")
        if not isinstance(metrics, dict) or not metrics:
            continue  # 缺失指标不填 0
        db.add(
            models.MetricSnapshot(
                owner_user_id=account.owner_user_id,
                connected_account_id=account.id,
                platform=account.platform,
                remote_content_id=record.get("remoteContentId"),
                normalized_metrics=metrics,
                raw_metrics=record.get("raw") or {},
                source="api",
            )
        )
        written += 1
    account.last_synced_at = _utcnow()
    if sync_job:
        sync_job.status = "succeeded"
        sync_job.completed_at = _utcnow()
        sync_job.cursor = payload.get("nextCursor") or sync_job.cursor
    db.commit()
    return {"records": written, "syncedAt": account.last_synced_at.isoformat()}


def _handle_platform_publish(db, job: models.BackgroundJob) -> dict:
    payload = job.payload or {}
    publish_job = db.get(models.PublishJob, payload.get("publishJobId"))
    if not publish_job:
        raise JobFailure("publish_job_not_found", "发布任务不存在")
    account = db.get(models.ConnectedAccount, publish_job.connected_account_id)
    if not account:
        raise JobFailure("account_not_found", "平台账号不存在")
    if (publish_job.target or "publish") == "draft" or payload.get("target") == "draft":
        return _handle_wechat_draft(db, job, publish_job, account)
    connector = registry.get_connector(account.platform, db)
    access_token = decrypt_secret(account.access_token_ciphertext)
    try:
        result = connector.publish(
            access_token=access_token, payload=publish_job.payload_snapshot or {}
        )
    except registry.ConnectorUnavailable as exc:
        raise JobFailure("connector_unavailable", str(exc)) from exc
    remote_id = (result or {}).get("remoteContentId")
    if not remote_id:
        raise JobFailure("invalid_platform_response", "平台未返回内容 ID，需人工核实是否已发布")
    publish_job.remote_content_id = remote_id
    publish_job.status = publish_state.SUCCEEDED
    db.commit()
    return {"remoteContentId": remote_id}


def _read_asset_bytes(asset: models.Asset) -> bytes:
    path = image_service.storage_path(get_settings().storage_dir, asset.storage_key)
    if not path.is_file():
        raise JobFailure("asset_file_missing", f"素材文件缺失：{asset.original_name}")
    return path.read_bytes()


def _handle_wechat_draft(
    db,
    job: models.BackgroundJob,
    publish_job: models.PublishJob,
    account: models.ConnectedAccount,
) -> dict:
    """把稿件推到公众号草稿箱：封面永久素材 → 正文图片 → draft/add。

    中间任何一步失败都带微信真实 errcode 说明，绝不写入假 media_id。
    """
    # 多账号：按任务所属账号取连接器（账号自有凭据优先，部署级默认凭据兜底）
    connector = registry.get_account_connector(account, db)
    if not isinstance(connector, WeChatMPConnector):
        raise JobFailure(
            "connector_unavailable",
            "该账号未绑定凭据，且未配置默认 AppID/AppSecret：请在账号上录入凭据或设置默认凭据",
        )
    if account.auth_source != "app_secret" or account.status != "connected":
        raise JobFailure(
            "authorization_required", "公众号账号未完成凭据绑定与验证，请重新「绑定并验证」"
        )

    snapshot = publish_job.payload_snapshot or {}
    assets: list[models.Asset] = []
    for asset_id in snapshot.get("assetIds") or []:
        asset = db.get(models.Asset, asset_id)
        if not asset or asset.owner_user_id != job.owner_user_id:
            raise JobFailure("asset_not_found", f"素材 {asset_id} 不存在或无权访问")
        assets.append(asset)
    if not assets:
        raise JobFailure("thumb_required", "微信草稿必须有封面图（第一张素材作为 thumb_media_id）")

    publish_job.status = publish_state.PUBLISHING
    db.commit()

    try:
        thumb_data, thumb_mime, thumb_name = wechat_article.prepare_thumb(
            _read_asset_bytes(assets[0])
        )
        thumb = connector.upload_thumb(
            db, data=thumb_data, filename=thumb_name, mime=thumb_mime
        )
        inline_urls: list[str] = []
        for asset in assets[1:]:
            data, mime, name = wechat_article.prepare_inline(_read_asset_bytes(asset))
            inline_urls.append(
                connector.upload_inline_image(db, data=data, filename=name, mime=mime)
            )
        html = wechat_article.render_html(
            snapshot.get("body") or "",
            inline_urls,
            snapshot.get("layoutTokens") or None,
        )
        article = wechat_article.build_article(
            title=snapshot.get("title") or "",
            html=html,
            thumb_media_id=thumb["mediaId"],
            digest=snapshot.get("digest") or "",
            author=snapshot.get("author") or "",
            content_source_url=snapshot.get("contentSourceUrl") or "",
            need_open_comment=1 if snapshot.get("needOpenComment") else 0,
            only_fans_can_comment=1 if snapshot.get("onlyFansCanComment") else 0,
        )
        media_id = connector.add_draft(db, article=article)
    except WeChatMPError as exc:
        raise JobFailure(exc.code, exc.message, exc.retryable) from exc
    except wechat_article.ArticleError as exc:
        raise JobFailure("invalid_image", str(exc)) from exc

    pushed_at = _utcnow()
    publish_job.remote_content_id = media_id
    publish_job.status = publish_state.SUCCEEDED
    publish_job.payload_snapshot = {
        **snapshot,
        "draftMediaId": media_id,
        "thumbMediaId": thumb["mediaId"],
        "inlineImages": len(inline_urls),
        "pushedAt": pushed_at.isoformat(),
    }
    db.add(
        models.PublishRecord(
            publish_job_id=publish_job.id,
            platform=publish_job.platform,
            remote_content_id=media_id,
            published_at=None,
            permalink=None,
            result_status="draft_created",
        )
    )
    db.commit()
    return {
        "draftMediaId": media_id,
        "thumbMediaId": thumb["mediaId"],
        "inlineImages": len(inline_urls),
        "target": "draft",
    }

