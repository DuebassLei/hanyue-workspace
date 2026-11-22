#!/usr/bin/env python
"""停用旧的「热点榜单采集」定时任务（幂等，可反复跑）。

背景：热点采集已改成「一个平台一条任务」——脚本自己请求、自己解析，再调用
`hot_topics.ingest`。旧的总任务调用的是已删除的内置采集（`hot_sources.collect`），
留着只会每小时失败一次，并在日志里刷 `ModuleNotFoundError`。

这个脚本把匹配到的脚本和定时任务都停用，数据不动。

用法（在 apps/api 目录下，或用绝对路径调用）：

    python scripts/retire_hot_board_task.py             # 停用并打印做了什么
    python scripts/retire_hot_board_task.py --dry-run   # 只看会停哪些，不改库
    python scripts/retire_hot_board_task.py --name 热点榜单采集 --name 旧采集

停用之后，热点数据只来自各平台任务；要恢复某个平台，在「定时任务」里新建一条
「热点入库」脚本任务即可（见 docs/hot-topics-config.md）。

注意：CLI 只写库。API 进程内的调度器在下次启动/reload 时才不再装载这条任务，
所以停用后建议顺手重启一次 API。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
if (API_DIR / "app" / "models.py").is_file():
    sys.path.insert(0, str(API_DIR))
elif not (Path("/app") / "app" / "models.py").is_file():
    sys.exit("找不到 app 包：请在 apps/api 目录下运行")

from sqlalchemy import select  # noqa: E402

from app import models  # noqa: E402,F401  注册全部 ORM 模型
from app.core.config import get_settings  # noqa: E402
from app.core.db import SessionLocal, init_db  # noqa: E402

LEGACY_SCRIPT_NAMES = ("热点榜单采集",)
RETIRE_REASON = "热点采集已改为按平台的脚本任务（docs/hot-topics-config.md），旧总任务不再运行"


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="停用旧的「热点榜单采集」任务")
    parser.add_argument(
        "--name",
        action="append",
        dest="names",
        help=f"脚本名，可重复；默认 {'、'.join(LEGACY_SCRIPT_NAMES)}",
    )
    parser.add_argument("--dry-run", action="store_true", help="只列出，不改库")
    args = parser.parse_args()

    names = tuple(args.names or LEGACY_SCRIPT_NAMES)
    print(f"目标数据库：{settings.database_url.split('@')[-1]}")
    print(f"匹配脚本名：{'、'.join(names)}")
    init_db()  # 空库也能跑

    stopped_tasks: list[str] = []
    with SessionLocal() as db:
        scripts = db.scalars(
            select(models.ScheduledScript).where(models.ScheduledScript.name.in_(names))
        ).all()
        script_ids = [script.id for script in scripts]

        tasks: list[models.ScheduledTask] = []
        if script_ids:
            tasks.extend(
                db.scalars(
                    select(models.ScheduledTask).where(
                        models.ScheduledTask.task_type.in_(
                            [f"user-script.{script_id}" for script_id in script_ids]
                        )
                    )
                ).all()
            )
        # 脚本行已经不在了、只剩任务行的孤儿也一起收掉：它同样调不到已删除的服务
        tasks.extend(
            db.scalars(
                select(models.ScheduledTask).where(models.ScheduledTask.name.in_(names))
            ).all()
        )
        tasks = list({task.id: task for task in tasks}.values())

        if not scripts and not tasks:
            print("没有找到旧的热点采集脚本/任务：不需要迁移。")
            return 0

        for script in scripts:
            print(
                f"脚本 {script.name}（id={script.id}）"
                f" 当前状态={'启用' if script.enabled else '停用'}"
            )
            if not args.dry_run and script.enabled:
                script.enabled = False

        for task in tasks:
            print(
                f"定时任务 {task.name}（id={task.id}，{task.task_type}）"
                f" 当前状态={'启用' if task.enabled else '停用'}"
            )
            if args.dry_run:
                continue
            if task.enabled or task.next_run_at is not None:
                task.enabled = False
                task.next_run_at = None
                task.paused_reason = RETIRE_REASON
                stopped_tasks.append(task.id)

        if args.dry_run:
            print("--dry-run：未改库。")
            return 0
        db.commit()

    # 库改完再摘调度器里的内存 job；CLI 里调度器没启动，unsync 是空操作
    if stopped_tasks:
        try:
            from app.services import scheduler as scheduler_module

            for task_id in stopped_tasks:
                scheduler_module.unsync(task_id)
        except Exception as exc:  # noqa: BLE001 - 摘 job 失败不影响已落库的停用
            print(f"⚠️  调度器同步失败（任务已停用，重启 API 后生效）：{exc}")

    print(f"完成：停用 {len(stopped_tasks)} 条定时任务。")
    if stopped_tasks:
        print("重启一次 API，运行中的调度器才会彻底不再装载它们。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
