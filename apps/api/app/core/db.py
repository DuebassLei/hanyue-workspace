"""数据库连接与初始化。

SQLite 开发库走 create_all + 轻量补列迁移：create_all 只建缺失的表，
不会给已存在的表加新列，因此新增字段必须在这里显式补，否则线上旧库会 500。

字段变更的自动同步：init_db 在手工清单之后跑 _sync_schema_from_models，
把模型与库内结构的差异自动对齐——缺列/缺索引直接补；改列名/删列/VARCHAR↔TEXT
类型对齐受 settings.db_auto_destructive_ddl 控制（默认开启），执行前把旧值归档进
_schema_archive（可人工恢复），收窄装不下时跳过不阻塞。改列名优先靠「1 删 1 增」
启发式；多列歧义、主键变更、时区类转换仍需手工迁移。
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    func,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings
from .logging import get_logger

settings = get_settings()
logger = get_logger("app.db")

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 已存在表需要补的列：(表名, 列名, 列定义)
# 注意：模型新增的普通列**不再需要登记在这里**——init_db 末尾的
# _sync_schema_from_models 会直接对比模型与库内结构自动补列（见该函数注释）。
# 这个清单保留两类用途：① 模型没写 server_default 但旧行需要合法默认值的列；
# ② 需要精确控制 DDL 的列（如 is_active 的默认值语义）。新写代码优先在模型上
# 加 server_default，让自动同步成为唯一路径。
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    # 正式登录：邮箱密码。历史库没有这些列，必须在这里补（create_all 只管新表）
    ("users", "email", "VARCHAR(190)"),
    ("users", "password_hash", "VARCHAR(255)"),
    ("users", "password_changed_at", "TIMESTAMP"),
    ("users", "last_login_at", "TIMESTAMP"),
    ("users", "failed_login_attempts", "INTEGER DEFAULT 0"),
    ("users", "locked_until", "TIMESTAMP"),
    # PG 不接受 BOOLEAN DEFAULT 0，统一写 false/true（SQLite 3.23+ 同样识别）。
    # 必须是 true：补列会给**已有行**填这个默认值，填 false 会把所有老账号判成停用。
    ("users", "is_active", "BOOLEAN DEFAULT true"),
    ("publish_jobs", "target", "VARCHAR(24) DEFAULT 'publish'"),
    ("publish_jobs", "layout_snapshot", "JSON"),
    ("content_versions", "layout_options", "JSON DEFAULT '{}'"),
    ("publish_jobs", "content_item_id", "VARCHAR(32)"),
    ("publish_jobs", "status_history", "JSON"),
    ("connected_accounts", "auth_source", "VARCHAR(24) DEFAULT 'none'"),
    ("connected_accounts", "app_id", "VARCHAR(64) DEFAULT ''"),
    ("connected_accounts", "app_secret_ciphertext", "TEXT DEFAULT ''"),
    ("image_jobs", "started_at", "TIMESTAMP"),
    ("image_jobs", "completed_at", "TIMESTAMP"),
    ("skill_registry_entries", "owner_user_id", "VARCHAR(32)"),
    # D6：自动草稿保存字段（不创建版本记录，防丢失用）
    ("content_items", "draft_title", "VARCHAR(200)"),
    ("content_items", "draft_body", "TEXT"),
    # 稿件库：归档软隐藏（默认 False；SQLite 旧行 NULL 在查询里按未归档处理）
    ("content_items", "archived", "BOOLEAN DEFAULT false"),
    ("content_items", "archived_at", "TIMESTAMP"),
    # 稿件锁定的基础风格快照（风格库本身是新表，不进这里）
    ("content_items", "base_style_id", "VARCHAR(32)"),
    ("content_items", "base_style_name", "VARCHAR(40) DEFAULT ''"),
    ("content_items", "base_style_prompt", "TEXT DEFAULT ''"),
    ("content_items", "base_style_deleted", "BOOLEAN DEFAULT false"),
    # 贴图：锚点反推描述缓存（Q10-B 一致性兜底）
    ("sticker_sets", "meta_json", "JSON"),
    # 贴图：该行最近一次入队用的描述，用于识别「改了描述但还没重画」
    ("sticker_images", "generated_prompt", "TEXT"),
    # 全局默认文本模型（model_catalog_entries.id）
    ("user_preferences", "default_text_model_id", "VARCHAR(32)"),
    # 创作空间 AI 自动配图偏好
    ("user_preferences", "studio_image_preferences", "JSON"),
    ("storyboard_shots", "asset_refs", "JSON"),
    # 提示词合并（地基契约 §7）：老库补列后已有行也拿到合法默认值；
    # JSON 用字符串字面量默认值，SQLite 与 PG 都能把 '[]'/'{}' 隐式转成 json。
    ("prompt_templates", "prompt_type", "VARCHAR(16) DEFAULT 'normal'"),
    ("prompt_templates", "negative_content", "TEXT DEFAULT ''"),
    ("prompt_templates", "summary", "VARCHAR(120) DEFAULT ''"),
    ("prompt_templates", "sort_order", "INTEGER DEFAULT 0"),
    ("prompt_templates", "enabled", "BOOLEAN DEFAULT true"),
    ("prompt_templates", "preview_asset_ids", "JSON DEFAULT '[]'"),
    ("prompt_templates", "source", "VARCHAR(16) DEFAULT 'manual'"),
    # 分镜组级统一风格配置（JSON 快照）
    ("storyboard_projects", "style_config", "JSON DEFAULT '{}'"),
    ("storyboard_projects", "deleted_at", "TIMESTAMP"),
    ("storyboard_scenes", "deleted_at", "TIMESTAMP"),
    ("storyboard_shots", "deleted_at", "TIMESTAMP"),
    # 角色库：AI 生成的候选参考图（采用前挂在角色卡上）
    ("characters", "pending_images", "JSON"),
]

# 已存在列的加宽：(表名, 列名, 新类型)。PG 的 varchar→text 是纯元数据变更，不重写行；
# SQLite 不强制列宽、无需处理。幂等靠读 information_schema 判断当前宽度。
_WIDENED_COLUMNS: list[tuple[str, str, str]] = [
    # 头条热榜 Url 带跟踪参数可达 800+ 字符，VARCHAR(500) 会整轮采集 500
    ("hot_items", "url", "TEXT"),
]

# 需要放开 NOT NULL 的已存在列：(表名, 列名)。模型把列改为可空后，create_all 与
# 补列迁移都不会动旧表的约束，必须在这里显式放开，否则写入全局行（owner 为 null）
# 会在启动播种时 IntegrityError。
_RELAXED_NOT_NULL: list[tuple[str, str]] = [
    # 提示词合并（地基契约 §7）：提示词库从按 owner 隔离改为全局资源，新记录恒为 null
    ("prompt_templates", "owner_user_id"),
]

# keyset 分页与队列恢复都按时间列做 seek，缺索引会退化成全表排序。
# create_all 不会给已存在的表补索引，因此这里用 IF NOT EXISTS 幂等创建。
_INDEXES: list[tuple[str, str]] = [
    ("ix_assets_owner_created", "assets (owner_user_id, created_at DESC, id DESC)"),
    ("ix_content_ws_updated", "content_items (workspace_id, updated_at DESC, id DESC)"),
    ("ix_publish_owner_created", "publish_jobs (owner_user_id, created_at DESC, id DESC)"),
    ("ix_bj_owner_created", "background_jobs (owner_user_id, created_at DESC, id DESC)"),
    ("ix_bj_status_started", "background_jobs (status, started_at)"),
    ("ix_metrics_owner_captured", "metric_snapshots (owner_user_id, captured_at DESC)"),
    ("ix_accounts_owner_created", "connected_accounts (owner_user_id, created_at, id)"),
    ("ix_connections_owner_created", "model_connections (owner_user_id, created_at, id)"),
    ("ix_skills_owner_created", "skill_registry_entries (owner_user_id, created_at, id)"),
    # 热点页默认按「最近活跃日 + 分数」取，两个条件都要进索引
    ("ix_hot_items_day_platform_rank", "hot_items (day, platform, rank)"),
    ("ix_hot_clusters_day_score", "hot_clusters (last_seen_day, score DESC, id)"),
    # 贴图列表按「最近改动」取；集合内图片按图序读
    ("ix_sticker_sets_owner_updated", "sticker_sets (owner_user_id, updated_at DESC, id DESC)"),
    ("ix_sticker_images_set_order", "sticker_images (set_id, sort_order)"),
    ("ix_storyboard_projects_owner_updated", "storyboard_projects (owner_user_id, updated_at DESC, id DESC)"),
    ("ix_storyboard_scenes_project_order", "storyboard_scenes (project_id, sort_order)"),
    ("ix_storyboard_shots_scene_order", "storyboard_shots (scene_id, sort_order)"),
    # 定时任务：启动装载与对账扫 enabled；列表按 scope+owner 取；历史按 task 分页
    ("ix_sched_enabled_next", "scheduled_tasks (enabled, next_run_at)"),
    ("ix_sched_scope_owner_fired", "scheduled_tasks (scope, owner_user_id, last_fired_at DESC)"),
    ("ix_sched_runs_task_time", "scheduled_task_runs (task_id, triggered_at DESC, id DESC)"),
    ("ix_sched_runs_status", "scheduled_task_runs (status)"),
]


def _dedupe_skill_entries(conn) -> None:
    """同一 (skill_key, owner) 只保留最早一行。

    旧版创建路径没有唯一约束，并发首次请求可能写入重复内置行；
    重复行会让设置页出现同名 Skill，且授权可能指向即将被删的那一条。
    """
    ranked = (
        "SELECT id FROM ("
        " SELECT id, ROW_NUMBER() OVER ("
        "   PARTITION BY skill_key, COALESCE(owner_user_id, '')"
        "   ORDER BY CASE WHEN source = 'builtin' THEN 0 ELSE 1 END, created_at, id"
        " ) AS rn FROM skill_registry_entries"
        ") ranked WHERE rn = 1"
    )
    tables = set(inspect(engine).get_table_names())
    if "workspace_skills" in tables:
        conn.execute(
            text(
                "DELETE FROM workspace_skills "
                f"WHERE skill_entry_id NOT IN ({ranked})"
            )
        )
    conn.execute(
        text(f"DELETE FROM skill_registry_entries WHERE id NOT IN ({ranked})")
    )


def _sqlite_drop_not_null(conn, table_name: str, column: str) -> None:
    """SQLite 没有 ALTER COLUMN DROP NOT NULL，按官方建议重建表放开约束。

    前提：本应用的 SQLite 连接未开 PRAGMA foreign_keys，且没有其他表外键引用
    目标表，事务内 drop+rename 不会破坏引用。补列迁移先于本函数执行，旧表列已
    与模型对齐；复制仍按列名交集，容忍个别差异。重建顺带补齐模型上的 CHECK
    约束与索引（DROP TABLE 会带走旧索引）。
    """
    from sqlalchemy import MetaData
    from sqlalchemy.schema import CreateIndex, CreateTable

    from .. import models

    info = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    target = next((row for row in info if row[1] == column), None)
    if target is None or not target[3]:  # 列不存在或已可空，幂等跳过
        return
    table = models.Base.metadata.tables[table_name]
    tmp_name = f"{table_name}_nullable_rebuild"
    tmp_meta = MetaData()
    # FK 编译要能解析到被引用表：先把它们复制进临时元数据（只编译目标表，不会建它们）
    for fk in table.foreign_key_constraints:
        for element in fk.elements:
            referred = element.column.table
            if referred.name not in tmp_meta.tables:
                referred.to_metadata(tmp_meta)
    tmp = table.to_metadata(tmp_meta, name=tmp_name)
    conn.execute(text(str(CreateTable(tmp).compile(dialect=conn.dialect))))
    old_columns = {row[1] for row in info}
    col_list = ", ".join(c.name for c in table.columns if c.name in old_columns)
    conn.execute(
        text(f"INSERT INTO {tmp_name} ({col_list}) SELECT {col_list} FROM {table_name}")
    )
    conn.execute(text(f"DROP TABLE {table_name}"))
    conn.execute(text(f"ALTER TABLE {tmp_name} RENAME TO {table_name}"))
    for index in table.indexes:
        ddl = str(CreateIndex(index).compile(dialect=conn.dialect))
        conn.execute(text(ddl.replace("CREATE INDEX", "CREATE INDEX IF NOT EXISTS", 1)))


# 退役排版列只保留库内数据，不再进入 ORM，也不由自动同步删除或猜测改名。
_RETIRED_LAYOUT_DEFAULTS = {
    "user_preferences": {"wechat_layout": "'{}'"},
    "publish_jobs": {"theme_id": "''"},
}


def _preserve_retired_layout_columns(conn) -> None:
    """给旧列补数据库默认值，避免新 ORM 插入被旧 NOT NULL 约束阻塞。"""
    from sqlalchemy.schema import CreateTable, DefaultClause

    inspector = inspect(conn)
    tables = set(inspector.get_table_names())
    quote = conn.dialect.identifier_preparer.quote
    for table_name, defaults in _RETIRED_LAYOUT_DEFAULTS.items():
        if table_name not in tables:
            continue
        columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        missing = {
            name: default for name, default in defaults.items()
            if name in columns and columns[name]["default"] is None and not columns[name]["nullable"]
        }
        if not missing:
            continue
        if conn.dialect.name == "postgresql":
            for name, default in missing.items():
                conn.execute(text(
                    f"ALTER TABLE {quote(table_name)} ALTER COLUMN {quote(name)} SET DEFAULT {default}"
                ))
            continue
        if conn.dialect.name != "sqlite":
            raise RuntimeError("退役排版列的默认值迁移仅支持 SQLite 和 PostgreSQL")
        if conn.exec_driver_sql("PRAGMA foreign_keys").scalar():
            raise RuntimeError("迁移退役排版列前需停服并使用关闭 foreign_keys 的 SQLite 连接")
        # 显式事务保证建表、复制、换表及恢复索引/触发器能够一起回滚。
        if not conn.connection.driver_connection.in_transaction:
            conn.exec_driver_sql("BEGIN")
        objects = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE tbl_name = :name "
            "AND type IN ('index', 'trigger') AND sql IS NOT NULL"
        ), {"name": table_name}).scalars().all()
        metadata = MetaData()
        original = Table(table_name, metadata, autoload_with=conn)
        temporary_metadata = MetaData()
        for reflected in metadata.tables.values():
            if reflected.name != table_name:
                reflected.to_metadata(temporary_metadata)
        temporary_name = f"{table_name}_retired_defaults_rebuild"
        temporary = original.to_metadata(temporary_metadata, name=temporary_name)
        for name, default in missing.items():
            temporary.c[name].server_default = DefaultClause(text(default))
        conn.execute(CreateTable(temporary))
        column_list = ", ".join(quote(column.name) for column in original.columns)
        conn.execute(text(
            f"INSERT INTO {quote(temporary_name)} ({column_list}) "
            f"SELECT {column_list} FROM {quote(table_name)}"
        ))
        conn.execute(text(f"DROP TABLE {quote(table_name)}"))
        conn.execute(text(f"ALTER TABLE {quote(temporary_name)} RENAME TO {quote(table_name)}"))
        for ddl in objects:
            conn.exec_driver_sql(ddl)


def _apply_additive_migrations() -> None:
    """只做加列 / 加宽 / 放开 NOT NULL / 加索引 / 必要去重，幂等可重复执行。"""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    pg = engine.dialect.name == "postgresql"
    with engine.begin() as conn:
        _preserve_retired_layout_columns(conn)
        for table, column, ddl_type in _ADDED_COLUMNS:
            if table not in existing_tables:
                continue
            columns = {col["name"] for col in inspector.get_columns(table)}
            if column in columns:
                continue
            # PostgreSQL 下 TIMESTAMP 不带时区，与模型的 DateTime(timezone=True) 不一致
            effective_type = "TIMESTAMPTZ" if pg and ddl_type == "TIMESTAMP" else ddl_type
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {effective_type}"))
        for table, column, new_type in _WIDENED_COLUMNS:
            # SQLite 不强制列宽，放宽只对 PG 有意义；新库 create_all 已按模型建好
            if not pg or table not in existing_tables:
                continue
            row = conn.execute(
                text(
                    "SELECT data_type, character_maximum_length FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = :c"
                ),
                {"t": table, "c": column},
            ).first()
            # 已是 text（character_maximum_length 为 NULL）就跳过，保持幂等
            if row is None or (row[0] == "text" and row[1] is None):
                continue
            conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {new_type}"))
        for table, column in _RELAXED_NOT_NULL:
            if table not in existing_tables:
                continue
            if pg:
                row = conn.execute(
                    text(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_name = :t AND column_name = :c"
                    ),
                    {"t": table, "c": column},
                ).first()
                if row is None or row[0] == "YES":
                    continue
                conn.execute(
                    text(f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL")
                )
            else:
                _sqlite_drop_not_null(conn, table, column)
        for name, target in _INDEXES:
            table = target.split(" ", 1)[0]
            if table not in existing_tables:
                continue
            try:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {target}"))
            except Exception as exc:  # 旧库缺列时不影响启动，明确告警即可
                logger.warning("索引 %s 创建失败（可忽略）：%s", name, exc)
        if "skill_registry_entries" in existing_tables:
            try:
                _dedupe_skill_entries(conn)
                # SQLite 直接写表达式；PostgreSQL 的表达式索引要求额外一层括号
                expression = (
                    "(COALESCE(owner_user_id, ''))"
                    if pg
                    else "COALESCE(owner_user_id, '')"
                )
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_skill_owner_key "
                        f"ON skill_registry_entries (skill_key, {expression})"
                    )
                )
            except Exception as exc:
                logger.warning("Skill 唯一索引创建失败（可忽略）：%s", exc)
        # 邮箱唯一：partial index 让「历史开发态账号 email 为 NULL」不参与唯一性，
        # 而 lower(email) 保证大小写不产生两个账号（写入前已小写，索引作兜底）。
        if "users" in existing_tables:
            try:
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email "
                        "ON users (lower(email)) WHERE email IS NOT NULL"
                    )
                )
            except Exception as exc:
                logger.warning("users 邮箱唯一索引创建失败（可忽略）：%s", exc)


# ── 归档表：破坏性 DDL（改名/删列）执行前的值兜底 ────────────────────────
# 不进 Base.metadata（避免 create_all/自动同步把它当成业务表处理），独立元数据
# 幂等创建。恢复方式：按 row_pk + archived_at 找到旧值，手工 UPDATE 回目标列。
_SCHEMA_ARCHIVE_META = MetaData()
_schema_archive = Table(
    "_schema_archive",
    _SCHEMA_ARCHIVE_META,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("action", String(16), nullable=False),  # rename / drop
    Column("table_name", String(120), nullable=False),
    Column("column_name", String(120), nullable=False),
    Column("row_pk", String(255), nullable=False),
    # json.dumps(default=str) 序列化，datetime/dict 等都能存；TEXT 两种方言都稳
    Column("value", Text),
    Column("archived_at", DateTime(timezone=True), server_default=func.now()),
)


def _archive_column_values(
    conn, table_name: str, column_name: str, pk_cols: list[str], action: str
) -> int:
    """把列的全部非 NULL 值归档进 _schema_archive，返回归档行数。

    与后续的 DDL 在同一事务里执行：DDL 失败则归档一并回滚，不留脏记录。
    """
    preparer = conn.dialect.identifier_preparer
    qt = preparer.quote(table_name)
    qc = preparer.quote(column_name)
    pk_expr = " || '|' || ".join(preparer.quote(c) for c in pk_cols) or "''"
    rows = conn.execute(
        text(f"SELECT {pk_expr}, {qc} FROM {qt} WHERE {qc} IS NOT NULL")
    ).fetchall()
    for pk, value in rows:
        conn.execute(
            _schema_archive.insert().values(
                action=action,
                table_name=table_name,
                column_name=column_name,
                row_pk=str(pk),
                value=json.dumps(value, ensure_ascii=False, default=str),
            )
        )
    return len(rows)


def _str_family(type_str: str) -> bool:
    """是否属于 VARCHAR/TEXT 字符串家族（类型自动对齐只处理这一族）。"""
    t = type_str.strip().upper()
    return (
        t == "TEXT"
        or t.startswith("VARCHAR")
        or t.startswith("CHARACTER VARYING")
        or t.startswith("CHAR(")
    )


def _sync_schema_from_models() -> list[str]:
    """模型驱动的 Schema 自动同步（幂等，additive 为主，破坏性变更受开关控制）。

    create_all 只建缺失的表，_ADDED_COLUMNS 只覆盖手工登记的列——两者都不
    会处理「库里已存在的表」与模型的差异。这里分两阶段直接对比模型元数据
    与库内实际结构：

    阶段一：破坏性差异（settings.db_auto_destructive_ddl，默认开启）
      - 改列名：库里恰好少 1 列且模型恰好多 1 列 → 视为改名（先归档旧值再
        RENAME COLUMN，误判可从 _schema_archive 恢复）；多列歧义不猜，走删列+新增
      - 删列：库里模型外的列 → 归档旧值后 DROP COLUMN（FK/CHECK 等约束挡住
        时告警跳过，不阻塞启动）
      - 类型对齐：仅限 PG 且两侧都在 VARCHAR/TEXT 家族（varchar→text 是纯
        元数据变更；收窄用 USING 显式转换，PG 对装不下的值直接报错回滚，
        绝不截断）。TIMESTAMP↔TIMESTAMPTZ 等涉及时区/数值语义，坚持手工。
        SQLite 不强制列宽，类型差异跳过。

    阶段二：加列 / 加索引（始终执行）
      - 模型有、库里没有的列 → ALTER TABLE ADD COLUMN
      - 模型有、库里没有的索引 → CREATE INDEX（CreateIndex 编译 + IF NOT EXISTS）
      - NOT NULL 列在非空表上按可空补入（旧行为 NULL，新行由 Python default 或
        server_default 填充）；不补外键；主键列缺失告警跳过。

    返回本次变更清单（"表.列" / "表::索引" / "表:旧->新" 等），供日志与测试断言。
    """
    from sqlalchemy.schema import CreateColumn, CreateIndex

    from .. import models  # noqa: F401  确保模型注册到元数据

    existing_tables = set(inspect(engine).get_table_names())
    dialect = engine.dialect
    preparer = dialect.identifier_preparer
    pg = dialect.name == "postgresql"
    allow_destructive = settings.db_auto_destructive_ddl
    changed: list[str] = []

    if allow_destructive:
        _schema_archive.create(engine, checkfirst=True)

    # ── 阶段一：破坏性差异（每个操作独立事务：失败只回滚该操作，不影响其余）──
    if allow_destructive:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            reflected = {
                c["name"]: c["type"].compile(dialect=dialect)
                for c in inspect(engine).get_columns(table.name)
            }
            model_names = {c.name for c in table.columns}
            preserved = set(_RETIRED_LAYOUT_DEFAULTS.get(table.name, {}))
            db_only = sorted(set(reflected) - model_names - preserved)
            model_only = sorted(model_names - set(reflected))
            pk_cols = [c.name for c in table.primary_key.columns]

            # 1) 改名启发式：恰好 1 删 1 增才猜改名，宁可不猜也不冒险
            if len(db_only) == 1 and len(model_only) == 1:
                old, new = db_only[0], model_only[0]
                try:
                    with engine.begin() as conn:
                        n = _archive_column_values(conn, table.name, old, pk_cols, "rename")
                        conn.execute(
                            text(
                                f"ALTER TABLE {preparer.quote(table.name)} "
                                f"RENAME COLUMN {preparer.quote(old)} TO {preparer.quote(new)}"
                            )
                        )
                    changed.append(f"{table.name}:{old}->{new}")
                    logger.info(
                        "自动改列：%s.%s → %s（旧值已归档 %s 行，可从 _schema_archive 恢复）",
                        table.name, old, new, n,
                    )
                except Exception as exc:
                    logger.warning(
                        "自动改列失败 %s.%s → %s（跳过，旧值未动，需手工处理）：%s",
                        table.name, old, new, exc,
                    )
            elif db_only:
                logger.info(
                    "表 %s 模型外的列 %s 与模型新增列 %s 数量不一致，不猜改名，按删列+新增处理",
                    table.name, db_only, model_only,
                )

            # 2) 删列：剩下的模型外列 → 归档 + DROP
            reflected_now = {
                c["name"]
                for c in inspect(engine).get_columns(table.name)
            }
            for old in sorted(reflected_now - model_names - preserved):
                if old in table.primary_key.columns:
                    logger.warning(
                        "表 %s 的主键列 %s 不在模型中：无法安全删除，需手工迁移",
                        table.name, old,
                    )
                    continue
                try:
                    with engine.begin() as conn:
                        n = _archive_column_values(conn, table.name, old, pk_cols, "drop")
                        conn.execute(
                            text(
                                f"ALTER TABLE {preparer.quote(table.name)} "
                                f"DROP COLUMN {preparer.quote(old)}"
                            )
                        )
                    changed.append(f"{table.name}:-{old}")
                    logger.info(
                        "自动删列：%s.%s（旧值已归档 %s 行）", table.name, old, n
                    )
                except Exception as exc:
                    logger.warning(
                        "自动删列失败 %s.%s（跳过，旧值未动，需手工处理）：%s",
                        table.name, old, exc,
                    )

            # 3) 类型对齐：仅 PG 且 VARCHAR/TEXT 家族（SQLite 不强制列宽，跳过）
            if pg:
                for col in table.columns:
                    ref = reflected.get(col.name)
                    if ref is None:
                        continue
                    model_type = col.type.compile(dialect=dialect)
                    if ref == model_type:
                        continue
                    if not (_str_family(ref) and _str_family(model_type)):
                        logger.info(
                            "类型差异不自动处理：%s.%s 库=%s 模型=%s"
                            "（涉及时区/数值语义，需手工迁移）",
                            table.name, col.name, ref, model_type,
                        )
                        continue
                    try:
                        with engine.begin() as conn:
                            conn.execute(
                                text(
                                    f"ALTER TABLE {preparer.quote(table.name)} "
                                    f"ALTER COLUMN {preparer.quote(col.name)} "
                                    f"TYPE {model_type} "
                                    f"USING {preparer.quote(col.name)}::{model_type}"
                                )
                            )
                        changed.append(f"{table.name}:{col.name}:{ref}->{model_type}")
                        logger.info(
                            "自动改列类型：%s.%s %s → %s",
                            table.name, col.name, ref, model_type,
                        )
                    except Exception as exc:
                        logger.warning(
                            "自动改列类型失败 %s.%s → %s（跳过，保持原类型）：%s",
                            table.name, col.name, model_type, exc,
                        )

    # ── 阶段二：加列 / 加索引（始终执行；破坏性阶段可能已补上改名，重新反射）──
    inspector = inspect(engine)  # 新实例：避免拿到阶段一 DDL 之前的反射缓存
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # 新表由 create_all 按模型完整建立，无需处理
            db_columns = {c["name"] for c in inspector.get_columns(table.name)}
            # 只在真的要给非空表补 NOT NULL 列时才数行数，避免每次启动空耗 count
            row_count: int | None = None

            for col in table.columns:
                if col.name in db_columns:
                    continue
                if col.name in table.primary_key.columns:
                    logger.warning(
                        "表 %s 缺少主键列 %s：无法自动补，需手工迁移", table.name, col.name
                    )
                    continue
                not_null = not col.nullable
                if not_null and col.server_default is None:
                    if row_count is None:
                        row_count = (
                            conn.execute(
                                text(f"SELECT count(*) FROM {preparer.quote(table.name)}")
                            ).scalar()
                            or 0
                        )
                    if row_count > 0:
                        not_null = False
                        logger.warning(
                            "表 %s 已有 %s 行，NOT NULL 列 %s.%s 无默认值，按可空补入"
                            "（旧行为 NULL，新行走 Python default）",
                            table.name, row_count, table.name, col.name,
                        )
                # 用 CreateColumn 编译列定义（含类型/DEFAULT/NOT NULL 的方言渲染），
                # 复制列以便按上面的策略降级 NOT NULL
                ddl_col = CreateColumn(
                    Column(
                        col.name,
                        col.type,
                        nullable=not not_null,
                        server_default=col.server_default,
                    )
                ).compile(dialect=dialect)
                conn.execute(
                    text(
                        f"ALTER TABLE {preparer.quote(table.name)} "
                        f"ADD COLUMN {str(ddl_col)}"
                    )
                )
                changed.append(f"{table.name}.{col.name}")
                logger.info("自动补列：%s.%s", table.name, col.name)

            # 表达式索引（如 users 的 lower(email) 唯一索引）无法被反射，SQLAlchemy
            # 会发 SAWarning；这类索引由 _apply_additive_migrations 手工维护，这里
            # 静音后按名字比对即可（名字已存在就不会重复创建）。
            import warnings

            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=".*Skipped unsupported reflection of expression-based index.*",
                )
                db_indexes = {i["name"] for i in inspector.get_indexes(table.name)}
            for index in table.indexes:
                if not index.name or index.name in db_indexes:
                    continue
                ddl = str(CreateIndex(index).compile(dialect=dialect))
                # 并发实例可能已建过；IF NOT EXISTS 保持幂等（PG 与 SQLite 3.24+ 均支持）
                ddl = ddl.replace(
                    "CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX IF NOT EXISTS ", 1
                )
                if "IF NOT EXISTS" not in ddl:
                    ddl = ddl.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1)
                conn.execute(text(ddl))
                changed.append(f"{table.name}::{index.name}")
                logger.info("自动补索引：%s（%s）", index.name, table.name)

    return changed


def init_db() -> None:
    from .. import models  # noqa: F401  确保模型注册到元数据

    Base.metadata.create_all(bind=engine)
    _apply_additive_migrations()
    # 手工清单跑完后，把模型与库内剩余差异自动对齐（加列/加索引始终做；
    # 改名/删列/类型对齐受 db_auto_destructive_ddl 控制，旧值先归档）。
    # 放在最后：手工清单优先（默认值语义精确），自动同步兜底其余变更。
    _sync_schema_from_models()

