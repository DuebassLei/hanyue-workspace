"""定时任务：类型注册表、时间语义、CAS 触发、触发账本（docs/scheduled-tasks.md）。

职责边界（重要，改动前先读）：
- 这里**不算时间**。时间只由 APScheduler 的 trigger 计算，本模块只调用它。
- 这里**不执行任务**。到点只做三件事：CAS 推进 `next_run_at`、判定并发、投递 job。
  真正的执行、重试、进度、取消全在 `job_runner` 与 `core/queue.py`。
- `scheduled_task_runs.status` 的终态以 `background_jobs` 为准，由 `sync_run_from_job`
  在 job 写终态时同一处回写；`reconcile_runs` 启动时兜底对账。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import BaseModel, ValidationError
from sqlalchemy import select, update

from .. import models
from ..core import queue
from ..core.config import get_settings
from ..core.db import SessionLocal
from ..core.logging import get_logger

logger = get_logger("app.schedules")

MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 7 * 24 * 3600
# 终态集合：写进这里之后不再被「回写」改写（只有对账能修）
RUN_TERMINAL = {"succeeded", "failed", "skipped", "cancelled", "missed"}
# 悬空超过该时长且无法解释的 run 视为失败，不让它永远显示「执行中」
STALE_RUN_HOURS = 24


class ScheduleError(RuntimeError):
    """领域错误：由 modules/schedules.py 翻译成 ApiError，服务层不认识 HTTP。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite 会把带时区的 datetime 读回 naive，比较前统一补 UTC（与 core/queue 同款）。"""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _zone(name: str) -> ZoneInfo:
    text = (name or "").strip()
    if not text:
        raise ScheduleError("invalid_timezone", "时区不能为空")
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise ScheduleError("invalid_timezone", f"无效时区：{text}") from exc


# ══════════════════════════════════════════════════════════════════════
# 任务类型注册表（声明驱动：由 manifest 加载，见 services/script_registry）
# ══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TaskType:
    """一种可被定时的任务。类型由 manifest 声明，不是用户可编辑的数据。

    `scope` 由类型决定而不是由创建者选：热点榜是全局数据（hot_items / hot_clusters
    没有 owner_user_id），所以只能是 system，否则 N 个用户会各打一遍采集源。
    """

    key: str
    title: str
    scope: str                      # system | user
    queue: str                      # 落到 background_jobs.queue
    kind: str                       # background_jobs.kind
    params_model: type[BaseModel]
    build_payload: Callable[[BaseModel], dict]
    description: str = ""
    default_cron: str = ""


TASK_TYPES: dict[str, TaskType] = {}


def register(spec: TaskType) -> None:
    """重复 key 直接抛错让进程起不来——覆盖式注册的后果是「改 A 影响 B」，最难查。"""
    if spec.key in TASK_TYPES:
        raise RuntimeError(f"定时任务类型重复注册：{spec.key}")
    if spec.scope not in ("system", "user"):
        raise RuntimeError(f"任务类型 {spec.key} 的 scope 非法：{spec.scope}")
    TASK_TYPES[spec.key] = spec


def task_type(key: str) -> TaskType:
    spec = TASK_TYPES.get((key or "").strip())
    if spec is None:
        raise ScheduleError("unknown_task_type", f"未注册的任务类型：{key}")
    return spec


def load_task_types() -> int:
    """从 manifest 装载全部任务类型。进程启动与测试 reload 时各调一次（幂等）。

    延迟 import 避开循环依赖：script_registry 读本模块的 TaskType/register。
    配置写错（未知 runner / 重复 key / 非法字段）在这里就抛，绝不留到运行期。
    """
    from . import script_registry

    return script_registry.load_all()


load_task_types()


def default_params(spec: TaskType) -> dict:
    try:
        return spec.params_model().model_dump(exclude_none=True)
    except ValidationError:  # 参数模型不需要必填项，理论上不会到这里
        return {}


def _simplify(prop: dict) -> dict:
    """把 pydantic 的 JSON Schema 压成前端能直接渲染的一层结构。

    `Optional[list[str]]` 在 pydantic v2 里是 anyOf[array, null]，直接丢给前端
    会让每个控件都要自己解 anyOf。这里归一成 {type, items, nullable}。
    """
    nullable = False
    node = prop
    if "anyOf" in prop:
        options = [item for item in prop["anyOf"] if item.get("type") != "null"]
        nullable = len(options) != len(prop["anyOf"])
        node = options[0] if options else {"type": "string"}
    result = {"type": node.get("type", "string"), "nullable": nullable}
    if node.get("type") == "array":
        result["items"] = (node.get("items") or {}).get("type", "string")
    if "enum" in node:
        result["enum"] = node["enum"]
    # Field(json_schema_extra={"options": [...]}) 的候选项：前端渲染成下拉，
    # 免得用户手打赛道名——打错一个字的后果是一份空简报。
    options = node.get("options") or prop.get("options")
    if options:
        result["options"] = list(options)
    item_label = node.get("item_label") or prop.get("item_label")
    if item_label:
        result["itemLabel"] = item_label
    return result


def params_fields(spec: TaskType) -> list[dict]:
    schema = spec.params_model.model_json_schema()
    required = set(schema.get("required", []))
    fields: list[dict] = []
    for name, prop in (schema.get("properties") or {}).items():
        item = {"name": name, "required": name in required, **_simplify(prop)}
        item["title"] = prop.get("title", name)
        item["description"] = prop.get("description", "")
        item["default"] = prop.get("default")
        fields.append(item)
    return fields


def list_types() -> list[dict]:
    return [
        {
            "key": spec.key,
            "title": spec.title,
            "scope": spec.scope,
            "description": spec.description,
            "defaultCron": spec.default_cron,
            "params": {"fields": params_fields(spec), "schema": spec.params_model.model_json_schema()},
        }
        for spec in TASK_TYPES.values()
    ]


def validate_params(spec: TaskType, raw: dict | None) -> dict:
    try:
        model = spec.params_model.model_validate(raw or {})
    except ValidationError as exc:
        detail = "；".join(
            f"{'.'.join(str(x) for x in err['loc']) or '参数'}: {err['msg']}" for err in exc.errors()
        )
        raise ScheduleError("invalid_params", f"参数不合法：{detail}") from exc

    data = model.model_dump(exclude_none=True)
    # 枚举候选来自 manifest 的 options（含 options_from 词表）：词表外的值配错只会
    # 得到空结果，必须在入口拒绝。对所有带 options 的字段统一生效。
    for field in params_fields(spec):
        options = field.get("options")
        if not options or field["name"] not in data:
            continue
        value = data[field["name"]]
        values = value if isinstance(value, (list, tuple)) else [value]
        allowed = [str(item) for item in options]
        unknown = [item for item in values if str(item) not in allowed]
        if unknown:
            label = field.get("itemLabel", "取值")
            raise ScheduleError(
                "invalid_params",
                f"参数不合法：{field['name']}: 未知{label}：{'、'.join(str(x) for x in unknown)}；可选：{'、'.join(allowed)}",
            )
        if isinstance(value, (list, tuple)):
            data[field["name"]] = list(dict.fromkeys(value))  # 去重保序
    return data


# ══════════════════════════════════════════════════════════════════════
# 时间语义
# ══════════════════════════════════════════════════════════════════════


def build_trigger(
    schedule_kind: str,
    cron: str,
    interval_seconds: int | None,
    timezone_name: str,
):
    """构造 trigger；解析不了就报明确错误，绝不存进去等运行期炸。"""
    tz = _zone(timezone_name)
    kind = (schedule_kind or "").strip().lower()
    if kind == "cron":
        text = (cron or "").strip()
        if not text:
            raise ScheduleError("missing_cron", "cron 表达式不能为空")
        try:
            return CronTrigger.from_crontab(text, timezone=tz)
        except ValueError as exc:
            raise ScheduleError("invalid_cron", f"cron 表达式无法解析：{exc}") from exc
    if kind == "interval":
        seconds = int(interval_seconds or 0)
        if seconds < MIN_INTERVAL_SECONDS:
            raise ScheduleError(
                "interval_too_small", f"间隔不能小于 {MIN_INTERVAL_SECONDS} 秒"
            )
        if seconds > MAX_INTERVAL_SECONDS:
            raise ScheduleError(
                "interval_too_large", "间隔不能超过 7 天，更长的周期请用 cron 表达"
            )
        # 不带 start_date：每次重建都以「此刻」为相位起点，语义是「从现在起每隔 N」
        return IntervalTrigger(seconds=seconds, timezone=tz)
    raise ScheduleError("invalid_schedule_kind", f"不支持的时间类型：{schedule_kind}")


def trigger_of(task: models.ScheduledTask):
    return build_trigger(task.schedule_kind, task.cron, task.interval_seconds, task.timezone)


def next_run_after(task: models.ScheduledTask, observed=None, now: datetime | None = None):
    """严格晚于 observed 的下一次触发，统一转成 UTC 存库。

    用 `get_next_fire_time(observed, max(observed, now))` 而不是 `(None, now)`：
    回调可能在触发点「正好」那一毫秒执行，用 now 当基准会算出同一个时间，
    next_run_at 不前进，CAS 令牌失效、任务原地自循环。
    """
    trigger = trigger_of(task)
    observed_utc = as_utc(observed)
    base = observed_utc or now_utc()
    reference = max(base, as_utc(now) or now_utc())
    nxt = trigger.get_next_fire_time(base, reference)
    return nxt.astimezone(timezone.utc) if nxt else None


def preview(
    schedule_kind: str,
    cron: str,
    interval_seconds: int | None,
    timezone_name: str,
    count: int = 3,
    now: datetime | None = None,
) -> list[str]:
    """未来 N 次触发时间（带时区偏移）。前端表单保存前就能看见「下次是明天 8:00」。"""
    tz = _zone(timezone_name)
    trigger = build_trigger(schedule_kind, cron, interval_seconds, timezone_name)
    cursor = (as_utc(now) or now_utc()).astimezone(tz)
    previous = None
    out: list[str] = []
    for _ in range(max(1, min(count, 10))):
        nxt = trigger.get_next_fire_time(previous, cursor)
        if nxt is None:
            break
        out.append(nxt.isoformat())
        previous = nxt
        cursor = nxt
    return out


# ══════════════════════════════════════════════════════════════════════
# 触发：CAS → 并发判定 → 投递
# ══════════════════════════════════════════════════════════════════════


def _lease_seconds(job: models.BackgroundJob | None) -> int:
    settings = get_settings()
    if job is not None and job.kind == "video.render":
        return max(settings.job_lease_video_render_seconds, settings.job_lease_seconds)
    return settings.job_lease_seconds


def _run_alive(db, run: models.ScheduledTaskRun, now: datetime) -> bool:
    """该 run 是否真的还在跑。

    判活不能只看 run.status：一个卡在 queued 的 job（典型原因：Redis 模式漏了
    worker 消费）会让任务永远跳过，而界面上看不出原因——静默死亡比失败更糟。
    因此按队列租约语义判活，超时的当僵尸放行，并留一条 warning。
    """
    job = db.get(models.BackgroundJob, run.job_id) if run.job_id else None
    started = as_utc(run.triggered_at) or now
    if job is not None:
        if job.status not in ("queued", "running"):
            return False  # job 已终态但 run 未回写，不算忙（对账会修）
        started = as_utc(job.started_at) or started
    elif run.job_id:
        return False  # 有 jobId 却查不到 job：已被清理，不算忙
    elapsed = (now - started).total_seconds()
    if elapsed <= _lease_seconds(job):
        return True
    logger.warning(
        "定时任务 run %s 超过租约仍未落定（%.0fs），按僵尸处理并放行本次触发", run.id, elapsed
    )
    return False


def _new_run(db, task, *, source: str, status: str, skip_reason: str = "") -> models.ScheduledTaskRun:
    run = models.ScheduledTaskRun(
        task_id=task.id,
        triggered_at=now_utc(),
        source=source,
        status=status,
        skip_reason=skip_reason[:300],
    )
    db.add(run)
    db.flush()
    return run


def _launch(db, task, *, source: str) -> dict:
    """并发判定 + 投递。定时与手动两条路径共用，保证互斥语义一致。"""
    now = now_utc()
    pending = db.scalars(
        select(models.ScheduledTaskRun)
        .where(
            models.ScheduledTaskRun.task_id == task.id,
            models.ScheduledTaskRun.status.in_(("queued", "running")),
        )
        .order_by(models.ScheduledTaskRun.triggered_at.desc())
    ).all()
    blocking = next((row for row in pending if _run_alive(db, row, now)), None)
    if blocking is not None:
        run = _new_run(
            db,
            task,
            source=source,
            status="skipped",
            skip_reason=f"上一次仍在执行（runId={blocking.id}）",
        )
        run.finished_at = now
        task.last_status = "skipped"
        task.last_fired_at = now
        db.commit()
        return {"action": "skipped", "runId": run.id, "reason": run.skip_reason}

    spec = task_type(task.task_type)
    run = _new_run(db, task, source=source, status="queued")
    payload = {**spec.build_payload(spec.params_model.model_validate(task.params or {}))}
    payload["scheduledTaskId"] = task.id
    payload["scheduledRunId"] = run.id
    job_id = queue.enqueue(
        db,
        owner_user_id=task.owner_user_id,
        queue=spec.queue,
        kind=spec.kind,
        payload=payload,
    )
    run.job_id = job_id
    task.last_status = "queued"
    task.last_fired_at = now
    db.commit()
    return {"action": "dispatched", "runId": run.id, "jobId": job_id}


def claim_trigger(db, task_id: str, observed, next_run_at) -> bool:
    """CAS 抢占某个触发点：只有 rowcount==1 的那个副本可以继续。

    令牌用的是「读出来的原值」（`observed`），不做时区归一——归一后的值与库里的
    存储格式不一定一致，条件会永远不成立，等于把所有触发都静默丢掉。
    """
    condition = [
        models.ScheduledTask.id == task_id,
        models.ScheduledTask.enabled.is_(True),
    ]
    if observed is None:
        condition.append(models.ScheduledTask.next_run_at.is_(None))
    else:
        condition.append(models.ScheduledTask.next_run_at == observed)
    changed = db.execute(
        update(models.ScheduledTask)
        .where(*condition)
        .values(next_run_at=next_run_at, last_fired_at=now_utc())
    )
    db.commit()
    return changed.rowcount == 1


def fire(task_id: str) -> dict:
    """APScheduler 到点回调。

    内存 jobstore 意味着每个副本都有自己的定时器，多副本会同时「到点」。
    只有把 next_run_at 从读到的旧值推进成功的那个才允许继续（CAS）。
    """
    db = SessionLocal()
    try:
        task = db.get(models.ScheduledTask, task_id)
        if task is None:
            return {"action": "ignore", "reason": "task_missing"}
        if not task.enabled:
            return {"action": "ignore", "reason": "task_disabled"}

        observed = task.next_run_at  # 用「读出来的原值」当令牌，不做归一化
        now = now_utc()
        if not claim_trigger(db, task_id, observed, next_run_after(task, observed, now)):
            # 别的副本已经处理过这个触发点。不是错误，也不写 run 记录。
            return {"action": "ignore", "reason": "claimed_elsewhere"}

        db.refresh(task)
        return _launch(db, task, source="schedule")
    finally:
        db.close()


def trigger_manual(db, task: models.ScheduledTask) -> dict:
    """立即执行一次：不消耗定时槽位（不动 next_run_at），但受同一套互斥约束。"""
    return _launch(db, task, source="manual")


# ══════════════════════════════════════════════════════════════════════
# 终态回写与对账
# ══════════════════════════════════════════════════════════════════════


def _apply_task_outcome(db, task: models.ScheduledTask, status: str) -> None:
    """更新任务级状态；连续失败达阈值自动暂停（不让坏任务无限重试打外网）。"""
    settings = get_settings()
    task.last_status = status
    if status == "succeeded":
        task.consecutive_failures = 0
    elif status == "failed":
        task.consecutive_failures = (task.consecutive_failures or 0) + 1
        limit = settings.scheduler_max_consecutive_failures
        if limit > 0 and task.consecutive_failures >= limit and task.enabled:
            task.enabled = False
            task.next_run_at = None
            task.paused_reason = f"连续 {task.consecutive_failures} 次失败已自动暂停，请检查配置后重新启用"
            logger.warning("定时任务 %s 连续失败 %s 次，已自动暂停", task.id, task.consecutive_failures)
            from . import scheduler as scheduler_module  # 延迟导入：避免循环引用

            scheduler_module.unsync(task.id)


def sync_run_from_job(
    db,
    job_id: str,
    *,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> bool:
    """job 写终态时调用（job_runner._write_terminal 是唯一入口）。

    以 job 为准：run 的 status 不自己推进，只跟着 job 走。
    """
    run = db.scalar(select(models.ScheduledTaskRun).where(models.ScheduledTaskRun.job_id == job_id))
    if run is None:
        return False
    run.status = status
    run.error_code = error_code
    run.error_message = (error_message or None) and error_message[:1000]
    finished = as_utc(completed_at) or now_utc()
    run.finished_at = finished
    begin = as_utc(started_at) or as_utc(run.triggered_at)
    if begin is not None:
        run.duration_ms = max(0, int((finished - begin).total_seconds() * 1000))
    task = db.get(models.ScheduledTask, run.task_id)
    if task is not None:
        _apply_task_outcome(db, task, status)
    db.commit()
    return True


def record_missed(task_id: str, *, reason: str = "") -> None:
    """错过宽限的触发：补一条 missed 账本，并推进 next_run_at。

    不推进的话界面上的「下次触发」会一直停在已经过去的时间。
    """
    db = SessionLocal()
    try:
        task = db.get(models.ScheduledTask, task_id)
        if task is None:
            return
        now = now_utc()
        task.next_run_at = next_run_after(task, task.next_run_at, now)
        task.last_status = "missed"
        task.last_fired_at = now
        db.add(
            models.ScheduledTaskRun(
                task_id=task.id,
                triggered_at=now,
                source="schedule",
                status="missed",
                skip_reason=(reason or "错过触发点")[:300],
                finished_at=now,
            )
        )
        db.commit()
    finally:
        db.close()


def mark_run_status(db, run: models.ScheduledTaskRun, status: str, *, note: str = "") -> bool:
    """对账用：直接改 run，并把结果反映到任务级统计。"""
    if run.status == status:
        return False
    run.status = status
    run.finished_at = run.finished_at or now_utc()
    if note:
        run.skip_reason = note[:300]
    task = db.get(models.ScheduledTask, run.task_id)
    if task is not None and status in ("succeeded", "failed"):
        _apply_task_outcome(db, task, status)
    return True


def reconcile_runs(db, now: datetime | None = None) -> dict:
    """启动对账：runs 是触发账本，job 是执行真相。

    回写可能漏（进程恰好死在两次写之间），漏了会让界面永久显示「执行中」。
    """
    now = as_utc(now) or now_utc()
    fixed = missing = stale = 0
    rows = db.scalars(
        select(models.ScheduledTaskRun).where(
            models.ScheduledTaskRun.status.in_(("queued", "running"))
        )
    ).all()
    for run in rows:
        job = db.get(models.BackgroundJob, run.job_id) if run.job_id else None
        if job is not None and job.status in ("succeeded", "failed", "cancelled", "stopped"):
            if mark_run_status(
                db,
                run,
                job.status if job.status in RUN_TERMINAL else "cancelled",
                note=run.skip_reason or "启动对账：以 background_jobs 终态为准",
            ):
                run.error_code = run.error_code or job.error_code
                run.error_message = run.error_message or job.error_message
                fixed += 1
            continue
        if job is not None:
            age = (now - (as_utc(job.started_at) or as_utc(run.triggered_at) or now)).total_seconds()
            if age <= max(_lease_seconds(job), STALE_RUN_HOURS * 3600):
                continue  # 还在租约内，交给队列的租约回收
        age_all = (now - (as_utc(run.triggered_at) or now)).total_seconds()
        if job is None and run.job_id is not None:
            if mark_run_status(db, run, "failed", note="启动对账：找不到对应的后台任务"):
                run.error_code = "job_missing"
                run.error_message = "后台任务记录不存在（可能已被清理）"
                missing += 1
        elif age_all > STALE_RUN_HOURS * 3600:
            if mark_run_status(db, run, "failed", note="启动对账：长时间悬空，无法确认结果"):
                run.error_code = "run_unresolved"
                run.error_message = "执行结果无法确认，已按失败处理"
                stale += 1
    db.commit()
    return {"fixed": fixed, "missing": missing, "stale": stale}


# ══════════════════════════════════════════════════════════════════════
# 装载 / 播种
# ══════════════════════════════════════════════════════════════════════


def compute_next_run(task: models.ScheduledTask, now: datetime | None = None) -> None:
    previous = task.next_run_at
    task.next_run_at = next_run_after(task, previous, as_utc(now) or now_utc())


def ensure_system_tasks(db, owner_user_id: str, timezone_name: str = "") -> int:
    """为 system 类型播种任务，绑定到首个登录用户。

    system 任务也必须有真实 owner：`background_jobs.owner_user_id` 是必填外键，
    留空会造出「跑不起来的任务」这种半截状态。多用户环境下只有第一条会留下。
    """
    created = 0
    for spec in TASK_TYPES.values():
        if spec.scope != "system":
            continue
        exists = db.scalar(
            select(models.ScheduledTask).where(
                models.ScheduledTask.task_type == spec.key,
                models.ScheduledTask.scope == "system",
            )
        )
        if exists is not None:
            continue
        task = models.ScheduledTask(
            scope="system",
            owner_user_id=owner_user_id,
            task_type=spec.key,
            name=spec.title,
            params=default_params(spec),
            schedule_kind="cron",
            cron=spec.default_cron,
            timezone=(timezone_name or "Asia/Shanghai"),
            misfire_grace_seconds=get_settings().scheduler_misfire_grace,
            enabled=True,
        )
        compute_next_run(task)
        db.add(task)
        db.commit()
        created += 1
        from . import scheduler as scheduler_module

        scheduler_module.sync(task)
        logger.info("已播种 system 定时任务 %s（%s）", spec.key, task.id)
    return created
