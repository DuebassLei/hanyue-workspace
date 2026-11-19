"""任务队列（设计 §5.8 / §11.1）：Redis 优先，本地线程池兜底。

- 生产：配置 `REDIS_URL`，API 只写任务 + RPUSH，Worker 进程 BLPOP 消费。
- 开发：没有 Redis 时用进程内线程池真实执行，任务状态照样落库。
- 断线恢复：`recover_pending()` 只补投「启动前创建且尚未投递」的任务；
  `recover_stale_running()` 回收执行中进程崩溃遗留的 running 任务。
  两者配合 `run_job` 的原子领取（CAS），保证不会重复执行同一任务。
"""

from __future__ import annotations

import os
import threading
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
# 进程内在跑去重：线程池模式没有 Redis SETNX，重复 dispatch（恢复 + 接力撞车）
# 会把同一 job 投两次。CAS 领取能保证只执行一次，但白占一个工作线程，这里直接挡掉。
_INFLIGHT: set[str] = set()
_INFLIGHT_LOCK = threading.Lock()


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
    with _INFLIGHT_LOCK:
        if job_id in _INFLIGHT:
            return False
        _INFLIGHT.add(job_id)
    try:
        _POOL.submit(_run, job_id)
    except RuntimeError as exc:  # 线程池已关闭（测试/进程退出）
        with _INFLIGHT_LOCK:
            _INFLIGHT.discard(job_id)
        logger.warning("线程池拒绝任务 %s：%s", job_id, exc)
        return False
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
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.discard(job_id)


# 同组串行：一个贴图集的多张图共享风格参考与供应商配额，并发打过去只会撞 429。
# 做法：同 stickerChainKey 的任务只投递第一个，其余等前序落定后由
# chain_next_if_done 接力投递（CAS 领取保证多消费者下也不重复执行）；跨集合照常并行。
_STICKER_CHAIN_KEY = "stickerChainKey"


def enqueue_chain(db, *, owner_user_id: str, queue: str, kind: str, payloads: list[dict]) -> list[str]:
    """把一组任务按序落库（不立即投递）；调用方给每个 payload 带上同一 stickerChainKey。

    首单由执行器侧的 chain_next_if_done 语义自然起头：这里先投第一单。
    """
    job_ids: list[str] = []
    for item in payloads:
        job = models.BackgroundJob(
            owner_user_id=owner_user_id,
            queue=queue,
            kind=kind,
            payload=item,
            status="queued",
        )
        db.add(job)
        db.flush()
        job_ids.append(job.id)
    db.commit()
    if job_ids:
        dispatch(job_ids[0])
    return job_ids


def _chain_members(db, group_key: str, *, owner_user_id: str | None = None):
    """取同链未落定的成员（queued/running），按创建顺序。

    payload 是 JSON 列，链键只能回 Python 侧比对；用 owner 谓词把扫描面收窄到
    单个用户的 ai-image 任务，避免一个用户的重试去翻全库。
    """
    stmt = select(models.BackgroundJob).where(
        models.BackgroundJob.queue == "ai-image",
        models.BackgroundJob.status.in_(("queued", "running")),
    )
    if owner_user_id:
        stmt = stmt.where(models.BackgroundJob.owner_user_id == owner_user_id)
    rows = db.scalars(stmt.order_by(models.BackgroundJob.created_at, models.BackgroundJob.id)).all()
    return [row for row in rows if (row.payload or {}).get(_STICKER_CHAIN_KEY) == group_key]


def chain_next_if_done(payload: dict, *, owner_user_id: str | None = None) -> None:
    """任务落定后由执行器回调：同链若没有 running 成员，投递最早的 queued 成员。"""
    group_key = (payload or {}).get(_STICKER_CHAIN_KEY)
    if not group_key:
        return
    db = SessionLocal()
    try:
        members = _chain_members(db, group_key, owner_user_id=owner_user_id)
        if any(row.status == "running" for row in members):
            return
        nxt = next((row for row in members if row.status == "queued"), None)
        if nxt is not None:
            dispatch(nxt.id)
    finally:
        db.close()


def chain_busy(db, *, owner_user_id: str, group_key: str) -> bool:
    """该链是否还有未落定成员（queued 或 running）——调用方据此拒绝新一批任务。

    返回「链上是否还有成员」而不是「本次是否真投出去」：调用方要据此拒绝新任务，
    若按投递结果判断，去重生效（已投递/在途）时反而会放行，让新旧两批并存，
    串行链就退化成两条交错链。
    顺带自愈：只剩排队、没人跑（接力断掉或重启后没补投）时把链头补投起来。
    """
    members = _chain_members(db, group_key, owner_user_id=owner_user_id)
    if not members:
        return False
    queued = [row for row in members if row.status == "queued"]
    if queued and not any(row.status == "running" for row in members):
        # from_recovery=True：Redis 路径靠 SETNX 标记去重，避免重复 rpush
        dispatch(queued[0].id, from_recovery=True)
    return True


def recover_pending() -> int:
    """重投「已入队但尚未开始」的任务（断线恢复，设计 §6.6）。

    线程池模式只处理本进程启动之前创建的任务，避免与本次 enqueue 叠加；
    Redis 模式由 SETNX 去重键保证不重复 rpush。
    贴图链成员不在这里全量补投——每链只投最早的一单，其余靠链式接力，
    否则重启会把一条串行链打回并发。
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
        plain_ids = [row.id for row in rows if not (row.payload or {}).get(_STICKER_CHAIN_KEY)]
        chain_first: dict[str, str] = {}
        chain_has_running: set[str] = set()
        chain_rows = db.scalars(
            select(models.BackgroundJob)
            .where(
                models.BackgroundJob.queue == "ai-image",
                models.BackgroundJob.status.in_(("queued", "running")),
            )
            .order_by(models.BackgroundJob.created_at, models.BackgroundJob.id)
        ).all()
        for row in chain_rows:
            key = (row.payload or {}).get(_STICKER_CHAIN_KEY)
            if not key:
                continue
            if row.status == "running":
                chain_has_running.add(key)
            else:
                chain_first.setdefault(key, row.id)
        ids = plain_ids + [jid for key, jid in chain_first.items() if key not in chain_has_running]
    finally:
        db.close()
    return sum(1 for job_id in ids if dispatch(job_id, from_recovery=True))


def recover_stale_running(lease_seconds: int | None = None) -> int:
    """回收执行中进程崩溃遗留的 running 任务。

    超过租约未推进的任务：未达重试上限则回到 queued（由随后的 recover_pending 统一投递），
    已达上限直接标 failed（明确 errorCode=worker_crashed），绝不永久卡在 running。

    判活字段是 started_at：长任务在推进阶段时会心跳刷新 started_at，
    因此「活着但慢」不会被误杀；真僵尸不心跳，照常回收。
    """
    from .config import get_settings

    settings = get_settings()
    default_lease = lease_seconds or settings.job_lease_seconds
    now = datetime.now(timezone.utc).timestamp()
    db = SessionLocal()
    stale: list[str] = []
    try:
        rows = db.scalars(
            select(models.BackgroundJob).where(models.BackgroundJob.status == "running")
        ).all()
        for row in rows:
            if row.kind.startswith("user-script."):
                cutoff = now - max(
                    default_lease,
                    int((row.payload or {}).get("scriptTimeoutSeconds") or 0) + 30,
                )
            else:
                cutoff = now - default_lease
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
