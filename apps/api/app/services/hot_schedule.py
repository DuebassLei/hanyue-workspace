"""把「热点采集」挂成一条通用定时脚本任务（幂等 provisioning）。

背景：热点选题的默认数据源（`app/hot_sources/catalog.yaml` 里那 22 个平台）以前由
内置任务 `hot_topics.refresh` 定时拉取；内置任务通道整体移除后，它只剩手动刷新。
现在改成**一条普通的脚本定时任务**，与其它定时任务同一条通道：

    仓库里的脚本代码（resources/script_examples.HOT_BOARD_REFRESH）
      → scheduled_scripts（草稿 + 发布快照，执行只认快照）
      → scheduled_tasks（task_type=user-script.<id>，scope=system）
      → 到点投 background_jobs → 进程内跑脚本 → hot_topics.refresh

为什么要有这个服务函数，而不是让人在界面上手点三件套：
- 新部署/换库要「建脚本 + 发布 + 建定时」，手点三步容易漏，也没有可复查的产物；
- 采集口径会随 catalog 演进，脚本代码得能从仓库**重新发布**一次，而不是靠人粘贴。

幂等口径（重复执行只补缺，不动人调过的东西）：
- 脚本按 `(owner_user_id, name)` 唯一约束复用；**代码有变化才发新版本**，
  没变化不动版本号（版本号是执行快照的定位键，别乱加）。
- 定时任务按 `task_type=user-script.<id>` 复用；已存在时**不覆盖**它的 cron 与参数
  （用户可能已经按自己的节奏调过），只有显式传 `cron` 才改时间。
- 已存在的任务不会被强制改回启用态：停用是用户的明确决定。
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..resources.script_examples import HOT_BOARD_REFRESH
from . import scheduled_tasks, script_runtime

# 脚本名即幂等键（(owner, name) 唯一），改名等于新建一条脚本，别轻易改。
DEFAULT_SCRIPT_NAME = "热点榜单采集"
# 每天 8~22 点每小时第 5 分跑一轮：15 轮/天足够算「升降 / 日内新鲜度」（各需 ≥2/≥3 轮），
# 又不会把 22 个上游源打成压测。分钟错开 0 分，避开整点的公共拥堵。
DEFAULT_CRON = "5 8-22 * * *"
DEFAULT_TIMEZONE = "Asia/Shanghai"
# 22 个源串行采集，单源超时 12s + 重试 1 次，正常 20~60s，留足余量
DEFAULT_TIMEOUT_SECONDS = 300


class HotScheduleError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def default_owner(db: Session) -> models.User:
    """单租户部署下脚本的归属人：优先真有密码的账号（人登录的那个），否则最早的账号。

    归属人不是装饰：`background_jobs.owner_user_id` 是必填外键，且执行时会校验
    `script.owner_user_id == job.owner_user_id`，两者必须一致。
    """
    user = db.scalar(
        select(models.User)
        .where(models.User.is_active.is_(True), models.User.password_hash.is_not(None))
        .order_by(models.User.created_at)
    )
    if user is None:
        user = db.scalar(
            select(models.User)
            .where(models.User.is_active.is_(True))
            .order_by(models.User.created_at)
        )
    if user is None:
        raise HotScheduleError("no_owner", "库里没有任何可用账号：先建账号，再挂定时任务")
    return user


def _iso(value: datetime | None) -> str | None:
    stamp = scheduled_tasks.as_utc(value)
    return stamp.isoformat() if stamp else None


def _publish(db: Session, script: models.ScheduledScript, code: str, *, force: bool = False) -> bool:
    """代码有变化才发新版本（force 时无条件发）。返回是否发布了新版本。

    发布不可省：投递时按 `published_version` 去 `scheduled_script_versions` 取快照，
    快照缺失或 hash 对不上会直接失败（`script_snapshot_missing`）。
    """
    if not code.strip():
        raise HotScheduleError("script_empty", "脚本代码为空")
    try:
        script_runtime.validate_published(code)
    except script_runtime.ScriptValidationError as exc:
        raise HotScheduleError(exc.code, exc.message) from exc

    if not force and script.published_code == code and (script.published_version or 0) > 0:
        return False

    script.draft_code = code
    script.published_code = code
    script.published_version = int(script.published_version or 0) + 1
    script.published_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    script.published_at = script_runtime.utcnow()
    db.add(
        models.ScheduledScriptVersion(
            script_id=script.id, version=script.published_version, code=code
        )
    )
    return True


def _ensure_script(
    db: Session, owner: models.User, *, name: str
) -> tuple[models.ScheduledScript, bool]:
    script = db.scalar(
        select(models.ScheduledScript).where(
            models.ScheduledScript.owner_user_id == owner.id,
            models.ScheduledScript.name == name,
        )
    )
    created = False
    if script is None:
        script = models.ScheduledScript(
            owner_user_id=owner.id,
            name=name,
            description="定时抓取默认数据源（app/hot_sources/catalog.yaml）写入热点库。",
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
            enabled=True,
        )
        db.add(script)
        db.flush()  # 拿 id：版本行与任务类型都要用它
        created = True
    elif (script.timeout_seconds or 0) < DEFAULT_TIMEOUT_SECONDS:
        # 老版本按 60s 建过的话，22 个源根本跑不完，顺手抬到安全值
        script.timeout_seconds = DEFAULT_TIMEOUT_SECONDS
    return script, created


def _ensure_task(
    db: Session,
    script: models.ScheduledScript,
    owner: models.User,
    *,
    cron: str | None,
    enabled: bool,
) -> tuple[models.ScheduledTask, bool, list[str]]:
    key = f"user-script.{script.id}"
    warnings: list[str] = []
    task = db.scalar(select(models.ScheduledTask).where(models.ScheduledTask.task_type == key))
    created = False

    if task is None:
        task = models.ScheduledTask(
            # 与 POST /schedules 同口径：所有定时配置统一 system（全局共享），
            # scope 只保留「内置类型」的内部语义
            scope="system",
            owner_user_id=owner.id,
            task_type=key,
            name=script.name,
            params={},
            schedule_kind="cron",
            cron=(cron or DEFAULT_CRON),
            timezone=DEFAULT_TIMEZONE,
            misfire_grace_seconds=300,
            enabled=enabled,
        )
        scheduled_tasks.compute_next_run(task)  # 顺带校验 cron 可解析，非法就在这里报
        db.add(task)
        created = True
    else:
        if task.owner_user_id != owner.id:
            # 任务归属人必须等于脚本归属人，否则执行时会被 script_not_found 挡下
            warnings.append(
                f"任务 {task.id} 的归属人与脚本不一致（{task.owner_user_id} != {owner.id}），已按脚本归属人修正"
            )
            task.owner_user_id = owner.id
        if cron and task.cron != cron:
            task.cron = cron
            task.schedule_kind = "cron"
            scheduled_tasks.compute_next_run(task)
        elif task.enabled and task.next_run_at is None:
            scheduled_tasks.compute_next_run(task)
    return task, created, warnings


def ensure_hot_schedule(
    db: Session,
    *,
    owner: models.User | None = None,
    name: str = DEFAULT_SCRIPT_NAME,
    cron: str | None = None,
    code: str | None = None,
    enabled: bool = True,
    force_publish: bool = False,
) -> dict:
    """建/更新「热点榜单采集」脚本 + 定时任务。返回本次做了什么。"""
    source = code or HOT_BOARD_REFRESH
    owner = owner or default_owner(db)
    script, script_created = _ensure_script(db, owner, name=name)
    published = _publish(db, script, source, force=force_publish)
    db.flush()
    task, task_created, warnings = _ensure_task(
        db, script, owner, cron=cron, enabled=enabled
    )
    db.commit()
    db.refresh(script)
    db.refresh(task)

    # 在 API 进程里调用时把调度器一起同步；CLI 里 scheduler 没启动，sync 是空操作，
    # 由 API 下次启动（含 uvicorn --reload）的 load_all 装载。
    try:
        from . import scheduler as scheduler_module

        scheduler_module.sync(task)
    except Exception as exc:  # noqa: BLE001 - 同步失败不该让 provisioning 回滚
        warnings.append(f"调度器同步失败（任务已落库，重启服务后自动装载）：{exc}")

    return {
        "ownerId": owner.id,
        "ownerEmail": owner.email,
        "scriptId": script.id,
        "scriptName": script.name,
        "scriptCreated": script_created,
        "published": published,
        "publishedVersion": script.published_version,
        "taskId": task.id,
        "taskCreated": task_created,
        "cron": task.cron,
        "timezone": task.timezone,
        "enabled": task.enabled,
        "nextRunAt": _iso(task.next_run_at),
        "warnings": warnings,
    }
