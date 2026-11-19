"""数据库连接与初始化。

SQLite 开发库走 create_all + 轻量补列迁移：create_all 只建缺失的表，
不会给已存在的表加新列，因此新增字段必须在这里显式补，否则线上旧库会 500。
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, inspect, text
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
    ("publish_jobs", "theme_id", "VARCHAR(32) DEFAULT ''"),
    ("publish_jobs", "layout_snapshot", "JSON"),
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
    # 微信排版页：用户级默认主题 / 收藏 / 隐藏
    ("user_preferences", "wechat_layout", "JSON"),
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
    ("video_shots", "intensity", "FLOAT"),
    ("video_shots", "focus", "JSON"),
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
    # 视频创作：列表按「最近改动」取；镜头按序读
    ("ix_video_projects_owner_updated", "video_projects (owner_user_id, updated_at DESC, id DESC)"),
    ("ix_video_shots_project_order", "video_shots (project_id, sort_order)"),
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


def _apply_additive_migrations() -> None:
    """只做加列 / 加宽 / 放开 NOT NULL / 加索引 / 必要去重，幂等可重复执行。"""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    pg = engine.dialect.name == "postgresql"
    with engine.begin() as conn:
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


def init_db() -> None:
    from .. import models  # noqa: F401  确保模型注册到元数据

    Base.metadata.create_all(bind=engine)
    _apply_additive_migrations()

