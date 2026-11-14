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
    # 贴图：锚点反推描述缓存（Q10-B 一致性兜底）
    ("sticker_sets", "meta_json", "JSON"),
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


def _apply_additive_migrations() -> None:
    """只做加列 / 加索引 / 必要去重，幂等可重复执行。"""
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


def init_db() -> None:
    from .. import models  # noqa: F401  确保模型注册到元数据

    Base.metadata.create_all(bind=engine)
    _apply_additive_migrations()

