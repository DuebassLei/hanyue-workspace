"""Durable draft schedules: DB compare-and-swap, never a browser-read side effect.

A claimed schedule and its BackgroundJob are committed together. Multiple API/worker
processes may scan concurrently; only the CAS winner creates an execution job.
"""
from datetime import datetime, timezone
from threading import Event, Thread

from sqlalchemy import select, update

from .. import models
from ..core import queue
from ..core.db import SessionLocal
from ..core.logging import get_logger
from . import publish_state

logger = get_logger(__name__)
_stop = Event()
_thread = None


def scan_due(now=None) -> int:
    now = now or datetime.now(timezone.utc)
    ids = []
    with SessionLocal() as db:
        rows = db.scalars(select(models.PublishJob).where(
            models.PublishJob.target == "draft",
            models.PublishJob.platform == "wechat_official",
            models.PublishJob.status == publish_state.QUEUED,
            models.PublishJob.scheduled_at.is_not(None),
            models.PublishJob.scheduled_at <= now,
        ).order_by(models.PublishJob.scheduled_at).limit(100)).all()
        for row in rows:
            history = [*(row.status_history or []), publish_state.history_entry(
                publish_state.APPROVED, note="计划到期，自动领取草稿推送任务",
            )]
            changed = db.execute(update(models.PublishJob).where(
                models.PublishJob.id == row.id,
                models.PublishJob.status == publish_state.QUEUED,
                models.PublishJob.scheduled_at <= now,
            ).values(status=publish_state.APPROVED, status_history=history)
                .execution_options(synchronize_session=False)).rowcount
            if changed != 1:
                continue
            background = queue.create_job(
                db, owner_user_id=row.owner_user_id, queue="platform-publish", kind="wechat_draft",
                payload={"publishJobId": row.id, "accountId": row.connected_account_id, "target": "draft"},
            )
            ids.append(background.id)
        db.commit()
        # Covers the commit->dispatch crash window without waiting for a restart.
        pending = db.scalars(select(models.BackgroundJob).where(
            models.BackgroundJob.kind == "wechat_draft", models.BackgroundJob.status == "queued",
        ).limit(200)).all()
        pending_ids = [job.id for job in pending]
    for job_id in pending_ids:
        queue.dispatch(job_id, from_recovery=True)
    return len(ids)


def _loop():
    while not _stop.is_set():
        try:
            scan_due()
        except Exception:
            logger.exception("草稿推送到期扫描失败，下轮将自动恢复")
        _stop.wait(5)


def start():
    global _thread
    # 与 APScheduler 共用同一个开关：测试 / 显式关闭定时任务的部署里，
    # 后台扫描线程不能偷偷起来执行排期（否则用例之间会互相干扰）。
    from ..core.config import get_settings
    if not get_settings().scheduler_enabled:
        logger.info("定时任务已关闭，草稿排期扫描器不启动")
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = Thread(target=_loop, name="draft-schedule-dispatcher", daemon=True)
    _thread.start()


def stop():
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=6)
