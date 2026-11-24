"""统一任务查询（设计 §6.6）：断线后可凭 jobId 恢复状态。

除新的 `background_jobs` 外，也兼容查询旧的 image_jobs / platform_sync_jobs /
ai_generations / publish_jobs，前端只需一个端点。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Response
from sqlalchemy import select

from .. import models
from ..core import pagination
from ..core.deps import ApiError, CurrentUser, DbSession, not_found
from ..core.queue import backend_name
from ..core.config import get_settings
from ..core.security import sign_asset_url
from ..services import scheduled_tasks

settings = get_settings()

router = APIRouter(tags=["jobs"])

TERMINAL = {"succeeded", "failed", "cancelled", "stopped"}


def _out(job: models.BackgroundJob) -> dict:
    return {
        "id": job.id,
        "type": "background",
        "queue": job.queue,
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "result": job.result or {},
        "errorCode": job.error_code,
        "errorMessage": job.error_message,
        "attemptCount": job.attempt_count,
        "startedAt": job.started_at.isoformat() if job.started_at else None,
        "completedAt": job.completed_at.isoformat() if job.completed_at else None,
        "createdAt": job.created_at.isoformat() if job.created_at else None,
    }


@router.get("/jobs/{job_id}")
def get_job(job_id: str, user: CurrentUser, db: DbSession) -> dict:
    job = db.get(models.BackgroundJob, job_id)
    if job:
        if job.owner_user_id != user.id:
            raise not_found("任务")
        return _out(job)

    legacy = db.get(models.ImageJob, job_id)
    if legacy:
        if legacy.owner_user_id != user.id:
            raise not_found("任务")
        return {
            "id": legacy.id, "type": "image", "queue": "image-processing",
            "status": legacy.status, "progress": legacy.progress,
            "result": {
                "assetIds": legacy.output_asset_ids or [],
                # 签名直链：前端直接写进正文，<img> 不带鉴权头也能取图
                "assetUrls": [
                    f"{settings.api_prefix}/assets/{aid}/raw?{sign_asset_url(aid)}"
                    for aid in (legacy.output_asset_ids or [])
                ],
            },
            "errorCode": legacy.error_code, "errorMessage": None,
            "attemptCount": 1,
            "startedAt": legacy.started_at.isoformat() if legacy.started_at else None,
            "completedAt": legacy.completed_at.isoformat() if legacy.completed_at else None,
            "createdAt": legacy.created_at.isoformat() if legacy.created_at else None,
        }

    sync_job = db.get(models.PlatformSyncJob, job_id)
    if sync_job:
        account = db.get(models.ConnectedAccount, sync_job.connected_account_id)
        if not account or account.owner_user_id != user.id:
            raise not_found("任务")
        return {
            "id": sync_job.id, "type": "platform-sync", "queue": "platform-sync",
            "status": sync_job.status, "progress": 100 if sync_job.status == "succeeded" else 0,
            "result": {}, "errorCode": sync_job.error_code, "errorMessage": None,
            "attemptCount": 1,
            "startedAt": sync_job.started_at.isoformat() if sync_job.started_at else None,
            "completedAt": sync_job.completed_at.isoformat() if sync_job.completed_at else None,
            "createdAt": sync_job.created_at.isoformat() if sync_job.created_at else None,
        }

    generation = db.get(models.AiGeneration, job_id)
    if generation:
        if generation.owner_user_id != user.id:
            raise not_found("任务")
        return {
            "id": generation.id, "type": "ai-generation", "queue": "ai-text",
            "status": generation.status, "progress": 100 if generation.status == "succeeded" else 0,
            "result": {"text": generation.result_text or ""},
            "errorCode": generation.error_code, "errorMessage": None,
            "attemptCount": 1, "startedAt": None, "completedAt": None,
            "createdAt": generation.created_at.isoformat() if generation.created_at else None,
        }

    publish_job = db.get(models.PublishJob, job_id)
    if publish_job:
        if publish_job.owner_user_id != user.id:
            raise not_found("任务")
        return {
            "id": publish_job.id, "type": "publish", "queue": "platform-publish",
            "status": publish_job.status, "progress": 100 if publish_job.status == "succeeded" else 0,
            "result": {"remoteContentId": publish_job.remote_content_id},
            "errorCode": publish_job.error_code, "errorMessage": publish_job.error_message,
            "attemptCount": publish_job.attempt_count,
            "startedAt": None, "completedAt": None,
            "createdAt": publish_job.created_at.isoformat() if publish_job.created_at else None,
        }
    raise not_found("任务")


@router.get("/jobs")
def list_jobs(
    user: CurrentUser,
    db: DbSession,
    response: Response,
    limit: int = Query(default=50, le=200),
    cursor: str | None = Query(default=None),
) -> dict:
    """按 (created_at, id) 倒序做真 keyset 分页；游标来自上一页的 nextCursor。"""
    stmt = select(models.BackgroundJob).where(
        models.BackgroundJob.owner_user_id == user.id
    )
    rows, next_cursor = pagination.paginate(
        db,
        stmt,
        models.BackgroundJob,
        sort_column=models.BackgroundJob.created_at,
        cursor=cursor,
        limit=limit,
    )
    pagination.set_cursor_header(response, next_cursor)
    return {
        "items": [_out(row) for row in rows],
        "nextCursor": next_cursor,
        "backend": backend_name(),
    }


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, user: CurrentUser, db: DbSession) -> dict:
    job = db.get(models.BackgroundJob, job_id)
    if not job or job.owner_user_id != user.id:
        raise not_found("任务")
    if job.kind == 'novel.continue':
        from ..services import novel_continue
        novel_continue.lock_project(db, (job.payload or {}).get('novelProjectId'))
    job = db.scalars(select(models.BackgroundJob).where(models.BackgroundJob.id == job_id).with_for_update().execution_options(populate_existing=True)).first()
    if job.status in TERMINAL:
        raise ApiError("invalid_transition", f"任务已处于终态 {job.status}", status_code=409)
    job.status = "cancelled"
    job.completed_at = datetime.now(timezone.utc)
    db.commit()
    # 定时任务触发的手动取消也要落账：否则 runs 会一直显示「执行中」，直到下次重启对账
    payload = job.payload or {}
    if payload.get("scheduledRunId"):
        scheduled_tasks.sync_run_from_job(
            db,
            job.id,
            status="cancelled",
            started_at=job.started_at,
            completed_at=job.completed_at,
        )
    return _out(job)
