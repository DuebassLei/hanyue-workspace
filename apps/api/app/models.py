"""领域模型。字段命名与设计文档第 9 章对齐；数据库列用 snake_case，API 层转 camelCase。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .core.db import Base


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
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    auth_subject: Mapped[str] = mapped_column(String(190), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="创作者")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")


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
    # 公众号排版偏好：{"defaultThemeId", "favoritePresetIds", "hiddenPresetIds"}
    # 读时兜底，写时整包替换（JSON 列必须赋新 dict，原地改不会被 SQLAlchemy 感知）
    wechat_layout: Mapped[dict] = mapped_column(JSON, default=dict)


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
    # 公众号排版：引用的主题 + 推送时的 tokens 快照（主题后续被改也能复现当时的排版）
    theme_id: Mapped[str] = mapped_column(String(32), default="")
    layout_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)


class WeChatTheme(Base, TimestampMixin):
    """公众号排版主题。owner_user_id 为空表示系统预设（只读，不可删改）。"""

    __tablename__ = "wechat_themes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(60), default="")
    tokens: Mapped[dict] = mapped_column(JSON, default=dict)
    is_preset: Mapped[bool] = mapped_column(Boolean, default=False)


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
    # 新建库由该约束兜底；已有库由 core/db.py 的表达式唯一索引兜住内置行（owner 为 NULL）
    __table_args__ = (
        UniqueConstraint("skill_key", "owner_user_id", name="uq_skill_owner_key"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    source: Mapped[str] = mapped_column(String(120), default="builtin")
    # 自定义 Skill 归属用户；内置 Skill 为 NULL，全员可见
    owner_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    skill_key: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    display_name: Mapped[str] = mapped_column(String(160), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    checksum: Mapped[str] = mapped_column(String(80), default="")
    trust_status: Mapped[str] = mapped_column(String(24), default="trusted")
    allowed_tool_ids: Mapped[list] = mapped_column(JSON, default=list)
    risk_level: Mapped[str] = mapped_column(String(24), default="read_only")


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
    """提示词库：用户维护的生图提示词模板，按 owner 隔离。"""

    __tablename__ = "prompt_templates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(32), default="通用")
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)


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
    url: Mapped[str] = mapped_column(String(500), default="")
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

