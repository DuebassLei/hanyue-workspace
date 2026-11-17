"""定时任务管理（docs/scheduled-tasks.md §3）。

接口只做三件事：校验、落库、通知调度器同步。时间语义与触发语义都在
`services/scheduled_tasks.py`，执行在 `services/job_runner.py`。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Query, Response
from sqlalchemy import select

from .. import models, schemas
from ..core import pagination
from ..core.config import get_settings
from ..core.deps import ApiError, CurrentUser, DbSession, conflict, forbidden, not_found
from ..services import scheduled_tasks
from ..services import scheduler as scheduler_module
from ..services.scheduled_tasks import ScheduleError

router = APIRouter(prefix="/schedules", tags=["schedules"])

settings = get_settings()


def _system_writable() -> bool:
    """system 任务（如拉热点）能不能经接口改。

    `users` 表没有角色字段，所以不虚构管理员角色，改用配置三档：
    dev = 仅开发态可改；any = 任何登录用户；never = 一律只读，由 seed 维护。
    """
    mode = (settings.scheduler_system_write or "dev").strip().lower()
    if mode == "any":
        return True
    if mode == "never":
        return False
    return not settings.is_production


def _visible(task: models.ScheduledTask, user: models.User) -> bool:
    if task.scope == "system":
        return True  # 全局数据类任务所有人可见（热点榜本来就是共享的）
    return task.owner_user_id == user.id


def _editable(task: models.ScheduledTask, user: models.User) -> bool:
    if task.scope == "system":
        return _system_writable()
    return task.owner_user_id == user.id


def _require(task_id: str, user: models.User, db) -> models.ScheduledTask:
    task = db.get(models.ScheduledTask, task_id)
    if task is None or not _visible(task, user):
        raise not_found("定时任务")
    return task


def _require_editable(task: models.ScheduledTask, user: models.User) -> None:
    if not _editable(task, user):
        if task.scope == "system":
            raise forbidden("系统级定时任务在当前部署下为只读，请用 seed 维护")
        raise not_found("定时任务")  # 别人的任务不暴露存在性


def _iso(value) -> str | None:
    """统一输出带 UTC 偏移的 ISO 串。

    SQLite 会把 DateTime(timezone=True) 读回 naive，直接序列化会让前端当成本地时间，
    差 8 小时。这里显式补上 UTC 偏移，前端按需本地化。
    """
    stamp = scheduled_tasks.as_utc(value)
    return stamp.isoformat() if stamp else None


def _human_schedule(task: models.ScheduledTask) -> str:
    """人能读的时间描述；认不出来的形态原样给表达式，不猜。"""
    if task.schedule_kind == "interval":
        seconds = int(task.interval_seconds or 0)
        if seconds and seconds % 3600 == 0:
            return f"每 {seconds // 3600} 小时"
        if seconds and seconds % 60 == 0:
            return f"每 {seconds // 60} 分钟"
        return f"每 {seconds} 秒"
    parts = (task.cron or "").split()
    if len(parts) != 5:
        return task.cron or "—"
    minute, hour, dom, month, dow = parts
    if dom == "*" and month == "*" and dow == "*" and minute.isdigit():
        if hour == "*":
            return f"每小时第 {int(minute):02d} 分"
        if hour.startswith("*/") and hour[2:].isdigit():
            return f"每 {int(hour[2:])} 小时第 {int(minute):02d} 分"
        if all(piece.isdigit() for piece in hour.split(",")):
            times = "、".join(f"{int(piece):02d}:{int(minute):02d}" for piece in hour.split(","))
            if minute == "0":
                times = "、".join(f"{int(piece):02d}:00" for piece in hour.split(","))
            return f"每天 {times}"
    if minute.startswith("*/") and minute[2:].isdigit() and hour == "*" and dom == month == dow == "*":
        return f"每 {int(minute[2:])} 分钟"
    if minute == "*" and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return "每分钟"
    return task.cron or "—"


def _out(task: models.ScheduledTask, user: models.User) -> dict:
    return {
        "id": task.id,
        "scope": task.scope,
        "taskType": task.task_type,
        "name": task.name,
        "params": task.params or {},
        "scheduleKind": task.schedule_kind,
        "cron": task.cron,
        "intervalSeconds": task.interval_seconds,
        "timezone": task.timezone,
        "misfireGraceSeconds": task.misfire_grace_seconds,
        "enabled": task.enabled,
        "scheduleLabel": _human_schedule(task),
        "nextRunAt": _iso(task.next_run_at),
        "lastFiredAt": _iso(task.last_fired_at),
        "lastStatus": task.last_status,
        "consecutiveFailures": task.consecutive_failures or 0,
        "pausedReason": task.paused_reason or None,
        "editable": _editable(task, user),
        "createdAt": _iso(task.created_at),
        "updatedAt": _iso(task.updated_at),
    }


def _run_out(run: models.ScheduledTaskRun, summary: str | None = None) -> dict:
    return {
        "id": run.id,
        "taskId": run.task_id,
        "jobId": run.job_id,
        "triggeredAt": _iso(run.triggered_at),
        "finishedAt": _iso(run.finished_at),
        "source": run.source,
        "status": run.status,
        "skipReason": run.skip_reason or None,
        "errorCode": run.error_code,
        "errorMessage": run.error_message,
        "durationMs": run.duration_ms,
        # 成功后至少要能回答「这次产出了什么」，否则界面只有「成功」两个字
        "resultSummary": summary,
    }


def _result_summary(result: dict | None) -> str | None:
    """把 job.result 压成一句人话。

    内置类型有各自的专属摘要；通用脚本任务不在此枚举——它们约定把摘要放进
    result['summary']，没有就退回 JSON 截断。这样加新脚本不用改这里，界面也不会
    只剩「成功」两个字。
    """
    if not isinstance(result, dict) or not result:
        return None
    if "total" in result and "draftsCreated" in result:
        landed = f"挑出 {result.get('total', 0)} 条"
        if result.get("draftsReused"):
            return f"{landed} · 新建草稿 {result.get('draftsCreated', 0)} 篇（{result['draftsReused']} 篇已存在）"
        return f"{landed} · 新建草稿 {result.get('draftsCreated', 0)} 篇"
    if "clusters" in result:
        return f"采集 {result.get('items', 0)} 条 · 归并 {result.get('clusters', 0)} 个事件"
    # 通用脚本：约定优先取 summary，否则 JSON 截断兜底
    summary = result.get("summary")
    if summary:
        return str(summary)[:300]
    try:
        text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return text[:200] + ("…" if len(text) > 200 else "")


def _translate(exc: ScheduleError) -> ApiError:
    return ApiError(exc.code, exc.message, status_code=400)


@router.get("/types")
def list_types(user: CurrentUser) -> dict:
    """类型清单：前端按 params.fields 渲染表单，不需要内置任何字段知识。"""
    return {"items": scheduled_tasks.list_types(), "systemWritable": _system_writable()}


@router.get("")
def list_schedules(user: CurrentUser, db: DbSession) -> dict:
    rows = db.scalars(
        select(models.ScheduledTask)
        .where(
            (models.ScheduledTask.scope == "system")
            | (models.ScheduledTask.owner_user_id == user.id)
        )
        .order_by(models.ScheduledTask.last_fired_at.desc(), models.ScheduledTask.created_at.desc())
    ).all()
    return {"items": [_out(row, user) for row in rows]}


@router.post("", status_code=201)
def create_schedule(
    payload: schemas.ScheduleCreateIn, user: CurrentUser, db: DbSession
) -> dict:
    try:
        spec = scheduled_tasks.task_type(payload.task_type)
    except ScheduleError as exc:
        raise _translate(exc) from exc

    if spec.scope == "system" and not _system_writable():
        raise forbidden("系统级定时任务在当前部署下不可新建，请用 seed 维护")

    timezone_name = (payload.timezone or user.timezone or "Asia/Shanghai").strip()
    try:
        params = scheduled_tasks.validate_params(spec, payload.params)
        # 先真解析一次 trigger：解析不了就不落库，绝不存进去等运行期炸。
        # 顺序在「重复检查」之前：参数写错时应该告诉他写错了，而不是先报「已存在」。
        scheduled_tasks.build_trigger(
            payload.schedule_kind, payload.cron, payload.interval_seconds, timezone_name
        )
    except ScheduleError as exc:
        raise _translate(exc) from exc

    if spec.scope == "system":
        exists = db.scalar(
            select(models.ScheduledTask).where(
                models.ScheduledTask.task_type == spec.key,
                models.ScheduledTask.scope == "system",
            )
        )
        if exists is not None:
            raise conflict(
                "schedule_exists",
                f"「{spec.title}」已经有一条系统级定时任务，直接改这条的时间即可",
            )

    task = models.ScheduledTask(
        scope=spec.scope,
        owner_user_id=user.id,
        task_type=spec.key,
        name=(payload.name or spec.title).strip()[:120] or spec.title,
        params=params,
        schedule_kind=payload.schedule_kind,
        cron=payload.cron.strip(),
        interval_seconds=payload.interval_seconds,
        timezone=timezone_name,
        misfire_grace_seconds=payload.misfire_grace_seconds or settings.scheduler_misfire_grace,
        enabled=payload.enabled,
    )
    scheduled_tasks.compute_next_run(task)
    db.add(task)
    db.commit()
    db.refresh(task)
    scheduler_module.sync(task)
    return _out(task, user)


@router.patch("/{task_id}")
def update_schedule(
    task_id: str, payload: schemas.ScheduleUpdateIn, user: CurrentUser, db: DbSession
) -> dict:
    task = _require(task_id, user, db)
    _require_editable(task, user)

    data = payload.model_dump(exclude_unset=True)
    spec = scheduled_tasks.task_type(task.task_type)

    if "params" in data and data["params"] is not None:
        try:
            task.params = scheduled_tasks.validate_params(spec, data["params"])
        except ScheduleError as exc:
            raise _translate(exc) from exc
    if "name" in data and data["name"] is not None:
        task.name = data["name"].strip()[:120] or spec.title

    schedule_touched = False
    for field in ("schedule_kind", "cron", "interval_seconds", "timezone"):
        if field in data and data[field] is not None:
            setattr(task, field, data[field])
            schedule_touched = True
    if "misfire_grace_seconds" in data and data["misfire_grace_seconds"] is not None:
        task.misfire_grace_seconds = data["misfire_grace_seconds"]

    if schedule_touched:
        task.cron = (task.cron or "").strip()
        try:
            scheduled_tasks.build_trigger(
                task.schedule_kind, task.cron, task.interval_seconds, task.timezone
            )
        except ScheduleError as exc:
            db.rollback()
            raise _translate(exc) from exc

    if "enabled" in data and data["enabled"] is not None:
        task.enabled = bool(data["enabled"])
        if task.enabled:
            # 手动恢复即清零失败计数：不让历史失败干扰下一次判断
            task.consecutive_failures = 0
            task.paused_reason = ""
    if task.enabled and (schedule_touched or task.next_run_at is None):
        scheduled_tasks.compute_next_run(task)
    if not task.enabled:
        task.next_run_at = None

    db.commit()
    db.refresh(task)
    scheduler_module.sync(task)
    return _out(task, user)


@router.delete("/{task_id}")
def delete_schedule(task_id: str, user: CurrentUser, db: DbSession) -> dict:
    task = _require(task_id, user, db)
    _require_editable(task, user)
    # SQLite 默认不启用外键级联，这里显式删账本，避免留下孤儿 run
    for run in db.scalars(
        select(models.ScheduledTaskRun).where(models.ScheduledTaskRun.task_id == task.id)
    ).all():
        db.delete(run)
    db.delete(task)
    db.commit()
    scheduler_module.unsync(task_id)
    return {"id": task_id, "deleted": True}


@router.post("/{task_id}/run")
def run_schedule_now(task_id: str, user: CurrentUser, db: DbSession) -> dict:
    """立即执行一次：走同一条投递路径，只是 source=manual、不消耗定时槽位。"""
    task = _require(task_id, user, db)
    _require_editable(task, user)
    if not task.enabled:
        raise ApiError("schedule_disabled", "任务已停用，请先启用再手动执行", status_code=409)
    result = scheduled_tasks.trigger_manual(db, task)
    db.refresh(task)
    return {**result, "schedule": _out(task, user)}


@router.get("/{task_id}/runs")
def list_runs(
    task_id: str,
    user: CurrentUser,
    db: DbSession,
    response: Response,
    limit: int = Query(default=20, le=100),
    cursor: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> dict:
    task = _require(task_id, user, db)
    stmt = select(models.ScheduledTaskRun).where(models.ScheduledTaskRun.task_id == task.id)
    if status:
        stmt = stmt.where(models.ScheduledTaskRun.status == status)
    rows, next_cursor = pagination.paginate(
        db,
        stmt,
        models.ScheduledTaskRun,
        sort_column=models.ScheduledTaskRun.triggered_at,
        cursor=cursor,
        limit=limit,
    )
    pagination.set_cursor_header(response, next_cursor)
    job_ids = [row.job_id for row in rows if row.job_id]
    jobs = {}
    if job_ids:
        jobs = {
            job.id: job
            for job in db.scalars(
                select(models.BackgroundJob).where(models.BackgroundJob.id.in_(job_ids))
            ).all()
        }
    items = []
    for row in rows:
        job = jobs.get(row.job_id) if row.job_id else None
        items.append(_run_out(row, _result_summary(job.result) if job else None))
    return {"items": items, "nextCursor": next_cursor}


@router.post("/preview")
def preview_schedule(payload: schemas.SchedulePreviewIn, user: CurrentUser) -> dict:
    timezone_name = (payload.timezone or user.timezone or "Asia/Shanghai").strip()
    try:
        times = scheduled_tasks.preview(
            payload.schedule_kind,
            payload.cron,
            payload.interval_seconds,
            timezone_name,
            count=payload.count,
        )
    except ScheduleError as exc:
        raise _translate(exc) from exc
    return {"timezone": timezone_name, "times": times}
