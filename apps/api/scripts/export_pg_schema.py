#!/usr/bin/env python
"""从 SQLAlchemy 模型导出完整的 PostgreSQL 建表脚本（DDL）。

为什么需要它
-----------
应用的建表完全依赖启动时 `init_db()`：create_all 建新表 + `_apply_additive_migrations()`
补列补索引。这对应用自己够用，但缺一份**可审阅、可版本管理、DBA 可直接在 PG 执行**的
完整 schema。生产环境若应用账号没有 DDL 权限，或需要提前评审数据结构，就无从下手。

本脚本以模型为唯一真相源生成 schema.sql，避免手工维护 DDL 与模型漂移：

  - 表、列、主键、外键、CHECK、模型内索引/唯一约束 -> 来自 Base.metadata（PG dialect 编译）
  - 补充索引（db.py 的 _INDEXES）与两个特殊唯一索引 -> 直接 import db.py 的清单复用，
    因为这些索引不在 metadata 中（当初是纯 SQL 创建的），不带上会导致生成的库与应用不一致。

生成物是**幂等**的（CREATE TABLE/INDEX IF NOT EXISTS + ADD COLUMN IF NOT EXISTS +
ON CONFLICT DO NOTHING），可在空库直接执行，也可在已有库重跑（只补缺失对象；老库
补列用可空语义，不带约束/默认值，约束差异由应用 init_db 兜底）。

生成物只包含结构，不包含引导账号或固定口令哈希。部署后必须立即运行
infra/scripts/dev-pg.sh bootstrap（Windows 用 dev-pg.ps1 bootstrap）设置首个账号的
独立强密码；旧部署若使用过固定口令，必须立即用 create_user.py reset-password 改密。
详见 docs/setup.md；这里不修改已有用户或 password_changed_at。

用法（在 apps/api 目录下）：

    python scripts/export_pg_schema.py                         # 输出到 stdout
    python scripts/export_pg_schema.py -o ../../infra/postgres/schema.sql
    python scripts/export_pg_schema.py -o ../../infra/postgres/schema.sql --check
                                                               # 校验现有文件是否与模型一致（CI 用）

--check 不写文件：若现有 schema.sql 与生成结果不一致，退出码 1，提示模型已变需重新生成。

注意：本脚本不需要数据库连接，纯本地编译，导入模型即可。
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

from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

# 导入模型，确保全部注册到 Base.metadata；同时复用 db.py 的补充索引清单
from app import models  # noqa: E402,F401
from app.core.db import Base, _INDEXES  # noqa: E402

PG_DIALECT = postgresql.dialect()

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")

HEADER = """\
-- ============================================================================
-- hanyue 数据模型完整初始化脚本（PostgreSQL）
-- ============================================================================
-- 本文件由 scripts/export_pg_schema.py 从 SQLAlchemy 模型自动生成，请勿手工编辑。
-- 模型变更后重新生成：
--   cd apps/api && python scripts/export_pg_schema.py -o ../../infra/postgres/schema.sql
-- CI 校验一致性（模型改了但忘了重新生成会失败）：
--   cd apps/api && python scripts/export_pg_schema.py -o ../../infra/postgres/schema.sql --check
--
-- 适用：全新数据库一次性建表。幂等（IF NOT EXISTS），已有库重跑只补缺失对象、
--       绝不覆盖已有数据；补列段用 ADD COLUMN IF NOT EXISTS 为老库补齐模型新增列
--       （可空、不带约束/默认值，约束差异仍由应用启动时的 init_db() 兜底）。
-- 部署后必须立即运行 bootstrap 设置独立强密码，见 docs/setup.md；本文件不播种账号。
-- 说明：本脚本按「当前模型的理想全新库」建表，个别列会比渐进迁移的老库更严格
--       （如新列是 NOT NULL，而老库经 ALTER TABLE ADD COLUMN 补列时为可空）——这是
--       正确且预期的差异，不影响功能；表/列/索引的集合与老库完全一致。
-- 前置：库已创建（见 create-role-and-db.sql），当前用户在该库有 CREATE 权限。
-- 执行：psql -U <user> -d <db> -v ON_ERROR_STOP=1 -f schema.sql
-- ============================================================================

\\set ON_ERROR_STOP on

BEGIN;
"""


def _stmt(node) -> str:
    """把 DDL 节点编译成 PG SQL，去掉 SQLAlchemy 追加的分号，末尾补分号统一格式。"""
    sql = str(node.compile(dialect=PG_DIALECT)).strip()
    sql = "\n".join(line.rstrip() for line in sql.splitlines())
    return sql.rstrip(";") + ";"


def build_schema() -> str:
    lines: list[str] = [HEADER]

    # 1) 表 + 表级的 CHECK / UNIQUE / 外键约束：按外键依赖顺序输出
    lines.append("-- ---------- 表（含主键 / 外键 / CHECK / 唯一约束） ----------\n")
    for table in Base.metadata.sorted_tables:
        lines.append(f"-- {table.name}")
        lines.append(_stmt(CreateTable(table, if_not_exists=True)))
        lines.append("")

    # 1.5) 老库补列：CREATE TABLE IF NOT EXISTS 对已存在的表是空操作，老库建表
    #      之后模型新增的列不会补上；索引段又引用这些列，ON_ERROR_STOP 下会因
    #      列缺失直接失败（如 scheduled_tasks.active_job_id）。这里为每列生成
    #      幂等的 ADD COLUMN IF NOT EXISTS——可空、不带约束/默认值，与应用
    #      init_db 的补列语义一致；新库上列已存在，均为空操作。
    lines.append("-- ---------- 老库补列（可空、不带约束，与应用 init_db 补列语义一致） ----------\n")
    for table in Base.metadata.sorted_tables:
        alters = [
            "ALTER TABLE {t} ADD COLUMN IF NOT EXISTS {c} {ty};".format(
                t=table.name,
                c=column.name,
                ty=str(column.type.compile(dialect=PG_DIALECT)),
            )
            for column in table.columns
        ]
        lines.append(f"-- {table.name}")
        lines.extend(alters)
        lines.append("")

    # 2) 模型内定义的索引（表级 Index，不在 CreateTable 输出里）
    lines.append("-- ---------- 模型内索引 ----------\n")
    model_index_names: set[str] = set()
    for table in Base.metadata.sorted_tables:
        for index in sorted(table.indexes, key=lambda ix: ix.name or ""):
            if not index.name:
                continue
            model_index_names.add(index.name)
            lines.append(f"-- {table.name}.{index.name}")
            lines.append(_stmt(CreateIndex(index, if_not_exists=True)))
            lines.append("")

    # 3) db.py 的补充索引（纯 SQL 维护，不在 metadata 中）
    lines.append("-- ---------- 补充索引（apps/api/app/core/db.py::_INDEXES） ----------\n")
    lines.append("-- 这些索引在 db.py 用字符串创建，未纳入模型 metadata；")
    lines.append("-- 修改清单后请重新生成本文件，避免两处漂移。\n")
    for name, target in _INDEXES:
        if name in model_index_names:
            continue  # 若将来收敛进模型，跳过以免重复
        lines.append(f"CREATE INDEX IF NOT EXISTS {name} ON {target};")
    lines.append("")

    # 4) db.py 里用纯 SQL 建的两个唯一索引（表达式索引 / 部分索引），模型无对应声明。
    #    收集模型里已声明的唯一约束/唯一索引名：PG 中索引与约束共享命名空间，
    #    同名时 CREATE UNIQUE INDEX IF NOT EXISTS 会被跳过——这与应用 init_db 的行为
    #    一致（以模型约束为准），生成物需保持同样语义，故显式跳过同名项避免 NOTICE。
    model_unique_names: set[str] = set()
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            if constraint.__class__.__name__ == "UniqueConstraint" and constraint.name:
                model_unique_names.add(constraint.name)
        for index in table.indexes:
            if index.unique and index.name:
                model_unique_names.add(index.name)

    lines.append("-- ---------- 特殊唯一索引（来自 db.py._apply_additive_migrations） ----------\n")
    # uq_skill_owner_key：db.py 用 COALESCE 表达式索引覆盖 NULL owner，但模型里
    #   已声明同名普通唯一约束（skill_key, owner_user_id）；PG 命名空间冲突会以
    #   模型约束为准（与应用 init_db 行为一致），故这里不重复输出。
    if "uq_skill_owner_key" not in model_unique_names:
        lines.append("-- skill_registry_entries：同一 (skill_key, owner) 只允许一条；")
        lines.append("--   用 COALESCE 让 owner 为 NULL 的内置 Skill 也参与唯一性判断。")
        lines.append(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_skill_owner_key "
            "ON skill_registry_entries (skill_key, (COALESCE(owner_user_id, '')));\n"
        )
    else:
        lines.append(
            "-- uq_skill_owner_key：模型已声明同名唯一约束（skipped，避免命名空间冲突；"
            "与应用 init_db 行为一致）。\n"
        )
    lines.append("-- users：邮箱大小写不敏感唯一；NULL（历史开发态账号）不参与唯一性。")
    lines.append(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email "
        "ON users (lower(email)) WHERE email IS NOT NULL;\n"
    )

    lines.append("COMMIT;")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "-o", "--output", type=Path, help="输出文件路径；不传则打印到 stdout"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="与 --output 指定的现有文件比对，不一致则退出码 1（不写文件）",
    )
    args = parser.parse_args()

    content = build_schema()

    if args.check:
        if args.output is None:
            parser.error("--check 需要配合 -o 指定要校验的文件")
        if not args.output.exists():
            print(f"[check] 文件不存在：{args.output}", file=sys.stderr)
            return 1
        existing = args.output.read_text(encoding="utf-8")
        if existing != content:
            print(
                f"[check] 不一致：{args.output} 与当前模型生成的 schema 不同，"
                "请重新运行 export_pg_schema.py -o <file>",
                file=sys.stderr,
            )
            return 1
        print(f"[check] 一致：{args.output}")
        return 0

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # 固定 LF：跨平台生成确定性产物，避免 Windows 的 CRLF 造成 git 噪音，
        # 也保证 --check 在任意平台都能逐字节比对。
        with args.output.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        print(f"已生成 {args.output}（{len(content.splitlines())} 行）", file=sys.stderr)
    else:
        sys.stdout.write(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
