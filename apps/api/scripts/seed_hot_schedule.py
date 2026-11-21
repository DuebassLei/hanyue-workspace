#!/usr/bin/env python
"""把「热点榜单采集」挂成一条通用定时脚本任务（幂等，可反复跑）。

背景：热点选题的默认数据源（`app/hot_sources/catalog.yaml` 里的 22 个平台）不再走
内置任务通道，改为一条普通脚本定时任务：仓库脚本 → 发布快照 → 到点采集写入热点库。
详见 `app/services/hot_schedule.py` 的模块注释。

用法（在 apps/api 目录下，或用绝对路径调用）：

    python scripts/seed_hot_schedule.py                       # 建/补，不动已有时间配置
    python scripts/seed_hot_schedule.py --cron "5 8-22 * * *" # 同时改定时时间
    python scripts/seed_hot_schedule.py --user me@example.com # 指定脚本归属账号
    python scripts/seed_hot_schedule.py --republish           # 强制重发一版（同一份代码）

可安全重复执行：脚本按 (归属人, 名称) 复用、代码没变不涨版本号；已存在的定时任务
不会被覆盖 cron/参数，也不会被强行改回启用态（停用是人的决定）。

数据库由 DATABASE_URL 决定（apps/api/.env 或同名环境变量），与 API 完全一致。
注意：CLI 只写库；API 进程内 APScheduler 的内存 jobstore 在**下次启动/reload** 时
才会装载新任务（界面里已经能看到，可以点「立即执行」验证）。
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

from sqlalchemy import func, select  # noqa: E402

from app import models  # noqa: E402,F401  注册全部 ORM 模型
from app.core.config import get_settings  # noqa: E402
from app.core.db import SessionLocal, init_db  # noqa: E402
from app.services import hot_schedule  # noqa: E402


def _resolve_owner(db, raw: str | None) -> models.User:
    if not raw:
        return hot_schedule.default_owner(db)
    value = raw.strip()
    user = db.get(models.User, value)
    if user is None:
        user = db.scalar(
            select(models.User).where(func.lower(models.User.email) == value.lower())
        )
    if user is None:
        sys.exit(f"找不到账号：{value}（用邮箱或 user id）")
    return user


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="把热点采集挂成通用定时脚本任务")
    parser.add_argument("--user", help="脚本归属账号（邮箱或 user id），默认取主账号")
    parser.add_argument("--name", default=hot_schedule.DEFAULT_SCRIPT_NAME, help="脚本名（幂等键）")
    parser.add_argument("--cron", help=f"定时表达式，默认新建时用 {hot_schedule.DEFAULT_CRON}")
    parser.add_argument("--republish", action="store_true", help="强制重发一版（同一份代码也发）")
    parser.add_argument("--disabled", action="store_true", help="新建时置为停用（不立即定时跑）")
    args = parser.parse_args()

    print(f"目标数据库：{settings.database_url.split('@')[-1]}")
    init_db()  # 空库也能跑：先建表，省得先启动一次 API
    with SessionLocal() as db:
        owner = _resolve_owner(db, args.user)
        result = hot_schedule.ensure_hot_schedule(
            db,
            owner=owner,
            name=args.name,
            cron=args.cron,
            enabled=not args.disabled,
            force_publish=args.republish,
        )

    print(f"归属账号：{result['ownerEmail'] or result['ownerId']}")
    print(
        f"脚本：{result['scriptName']}（id={result['scriptId']}，"
        f"{'新建' if result['scriptCreated'] else '复用'}，"
        f"发布版本 v{result['publishedVersion']}"
        f"{' 已更新' if result['published'] else ' 无变化'}）"
    )
    print(
        f"定时任务：{result['taskId']}（{'新建' if result['taskCreated'] else '复用'}）"
        f" cron={result['cron']} 时区={result['timezone']}"
        f" 状态={'启用' if result['enabled'] else '停用'}"
    )
    print(f"下次触发：{result['nextRunAt'] or '—（任务停用）'}")
    for warning in result["warnings"]:
        print(f"⚠️  {warning}")
    print("完成。API 进程下次启动/reload 后生效；界面「定时任务」里可直接改时间、立即执行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
