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
def test_sync_schema_from_models_adds_missing_columns_and_indexes(tmp_path, monkeypatch):
    """模型驱动的自动补列/补索引：三条约路径 + 幂等。

    用独立的探针表验证（不动真实模型的元数据）：
      - 带模型 server_default 的 NOT NULL 列 → 保留 NOT NULL 并补上默认值；
      - 无默认值的 NOT NULL 列在非空表上 → 按约定降级为可空；
      - 库里被删的模型索引 → 自动重建；
      - 已对齐的表 → 二次调用零产出（幂等）。
    """
    from sqlalchemy import (
        Column, Index, Integer, MetaData, String, Table, Text, create_engine, text,
    )

    from app.core import db as db_module

    engine = create_engine(f"sqlite:///{(tmp_path / 'probe.db').as_posix()}", future=True)
    monkeypatch.setattr(db_module, "engine", engine)

    probe = Table(
        "sync_probe",
        db_module.Base.metadata,
        Column("id", String(32), primary_key=True),
        Column("existing_col", Text, nullable=False),
        Column("late_col", Integer, nullable=False, server_default="0"),
        Column("relaxed_col", Text, nullable=False),  # 模型无默认值
        Index("ix_sync_probe_existing", "existing_col"),
    )
    # 先手工建一个「旧版」探针表：只有旧列与旧索引，一行旧数据
    legacy = Table(
        "sync_probe",
        MetaData(),
        Column("id", String(32), primary_key=True),
        Column("existing_col", Text, nullable=False),
        Index("ix_sync_probe_existing", "existing_col"),
    )
    legacy.create(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO sync_probe (id, existing_col) VALUES ('r1', 'x')"))

    try:
        added = db_module._sync_schema_from_models()
        assert "sync_probe.late_col" in added
        assert "sync_probe.relaxed_col" in added

        with engine.begin() as conn:
            conn.execute(text("INSERT INTO sync_probe (id, existing_col) VALUES ('r2', 'y')"))
            rows = conn.execute(
                text("SELECT id, late_col, relaxed_col FROM sync_probe ORDER BY id")
            ).fetchall()
            # 旧行与新行：late_col 都拿到 server_default 的 0；relaxed_col 旧行 NULL、可空
            assert rows == [("r1", 0, None), ("r2", 0, None)]
            # 已存在的模型索引没有被重复创建
            idx = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='index' "
                     "AND name='ix_sync_probe_existing'")
            ).fetchall()
            assert len(idx) == 1

        # 删掉模型索引后，自动同步应补回
        with engine.begin() as conn:
            conn.execute(text("DROP INDEX ix_sync_probe_existing"))
        assert "sync_probe::ix_sync_probe_existing" in db_module._sync_schema_from_models()
        with engine.begin() as conn:
            idx = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='index' "
                     "AND name='ix_sync_probe_existing'")
            ).fetchall()
            assert len(idx) == 1

        # 库内结构已与模型对齐：再跑一次应零产出（幂等）
        assert db_module._sync_schema_from_models() == []
    finally:
        db_module.Base.metadata.remove(probe)

def _make_probe(engine, legacy_table, rows):
    """按旧版 DDL 建探针表并插入数据。"""
    from sqlalchemy import text

    legacy_table.create(engine)
    with engine.begin() as conn:
        for row in rows:
            cols = ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in row)
            conn.execute(text(f"INSERT INTO {legacy_table.name} VALUES ({cols})"))


def test_sync_renames_column_and_archives_old_values(tmp_path, monkeypatch):
    """改列名（1 删 1 增启发式）：数据跟随改名，旧值归档可恢复，幂等。"""
    from sqlalchemy import (
        Column, MetaData, String, Table, Text, create_engine, text,
    )

    from app.core import db as db_module

    engine = create_engine(f"sqlite:///{(tmp_path / 'rename.db').as_posix()}", future=True)
    monkeypatch.setattr(db_module, "engine", engine)

    probe = Table(
        "sync_probe_rename", db_module.Base.metadata,
        Column("id", String(32), primary_key=True),
        Column("renamed_col", Text, nullable=False),  # 旧名 old_col
    )
    legacy = Table(
        "sync_probe_rename", MetaData(),
        Column("id", String(32), primary_key=True),
        Column("old_col", Text, nullable=False),
    )
    try:
        _make_probe(engine, legacy, [("r1", "旧值")])

        changed = db_module._sync_schema_from_models()
        assert "sync_probe_rename:old_col->renamed_col" in changed

        with engine.begin() as conn:
            # 数据跟着改名走，没有丢
            assert conn.execute(text("SELECT renamed_col FROM sync_probe_rename")).scalar() == "旧值"
            archived = conn.execute(
                text("SELECT count(*) FROM _schema_archive WHERE action='rename' "
                     "AND table_name='sync_probe_rename' AND column_name='old_col'")
            ).scalar()
            assert archived == 1

        assert db_module._sync_schema_from_models() == []  # 幂等
    finally:
        db_module.Base.metadata.remove(probe)


def test_sync_drops_stale_column_archives_and_skips_sqlite_narrow(tmp_path, monkeypatch):
    """删列：旧值归档后移除；类型收窄在 SQLite 上跳过（不强制列宽），不丢数据。"""
    from sqlalchemy import (
        Column, MetaData, String, Table, Text, create_engine, inspect, text,
    )

    from app.core import db as db_module

    engine = create_engine(f"sqlite:///{(tmp_path / 'drop.db').as_posix()}", future=True)
    monkeypatch.setattr(db_module, "engine", engine)

    probe = Table(
        "sync_probe_drop", db_module.Base.metadata,
        Column("id", String(32), primary_key=True),
        Column("narrowed", String(10), nullable=True),  # 旧库是 VARCHAR(50)
    )
    legacy = Table(
        "sync_probe_drop", MetaData(),
        Column("id", String(32), primary_key=True),
        Column("stale_col", Text),                       # 模型里已删除
        Column("narrowed", String(50)),
    )
    try:
        _make_probe(engine, legacy, [("r1", "垃圾数据", "abcdefgh")])

        changed = db_module._sync_schema_from_models()
        assert "sync_probe_drop:-stale_col" in changed

        with engine.begin() as conn:
            names = {c["name"] for c in inspect(engine).get_columns("sync_probe_drop")}
            assert "stale_col" not in names and "narrowed" in names
            # 归档里有被删列的旧值，可人工恢复
            archived = conn.execute(
                text("SELECT value FROM _schema_archive WHERE action='drop' "
                     "AND table_name='sync_probe_drop' AND column_name='stale_col'")
            ).scalar()
            assert archived == '"垃圾数据"'
            # 数据都在
            row = conn.execute(text("SELECT narrowed FROM sync_probe_drop")).scalar()
            assert row == "abcdefgh"

        # SQLite 跳过类型收窄：narrowed 仍是 VARCHAR(50)，值未动
        assert db_module._sync_schema_from_models() == []  # 幂等
        narrowed_type = next(
            c for c in inspect(engine).get_columns("sync_probe_drop")
            if c["name"] == "narrowed"
        )
        assert narrowed_type["type"].compile(dialect=engine.dialect) == "VARCHAR(50)"
    finally:
        db_module.Base.metadata.remove(probe)


def test_sync_destructive_disabled_keeps_old_column(tmp_path, monkeypatch):
    """开关关闭：只做加列，模型外的列原样保留，也不写归档。"""
    from sqlalchemy import (
        Column, MetaData, String, Table, Text, create_engine, inspect, text,
    )

    from app.core import db as db_module

    monkeypatch.setattr(db_module.settings, "db_auto_destructive_ddl", False)
    engine = create_engine(f"sqlite:///{(tmp_path / 'flag.db').as_posix()}", future=True)
    monkeypatch.setattr(db_module, "engine", engine)

    probe = Table(
        "sync_probe_flag", db_module.Base.metadata,
        Column("id", String(32), primary_key=True),
        Column("new_col", Text, nullable=True),
    )
    legacy = Table(
        "sync_probe_flag", MetaData(),
        Column("id", String(32), primary_key=True),
        Column("old_col", Text, nullable=False),
    )
    try:
        _make_probe(engine, legacy, [("r1", "保留我")])

        changed = db_module._sync_schema_from_models()
        assert "sync_probe_flag.new_col" in changed
        assert not any("->" in c for c in changed)  # 没有触发改名

        names = {c["name"] for c in inspect(engine).get_columns("sync_probe_flag")}
        assert "old_col" in names and "new_col" in names
        with engine.begin() as conn:
            assert conn.execute(text("SELECT old_col FROM sync_probe_flag")).scalar() == "保留我"
            # 没建归档表（破坏性阶段整体跳过）
            assert not conn.execute(
                text("SELECT count(*) FROM sqlite_master WHERE name='_schema_archive'")
            ).scalar()
    finally:
        db_module.Base.metadata.remove(probe)
