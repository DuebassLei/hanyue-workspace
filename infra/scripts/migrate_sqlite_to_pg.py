#!/usr/bin/env python
"""把本地 SQLite 数据搬迁到 PostgreSQL（首次上 Docker 部署时用一次）。

设计取舍：
- **复用应用自己的模型元数据**，而不是反射 SQLite 的结构。列类型、JSON、布尔、
  DateTime 的转换都由 SQLAlchemy 按方言处理，避免手写映射与模型漂移。
- **按依赖顺序整表复制**（metadata.sorted_tables），不临时关外键约束，
  复制完再重置自增序列，避免之后新插入撞主键。
- 幂等：目标表已有数据时默认跳过该表；加 `--truncate` 则先清空再灌。
- 只搬数据，不搬文件。storage 目录里的图片/音频请单独拷进目标卷。

运行方式（必须在目标 PG 可达的环境里执行，推荐在 api 容器内）：

    ./scripts/deploy.sh migrate                 # 默认搬 ../apps/api/hanyue.db
    ./scripts/deploy.sh migrate --dry-run       # 只统计行数，不写库
    ./scripts/deploy.sh migrate --truncate      # 目标库先清空再灌

也可以直接跑：
    python scripts/migrate_sqlite_to_pg.py --sqlite /data/source.db [--truncate] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

# 容器内 /app 是工作目录；本地直接跑时用仓库相对路径兜底
_HERE = Path(__file__).resolve()
for candidate in (_HERE.parents[2] / "apps" / "api", Path("/app")):
    if (candidate / "app" / "models.py").is_file():
        sys.path.insert(0, str(candidate))
        break
else:  # pragma: no cover
    sys.exit("找不到 app 包：请在 api 容器内执行，或从仓库根目录运行")

from sqlalchemy import DateTime, Integer, create_engine, inspect, select, text  # noqa: E402
from sqlalchemy.engine import Connection  # noqa: E402

from app import models  # noqa: E402,F401  注册全部 ORM 模型
from app.core.config import get_settings  # noqa: E402
from app.core.db import Base, engine as dst_engine, init_db  # noqa: E402

SKIP_TABLES = {"sqlite_sequence", "alembic_version"}
BATCH = 500


def _normalize_row(row: dict, table) -> dict:
    """SQLite 存 DATETIME 会丢掉时区，而应用写库用的是 UTC，这里补回来。
    否则插进 TIMESTAMPTZ 会被 PG 当成服务器本地时间，整库时间戳偏 8 小时。"""
    for key, value in row.items():
        if not isinstance(value, datetime):
            continue
        column = table.columns.get(key)
        if column is not None and isinstance(column.type, DateTime) and value.tzinfo is None:
            row[key] = value.replace(tzinfo=timezone.utc)
    return row


def _reset_sequence(conn: Connection, table) -> None:
    """复制后修正自增序列，否则新插入会从 1 开始撞主键。"""
    pk_cols = [c.name for c in table.primary_key.columns if isinstance(c.type, Integer)]
    if not pk_cols or conn.dialect.name != "postgresql":
        return
    col = pk_cols[0]
    seq = conn.execute(
        text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": table.name, "c": col}
    ).scalar()
    if not seq:
        return
    conn.execute(
        text(
            f'SELECT setval(:seq, GREATEST(COALESCE((SELECT MAX("{col}") FROM "{table.name}"), 0), 1), true)'
        ),
        {"seq": seq},
    )


def _source_columns(src, table) -> tuple[list, list[str]]:
    """只取源库里真实存在的列。

    必须这么做：脚本按**当前模型**列去查源库，而旧 SQLite 往往没有新增列
    （例如 users.email / password_hash），直接 `select(table)` 会报
    `no such column: users.email`，整个迁移中断。缺失列交给目标库默认值/NULL。
    """
    existing = {col["name"] for col in inspect(src).get_columns(table.name)}
    present = [col for col in table.columns if col.name in existing]
    missing = [col.name for col in table.columns if col.name not in existing]
    return present, missing


def copy_table(src, table, *, truncate: bool, dry_run: bool) -> tuple[int, str]:
    columns, missing = _source_columns(src, table)
    if not columns:
        return 0, "源表无匹配列"
    with src.connect() as s:
        rows = s.execute(select(*columns)).all()
    count = len(rows)
    note = f"缺列用默认值：{','.join(missing)}" if missing else ""
    if dry_run:
        return count, f"统计（{note}）" if note else "统计"

    with dst_engine.begin() as d:
        has_rows = d.execute(select(table).limit(1)).first() is not None
        if has_rows:
            if not truncate:
                return 0, "目标已有数据，跳过（需要 --truncate）"
            d.execute(table.delete())

    if not rows:
        return 0, "空表"

    # row 是按 columns 顺序取出的位置元组，映射回列名后再做时区归一
    payload = [
        _normalize_row({col.name: row[i] for i, col in enumerate(columns)}, table)
        for row in rows
    ]
    with dst_engine.begin() as d:
        for start in range(0, len(payload), BATCH):
            d.execute(table.insert(), payload[start : start + BATCH])

    with dst_engine.begin() as d:
        _reset_sequence(d, table)
    return count, f"写入（{note}）" if note else "写入"


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite → PostgreSQL 数据搬迁")
    parser.add_argument("--sqlite", default="/data/source.db", help="源 SQLite 文件路径")
    parser.add_argument("--truncate", action="store_true", help="目标表已有数据时先清空")
    parser.add_argument("--dry-run", action="store_true", help="只统计行数，不写库")
    args = parser.parse_args()

    settings = get_settings()
    target_url = settings.database_url
    if not target_url.startswith("postgresql"):
        print(f"[abort] 目标库不是 PostgreSQL：{target_url}", file=sys.stderr)
        print("        请确认容器内 DATABASE_URL=postgresql+psycopg://...", file=sys.stderr)
        return 2

    source_path = Path(args.sqlite)
    if not source_path.is_file():
        print(f"[abort] 源文件不存在：{source_path}", file=sys.stderr)
        return 2

    print(f"源  ：{source_path} ({source_path.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"目标：{target_url.rsplit('@', 1)[-1]}")
    print("")

    src = create_engine(f"sqlite:///{source_path.as_posix()}", future=True)
    init_db()  # 建表 + 补列 + 建索引，与 API 启动走同一条路径

    src_tables = set(inspect(src).get_table_names())
    total_rows = 0
    print(f"{'表名':<34}{'行数':>8}  说明")
    print("-" * 62)
    for table in Base.metadata.sorted_tables:
        if table.name in SKIP_TABLES or table.name not in src_tables:
            continue
        try:
            count, note = copy_table(
                src, table, truncate=args.truncate, dry_run=args.dry_run
            )
        except Exception as exc:  # 单表失败要能定位，不要整批静默回滚
            print(f"{table.name:<34}{'-':>8}  失败：{exc}")
            return 1
        total_rows += count
        print(f"{table.name:<34}{count:>8}  {note}")

    print("-" * 62)
    print(f"{'合计':<34}{total_rows:>8}")
    if not args.dry_run:
        print("")
        print("数据已写入。素材文件（storage 目录）不在此脚本范围内，请另行拷贝到 storage 卷。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
