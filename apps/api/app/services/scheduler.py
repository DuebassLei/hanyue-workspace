"""APScheduler 装配：只当定时器，执行仍然回到 background_jobs（docs/scheduled-tasks.md §2.2）。

- 用 `AsyncIOScheduler`：它绑定当前事件循环。`BackgroundScheduler` 会自己起线程和
  事件循环，`uvicorn --reload` 下父子进程各起一份，同一任务被触发两次。
- jobstore 用内存：`scheduled_tasks` 表才是唯一真相，这里只是运行期投影。
- 到点只调 `scheduled_tasks.fire()`，它负责 CAS 推进 next_run_at、判并发、投递 job。
  重试/进度/取消/租约全在 job_runner 与 core/queue，这里一行不碰。
"""

from __future__ import annotations

from datetime import timezone

from apscheduler.events import EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.base import JobLookupError
from sqlalchemy import select

from .. import models
from ..core.config import get_settings
from ..core.logging import get_logger
from . import scheduled_tasks
from .scheduled_tasks import ScheduleError

logger = get_logger("app.scheduler")

_scheduler: AsyncIOScheduler | None = None


def running() -> bool:
    return _scheduler is not None and _scheduler.running


def instance() -> AsyncIOScheduler | None:
    return _scheduler


def _job_options(task: models.ScheduledTask) -> dict:
    settings = get_settings()
    grace = task.misfire_grace_seconds or settings.scheduler_misfire_grace
    return {
        "id": task.id,
        "name": f"{task.task_type}:{task.name}",
        "replace_existing": True,
        # 停机 6 小时醒来补跑 12 次拉取毫无意义：合并成一次
        "coalesce": True,
        "misfire_grace_time": max(1, int(grace)),
        # 注意：这保护的是「回调函数」而不是任务执行（回调投递完就返回），
        # 真正的执行互斥在 scheduled_tasks._launch 里自己判。
        "max_instances": 1,
        "kwargs": {"task_id": task.id},
    }


def _add(task: models.ScheduledTask) -> bool:
    if _scheduler is None:
        return False
    try:
        trigger = scheduled_tasks.trigger_of(task)
    except ScheduleError as exc:
        # 一条脏数据（手改库、旧数据）不能让整个调度器起不来，只跳过这一条
        logger.warning("定时任务 %s 的时间表达式非法，已跳过装载：%s", task.id, exc.message)
        return False
    _scheduler.add_job(scheduled_tasks.fire, trigger=trigger, **_job_options(task))
    return True


def sync(task: models.ScheduledTask) -> bool:
    """单条同步：启用的装进调度器，停用/删除的移出去。接口增删改后调用。"""
    if not running() or _scheduler is None:
        return False
    if task.enabled:
        return _add(task)
    unsync(task.id)
    return False


def unsync(task_id: str) -> None:
    if _scheduler is None:
        return
    try:
        _scheduler.remove_job(task_id)
    except JobLookupError:
        pass  # 本来就不在调度器里，不是错误


def load_all(db) -> int:
    """全量装载 enabled 的任务。返回真正装进去的条数。"""
    if _scheduler is None:
        return 0
    rows = db.scalars(
        select(models.ScheduledTask).where(models.ScheduledTask.enabled.is_(True))
    ).all()
    return sum(1 for task in rows if _add(task))


def _on_missed(event) -> None:
    """错过宽限的触发不会进 fire()，这里补一条 missed 账本，否则界面上无从解释。"""
    task_id = event.job_id
    try:
        scheduled_tasks.record_missed(task_id, reason="错过触发点，超出宽限时间已跳过")
    except Exception as exc:  # 监听器里抛异常会污染调度器日志，兜住并明确记录
        logger.warning("记录 missed 触发失败（task=%s）：%s", task_id, exc)


def start(db) -> int:
    """进程启动时装配。重复调用安全（已启动则只做一次增量装载）。"""
    global _scheduler
    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("SCHEDULER_ENABLED=false，定时调度器未启动（接口与表照常可用）")
        return 0
    if _scheduler is not None and _scheduler.running:
        return load_all(db)
    _scheduler = AsyncIOScheduler(timezone=timezone.utc)
    _scheduler.add_listener(_on_missed, EVENT_JOB_MISSED)
    count = load_all(db)
    _scheduler.start()
    logger.info("定时调度器已启动，装载任务 %s 个", count)
    return count


def stop() -> None:
    global _scheduler
    if _scheduler is None:
        return
    try:
        if _scheduler.running:
            # 不等在跑的回调：它们在投递后很快就返回，等反而拖慢退出
            _scheduler.shutdown(wait=False)
    except Exception as exc:
        logger.warning("调度器关闭异常（不影响退出）：%s", exc)
    finally:
        _scheduler = None
