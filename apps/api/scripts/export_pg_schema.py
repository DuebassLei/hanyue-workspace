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

生成物是**幂等**的（CREATE TABLE/INDEX IF NOT EXISTS + ON CONFLICT DO NOTHING），可在空库直接执行，
也可在已有库重跑（不会改已有表结构；老库补列仍由应用 init_db 负责）。

除表结构外，生成物末尾还包含一段**引导超管账号**种子（角色 superadmin + 用户
ADMIN_EMAIL + 角色绑定），让纯 SQL 初始化的库开箱即可登录。初始密码是仓库里的
固定值，仅作引导——首次登录后必须立即改密。

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
-- 适用：全新数据库一次性建表 + 播种引导超管账号。幂等（IF NOT EXISTS / ON CONFLICT
--       DO NOTHING），已有库重跑只补缺失对象、绝不覆盖已有数据——老库补列仍由应用
--       启动时的 init_db() 负责。
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
    return sql.rstrip(";") + ";"


def build_schema() -> str:
    lines: list[str] = [HEADER]

    # 1) 表 + 表级的 CHECK / UNIQUE / 外键约束：按外键依赖顺序输出
    lines.append("-- ---------- 表（含主键 / 外键 / CHECK / 唯一约束） ----------\n")
    for table in Base.metadata.sorted_tables:
        lines.append(f"-- {table.name}")
        lines.append(_stmt(CreateTable(table, if_not_exists=True)))
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

    # 5) 引导超管账号（幂等种子，不覆盖已有数据）——与建表同一事务提交
    lines.append(build_admin_seed())
    lines.append("COMMIT;")
    lines.append("")
    return "\n".join(lines)


# ── 引导超管账号 ──────────────────────────────────────────────────────
# 应用的 RBAC 数据（角色/权限/菜单）由启动时的 seed() 播种，但「首个超管账号」
# 此前只能靠 scripts/create_user.py 手工建——纯 SQL 初始化的库（DBA 预建、
# 无 DDL 权限环境）没有账号就登不进去。这里把引导超管并入 schema.sql：
#
#   - 邮箱/显示名/初始密码见下方常量，**初始密码是公开仓库里的固定值**，
#     仅作首次登录引导；生产环境登录后必须立即改密（或用 create_user.py reset-password）。
#   - 全部 INSERT 用 ON CONFLICT DO NOTHING：已有库重跑绝不会覆盖管理员
#     后来改过的密码、显示名或角色绑定。
#   - scrypt 哈希用固定盐在生成期算出（产物确定性是 --check 的前提）。
#     固定盐仅降低这**一个引导口令**的哈希强度，改密后走正常随机盐路径。

ADMIN_EMAIL = "1130122701@qq.com"
ADMIN_DISPLAY_NAME = "钥见AI"
ADMIN_PASSWORD = "Ztesoft_1234"
ADMIN_SUBJECT = f"password:{ADMIN_EMAIL}"
# 固定 id（32 位字符串）：让 SQL 与应用两侧对同一引导实体达成共识，便于审计定位
ADMIN_USER_ID = "bootstrap_admin00000000000000000"  # 15 + 17 = 32
ADMIN_ROLE_ID = "bootstrap_superadmin000000000000"  # 20 + 12 = 32


def _admin_password_hash() -> str:
    """用固定盐复现 core/security.hash_password 的 scrypt 格式，保证生成物确定性。"""
    from app.core.security import (  # noqa: PLC0415 - 生成期局部导入，保持模块加载轻量
        _PWD_DKLEN,
        _PWD_N,
        _PWD_P,
        _PWD_R,
        _PWD_SCHEME,
        _b64,
        _scrypt,
    )

    salt = b"hanyue-admin-boot"  # 恰好 16 字节固定盐
    digest = _scrypt(
        ADMIN_PASSWORD.encode(), salt, _PWD_N, _PWD_R, _PWD_P, _PWD_DKLEN
    )
    return f"{_PWD_SCHEME}${_PWD_N}${_PWD_R}${_PWD_P}${_b64(salt)}${_b64(digest)}"


def build_admin_seed() -> str:
    hash_value = _admin_password_hash()
    lines = [
        "",
        "-- ---------- 引导超管账号（幂等种子） ----------",
        "-- 初始密码为公开固定值，首次登录后必须立即修改！改密命令：",
        "--   cd apps/api && python scripts/create_user.py reset-password "
        f"--email {ADMIN_EMAIL}",
        "",
        "-- 内置超管角色（与 rbac_service.ROLE_META 一致；应用启动播种可兼容补齐其余字段）",
        "INSERT INTO roles (id, role_key, name, description, is_superadmin, is_system, sort, created_at, updated_at)",
        "VALUES (",
        f"    '{ADMIN_ROLE_ID}',",
        "    'superadmin',",
        "    '超级管理员',",
        "    '拥有全部权限，不受菜单与权限点限制',",
        "    TRUE,",
        "    TRUE,",
        "    0,",
        "    now(),",
        "    now()",
        ")",
        "ON CONFLICT (role_key) DO NOTHING;",
        "",
        "-- 超管账号",
        "INSERT INTO users (id, auth_subject, display_name, timezone, email, password_hash, password_changed_at, failed_login_attempts, locked_until, is_active, created_at, updated_at)",
        "VALUES (",
        f"    '{ADMIN_USER_ID}',",
        f"    '{ADMIN_SUBJECT}',",
        f"    '{ADMIN_DISPLAY_NAME}',",
        "    'Asia/Shanghai',",
        f"    '{ADMIN_EMAIL}',",
        f"    '{hash_value}',",
        "    now(),",
        "    0,",
        "    NULL,",
        "    TRUE,",
        "    now(),",
        "    now()",
        ")",
        "ON CONFLICT (auth_subject) DO NOTHING;",
        "",
        "-- 绑定超管角色（user_roles 主键冲突即跳过）",
        "INSERT INTO user_roles (user_id, role_id)",
        "SELECT u.id, r.id FROM users u, roles r",
        f"WHERE u.auth_subject = '{ADMIN_SUBJECT}' AND r.role_key = 'superadmin'",
        "ON CONFLICT DO NOTHING;",
        "",
        "-- 全部完成。用上面的邮箱与初始密码登录后请立即改密。",
        "",
    ]
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
