"""地基补列/放开约束迁移的回归网。

背景：提示词合并（a8e1133）把 prompt_templates.owner_user_id 改为可空，
但 create_all 不动已存在表的约束，老库仍是 NOT NULL，启动播种出厂风格
（owner 为 null）直接 IntegrityError。这里用旧版 DDL 建一个临时库，
验证 _apply_additive_migrations 能放开约束、保住数据、幂等重跑。

约定：app 模块都在测试函数内延迟导入，避免收集阶段绑定 engine 到开发库。
"""

from __future__ import annotations

import pytest

# a8e1133 之前的 prompt_templates：owner NOT NULL、没有统一数据模型新列。
_LEGACY_PROMPT_DDL = """
CREATE TABLE prompt_templates (
    id VARCHAR(32) NOT NULL,
    owner_user_id VARCHAR(32) NOT NULL,
    title VARCHAR(120) NOT NULL,
    category VARCHAR(32) NOT NULL,
    content TEXT NOT NULL,
    tags JSON NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(owner_user_id) REFERENCES users (id)
)
"""


@pytest.fixture()
def legacy_engine(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text

    from app.core import db as db_module

    engine = create_engine(
        f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}", future=True
    )
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id VARCHAR(32) PRIMARY KEY)"))
        conn.execute(text(_LEGACY_PROMPT_DDL))
        conn.execute(
            text(
                "CREATE INDEX ix_prompt_templates_owner_user_id "
                "ON prompt_templates (owner_user_id)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO prompt_templates VALUES "
                "('p1', 'u1', '旧提示词', '通用', '内容', '[]', "
                "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
    monkeypatch.setattr(db_module, "engine", engine)
    yield engine
    engine.dispose()


def test_relax_owner_not_null_keeps_rows_and_allows_global_insert(legacy_engine):
    from sqlalchemy import text

    from app.core import db as db_module

    db_module._apply_additive_migrations()

    with legacy_engine.begin() as conn:
        info = conn.execute(text("PRAGMA table_info(prompt_templates)")).fetchall()
        owner = next(row for row in info if row[1] == "owner_user_id")
        assert not owner[3], "owner_user_id 应已放开 NOT NULL"

        # 旧行保留，补列迁移给的新列拿到合法默认值
        row = conn.execute(
            text(
                "SELECT title, prompt_type, source, enabled "
                "FROM prompt_templates WHERE id = 'p1'"
            )
        ).first()
        assert row == ("旧提示词", "normal", "manual", 1)

        # 全局行（owner=null）现在写得进去——启动播种不再 IntegrityError
        conn.execute(
            text(
                "INSERT INTO prompt_templates "
                "(id, owner_user_id, title, category, content, tags, preview_asset_ids, "
                "created_at, updated_at) "
                "VALUES ('p2', NULL, '出厂风格', '封面', 'x', '[]', '[]', "
                "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )

        # 重建带走了旧索引，必须按模型补回来
        indexes = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'index' "
                     "AND tbl_name = 'prompt_templates'")
            )
        }
        assert "ix_prompt_templates_owner_user_id" in indexes


def test_relax_not_null_is_idempotent(legacy_engine):
    from sqlalchemy import text

    from app.core import db as db_module

    db_module._apply_additive_migrations()
    db_module._apply_additive_migrations()  # 重跑不应重建表或丢数据

    with legacy_engine.begin() as conn:
        count = conn.execute(text("SELECT count(*) FROM prompt_templates")).scalar()
        assert count == 1
        info = conn.execute(text("PRAGMA table_info(prompt_templates)")).fetchall()
        owner = next(row for row in info if row[1] == "owner_user_id")
        assert not owner[3]
        # 重建后的表不应残留临时表
        leftovers = conn.execute(
            text("SELECT name FROM sqlite_master WHERE name LIKE '%nullable_rebuild%'")
        ).fetchall()
        assert leftovers == []


def test_rebuilt_table_enforces_model_check_constraints(legacy_engine):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from app.core import db as db_module

    db_module._apply_additive_migrations()

    with legacy_engine.begin() as conn:
        with pytest.raises(IntegrityError, match="ck_prompt_templates_type"):
            conn.execute(
                text(
                    "INSERT INTO prompt_templates "
                    "(id, owner_user_id, title, category, content, tags, preview_asset_ids, "
                    "prompt_type, created_at, updated_at) "
                    "VALUES ('p3', NULL, '坏类型', '通用', 'x', '[]', '[]', 'bogus', "
                    "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
                )
            )