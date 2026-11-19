"""schema.sql 导出器回归网：确保生成的 DDL 覆盖全部表与关键索引，且保持幂等。

模型/ db.py 的补充索引清单变更后，这个文件会第一时间提醒你重新生成 schema.sql。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = API_DIR.parents[1]
SCHEMA_FILE = REPO_ROOT / "infra" / "postgres" / "schema.sql"


def _generate() -> str:
    result = subprocess.run(
        [sys.executable, "scripts/export_pg_schema.py"],
        cwd=API_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


def test_schema_covers_all_model_tables():
    """生成物必须为每个模型表输出一条 CREATE TABLE IF NOT EXISTS。"""
    from app.core.db import Base

    out = _generate()
    for table in Base.metadata.tables:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in out, f"缺表 DDL：{table}"


def test_schema_includes_supplementary_indexes():
    """db.py 里的补充索引与特殊唯一索引必须出现在生成物中，否则新库会缺索引。"""
    from app.core.db import _INDEXES

    out = _generate()
    for name, _target in _INDEXES:
        assert f"CREATE INDEX IF NOT EXISTS {name}" in out, f"缺补充索引：{name}"
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email" in out


def test_schema_is_idempotent_and_wrapped():
    """所有 DDL 用 IF NOT EXISTS 包裹，且整体在事务内、ON_ERROR_STOP 开启。"""
    out = _generate()
    assert "\\set ON_ERROR_STOP on" in out
    assert out.count("BEGIN;") == 1
    assert out.count("COMMIT;") == 1
    # 不允许出现非幂等的裸 CREATE TABLE / CREATE INDEX
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("CREATE TABLE "):
            assert stripped.startswith("CREATE TABLE IF NOT EXISTS "), line
        if stripped.startswith("CREATE INDEX ") or stripped.startswith("CREATE UNIQUE INDEX "):
            assert "IF NOT EXISTS" in stripped, line


def test_committed_schema_is_in_sync_with_models():
    """仓库里提交的 schema.sql 必须与当前模型生成结果一致（防改模型忘重新生成）。"""
    assert SCHEMA_FILE.is_file(), f"缺少 {SCHEMA_FILE}"
    assert SCHEMA_FILE.read_text(encoding="utf-8") == _generate(), (
        "schema.sql 与模型不一致，请运行：\n"
        "  cd apps/api && python scripts/export_pg_schema.py -o ../../infra/postgres/schema.sql"
    )


def test_schema_seeds_bootstrap_admin():
    """生成物必须包含引导超管账号种子：角色、用户、绑定三段齐全且幂等。"""
    from scripts.export_pg_schema import ADMIN_EMAIL, ADMIN_SUBJECT, build_admin_seed

    out = _generate()
    assert "INSERT INTO roles" in out and "'superadmin'" in out
    assert f"INSERT INTO users" in out and ADMIN_SUBJECT in out
    assert "INSERT INTO user_roles" in out
    assert out.count("ON CONFLICT (auth_subject) DO NOTHING;") == 1
    assert out.count("ON CONFLICT (role_key) DO NOTHING;") == 1

    # 种子里的哈希必须能被应用侧 verify_password 验证通过（否则登录不了）
    seed = build_admin_seed()
    hash_line = next(
        line.strip().rstrip(",")
        for line in seed.splitlines()
        if line.strip().startswith("'scrypt$")
    )
    from app.core.security import verify_password

    assert verify_password("Ztesoft_1234", hash_line.strip("'")), (
        "引导超管的 scrypt 哈希与初始密码不匹配，登录会失败"
    )
    assert ADMIN_EMAIL in seed
