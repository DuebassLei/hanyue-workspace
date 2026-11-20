"""领域模型。字段命名与设计文档第 9 章对齐；数据库列用 snake_case，API 层转 camelCase。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .core.db import Base

# 生产用 PG 的 JSONB（可索引、可算子查询）；测试跑 SQLite 时自动退化成 JSON。
JSONBDoc = JSON().with_variant(JSONB, "postgresql")


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class User(Base, TimestampMixin):
    """账号。

    auth_subject 是**稳定的身份键**，与登录方式解耦：
      - 密码账号：`password:<email>`
      - 开发态账号：用户填的 subject
      - 将来接 IdP：断言里的 sub
    这样同一个用户换登录方式时，workspace/内容的归属不会断。

    密码列一律可空：历史开发态账号没有密码，IdP 账号将来也不需要。
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    auth_subject: Mapped[str] = mapped_column(String(190), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="创作者")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    # 登录邮箱（统一小写存储，唯一索引见 core/db.py::_INDEXES_EXTRA）
    email: Mapped[str | None] = mapped_column(String(190), nullable=True, index=True)
    # scrypt 哈希串，格式 scrypt$n$r$p$salt$digest（见 core/security.py）
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 改密时间：令牌里带 pwdv，早于该时间的会话一律失效
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 连续失败计数与锁定截止时间：达到阈值后锁定一段时间，防在线爆破
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1"
    )


class RevokedToken(Base):
    """已吊销的会话令牌（jti）。logout 写入，鉴权时命中即拒绝。"""

    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(32), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserPreference(Base, TimestampMixin):
    """用户级偏好：主题等跨设备设置（设计 §5.4 / 验收 6）。"""

    __tablename__ = "user_preferences"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), unique=True, index=True
    )
    # system | light | dark
    theme: Mapped[str] = mapped_column(String(16), default="system")
    # 全局默认文本模型：model_catalog_entries.id。不选择模型时它排候选链首位，
    # 后面仍保留自动降级；失效时忽略但不清除偏好，恢复后自动生效。
    default_text_model_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 全局默认生图模型：model_catalog_entries.id。不指定 modelId 时优先用它，
    # 优先于环境变量供应商；失效时忽略但不清除偏好，恢复后自动生效。
    default_image_model_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 创作空间 AI 自动配图偏好；读时兜底，写时整包替换
    studio_image_preferences: Mapped[dict] = mapped_column(JSON, default=dict)


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="我的工作区")
    settings: Mapped[dict] = mapped_column(JSON, default=dict)


class ContentItem(Base, TimestampMixin):
    __tablename__ = "content_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="未命名稿件")
    type: Mapped[str] = mapped_column(String(32), default="article")
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    current_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 自动草稿字段：自动保存写这里，不创建版本记录；手动「存为版本」才写 content_versions
    draft_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    draft_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 归档：软隐藏而非删除。侧栏与稿件库默认视图排除，详情接口照常可开
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 基础风格快照：锁定当时的 id/名称/提示词，风格被删后只打 deleted，不回写快照
    base_style_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    base_style_name: Mapped[str] = mapped_column(String(40), default="", server_default="")
    base_style_prompt: Mapped[str] = mapped_column(Text, default="", server_default="")
    base_style_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class ImageStyle(Base, TimestampMixin):
    """全局风格库。无归属用户，启停与维护走 image_styles.manage。"""

    __tablename__ = "image_styles"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('base','cover','inline')",
            name="ck_image_styles_purpose",
        ),
        CheckConstraint(
            "sort_order >= 0 AND sort_order <= 9999",
            name="ck_image_styles_sort",
        ),
        Index("ix_image_styles_purpose_sort", "purpose", "sort_order", "id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(40))
    purpose: Mapped[str] = mapped_column(String(16))
    prompt: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(String(120), default="", server_default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class ContentVersion(Base, TimestampMixin):
    __tablename__ = "content_versions"
    __table_args__ = (UniqueConstraint("content_item_id", "version_no", name="uq_version_no"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    content_item_id: Mapped[str] = mapped_column(ForeignKey("content_items.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    parent_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_type: Mapped[str] = mapped_column(String(24), default="manual")
    model_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    prompt_template_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    # 按文章版本保存独立工作台设置，发布入队时复制为不可变快照。
    layout_options: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")


class PlatformVariant(Base, TimestampMixin):
    __tablename__ = "platform_variants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    content_item_id: Mapped[str] = mapped_column(ForeignKey("content_items.id"), index=True)
    base_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    platform: Mapped[str] = mapped_column(String(24), index=True)
    revision_no: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="draft")


class CalendarEntry(Base, TimestampMixin):
    __tablename__ = "calendar_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    content_item_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(24), nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    planned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(24), default="planned")


class Asset(Base, TimestampMixin):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    storage_key: Mapped[str] = mapped_column(String(400))
    original_name: Mapped[str] = mapped_column(String(240), default="")
    mime_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_type: Mapped[str] = mapped_column(String(24), default="upload")
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    license_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(80), nullable=True)
    parent_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ContentAsset(Base):
    __tablename__ = "content_assets"

    content_version_id: Mapped[str] = mapped_column(
        ForeignKey("content_versions.id"), primary_key=True
    )
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), primary_key=True)
    usage_role: Mapped[str] = mapped_column(String(32), default="inline")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class ModelConnection(Base, TimestampMixin):
    __tablename__ = "model_connections"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider_type: Mapped[str] = mapped_column(String(48), default="openai_compatible")
    display_name: Mapped[str] = mapped_column(String(120))
    base_url: Mapped[str] = mapped_column(String(300), default="")
    # 生产环境使用 KMS/信封加密；此处保存 Fernet 风格密文，绝不返回明文
    credential_ciphertext: Mapped[str] = mapped_column(Text, default="")
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="untested")


class ModelCatalogEntry(Base, TimestampMixin):
    __tablename__ = "model_catalog_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    connection_id: Mapped[str] = mapped_column(ForeignKey("model_connections.id"), index=True)
    model_code: Mapped[str] = mapped_column(String(120))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    supports_streaming: Mapped[bool] = mapped_column(Boolean, default=True)
    options: Mapped[dict] = mapped_column(JSON, default=dict)


class AiGeneration(Base, TimestampMixin):
    __tablename__ = "ai_generations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    task_type: Mapped[str] = mapped_column(String(48), default="article")
    model_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    request_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    prompt_version: Mapped[str] = mapped_column(String(32), default="v1")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_text: Mapped[str] = mapped_column(Text, default="")


class ConnectedAccount(Base, TimestampMixin):
    __tablename__ = "connected_accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    platform: Mapped[str] = mapped_column(String(24), index=True)
    platform_subject: Mapped[str] = mapped_column(String(190), default="")
    display_name: Mapped[str] = mapped_column(String(190), default="")
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    access_token_ciphertext: Mapped[str] = mapped_column(Text, default="")
    refresh_token_ciphertext: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # not_connected | pending_authorization | connected | expiring | reauthorization_required | unbound
    status: Mapped[str] = mapped_column(String(32), default="not_connected", index=True)
    auto_publish_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # none | demo（本地演示，非平台授权） | oauth（平台官方授权回调写入） | app_secret（凭据验证写入）
    auth_source: Mapped[str] = mapped_column(String(24), default="none", index=True)
    # 账号级凭据（公众号）：支持同一平台维护多个账号，各自持有 AppID/AppSecret
    app_id: Mapped[str] = mapped_column(String(64), default="")
    app_secret_ciphertext: Mapped[str] = mapped_column(Text, default="")


class PlatformAccessToken(Base, TimestampMixin):
    """平台级 access_token 缓存（公众号 AppID/AppSecret 模式）。

    微信令牌全局唯一且有配额限制，多个进程各自去换会互相顶掉，
    因此必须落库共享：`(platform, app_id)` 唯一。
    """

    __tablename__ = "platform_access_tokens"
    __table_args__ = (UniqueConstraint("platform", "app_id", name="uq_platform_token"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    platform: Mapped[str] = mapped_column(String(24), index=True)
    app_id: Mapped[str] = mapped_column(String(64))
    access_token_ciphertext: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(190), nullable=True)


class PublishJob(Base, TimestampMixin):
    __tablename__ = "publish_jobs"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "idempotency_key", name="uq_publish_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    platform_variant_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 关联创作空间稿件；为空表示手工填写的独立任务
    content_item_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    connected_account_id: Mapped[str] = mapped_column(String(32), index=True)
    platform: Mapped[str] = mapped_column(String(24), default="")
    # publish：对外发布（需平台发布能力核验）；draft：只推送到平台草稿箱（公众号）
    target: Mapped[str] = mapped_column(String(24), default="publish", index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), default="pending_review", index=True)
    # 状态流转历史：每条 {"status", "at", "actor", "note"}，由 publish_state 的
    # apply_transition / record_history 追加；详情页时间线据此展示任务经历了什么
    status_history: Mapped[list] = mapped_column(JSON, default=list)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    remote_content_id: Mapped[str | None] = mapped_column(String(190), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str] = mapped_column(String(64), default="")
    reviewed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    # 公众号排版：推送时的工作室配置快照。
    layout_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)


class PublishRecord(Base, TimestampMixin):
    __tablename__ = "publish_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    publish_job_id: Mapped[str] = mapped_column(ForeignKey("publish_jobs.id"), index=True)
    platform: Mapped[str] = mapped_column(String(24))
    remote_content_id: Mapped[str | None] = mapped_column(String(190), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    permalink: Mapped[str | None] = mapped_column(String(500), nullable=True)
    result_status: Mapped[str] = mapped_column(String(32), default="unknown")


class MetricSnapshot(Base, TimestampMixin):
    __tablename__ = "metric_snapshots"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    connected_account_id: Mapped[str] = mapped_column(String(32), index=True)
    platform: Mapped[str] = mapped_column(String(24))
    remote_content_id: Mapped[str | None] = mapped_column(String(190), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    normalized_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    raw_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(24), default="api")


class PlatformSyncJob(Base, TimestampMixin):
    __tablename__ = "platform_sync_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    connected_account_id: Mapped[str] = mapped_column(String(32), index=True)
    sync_type: Mapped[str] = mapped_column(String(32), default="incremental")
    cursor: Mapped[str | None] = mapped_column(String(190), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ImageJob(Base, TimestampMixin):
    __tablename__ = "image_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    asset_id: Mapped[str] = mapped_column(String(32), index=True)
    operation: Mapped[str] = mapped_column(String(32))
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    output_asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WeekReview(Base, TimestampMixin):
    __tablename__ = "week_reviews"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    week_start: Mapped[str] = mapped_column(String(10), index=True)
    planned_count: Mapped[int] = mapped_column(Integer, default=0)
    published_count: Mapped[int] = mapped_column(Integer, default=0)
    learnings: Mapped[str] = mapped_column(Text, default="")
    blockers: Mapped[str] = mapped_column(Text, default="")
    next_week_focus: Mapped[str] = mapped_column(Text, default="")


# ---------------- Agent / Skill ----------------

class AgentProviderConnection(Base, TimestampMixin):
    __tablename__ = "agent_provider_connections"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(48), default="native")
    credential_ciphertext: Mapped[str] = mapped_column(Text, default="")
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="unconfigured")


class SkillRegistryEntry(Base, TimestampMixin):
    __tablename__ = "skill_registry_entries"
    # 新库由该约束保证 (skill_key, owner) 唯一；老库由 core/db.py 的表达式唯一索引
    # `uq_skill_owner_key` 兜住内置行（owner 为 NULL）。两者在 SQLite 与 PG 共用同一套
    # 索引名，同名会冲突，所以模型约束改用 _plain；db.py 仍是 NULL owner 口径的唯一来源。
    __table_args__ = (
        UniqueConstraint("skill_key", "owner_user_id", name="uq_skill_owner_key_plain"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    source: Mapped[str] = mapped_column(String(120), default="builtin")
    # 自定义 Skill 归属用户；内置 Skill 为 NULL，全员可见
    owner_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    skill_key: Mapped[str] = mapped_column(String(120), index=True)
    # 分组用：flow=流程型，style=风格型；内置行由启动同步从出厂定义回填，
    # 自定义行在创建时写入，之后不可改（改分组会牵动 manifest 形态）
    category: Mapped[str] = mapped_column(String(24), default="style")
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    display_name: Mapped[str] = mapped_column(String(160), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    checksum: Mapped[str] = mapped_column(String(80), default="")
    trust_status: Mapped[str] = mapped_column(String(24), default="trusted")
    allowed_tool_ids: Mapped[list] = mapped_column(JSON, default=list)
    risk_level: Mapped[str] = mapped_column(String(24), default="read_only")


class SkillWritingRule(Base, TimestampMixin):
    """Skill 的写作规则：一行一条，type+key 唯一。

    owner_user_id 为空 = 全局规则（内置 / 管理员维护）；非空 = 该用户的个人副本。
    manifest 里不再保存规则型 key，运行时由 skill_registry.compose_manifest 合成。
    """

    __tablename__ = "skill_writing_rules"
    __table_args__ = (
        UniqueConstraint("skill_key", "owner_user_id", "rule_type", "rule_key", name="uq_swr_rule"),
        CheckConstraint(
            "rule_type IN ('tone','style_rule','prompt_template','theme','target_length',"
            "'workflow_step','review_rule','fact_policy','image_policy','repair_policy','exemplar')",
            name="ck_swr_type",
        ),
        Index("ix_swr_lookup", "skill_key", "owner_user_id", "rule_type", "sort_order"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # 不指向 skill_registry_entries：规则可以先于条目存在（出厂导入、个人副本），
    # 也可能在条目被删后残留，由维护页展示「孤儿规则」，不应被外键阻断。
    skill_key: Mapped[str] = mapped_column(String(120), index=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    rule_type: Mapped[str] = mapped_column(String(24))
    rule_key: Mapped[str] = mapped_column(String(64))
    # 单行类（tone/prompt_template/theme/target_length 与三个策略块）：存对象；列表类条目：存单条对象
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # factory = 出厂初始化导入，可被「按出厂重建」覆盖；user = 界面创建
    source: Mapped[str] = mapped_column(String(16), default="user", server_default="user")


class PromptRule(Base, TimestampMixin):
    """全局提示词与词表：一行一个 key，管理员维护。"""

    __tablename__ = "prompt_rules"
    __table_args__ = (
        CheckConstraint(
            "category IN ('contract','system','outline','repair','tone_lexicon','workflow_note')",
            name="ck_prompt_rules_category",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # key 是 SQL 关键字，但作为列名合法（建表/反射都会被正确引号化）；读取一律走 services.prompt_rules
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    # 词表/句式等结构化规则
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    source: Mapped[str] = mapped_column(String(16), default="factory", server_default="factory")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class WorkspaceSkill(Base, TimestampMixin):
    __tablename__ = "workspace_skills"
    __table_args__ = (
        UniqueConstraint("workspace_id", "skill_entry_id", name="uq_workspace_skill"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    skill_entry_id: Mapped[str] = mapped_column(ForeignKey("skill_registry_entries.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    granted_tool_ids: Mapped[list] = mapped_column(JSON, default=list)
    approval_policy: Mapped[str] = mapped_column(String(24), default="risk_based")


class AgentSession(Base, TimestampMixin):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    provider_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="新会话")
    status: Mapped[str] = mapped_column(String(24), default="idle")


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    goal: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    permission_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    budget_limit: Mapped[dict] = mapped_column(JSON, default=dict)
    rounds_used: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AgentEvent(Base, TimestampMixin):
    __tablename__ = "agent_events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_agent_event_seq"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=1)
    event_type: Mapped[str] = mapped_column(String(48), default="message")
    safe_payload: Mapped[dict] = mapped_column(JSON, default=dict)


class AgentToolCall(Base, TimestampMixin):
    __tablename__ = "agent_tool_calls"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    tool_id: Mapped[str] = mapped_column(String(64))
    input_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    approval_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentApproval(Base, TimestampMixin):
    __tablename__ = "agent_approvals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    requested_action: Mapped[str] = mapped_column(String(120), default="")
    risk_level: Mapped[str] = mapped_column(String(24), default="external_side_effect")
    parameter_digest: Mapped[str] = mapped_column(String(120), default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    decided_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewComment(Base, TimestampMixin):
    __tablename__ = "review_comments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    content_item_id: Mapped[str] = mapped_column(ForeignKey("content_items.id"), index=True)
    author_user_id: Mapped[str] = mapped_column(String(32))
    body: Mapped[str] = mapped_column(Text, default="")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------- 异步任务 / OAuth ----------------

class BackgroundJob(Base, TimestampMixin):
    """统一异步任务（设计 §6.6 / §11.1）。

    入队后由 Redis 消费者或本地线程池执行；状态与结果落库，
    `GET /jobs/:id` 可随时查询，断线可恢复，绝不只写一条 queued 就再也不动。
    """

    __tablename__ = "background_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    queue: Mapped[str] = mapped_column(String(48), index=True)
    kind: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CardTemplate(Base, TimestampMixin):
    """图文卡片的自定义模板（底纹 / 边框 / 配色等整份配置存 JSON）。"""

    __tablename__ = "card_templates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    subtitle: Mapped[str] = mapped_column(String(200), default="")
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class PromptTemplate(Base, TimestampMixin):
    """提示词库：统一为全局资源（owner_user_id 可空，新记录为 null）。

    提示词类型区分普通/封面/配图/基础风格等；正反向两段提示词分列存储。
    """

    __tablename__ = "prompt_templates"
    __table_args__ = (
        CheckConstraint(
            "prompt_type IN ('normal','cover','illustration','base','comic_panel','reference')",
            name="ck_prompt_templates_type",
        ),
        CheckConstraint(
            "source IN ('manual','ai','factory')",
            name="ck_prompt_templates_source",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(32), default="通用")
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    # 统一数据模型（地基契约 §7）；server_default 保证老库补列后已有行也读到合法值。
    prompt_type: Mapped[str] = mapped_column(String(16), default="normal", server_default="normal")
    negative_content: Mapped[str] = mapped_column(Text, default="", server_default="")
    summary: Mapped[str] = mapped_column(String(120), default="", server_default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    preview_asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")


class HanddrawStyle(Base, TimestampMixin):
    """手绘风格库（内置只读）：来自 handdraw-style-prompter 的资源快照。"""

    __tablename__ = "handdraw_styles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    style_number: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    legacy_number: Mapped[str] = mapped_column(String(8), default="", index=True)
    group_key: Mapped[str] = mapped_column(String(8), default="", index=True)
    group_name: Mapped[str] = mapped_column(String(120), default="")
    reference_author: Mapped[str] = mapped_column(String(190), default="")
    generation_name: Mapped[str] = mapped_column(String(190), default="")
    traits_raw: Mapped[str] = mapped_column(Text, default="")
    reference_file: Mapped[str | None] = mapped_column(String(300), nullable=True)
    reference_kind: Mapped[str] = mapped_column(String(16), default="single")
    reference_available: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)


class HanddrawColor(Base, TimestampMixin):
    """手绘风格库的主题色（内置只读）。"""

    __tablename__ = "handdraw_colors"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    color_id: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(32), default="")
    category_zh: Mapped[str] = mapped_column(String(60), default="")
    category_en: Mapped[str] = mapped_column(String(60), default="")
    name_zh: Mapped[str] = mapped_column(String(60), default="")
    name_en: Mapped[str] = mapped_column(String(60), default="")
    quote_zh: Mapped[str] = mapped_column(String(120), default="")
    quote_en: Mapped[str] = mapped_column(String(120), default="")
    prompt_zh: Mapped[str] = mapped_column(Text, default="")
    prompt_en: Mapped[str] = mapped_column(Text, default="")
    swatch_file: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class HanddrawLibraryMeta(Base, TimestampMixin):
    """内置手绘库的导入元信息：资源哈希、数量、缺失参考图。"""

    __tablename__ = "handdraw_library_meta"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    library_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    version: Mapped[str] = mapped_column(String(32), default="")
    source_hash: Mapped[str] = mapped_column(String(64), default="")
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    style_count: Mapped[int] = mapped_column(Integer, default=0)
    color_count: Mapped[int] = mapped_column(Integer, default=0)
    reference_count: Mapped[int] = mapped_column(Integer, default=0)
    missing_references: Mapped[list] = mapped_column(JSON, default=list)


class AppSetting(Base, TimestampMixin):
    """部署级键值配置（如公众号凭据），值存 JSON；敏感字段须先加密再写入。"""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------- 热点选题 ----------------
#
# 三个表的关系：hot_items 是「某平台某标题」的条目，hot_clusters 是「同一件事」
# 的归并结果，hot_rank_history 是名次采样（用于算升降）。
# 热点是公开数据，不按 owner 隔离；同实例多用户看到同一份榜单。


class HotItem(Base, TimestampMixin):
    """单个平台的一条热榜条目。身份键是 platform + title_norm，不是名次。"""

    __tablename__ = "hot_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    item_uid: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    title_norm: Mapped[str] = mapped_column(String(300), index=True)
    hot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 部分热榜源（头条）的 Url 带一大段跟踪参数，列宽放宽成 TEXT；
    # 采集侧仍会去参/截断到 URL_MAX_LENGTH，这里只是入库兜底不再炸。
    url: Mapped[str] = mapped_column(Text, default="")
    first_seen_at: Mapped[str] = mapped_column(String(16), default="")
    last_seen_at: Mapped[str] = mapped_column(String(16), default="")
    first_seen_day: Mapped[str] = mapped_column(String(10), default="")
    days_json: Mapped[list] = mapped_column(JSON, default=list)
    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    prev_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    best_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cluster_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


class HotRankHistory(Base):
    """名次采样。同一 (item, captured_at) 只留一条，重跑同一轮不会重复插。"""

    __tablename__ = "hot_rank_history"
    __table_args__ = (
        UniqueConstraint("item_id", "captured_at", name="uq_hot_rank_sample"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(String(32), index=True)
    captured_at: Mapped[str] = mapped_column(String(16), index=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hot: Mapped[int | None] = mapped_column(Integer, nullable=True)


class HotCluster(Base, TimestampMixin):
    """同一件事的归并结果 + 脚本算好的 100 分制评分。"""

    __tablename__ = "hot_clusters"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # 稳定标识：一旦写入不再改，否则历史稿件的来源关联会漂移
    fingerprint: Mapped[str] = mapped_column(String(120), index=True)
    title_norm: Mapped[str] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(String(300))
    track: Mapped[str] = mapped_column(String(32), default="", index=True)
    first_seen_day: Mapped[str] = mapped_column(String(10), default="")
    last_seen_day: Mapped[str] = mapped_column(String(10), default="", index=True)
    days_seen: Mapped[int] = mapped_column(Integer, default=1)
    best_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    platform_count: Mapped[int] = mapped_column(Integer, default=0)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    platforms: Mapped[list] = mapped_column(JSON, default=list)
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    grade: Mapped[str] = mapped_column(String(16), default="")
    score_detail: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_flags: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)
    captured_at: Mapped[str] = mapped_column(String(16), default="")
    # 从热点直接成稿后回填，用于「已出稿」判断与跳转
    content_item_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


# ---------------- 贴图创作 ----------------
#
# 贴图集是一等公民：图序、配文、逐张状态、单张重试都要行级记录。
# hot_cluster_id 是弱关联（无外键）——热点簇会被重算合并，集合不应被它锁死。


class StickerSet(Base, TimestampMixin):
    """一组风格统一的贴图：主题 + 风格 + 叙事线 + 生命周期状态。"""

    __tablename__ = "sticker_sets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    theme: Mapped[str] = mapped_column(Text, default="")
    style_id: Mapped[str] = mapped_column(String(40), default="")
    track: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 弱关联热点簇：仅用于预填与统计，不建外键约束
    hot_cluster_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # scene|knowledge|parallel|contrast|suspense
    narrative: Mapped[str] = mapped_column(String(24), default="parallel")
    # draft|outlined|generating|review|done|deleted
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 内部元数据 JSON（如锚点反推描述缓存），不下发给前端
    meta_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class StickerImage(Base, TimestampMixin):
    """贴图集里的一张图：画面描述 + 配文 + 最近一次生成任务的状态。"""

    __tablename__ = "sticker_images"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    set_id: Mapped[str] = mapped_column(ForeignKey("sticker_sets.id"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    caption: Mapped[str] = mapped_column(Text, default="")
    frame_prompt: Mapped[str] = mapped_column(Text, default="")
    # info|pace（信息位/节奏位标注，见配图路线）
    slot_type: Mapped[str] = mapped_column(String(8), default="info")
    asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # pending|queued|generating|succeeded|failed
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # 最近一次入队时的画面描述。与 frame_prompt 不一致 = 用户改了描述但还没重画，
    # 此时不能直接 retry（会把旧文案再跑一遍），必须走「保存并重画这张」。
    generated_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------- 角色库（用户级，跨分镜项目复用） ----------------


class Character(Base, TimestampMixin):
    """视觉角色卡：名字/别名、结构化外貌、多张带角度标签的参考图。

    分镜项目只存 `role:` 前缀的引用，不复制角色内容；改名或换图后，
    下一次生成会用到新数据，历史生成结果由 job.payload 的快照负责追溯。
    """

    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    appearance: Mapped[dict] = mapped_column(JSON, default=dict)
    images: Mapped[list] = mapped_column(JSON, default=list)
    # AI 生成的候选参考图（未采用）：{assetId, angle, jobId, status, prompt, degraded}
    # 采用才进 images；同角度重新生成时旧候选的 asset 一并删除。
    pending_images: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


# ---------------- 漫画分镜 ----------------


class StoryboardProject(Base, TimestampMixin):
    __tablename__ = "storyboard_projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="未命名分镜")
    summary: Mapped[str] = mapped_column(Text, default="")
    assets: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    # 组级统一出图风格：{style_number, generation_name, group_key}，指向手绘风格库。
    style_config: Mapped[dict] = mapped_column(JSON, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class StoryboardScene(Base, TimestampMixin):
    __tablename__ = "storyboard_scenes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("storyboard_projects.id"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(200), default="新场景")
    summary: Mapped[str] = mapped_column(Text, default="")
    chapter: Mapped[str] = mapped_column(String(200), default="第一章")
    # 场级风格覆盖：空 = 跟随项目级 style_config；非空时覆盖项目级风格。
    style_config: Mapped[dict] = mapped_column(JSON, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class StoryboardShot(Base, TimestampMixin):
    __tablename__ = "storyboard_shots"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    scene_id: Mapped[str] = mapped_column(ForeignKey("storyboard_scenes.id"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    frame_description: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(Text, default="")
    dialogue: Mapped[str] = mapped_column(Text, default="")
    shot_size: Mapped[str] = mapped_column(String(24), default="medium")
    view_angle: Mapped[str] = mapped_column(String(24), default="eye_level")
    camera_motion: Mapped[str] = mapped_column(String(24), default="static")
    duration_seconds: Mapped[int] = mapped_column(Integer, default=3)
    prompt: Mapped[str] = mapped_column(Text, default="")
    asset_refs: Mapped[list] = mapped_column(JSON, default=list)
    sketch_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sketch_job_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    sketch_status: Mapped[str] = mapped_column(String(16), default="pending")
    sketch_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    final_job_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    final_status: Mapped[str] = mapped_column(String(16), default="pending")
    final_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_version: Mapped[int] = mapped_column(Integer, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


def _empty_facts() -> dict:
    return {"characterChanges": [], "itemHoldings": [], "events": [], "openHooks": []}


# ---------------- 小说 ----------------


class NovelProject(Base, TimestampMixin):
    __tablename__ = "novel_projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="未命名小说")
    source_text: Mapped[str] = mapped_column(Text, default="")
    rights_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rules: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)


class NovelChapter(Base, TimestampMixin):
    __tablename__ = "novel_chapters"
    __table_args__ = (UniqueConstraint("project_id", "sort_order", name="uq_novel_chapter_order"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("novel_projects.id"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="rewrite")
    title: Mapped[str] = mapped_column(String(200), default="")
    source_text: Mapped[str] = mapped_column(Text, default="")
    rewrite_text: Mapped[str] = mapped_column(Text, default="")
    draft_text: Mapped[str] = mapped_column(Text, default="")
    accepted_text: Mapped[str] = mapped_column(Text, default="")
    draft_version: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    facts: Mapped[dict] = mapped_column(JSON, default=_empty_facts)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    impact: Mapped[dict] = mapped_column(JSON, default=dict)
    pending_candidate: Mapped[str | None] = mapped_column(Text, nullable=True)


class NovelCharacter(Base, TimestampMixin):
    __tablename__ = "novel_characters"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("novel_projects.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(120))
    new_name: Mapped[str] = mapped_column(String(120), default="")
    relations: Mapped[str] = mapped_column(Text, default="")
    speech_style: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(24), default="present")


class NovelAlias(Base, TimestampMixin):
    __tablename__ = "novel_aliases"
    __table_args__ = (
        UniqueConstraint("project_id", "kind", "original_name", name="uq_novel_alias"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("novel_projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    original_name: Mapped[str] = mapped_column(String(120))
    new_name: Mapped[str] = mapped_column(String(120), default="")


class ScheduledTask(Base, TimestampMixin):
    """定时任务定义（docs/scheduled-tasks.md §4.1）。

    这张表是唯一真相：APScheduler 的内存 jobstore 只是它的运行期投影。
    `next_run_at` 同时干两件事——界面展示「下次触发」、回调里做多副本 CAS 令牌；
    真正的时间计算只发生在 APScheduler 的 trigger 里，业务代码不另算一份。
    """

    __tablename__ = "scheduled_tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # system：全局数据类任务（拉热点），全库同类只应有一条；user：按 owner 隔离
    scope: Mapped[str] = mapped_column(String(16), default="user", index=True)
    # system 任务也必须有真实 owner：background_jobs.owner_user_id 是必填外键
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    task_type: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(120))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    # cron | interval
    schedule_kind: Mapped[str] = mapped_column(String(16), default="cron")
    cron: Mapped[str] = mapped_column(String(120), default="")
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    misfire_grace_seconds: Mapped[int] = mapped_column(Integer, default=300)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(24), default="")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    # 自动暂停原因；手动停用时为空（两者在界面上要能区分开）
    paused_reason: Mapped[str] = mapped_column(String(300), default="")
    # 原子占用槽位：同一任务的手动与定时入口不得并发创建执行。
    active_job_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


class ScheduledTaskRun(Base):
    """定时任务的触发账本（docs/scheduled-tasks.md §4.2）。

    `status` 的终态以 background_jobs 为准，由 job_runner 写终态时同一处回写；
    启动时再对账一次修正漏写。跳过的触发（上次未落定、错过宽限）也记在这里，
    否则「为什么这次没跑」在界面上无从解释。
    """

    __tablename__ = "scheduled_task_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("scheduled_tasks.id"), index=True)
    # 跳过的 run 没有 job；终态回写靠它定位
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # schedule | manual
    source: Mapped[str] = mapped_column(String(16), default="schedule")
    # queued / running / succeeded / failed / skipped / cancelled / missed
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    skip_reason: Mapped[str] = mapped_column(String(300), default="")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScheduledScript(Base, TimestampMixin):
    """UI 可维护的受信任定时脚本：草稿与发布代码分离，执行只用发布快照。"""

    __tablename__ = "scheduled_scripts"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id", "name", name="uq_scheduled_script_owner_name"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500), default="", server_default="")
    draft_code: Mapped[str] = mapped_column(Text, default="", server_default="")
    published_code: Mapped[str] = mapped_column(Text, default="", server_default="")
    published_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_hash: Mapped[str] = mapped_column(String(64), default="", server_default="")
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60, server_default="60")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    versions: Mapped[list["ScheduledScriptVersion"]] = relationship(
        back_populates="script",
        cascade="all, delete-orphan",
        order_by="ScheduledScriptVersion.version.desc()",
    )


class ScheduledScriptVersion(Base):
    """发布历史快照：同一次发布的代码必须可追溯。"""

    __tablename__ = "scheduled_script_versions"
    __table_args__ = (
        UniqueConstraint("script_id", "version", name="uq_scheduled_script_version"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    script_id: Mapped[str] = mapped_column(
        ForeignKey("scheduled_scripts.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    script: Mapped[ScheduledScript] = relationship(back_populates="versions")


# ---------------- RBAC：角色 / 权限 / 菜单 ----------------
#
# 关系：用户 —(user_roles, 多对多)— 角色 —(role_permissions, 多对多)— 权限
#       角色 —(role_menus, 多对多)— 菜单
#
# 两条不可动摇的约定：
#   1. **菜单是权限的投影，不是权限本身。** 隐藏菜单只影响可见性，接口一律按
#      require_permission 校验；绕过前端直接调接口照样 403。
#   2. **超管靠 is_superadmin 表达，不靠塞一条 `*` 权限。** 通配符进表会让
#      「这个角色到底有哪些权限」变得无法用 SQL 回答，审计时说不清楚。
#
# 菜单树用 parent_id + 冗余 path（写入时维护）表达：path 可索引、可排序、
# 可直接做面包屑，避免每次渲染都跑递归 CTE。菜单总量是几十行量级，一次全查
# 再在内存里组树，比递归查询更快也更好读。


class Role(Base, TimestampMixin):
    """角色。`is_system` 的内置角色不可删除（可改权限），避免把超管角色误删后锁死系统。"""

    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint("sort >= 0", name="ck_roles_sort"),
        Index("ix_roles_sort", "sort"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # 稳定标识（代码里引用它，不用 id）：superadmin / operator / editor / member
    role_key: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(48))
    description: Mapped[str] = mapped_column(String(200), default="")
    # 超管：拥有全部权限，且不受「权限点被删除」影响
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    sort: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Permission(Base):
    """权限点目录。code 是稳定契约（前端 meta.permission 与后端 require_permission 共用）。

    由启动播种维护：新增代码里的权限点会插入，删除的**不会**自动清理（可能有角色绑定），
    只在管理页标记为「已废弃」由人工处理。
    """

    __tablename__ = "permissions"
    __table_args__ = (Index("ix_permissions_module_sort", "module", "sort"),)

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    # 分组：创作 / 运营 / 系统 / 权限
    module: Mapped[str] = mapped_column(String(32), default="general")
    sort: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("permissions.code", ondelete="CASCADE"), primary_key=True
    )


class Menu(Base, TimestampMixin):
    """菜单项。`menu_key` 对 page 类型必须等于前端路由 name，对 group 类型是分组标识。

    icon 只存**字典键**（如 `studio`），不存 SVG：图标是前端资产，存进数据库会让
    一次视觉调整变成数据迁移。
    """

    __tablename__ = "menus"
    __table_args__ = (
        CheckConstraint("kind IN ('group', 'page', 'link')", name="ck_menus_kind"),
        CheckConstraint("sort >= 0", name="ck_menus_sort"),
        Index("ix_menus_parent_sort", "parent_id", "sort"),
        Index("ix_menus_path", "path"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # 自引用用 RESTRICT：有子菜单时不允许删父级，必须先把子级移走
    parent_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("menus.id", ondelete="RESTRICT"), nullable=True
    )
    menu_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(48))
    short_label: Mapped[str] = mapped_column(String(24), default="")
    icon: Mapped[str] = mapped_column(String(32), default="")
    # 物化路径，如 /create/overview；写入时由服务端维护，不接受前端传入
    path: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str] = mapped_column(String(16), default="page", server_default="page")
    # 该菜单要求的权限点；为空表示登录即可见（如「设置」）
    permission_code: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("permissions.code", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    sort: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    visible: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class RoleMenu(Base):
    __tablename__ = "role_menus"

    role_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    menu_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("menus.id", ondelete="CASCADE"), primary_key=True
    )


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )


class OriginalityAnalysis(Base, TimestampMixin):
    """文章原创度检测结果历史。
    
    存储检测分析结果，支持历史查询与对比。
    """

    __tablename__ = "originality_analyses"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    content_version_id: Mapped[str] = mapped_column(
        ForeignKey("content_versions.id"), index=True
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # 五个维度评分（0-100）
    similarity_score: Mapped[int] = mapped_column(Integer, default=0)
    originality_score: Mapped[int] = mapped_column(Integer, default=0)
    structure_score: Mapped[int] = mapped_column(Integer, default=0)
    data_accuracy_score: Mapped[int] = mapped_column(Integer, default=0)
    style_uniqueness_score: Mapped[int] = mapped_column(Integer, default=0)

    # 综合评分与等级
    total_score: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[str] = mapped_column(String(20), default="fair")  # excellent/good/fair/poor

    # 详情存储（JSONB）
    similar_articles: Mapped[dict] = mapped_column(JSONBDoc, default=dict)
    key_points: Mapped[dict] = mapped_column(JSONBDoc, default=dict)
    risk_flags: Mapped[dict] = mapped_column(JSONBDoc, default=dict)
    recommendations: Mapped[str] = mapped_column(Text, default="")

    # 检测参数
    compare_scope: Mapped[str] = mapped_column(String(20), default="all")  # all/own/public
    model_version: Mapped[str] = mapped_column(String(50), default="v1.0-tfidf")

    # ── 口径与缓存（v3.0）─────────────────────────────────────────
    # 正文指纹：同一版本正文没改时直接复用上次结果，避免每次全量重算
    body_hash: Mapped[str] = mapped_column(String(64), default="", server_default="")
    # 本次参与比对的其它稿件数量；0 表示「无从判断相似度」，不是「完全原创」
    corpus_size: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # 同一篇稿件的历史版本数（不计入比对，只在结论里提示）
    self_version_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # 一句话中文结论，界面和报告首屏用它
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")


class RbacAuditLog(Base):
    """权限变更审计。改角色、改菜单、给别人授权，都必须留一条。

    没有这张表，"谁把我的后台权限关了" 这种问题是查不出来的。
    """

    __tablename__ = "rbac_audit_logs"
    __table_args__ = (Index("ix_rbac_audit_target", "target_type", "target_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # 操作人；账号被删时置空，但审计记录本身保留
    actor_user_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(48), index=True)
    target_type: Mapped[str] = mapped_column(String(24), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[dict] = mapped_column(JSONBDoc, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


# ─────────────────────────────────────────────────────────────────
# Phase 2：高级分析引擎（NLP + 结构对比 + 缓存）
# ─────────────────────────────────────────────────────────────────


class SimilarityCache(Base):
    """相似度检测缓存表 — 加速后续查询"""

    __tablename__ = "similarity_cache"
    __table_args__ = (Index("ix_similarity_cache_pair", "source_version_id", "target_version_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # 源稿件版本 ID
    source_version_id: Mapped[str] = mapped_column(String(32), index=True)
    # 目标稿件版本 ID
    target_version_id: Mapped[str] = mapped_column(String(32), index=True)
    # 相似度分数（0-1）
    similarity_score: Mapped[float] = mapped_column(Float)
    # 匹配的文本片段（JSON 数组）
    matching_segments: Mapped[dict] = mapped_column(JSONBDoc, default=dict)
    # TF-IDF 向量（序列化存储）
    tfidf_vector: Mapped[str] = mapped_column(Text, default="")
    # 缓存过期时间
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class ContentEmbeddings(Base):
    """内容向量存储表 — 用于语义相似度计算"""

    __tablename__ = "content_embeddings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # 稿件版本 ID
    content_version_id: Mapped[str] = mapped_column(String(32), ForeignKey("content_versions.id"), index=True)
    # 向量模型版本
    model_version: Mapped[str] = mapped_column(String(32), default="sentence-bert-v1")
    # 向量数据（JSON 序列化的浮点数组）
    embedding_vector: Mapped[str] = mapped_column(Text)
    # 向量维度
    embedding_dim: Mapped[int] = mapped_column(Integer, default=384)
    # 是否已过时（模型升级时置 true）
    deprecated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AnalysisKeywords(Base):
    """检测结果关键词提取表 — 用于观点独特性分析"""

    __tablename__ = "analysis_keywords"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # 检测 ID
    analysis_id: Mapped[str] = mapped_column(String(32), ForeignKey("originality_analyses.id"), index=True)
    # 关键词
    keyword: Mapped[str] = mapped_column(String(100))
    # 词频
    frequency: Mapped[int] = mapped_column(Integer, default=1)
    # 相对重要性（0-1）
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    # 来源：source（原文） | reference（参考文献）
    source: Mapped[str] = mapped_column(String(16), default="source")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ─────────────────────────────────────────────────────────────────
# Phase 3：向量模型与报告系统
# ─────────────────────────────────────────────────────────────────


class OriginalityReport(Base):
    """检测报告表 — 支持 PDF 与 Excel 导出"""

    __tablename__ = "originality_reports"
    __table_args__ = (Index("ix_originality_report_version", "content_version_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # 稿件版本 ID
    content_version_id: Mapped[str] = mapped_column(String(32), ForeignKey("content_versions.id"), index=True)
    # 关联的检测结果 ID
    analysis_id: Mapped[str] = mapped_column(String(32), ForeignKey("originality_analyses.id"))
    # 报告标题
    title: Mapped[str] = mapped_column(String(200))
    # 报告摘要
    summary: Mapped[str] = mapped_column(Text, default="")
    # 导出格式：pdf | excel | json
    export_formats: Mapped[dict] = mapped_column(JSONBDoc, default=dict)  # {"pdf": "url", "excel": "url"}
    # 报告存储位置（文件 URL）
    pdf_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    excel_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 报告生成的详细数据（JSON）
    report_data: Mapped[dict] = mapped_column(JSONBDoc, default=dict)
    # 报告状态：generating | ready | failed
    status: Mapped[str] = mapped_column(String(16), default="generating")
    # 错误信息（失败时）
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AnalysisHistory(Base):
    """检测历史与对比表 — 追踪版本变化"""

    __tablename__ = "analysis_history"
    __table_args__ = (Index("ix_analysis_history_content", "content_version_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # 稿件版本 ID
    content_version_id: Mapped[str] = mapped_column(String(32), ForeignKey("content_versions.id"), index=True)
    # 检测 ID
    analysis_id: Mapped[str] = mapped_column(String(32), ForeignKey("originality_analyses.id"))
    # 版本序号（第几次检测）
    revision: Mapped[int] = mapped_column(Integer, default=1)
    # 本次检测的总分
    score_snapshot: Mapped[float] = mapped_column(Float)
    # 与前一次检测的分数变化
    score_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 主要改进点（JSON）
    improvements: Mapped[dict] = mapped_column(JSONBDoc, default=dict)
    # 新增相似内容
    new_similarities: Mapped[dict] = mapped_column(JSONBDoc, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisTask(Base):
    """后台检测任务队列 — 支持批量检测"""

    __tablename__ = "analysis_tasks"
    __table_args__ = (Index("ix_analysis_task_status", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # 工作空间 ID
    workspace_id: Mapped[str] = mapped_column(String(32), ForeignKey("workspaces.id"), index=True)
    # 创建人 ID
    created_by_user_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # 任务类型：single | batch | compare
    task_type: Mapped[str] = mapped_column(String(16), default="single")
    # 单个检测：稿件版本 ID；批量检测：版本 ID 列表
    target_ids: Mapped[list] = mapped_column(JSONBDoc, default=list)
    # 检测参数
    analysis_params: Mapped[dict] = mapped_column(JSONBDoc, default=dict)
    # 任务状态：pending | running | completed | failed
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # 进度百分比（0-100）
    progress: Mapped[int] = mapped_column(Integer, default=0)
    # 已完成的检测 ID 列表
    completed_analysis_ids: Mapped[list] = mapped_column(JSONBDoc, default=list)
    # 错误消息（失败时）
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 预计完成时间
    estimated_completion_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 实际完成时间
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

