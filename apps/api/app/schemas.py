from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


class CamelModel(BaseModel):
    model_config = {"populate_by_name": True, "alias_generator": camel}

    def dump(self) -> dict:
        return self.model_dump(by_alias=True)


# ---------- auth ----------
class LoginRequest(CamelModel):
    subject: str = Field(default="local-creator", min_length=1, max_length=190)
    display_name: str = Field(default="创作者", max_length=120)


class UserOut(CamelModel):
    id: str
    display_name: str
    timezone: str


class LoginResponse(CamelModel):
    token: str
    user: UserOut
    workspace_id: str


# ---------- preferences ----------
class PreferenceOut(CamelModel):
    theme: str
    # default = 服务端尚无记录；server = 已从服务端读取
    source: str


class PreferencePatch(CamelModel):
    theme: Literal["system", "light", "dark"]


# ---------- workspaces ----------
class WorkspaceIn(CamelModel):
    name: str = Field(min_length=1, max_length=120)


class WorkspacePatch(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)


class WorkspaceOut(CamelModel):
    id: str
    name: str
    created_at: datetime


# ---------- content ----------
class ContentIn(CamelModel):
    title: str = Field(default="未命名稿件", max_length=200)
    type: str = "article"


class VersionIn(CamelModel):
    title: str = ""
    body: str = ""
    summary: str = ""
    source_type: str = "manual"
    model_id: str | None = None


class VersionOut(CamelModel):
    id: str
    version_no: int
    title: str
    body: str
    summary: str
    source_type: str
    created_at: datetime


class ContentOut(CamelModel):
    id: str
    title: str
    status: str
    type: str
    current_version_id: str | None
    updated_at: datetime
    latest_version: VersionOut | None = None
    # 自动草稿字段：未 None 时说明有未提交为版本的自动保存内容
    draft_title: str | None = None
    draft_body: str | None = None


class DraftPatch(CamelModel):
    """自动保存草稿，不创建版本记录。"""
    title: str | None = Field(default=None, max_length=200)
    body: str | None = None


class ContentDetail(CamelModel):
    item: ContentOut
    versions: list[VersionOut]
    variants: list["VariantOut"]


class VariantIn(CamelModel):
    platform: str
    title: str = ""
    body: str = ""
    tags: list[str] = Field(default_factory=list)
    base_version_id: str | None = None


class VariantOut(CamelModel):
    id: str
    platform: str
    title: str
    body: str
    tags: list[str]
    status: str
    revision_no: int


# ---------- calendar ----------
class CalendarIn(CamelModel):
    title: str = ""
    planned_at: datetime
    platform: str | None = None
    content_item_id: str | None = None


class CalendarOut(CamelModel):
    id: str
    title: str
    planned_at: datetime
    platform: str | None
    status: str
    content_item_id: str | None


# ---------- review ----------
class WeekReviewIn(CamelModel):
    week_start: str
    planned_count: int = 0
    published_count: int = 0
    learnings: str = ""
    blockers: str = ""
    next_week_focus: str = ""


class WeekReviewOut(CamelModel):
    id: str
    week_start: str
    planned_count: int
    published_count: int
    learnings: str
    blockers: str
    next_week_focus: str


# ---------- models / ai ----------
class ConnectionIn(CamelModel):
    display_name: str = Field(min_length=1, max_length=120)
    provider_type: str = "openai_compatible"
    base_url: str = ""
    api_key: str = ""


class ConnectionPatch(CamelModel):
    display_name: str | None = None
    provider_type: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    status: str | None = None


class CatalogIn(CamelModel):
    connection_id: str
    model_code: str
    display_name: str = ""
    capabilities: list[str] = Field(default_factory=lambda: ["text.generate"])
    supports_streaming: bool = True


class CatalogPatch(CamelModel):
    model_code: str | None = None
    display_name: str | None = None
    capabilities: list[str] | None = None
    supports_streaming: bool | None = None
    enabled: bool | None = None


class ConnectionOut(CamelModel):
    id: str
    display_name: str
    provider_type: str
    base_url: str
    key_masked: str
    status: str
    models: list["CatalogOut"] = Field(default_factory=list)


class CatalogOut(CamelModel):
    id: str
    model_code: str
    display_name: str
    capabilities: list[str]
    enabled: bool
    supports_streaming: bool


class TextGenerationIn(CamelModel):
    task_type: str = "article"
    model_id: str | None = None
    prompt: str = Field(min_length=1)
    constraints: dict[str, Any] = Field(default_factory=dict)
    content_item_id: str | None = None
    skill_key: str | None = None


class ImageGenerationIn(CamelModel):
    prompt: str = Field(min_length=1)
    purpose: str = "cover"
    aspect_ratio: str = "3:4"
    count: int = 1
    model_id: str | None = None
    # 参考图（图生图）：最多 4 张已上传素材，走供应商 /images/edits
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=4)
    # 内置手绘风格编号（FA-001 或旧三位 001）：需要参考图的风格由任务执行器自动附加
    builtin_reference_style_number: str | None = None


class GenerationOut(CamelModel):
    id: str
    task_type: str
    status: str
    result_text: str
    error_code: str | None
    duration_ms: int | None


# ---------- assets / image jobs ----------
class AssetOut(CamelModel):
    id: str
    original_name: str
    mime_type: str
    size_bytes: int
    width: int | None
    height: int | None
    source_type: str
    prompt: str | None = None
    created_at: datetime
    url: str


class ImageJobIn(CamelModel):
    asset_id: str
    operation: str
    options: dict[str, Any] = Field(default_factory=dict)


class ImageJobOut(CamelModel):
    id: str
    asset_id: str
    operation: str
    status: str
    progress: int
    output_asset_ids: list[str]
    error_code: str | None


# ---------- platform / publishing ----------
class AccountConnectIn(CamelModel):
    platform: str
    display_name: str = ""


class AccountOut(CamelModel):
    id: str
    platform: str
    platform_label: str
    display_name: str
    status: str
    scopes: list[str]
    auto_publish_enabled: bool
    last_synced_at: datetime | None
    expires_at: datetime | None
    # none | demo（本地演示） | oauth（平台官方授权） | app_secret（凭据验证）
    auth_source: str = "none"
    # 账号级凭据：True 表示该账号持有自己的 AppID/AppSecret；False 时使用部署级默认凭据
    has_own_credential: bool = False
    app_id_masked: str = ""


class PublishPreviewIn(CamelModel):
    account_id: str
    title: str = ""
    body: str = ""
    asset_ids: list[str] = Field(default_factory=list)
    scheduled_at: datetime | None = None
    variant_id: str | None = None


class PreviewOut(CamelModel):
    platform: str
    platform_label: str
    can_auto_publish: bool
    block_reason: str
    issues: list[dict]
    requires_confirmation: bool
    scheduled_at: datetime | None
    asset_count: int


class PublishJobIn(CamelModel):
    account_id: str
    idempotency_key: str = Field(min_length=6, max_length=120)
    # 关联创作空间稿件；传入后未显式给标题/正文时按稿件最新内容自动填充
    content_item_id: str | None = None
    title: str = ""
    body: str = ""
    asset_ids: list[str] = Field(default_factory=list)
    scheduled_at: datetime | None = None
    variant_id: str | None = None
    reviewed: bool = False
    # auto：仅在平台能力核验通过后走自动发布；manual：只排期与记录，绝不调用平台接口
    mode: str = Field(default="auto", pattern="^(auto|manual)$")


class ManualResultIn(CamelModel):
    remote_content_id: str | None = None
    permalink: str | None = None
    published_at: datetime | None = None


class PublishJobOut(CamelModel):
    id: str
    platform: str
    platform_label: str
    account_id: str = ""
    account_name: str = ""
    # 关联的创作空间稿件，便于发布队列追溯来源
    content_item_id: str = ""
    content_title: str = ""
    status: str
    scheduled_at: datetime | None
    attempt_count: int
    remote_content_id: str | None
    error_code: str | None
    error_message: str | None
    request_id: str
    created_at: datetime
    # publish：对外发布；draft：推送到平台草稿箱（公众号）
    target: str = "publish"
    theme_id: str = ""


# ---------- wechat official account（草稿箱）----------
class WeChatCredentialsIn(CamelModel):
    app_id: str
    app_secret: str


class WeChatBindIn(CamelModel):
    display_name: str = ""
    # 可选：绑定即为该账号录入自己的 AppID/AppSecret（多账号各持凭据）；
    # 留空则使用部署级默认凭据（界面保存的或环境变量）
    credentials: WeChatCredentialsIn | None = None


class WeChatDraftIn(CamelModel):
    account_id: str
    idempotency_key: str = Field(min_length=6, max_length=120)
    # 关联创作空间稿件，便于发布队列追溯来源
    content_item_id: str | None = None
    title: str = ""
    body: str = ""
    digest: str = ""
    author: str = ""
    content_source_url: str = ""
    asset_ids: list[str] = Field(default_factory=list)
    need_open_comment: bool = False
    only_fans_can_comment: bool = False
    # 排版主题；留空使用默认预设
    theme_id: str = ""


class WeChatDraftPreviewIn(CamelModel):
    account_id: str = ""
    title: str = ""
    body: str = ""
    digest: str = ""
    author: str = ""
    content_source_url: str = ""
    asset_ids: list[str] = Field(default_factory=list)
    theme_id: str = ""


class WeChatRenderIn(CamelModel):
    """排版预览：不落库、不调微信，纯渲染。"""

    title: str = ""
    body: str = ""
    theme_id: str = ""
    # 直接用已有图片 URL 预览（前端本地已有上传结果时）；为空时用 assetIds 的占位
    image_urls: list[str] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)


class WeChatThemeIn(CamelModel):
    name: str = Field(default="", max_length=60)
    tokens: dict[str, Any] = Field(default_factory=dict)
    # 基于哪个预设复制（tokens 会覆盖在预设之上）
    base_theme_id: str = ""


class WeChatThemeOut(CamelModel):
    id: str
    name: str
    is_preset: bool
    description: str = ""
    tokens: dict[str, Any]
    editable: bool = True


# ---------- agent ----------
class AgentSessionIn(CamelModel):
    title: str = "新会话"


class AgentMessageIn(CamelModel):
    goal: str = Field(min_length=1)
    skill_ids: list[str] = Field(default_factory=list)
    max_rounds: int = 8


class AgentRunOut(CamelModel):
    id: str
    session_id: str
    status: str
    goal: str
    rounds_used: int
    error_code: str | None
    created_at: datetime


class AgentEventOut(CamelModel):
    seq: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class ApprovalDecisionIn(CamelModel):
    decision: str = Field(pattern="^(approve|reject)$")


class ApprovalOut(CamelModel):
    id: str
    run_id: str
    requested_action: str
    risk_level: str
    status: str
    params: dict[str, Any]
    expires_at: datetime | None


class SkillOut(CamelModel):
    id: str
    skill_key: str
    version: str
    display_name: str
    description: str
    risk_level: str
    allowed_tool_ids: list[str]
    trust_status: str
    enabled: bool
    granted_tool_ids: list[str]
    checksum: str = ""
    manifest: dict[str, Any] = Field(default_factory=dict)
    source: str = "builtin"
    # 该自定义 Skill 是从内置风格定制而来（skill_key 与某内置 Skill 相同）
    customized: bool = False


class SkillToggleIn(CamelModel):
    enabled: bool
    granted_tool_ids: list[str] | None = None
    approval_policy: str = "risk_based"


class SkillCreateIn(CamelModel):
    skill_key: str = Field(min_length=3, max_length=120)
    display_name: str = Field(min_length=1, max_length=160)
    description: str = ""
    version: str = "1.0.0"
    allowed_tool_ids: list[str] = Field(default_factory=list)
    manifest: dict[str, Any] = Field(default_factory=dict)


class SkillUpdateIn(CamelModel):
    """更新自定义写作风格 Skill；只允许改自定义 Skill。"""
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    version: str | None = None
    manifest: dict[str, Any] | None = None


# ---------- dashboard ----------
class DashboardOut(CamelModel):
    publish_progress: dict[str, Any]
    pending_review: int
    failed_jobs: int
    unsynced_accounts: int
    platform_status: list[dict]
    recent_content: list[dict]
    ai_status: dict[str, Any]


ContentDetail.model_rebuild()
ConnectionOut.model_rebuild()


# ---------- cards ----------
class CardPaginateIn(CamelModel):
    markdown: str = Field(default="", max_length=200_000)
    preset_id: str = "xhs-3-4"
    custom_width: int | None = Field(default=None, ge=240, le=1200)
    custom_height: int | None = Field(default=None, ge=240, le=1600)
    template_id: str | None = None
    custom_template: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    pagination_mode: Literal["smart-auto", "manual-hr", "by-h2"] = "smart-auto"


class CardTemplateIn(CamelModel):
    name: str = Field(min_length=1, max_length=120)
    subtitle: str = Field(default="", max_length=200)
    config: dict[str, Any] = Field(default_factory=dict)


class CardTemplatePatch(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    subtitle: str | None = Field(default=None, max_length=200)
    config: dict[str, Any] | None = None


class CardTemplateOut(CamelModel):
    id: str
    name: str
    subtitle: str
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime


# ---------- prompts（提示词库） ----------
class PromptTemplateIn(CamelModel):
    title: str = Field(min_length=1, max_length=120)
    category: str = Field(default="通用", max_length=32)
    content: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list, max_length=12)


class PromptTemplatePatch(CamelModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, max_length=32)
    content: str | None = Field(default=None, min_length=1)
    tags: list[str] | None = Field(default=None, max_length=12)


class PromptTemplateOut(CamelModel):
    id: str
    title: str
    category: str
    content: str
    tags: list[str]
    created_at: datetime
    updated_at: datetime


# ---------- hot topics（创作空间灵感来源） ----------
class HotTopicOut(CamelModel):
    title: str
    hot: str = ""
    url: str | None = None
    source: str = ""


class HotTopicsStatusOut(CamelModel):
    configured: bool
    source: str
    note: str


