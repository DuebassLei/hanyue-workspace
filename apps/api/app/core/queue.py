"""任务队列（设计 §5.8 / §11.1）：Redis 优先，本地线程池兜底。

- 生产：配置 `REDIS_URL`，API 只写任务 + RPUSH，Worker 进程 BLPOP 消费。
- 开发：没有 Redis 时用进程内线程池真实执行，任务状态照样落库。
- 断线恢复：`recover_pending()` 只补投「启动前创建且尚未投递」的任务；
  `recover_stale_running()` 回收执行中进程崩溃遗留的 running 任务。
  两者配合 `run_job` 的原子领取（CAS），保证不会重复执行同一任务。
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import select, update

from .. import models
from .db import SessionLocal
from .logging import get_logger

QUEUE_KEY = "hanyue:jobs"
_DEDUPE_PREFIX = "hanyue:job:pending:"
_DEDUPE_TTL = 24 * 3600
MAX_ATTEMPTS = 3
# 进程启动瞬间：本进程内 enqueue 的任务 created_at 一定晚于它，恢复时自动排除
BOOT_TIME = datetime.now(timezone.utc)

logger = get_logger("app.queue")
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hanyue-job")


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite 会把带时区的 datetime 读回 naive，比较前统一补 UTC。"""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def backend_name() -> str:
    try:
        import redis  # noqa: F401
    except ImportError:
        return "threadpool"
    return "redis" if os.getenv("REDIS_URL", "").strip() else "threadpool"


def _redis_client():
    import redis

    return redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)


def enqueue(
    db,
    *,
    owner_user_id: str,
    queue: str,
    kind: str,
    payload: dict,
) -> str:
    """创建任务并投递；返回 jobId。调用方负责在此之前已 commit 业务实体。"""
    job = models.BackgroundJob(
        owner_user_id=owner_user_id,
        queue=queue,
        kind=kind,
        payload=payload,
        status="queued",
    )
    db.add(job)
    db.commit()
    dispatch(job.id)
    return job.id


def dispatch(job_id: str, *, from_recovery: bool = False) -> bool:
    """投递一个 jobId；Redis 模式用 SETNX 去重，返回是否真的投递。"""
    if backend_name() == "redis":
        try:
            client = _redis_client()
            # 已在队列中（或已投递）就不重复 rpush；领取成功后会删除该标记
            if not client.set(_DEDUPE_PREFIX + job_id, "1", nx=True, ex=_DEDUPE_TTL):
                if from_recovery:
                    return False
            client.rpush(QUEUE_KEY, job_id)
            return True
        except Exception as exc:  # 配置错误时明确告警并回退，绝不吞掉任务
            logger.warning("Redis 投递失败，回退本地线程池：%s", exc)
    _POOL.submit(_run, job_id)
    return True


def mark_dispatched_done(job_id: str) -> None:
    """任务被原子领取后清除去重标记，之后若崩溃仍可由恢复逻辑补投。"""
    if backend_name() != "redis":
        return
    try:
        _redis_client().delete(_DEDUPE_PREFIX + job_id)
    except Exception as exc:
        logger.warning("清除投递标记失败（不影响执行）：%s", exc)


def _run(job_id: str) -> None:
    from ..services import job_runner

    try:
        job_runner.run_job(job_id)
    except Exception as exc:  # 兜底：线程异常也不能让任务永远停在 queued
        logger.exception("任务 %s 执行器异常", job_id)
        job_runner.mark_failed(job_id, "internal_error", str(exc))


def recover_pending() -> int:
    """重投「已入队但尚未开始」的任务（断线恢复，设计 §6.6）。

    线程池模式只处理本进程启动之前创建的任务，避免与本次 enqueue 叠加；
    Redis 模式由 SETNX 去重键保证不重复 rpush。
    """
    db = SessionLocal()
    try:
        rows = db.scalars(
            select(models.BackgroundJob).where(
                models.BackgroundJob.status == "queued",
                models.BackgroundJob.started_at.is_(None),
            )
        ).all()
        if backend_name() == "threadpool":
            rows = [row for row in rows if _as_utc(row.created_at) < BOOT_TIME]
        ids = [row.id for row in rows]
    finally:
        db.close()
    return sum(1 for job_id in ids if dispatch(job_id, from_recovery=True))


def recover_stale_running(lease_seconds: int | None = None) -> int:
    """回收执行中进程崩溃遗留的 running 任务。

    超过租约未推进的任务：未达重试上限则回到 queued（由随后的 recover_pending 统一投递），
    已达上限直接标 failed（明确 errorCode=worker_crashed），绝不永久卡在 running。
    """
    from .config import get_settings

    lease = lease_seconds or get_settings().job_lease_seconds
    cutoff = datetime.now(timezone.utc).timestamp() - lease
    db = SessionLocal()
    stale: list[str] = []
    try:
        rows = db.scalars(
            select(models.BackgroundJob).where(models.BackgroundJob.status == "running")
        ).all()
        for row in rows:
            started = _as_utc(row.started_at)
            if started is None or started.timestamp() > cutoff:
                continue
            if (row.attempt_count or 0) >= MAX_ATTEMPTS:
                db.execute(
                    update(models.BackgroundJob)
                    .where(
                        models.BackgroundJob.id == row.id,
                        models.BackgroundJob.status == "running",
                    )
                    .values(
                        status="failed",
                        error_code="worker_crashed",
                        error_message="任务执行进程中断且已达最大重试次数",
                        completed_at=datetime.now(timezone.utc),
                    )
                )
            else:
                changed = db.execute(
                    update(models.BackgroundJob)
                    .where(
                        models.BackgroundJob.id == row.id,
                        models.BackgroundJob.status == "running",
                    )
                    .values(status="queued", started_at=None, progress=0)
                )
                if changed.rowcount == 1:
                    stale.append(row.id)
        db.commit()
    finally:
        db.close()
    # 不在这里单独投递，交给调用方紧随其后的 recover_pending()，避免双重入队
    return len(stale)
