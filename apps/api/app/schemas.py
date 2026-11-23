from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, BaseModel, Field, StrictBool, StrictStr, field_validator


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


class CamelModel(BaseModel):
    model_config = {"populate_by_name": True, "alias_generator": camel}

    def dump(self) -> dict:
        return self.model_dump(by_alias=True)


# ---------- auth ----------
class LoginRequest(CamelModel):
    """两种登录方式二选一。

    - 邮箱密码：`identifier`（或 `email`）+ `password`
    - 开发态便利入口：只给 `subject`，且服务端 ALLOW_DEV_LOGIN=true

    两者都不给时按「缺少凭据」返回 400，不再默默给一个默认账号。
    """

    identifier: str | None = Field(default=None, max_length=190)
    email: str | None = Field(default=None, max_length=190)
    password: str | None = Field(default=None, max_length=200)
    subject: str | None = Field(default=None, min_length=1, max_length=190)
    display_name: str | None = Field(default=None, max_length=120)


class RegisterRequest(CamelModel):
    email: str = Field(min_length=3, max_length=190)
    password: str = Field(min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=120)


class PasswordChangeRequest(CamelModel):
    """改密。已有密码时必须提供 current_password，防止令牌被盗后直接换密码。"""

    current_password: str | None = Field(default=None, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class AuthConfigOut(CamelModel):
    """登录页渲染所需的能力开关（未登录可访问，不泄露任何账号信息）。"""

    password_login: bool = True
    allow_dev_login: bool
    allow_registration: bool
    # 库里还没有密码账号时为 true：此时允许注册首个账号
    bootstrap_available: bool
    password_min_length: int


class UserOut(CamelModel):
    id: str
    display_name: str
    timezone: str
    # 身份来源：password = 邮箱密码；local-dev = 开发态按 subject 换的本地会话；idp = 正式身份提供者
    auth_subject: str
    auth_provider: str
    # 登录邮箱。开发态/IdP 账号可能为空
    email: str | None = None
    # 是否可用密码登录（前端据此显示「修改密码」入口）
    has_password: bool = False
    created_at: datetime


class ProfilePatch(CamelModel):
    """只允许改用户自己维护的两项；身份、id、创建时间不可由请求体改写。"""

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)


class LoginResponse(CamelModel):
    token: str
    user: UserOut
    workspace_id: str


# ---------- preferences ----------
_IMAGE_RATIOS = Literal["3:4", "1:1", "4:3", "16:9"]


class StudioImagePreferences(CamelModel):
    """创作空间自动配图设置；未知字段由 Pydantic 默认忽略。"""

    auto_images: StrictBool = False
    generate_cover: StrictBool = True
    cover_aspect_ratio: _IMAGE_RATIOS = "3:4"
    inline_aspect_ratio: _IMAGE_RATIOS = "1:1"
    prompt_suffix: StrictStr = Field(default="", max_length=200)


class PreferenceOut(CamelModel):
    theme: str
    # default = 服务端尚无记录；server = 已从服务端读取
    source: str
    # 全局默认文本模型（model_catalog_entries.id）；未设置时为 None
    default_text_model_id: str | None = None
    # 「连接名 · modelCode」，供下拉的「自动（默认：xxx）」直接显示
    default_text_model_label: str | None = None
    # 默认模型配置失效时给出原因，但不清除偏好（恢复后自动生效）
    default_text_model_ignored: dict | None = None
    # 全局默认生图模型（model_catalog_entries.id）；未设置时为 None
    default_image_model_id: str | None = None
    # 「连接名 · modelCode」，供模型管理页直接显示当前默认
    default_image_model_label: str | None = None
    # 默认生图模型失效原因：解释为什么实际出图用的是别的供应商
    default_image_model_ignored: dict | None = None
    studio_image_preferences: StudioImagePreferences = Field(default_factory=StudioImagePreferences)


class PreferencePatch(CamelModel):
    """部分更新：只改传了的字段，避免设默认模型时把主题冲掉。"""

    theme: Literal["system", "light", "dark"] | None = None
    # 传空字符串 / null 表示清除默认；不传该字段表示保持不变
    default_text_model_id: str | None = None
    # 同上，作用于生图链路
    default_image_model_id: str | None = None
    studio_image_preferences: StudioImagePreferences | None = None


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
    created_at: datetime | None = None
    archived: bool = False
    archived_at: datetime | None = None
    latest_version: VersionOut | None = None
    # 自动草稿字段：未 None 时说明有未提交为版本的自动保存内容
    draft_title: str | None = None
    draft_body: str | None = None
    # 基础风格快照。风格库改名/改词不回写；删除后只把 deleted 打成 true
    base_style_id: str | None = None
    base_style_name: str = ""
    base_style_prompt: str = ""
    base_style_deleted: bool = False
    # fields=list 瘦身模式附带：最新版本号与平台版本数（列表页展示用）
    version_no: int | None = None
    variant_count: int | None = None


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
    # 成稿台排期同步过来的条目带这个 id：前端据此显示来源并跳回成稿台
    studio_article_id: str | None = None


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


class ModelProbeIn(CamelModel):
    """配置弹窗里的「拉取模型列表 / 测试模型」入参。

    允许直接用还没保存的地址与密钥探测；带了 connectionId 时，空的 apiKey 与
    baseUrl 回落到该连接已存的凭据与地址（编辑时不必重新输入密钥）。
    """

    base_url: str = ""
    api_key: str = ""
    provider_type: str = "openai_compatible"
    connection_id: str | None = None
    model_code: str = ""
    # text / image：决定模型级测试是真发一次 1 token 对话，还是只核对模型列表
    kind: str = "text"


class CatalogIn(CamelModel):
    supported_image_sizes: dict[str, str] = Field(default_factory=dict)
    connection_id: str
    model_code: str
    display_name: str = ""
    capabilities: list[str] = Field(default_factory=lambda: ["text.generate"])
    supports_streaming: bool = True


class CatalogPatch(CamelModel):
    supported_image_sizes: dict[str, str] | None = None
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
    supported_image_sizes: dict[str, str] = Field(default_factory=dict)
    id: str
    model_code: str
    display_name: str
    capabilities: list[str]
    enabled: bool
    supports_streaming: bool
    # 模型选择器需要连接名、状态，以及「是否为当前默认」
    connection_id: str | None = None
    connection_name: str | None = None
    connection_status: str | None = None
    is_default: bool = False
    # 空能力的历史模型按文本模型处理，与网关 _is_text_model 保持一致
    is_text_model: bool = False


class ModelStatusOut(CamelModel):
    """顶栏「当前文本 / 生图模型」状态条。

    只看配置链路是否完整（模型启用、连接存在、凭据已登记），不发真实请求：
    顶栏每次进站都要读，不能因为探测供应商把启动拖慢，也不该吞掉供应商额度。
    """

    kind: str  # text | image
    # ok 可用 / blocked 链路断（红色）/ unchecked 没测过或没设默认（灰色）/ none 没配（灰色）
    state: str
    model_code: str | None = None
    display_name: str | None = None
    connection_name: str | None = None
    connection_status: str | None = None
    message: str


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
    # 负向提示词：供应商支持时独立字段透传，否则由服务端拼进 prompt 并在结果标注
    negative_prompt: str | None = None
    # 固定随机种子：仅供应商明确支持时透传，否则不生效（结果里标注）
    seed: int | None = None


class ImageInterrogateIn(CamelModel):
    """反推提示词：给定素材图片，用视觉语言模型生成描述该图的提示词。"""
    asset_id: str
    # 可选：指定文本/视觉模型 catalog id；留空时走 resolve_connection 取第一个可用模型
    model_id: str | None = None
    # 是否将生成的提示词回写到素材的 prompt 字段
    save_to_asset: bool = False


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
    # manual | auto | draft（草稿箱推送），来自 payload_snapshot.mode
    mode: str = ""
    # 手动回填/草稿推送时记录的落地页链接（如有）
    permalink: str = ""
    updated_at: datetime | None = None


class PublishRecordOut(CamelModel):
    id: str
    platform: str
    remote_content_id: str | None
    permalink: str | None
    published_at: datetime | None
    result_status: str
    created_at: datetime | None = None


class PublishJobDetailOut(PublishJobOut):
    """任务详情：完整状态流转历史 + 每次尝试的结果记录，供详情抽屉时间线展示。"""

    status_history: list[dict] = Field(default_factory=list)
    records: list[PublishRecordOut] = Field(default_factory=list)


class PublishJobSummaryOut(CamelModel):
    """按状态聚合的任务计数，供队列页筛选 chips 显示。"""

    total: int
    by_status: dict[str, int] = Field(default_factory=dict)


class PublishJobPatchIn(CamelModel):
    """改期 / 修改内容：仅 approved / queued 且未开始执行的任务可编辑。

    字段全部可选，用 model_fields_set 区分「未传」与「显式传 null」。
    """

    scheduled_at: datetime | None = None
    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=4000)


# ---------- wechat official account（草稿箱）----------
class WeChatCredentialsIn(CamelModel):
    app_id: str
    app_secret: str


class WeChatBindIn(CamelModel):
    display_name: str = ""
    # 可选：绑定即为该账号录入自己的 AppID/AppSecret（多账号各持凭据）；
    # 留空则使用部署级默认凭据（界面保存的或环境变量）
    credentials: WeChatCredentialsIn | None = None


class WeChatStudioOptions(CamelModel):
    model_config = {"populate_by_name": True, "alias_generator": camel, "extra": "forbid"}

    style: Literal["std", "mag", "min", "geek", "bold", "neo", "note", "zen", "bento", "dark"] = "std"
    theme: Literal["orange", "blue", "teal", "ink", "navy", "violet", "rose", "amber", "morandi", "forest", "petrol", "mist", "matcha", "cocoa", "dusty"] = "orange"
    banner: Literal["none", "poster", "neo", "card"] = "none"
    outro: StrictBool = True
    brand_author: str = Field(default="", max_length=80)
    brand_slogan: str = Field(default="", max_length=200)
    avatar_url: str = Field(default="", max_length=2048)
    title: str = Field(default="", max_length=200)

    @field_validator("avatar_url")
    @classmethod
    def validate_avatar_url(cls, value: str) -> str:
        if not value:
            return value
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or any(char.isspace() or ord(char) < 32 for char in value):
            raise ValueError("头像必须是安全的 HTTPS 图片地址")
        return value


class WeChatStudioSettingsIn(CamelModel):
    options: WeChatStudioOptions = Field(default_factory=WeChatStudioOptions)


class WeChatStudioRenderIn(WeChatStudioSettingsIn):
    title: str | None = Field(default=None, max_length=200)
    body: str = Field(default="", max_length=100000)
    image_urls: list[str] = Field(default_factory=list, max_length=100)
    asset_ids: list[str] = Field(default_factory=list, max_length=100)


class WeChatSvgCardGenerateIn(CamelModel):
    """排版工作台「选段生成动画卡」的请求体。

    text 是选中的目标段落（柱状图数值防幻觉的对照原文）；
    context_before / after 是前后各一段，帮模型理解上下文，不参与数值校验。
    """

    model_config = {"populate_by_name": True, "alias_generator": camel, "extra": "forbid"}

    text: str = Field(min_length=8, max_length=8000)
    kind: Literal[
        "svg-table", "svg-bars", "svg-steps", "svg-timeline",
        "svg-breakdown", "svg-compare", "svg-concept", "svg-phone",
    ]
    article_title: str = Field(default="", max_length=200)
    context_before: str = Field(default="", max_length=2000)
    context_after: str = Field(default="", max_length=2000)


class WeChatDraftIn(CamelModel):
    model_config = {"populate_by_name": True, "alias_generator": camel, "extra": "forbid"}

    account_id: str
    idempotency_key: str = Field(min_length=6, max_length=120)
    # 关联创作空间稿件，便于发布队列追溯来源
    content_item_id: str | None = None
    version_id: str | None = Field(default=None, validation_alias=AliasChoices("versionId", "contentVersionId", "version_id"))
    title: str = ""
    body: str = ""
    digest: str = ""
    author: str = ""
    content_source_url: str = ""
    asset_ids: list[str] = Field(default_factory=list)
    need_open_comment: bool = False
    only_fans_can_comment: bool = False


class WeChatDraftPreviewIn(CamelModel):
    model_config = {"populate_by_name": True, "alias_generator": camel, "extra": "forbid"}

    account_id: str = ""
    content_item_id: str | None = None
    version_id: str | None = Field(default=None, validation_alias=AliasChoices("versionId", "contentVersionId", "version_id"))
    title: str = ""
    body: str = ""
    digest: str = ""
    author: str = ""
    content_source_url: str = ""
    asset_ids: list[str] = Field(default_factory=list)


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
    # 近一年按本地日聚合的新建稿件数；没有稿的日期不出现，前端补 0
    activity: list[dict] = Field(default_factory=list)


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
    prompt_type: str = Field(default="normal", max_length=16)
    negative_content: str = Field(default="")
    summary: str = Field(default="", max_length=120)
    sort_order: int = 0
    enabled: bool = True
    preview_asset_ids: list[str] = Field(default_factory=list)
    source: str = Field(default="manual", max_length=16)


class PromptTemplatePatch(CamelModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, max_length=32)
    content: str | None = Field(default=None, min_length=1)
    tags: list[str] | None = Field(default=None, max_length=12)
    prompt_type: str | None = Field(default=None, max_length=16)
    negative_content: str | None = None
    summary: str | None = Field(default=None, max_length=120)
    sort_order: int | None = None
    enabled: bool | None = None
    preview_asset_ids: list[str] | None = None
    source: str | None = Field(default=None, max_length=16)


class PromptTemplateOut(CamelModel):
    id: str
    title: str
    category: str
    content: str
    tags: list[str]
    prompt_type: str = "normal"
    negative_content: str = ""
    summary: str = ""
    sort_order: int = 0
    enabled: bool = True
    preview_asset_ids: list[str] = Field(default_factory=list)
    source: str = "manual"
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


# ---------- hot topics（热点选题） ----------
# 分数与档位由服务端算好原样返回，前端只排序/筛选，不重算。


class HotScoreDetailOut(CamelModel):
    """评分证据。全部可选：脏数据/旧数据不应该让整个列表 500。"""

    items: int = 0
    platforms: list[str] = []
    captured_at: str = ""
    resonance: int = 0
    momentum: int = 0
    best_rank: int | None = None
    rank_points: int = 0
    rise_points: int = 0
    hot_points: int = 0
    hot_evidence: list[str] = []
    freshness: int = 0
    first_seen_at: str = ""
    fresh_points: int = 0
    days_points: int = 0
    days_seen: int = 0
    track: str = ""
    track_points: int = 0
    track_hits: list[str] = []
    writability: int = 0
    writability_evidence: list[str] = []
    representative: str = ""
    risk_flags: list[str] = []
    risk_penalty: int = 0
    total: int = 0
    evidence: list[str] = []


class HotClusterOut(CamelModel):
    id: str
    title: str
    track: str = ""
    score: int = 0
    grade: str = ""
    platforms: list[str] = []
    platform_count: int = 0
    item_count: int = 0
    best_rank: int | None = None
    days_seen: int = 1
    first_seen_at: str = ""
    last_seen_day: str = ""
    risk_flags: list[str] = []
    status: str = "new"
    captured_at: str = ""
    content_item_id: str | None = None
    score_detail: HotScoreDetailOut = Field(default_factory=HotScoreDetailOut)


class HotClusterItemOut(CamelModel):
    id: str
    platform: str
    rank: int | None = None
    title: str
    hot: int | None = None
    url: str = ""
    # 这一条是哪一轮从哪个榜单地址采到的；与 url（单条内容链接）分开
    source_url: str = ""
    first_seen_at: str = ""
    last_seen_at: str = ""
    history: list[dict[str, Any]] = []


class HotClusterDetailOut(HotClusterOut):
    items: list[HotClusterItemOut] = []
    content_item_title: str = ""


class HotRefreshIn(CamelModel):
    """页面「重算评分」入参：只按已入库数据重算，不抓外网。"""

    date: str | None = None


class HotIngestItemIn(CamelModel):
    """一条热点。上游字段名由调用方的定时脚本负责映射，这里只认这四个键。

    `title` 空串是合法的：空标题在这一批里丢弃，不让整批 422。
    """

    title: str = Field(default="", max_length=2000)
    rank: int | None = None
    hot: int | None = None
    url: str = Field(default="", max_length=2000)


class HotIngestIn(CamelModel):
    """一个平台的一轮榜单。

    `source_url` 必填：记的是这一轮脚本真正请求的地址，不是任务参数里打算用的。
    缺省或不是 http/https 绝对地址时整批拒绝（422），不入库。
    """

    platform: str = Field(min_length=1, max_length=32)
    source_url: str = Field(min_length=1, max_length=2000)
    captured_at: str | None = Field(default=None, max_length=16)
    date: str | None = Field(default=None, max_length=10)
    items: list[HotIngestItemIn] = Field(default_factory=list)

    @field_validator("platform")
    @classmethod
    def _clean_platform(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("platform 不能为空")
        return text

    @field_validator("source_url")
    @classmethod
    def _check_source_url(cls, value: str) -> str:
        text = value.strip()
        parts = urlsplit(text)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("sourceUrl 必须是 http/https 绝对地址")
        return text


class HotIngestOut(CamelModel):
    day: str
    captured_at: str
    platform: str
    source_url: str
    items: int
    clusters: int
    new_clusters: int
    matched_clusters: int
    recommended: int = 0


class HotRefreshOut(CamelModel):
    day: str
    captured_at: str
    items: int
    clusters: int
    new_clusters: int
    matched_clusters: int
    recommended: int
    notes: list[str] = []


class HotSummaryOut(CamelModel):
    day: str = ""
    items: int = 0
    clusters: int = 0
    recommended: int = 0
    generated: int = 0
    tracks: dict[str, int] = {}
    # 每个平台各有多少个事件（一个事件横跨多平台时各计一次），
    # 平台切换条上的条数直接用它，前端不再自己统计。
    platforms: dict[str, int] = {}
    captured_at: str = ""


class HotDegradedOut(CamelModel):
    dim: str
    reason: str


class HotStatusIn(CamelModel):
    status: str


# ---------- stickers（贴图创作） ----------

STICKER_NARRATIVES = {"scene", "knowledge", "parallel", "contrast", "suspense"}


class StickerSetCreateIn(CamelModel):
    theme: str = Field(min_length=1, max_length=2000)
    title: str = Field(default="", max_length=200)
    style_id: str = Field(min_length=1, max_length=40)
    track: str | None = Field(default=None, max_length=32)
    hot_cluster_id: str | None = Field(default=None, max_length=32)
    narrative: str = Field(default="parallel")


class StickerSetUpdateIn(CamelModel):
    title: str | None = Field(default=None, max_length=200)
    theme: str | None = Field(default=None, max_length=2000)
    style_id: str | None = Field(default=None, max_length=40)
    narrative: str | None = None


class StickerOutlineItemIn(CamelModel):
    caption: str = Field(default="", max_length=500)
    frame_prompt: str = Field(min_length=1, max_length=2000)
    slot_type: str = Field(default="info")


class StickerOutlineIn(CamelModel):
    # 张数上下界在模块层校验（2~9），这里放宽以免边界错误变成 422 而不是业务码
    items: list[StickerOutlineItemIn] = Field(min_length=1, max_length=20)


class StickerImagePatchIn(CamelModel):
    """第 3 步的单张编辑：只改这一行的画面描述/配文/类型，不动图序与已出素材。"""

    caption: str | None = Field(default=None, max_length=500)
    frame_prompt: str | None = Field(default=None, min_length=1, max_length=2000)
    slot_type: str | None = Field(default=None, max_length=8)
    # True 且画面描述确实变了：保存后立刻用新描述重画这一张（旧素材作废）
    regenerate: bool = False


class StickerImageOut(CamelModel):
    id: str
    sort_order: int
    caption: str
    frame_prompt: str
    # 生成提示词已按最新描述应用；False = 改了描述但还没重画
    prompt_applied: bool = True
    slot_type: str
    status: str
    asset_id: str | None = None
    url: str | None = None
    job_id: str | None = None
    error_code: str | None = None


class StickerSetOut(CamelModel):
    id: str
    title: str
    theme: str
    style_id: str
    track: str | None = None
    hot_cluster_id: str | None = None
    narrative: str
    status: str
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    cover_url: str | None = None
    # 画风签名（锚点反推的一致性锚），仅详情接口回显
    anchor_desc: str = ""
    # 本集选定的生图模型；空表示环境变量 / 首个可用连接
    image_model_id: str | None = None
    created_at: datetime
    updated_at: datetime
    images: list[StickerImageOut] = []


class StickerImageModelIn(CamelModel):
    """空串 / 省略表示环境变量供应商（或回退到首个可用连接）。"""

    model_id: str | None = Field(default=None, max_length=64)


class StickerStylesOut(CamelModel):
    styles: list[dict] = []
    track_defaults: dict[str, str] = {}
    narratives: list[dict] = []


# ---------- storyboards（漫画分镜） ----------


# ---------- 角色库（跨项目复用） ----------
# 分镜项目用 `role:<characterId>` 引用全局角色；老项目里的本地角色条目继续可读。

CHARACTER_ANGLES = ("front", "side", "full", "close", "back", "any")
APPEARANCE_KEYS = ("hair", "hairColor", "eyes", "face", "outfit", "body", "age", "extra")


class CharacterImageIn(CamelModel):
    asset_id: str = Field(min_length=1, max_length=64)
    angle: str = Field(default="front", max_length=16)
    is_primary: bool = False
    label: str = Field(default="", max_length=60)


class CharacterImageOut(CharacterImageIn):
    url: str | None = None


class CharacterIn(CamelModel):
    name: str = Field(min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=12)
    appearance: dict[str, str] = Field(default_factory=dict)
    images: list[CharacterImageIn] = Field(default_factory=list, max_length=8)
    notes: str = Field(default="", max_length=2000)


class CharacterPatch(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    aliases: list[str] | None = Field(default=None, max_length=12)
    appearance: dict[str, str] | None = None
    images: list[CharacterImageIn] | None = Field(default=None, max_length=8)
    notes: str | None = Field(default=None, max_length=2000)


class CharacterPendingImageOut(CamelModel):
    asset_id: str | None = None
    angle: str = "front"
    job_id: str | None = None
    status: str = "queued"  # queued / running / succeeded / failed
    prompt: str = ""
    degraded: bool = False
    error: str | None = None
    url: str | None = None


class CharacterOut(CamelModel):
    id: str
    name: str
    aliases: list[str]
    appearance: dict[str, str]
    images: list[CharacterImageOut]
    pending_images: list[CharacterPendingImageOut] = Field(default_factory=list)
    notes: str
    created_at: datetime
    updated_at: datetime


class CharacterExtractIn(CamelModel):
    source_text: str = Field(min_length=1, max_length=12000)
    model_id: str | None = None


class CharacterDraftOut(CamelModel):
    name: str
    aliases: list[str]
    appearance: dict[str, str]
    exists: bool = False


class CharacterExtractOut(CamelModel):
    drafts: list[CharacterDraftOut]
    model_code: str | None = None
    fallback_used: bool = False


CHARACTER_STYLES = ("manga", "realistic", "guoman", "watercolor")


class CharacterImageGenerateIn(CamelModel):
    angle: str = Field(default="front", max_length=16)
    style: str = Field(default="manga", max_length=24)
    model_id: str | None = None
    # 用户改过的最终提示词；给定时整体替换模板合成结果
    prompt_override: str = Field(default="", max_length=4000)


class CharacterImageGenerateOut(CamelModel):
    job_id: str
    prompt: str
    anchored: bool = False


class CharacterAppearanceCompleteIn(CamelModel):
    hint_text: str = Field(default="", max_length=2000)
    source_text: str = Field(default="", max_length=8000)
    model_id: str | None = None


class CharacterAppearanceCompleteOut(CamelModel):
    appearance: dict[str, str]
    model_code: str | None = None
    fallback_used: bool = False


class StoryboardAssetItem(CamelModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    asset_id: str | None = Field(default=None, max_length=64)
    description: str = Field(default="", max_length=500)
    # 引用全局角色时由服务端回填；输入侧只需给 roleId，其余字段一律以角色库为准
    role_id: str | None = Field(default=None, max_length=64)
    aliases: list[str] = Field(default_factory=list)
    appearance: dict[str, str] = Field(default_factory=dict)
    images: list[CharacterImageOut] = Field(default_factory=list)
    image_urls: list[str] = Field(default_factory=list)


class StoryboardAssets(CamelModel):
    characters: list[StoryboardAssetItem] = Field(default_factory=list, max_length=24)
    locations: list[StoryboardAssetItem] = Field(default_factory=list, max_length=24)
    styles: list[StoryboardAssetItem] = Field(default_factory=list, max_length=12)


class StoryboardProjectCreate(CamelModel):
    title: str = Field(default="未命名分镜", min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    assets: StoryboardAssets = Field(default_factory=StoryboardAssets)
    style_config: dict | None = None


class StoryboardProjectPatch(CamelModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=4000)
    assets: StoryboardAssets | None = None
    style_config: dict | None = None


class StoryboardSceneCreate(CamelModel):
    title: str = Field(default="新场景", min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    chapter: str = Field(default="第一章", max_length=200)
    # 场级风格覆盖：None/缺省 = 空覆盖（跟随项目级）
    style_config: dict | None = None


class StoryboardScenePatch(CamelModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=4000)
    chapter: str | None = Field(default=None, max_length=200)
    # None = 不动；null/{} = 清除覆盖回落项目级；dict = 覆盖
    style_config: dict | None = None


class StoryboardShotCreate(CamelModel):
    frame_description: str = Field(default="", max_length=8000)
    action: str = Field(default="", max_length=4000)
    dialogue: str = Field(default="", max_length=4000)
    shot_size: str = Field(default="medium", max_length=24)
    view_angle: str = Field(default="eye_level", max_length=24)
    camera_motion: str = Field(default="static", max_length=24)
    duration_seconds: int = Field(default=3, ge=1, le=120)
    prompt: str = Field(default="", max_length=12000)
    asset_refs: list[str] = Field(default_factory=list, max_length=12)


class StoryboardShotPatch(StoryboardShotCreate):
    pass


class NovelModelIn(CamelModel):
    """小说 4 个 AI 动作共用的可选请求体。

    不传或 modelId 为空 = 用当前默认模型（入队时快照）；显式指定 = 钉死该模型，
    失败明确报错不降级。历史调用完全不带 body，保持兼容。
    """

    model_id: str | None = None


class StoryboardBreakdownIn(CamelModel):
    source_text: str = Field(min_length=1, max_length=12000)
    model_id: str | None = None


class StoryboardOrderIn(CamelModel):
    ids: list[str] = Field(min_length=1, max_length=300)


class StoryboardGenerateIn(CamelModel):
    model_id: str | None = None
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=4)


class StoryboardShotOut(CamelModel):
    id: str
    sort_order: int
    deleted_at: datetime | None = None
    frame_description: str
    action: str
    dialogue: str
    shot_size: str
    view_angle: str
    camera_motion: str
    duration_seconds: int
    prompt: str
    asset_refs: list[str] = Field(default_factory=list)
    sketch_asset_id: str | None = None
    sketch_url: str | None = None
    sketch_job_id: str | None = None
    sketch_status: str
    sketch_error: str | None = None
    # 实际入队生图时使用的完整提示词（含参考图标注与角色一致性文案），供前端查看/复制
    sketch_prompt: str | None = None
    final_asset_ids: list[str] = Field(default_factory=list)
    final_urls: list[str] = Field(default_factory=list)
    final_job_id: str | None = None
    final_status: str
    final_error: str | None = None
    final_prompt: str | None = None
    # 快照来自该阶段入队时的 job payload；最终图优先，否则回退草图阶段。
    style_snapshot: dict | None = None
    generation_version: int


class StoryboardSceneOut(CamelModel):
    id: str
    sort_order: int
    deleted_at: datetime | None = None
    title: str
    summary: str
    chapter: str
    # 场级风格覆盖：空 dict = 跟随项目级
    style_config: dict | None = None
    shots: list[StoryboardShotOut] = Field(default_factory=list)


class StoryboardProjectOut(CamelModel):
    id: str
    workspace_id: str
    title: str
    summary: str
    deleted_at: datetime | None = None
    assets: StoryboardAssets
    status: str
    style_config: dict | None = None
    created_at: datetime
    updated_at: datetime
    scenes: list[StoryboardSceneOut] = Field(default_factory=list)


# ---------- novels ----------


def _empty_novel_facts() -> dict:
    return {"characterChanges": [], "itemHoldings": [], "events": [], "openHooks": []}


class NovelRules(CamelModel):
    person: str = ""
    style: str = ""
    banned: str = ""


class NovelProjectCreate(CamelModel):
    title: str | None = Field(default=None, max_length=200)
    source_text: str
    rights_confirmed: bool = False


class NovelProjectPatch(CamelModel):
    title: str | None = Field(default=None, max_length=200)
    rules: NovelRules | None = None


class NovelChapterPatch(CamelModel):
    title: str | None = Field(default=None, max_length=200)
    source_text: str | None = None


class NovelFacts(CamelModel):
    character_changes: list[Any] = Field(default_factory=list)
    item_holdings: list[Any] = Field(default_factory=list)
    events: list[Any] = Field(default_factory=list)
    open_hooks: list[Any] = Field(default_factory=list)


class NovelChapterOut(CamelModel):
    id: str
    sort_order: int
    source: str
    title: str
    source_text: str
    rewrite_text: str
    draft_text: str
    accepted_text: str
    draft_version: int
    status: str
    facts: NovelFacts = Field(default_factory=NovelFacts)
    stale: bool
    impact: dict[str, Any] = Field(default_factory=dict)
    pending_candidate: str | None = None
    created_at: datetime
    updated_at: datetime


class NovelCharacterOut(CamelModel):
    id: str
    original_name: str
    new_name: str
    relations: str
    speech_style: str
    state: str
    created_at: datetime
    updated_at: datetime


class NovelAliasOut(CamelModel):
    id: str
    kind: str
    original_name: str
    new_name: str
    created_at: datetime
    updated_at: datetime


class NovelProjectOut(CamelModel):
    id: str
    workspace_id: str
    title: str
    source_text: str
    rights_confirmed_at: datetime | None = None
    rules: NovelRules = Field(default_factory=NovelRules)
    status: str
    created_at: datetime
    updated_at: datetime
    chapters: list[NovelChapterOut] = Field(default_factory=list)
    characters: list[NovelCharacterOut] = Field(default_factory=list)
    aliases: list[NovelAliasOut] = Field(default_factory=list)


class NovelProjectListItem(CamelModel):
    id: str
    title: str
    status: str
    chapter_count: int
    updated_at: datetime


# ---------- 定时任务（docs/scheduled-tasks.md §3）----------
class ScheduleCreateIn(CamelModel):
    """新建定时任务。`scope` 不接受客户端指定——由任务类型自己决定。"""

    task_type: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=120)
    params: dict = Field(default_factory=dict)
    schedule_kind: str = Field(default="cron", pattern="^(cron|interval)$")
    cron: str = Field(default="", max_length=120)
    interval_seconds: int | None = Field(default=None, ge=1, le=31 * 24 * 3600)
    timezone: str | None = Field(default=None, max_length=64)
    misfire_grace_seconds: int | None = Field(default=None, ge=1, le=24 * 3600)
    enabled: bool = True


class ScheduleUpdateIn(CamelModel):
    """局部更新：只改传上来的字段，未传的保持原值。"""

    name: str | None = Field(default=None, max_length=120)
    params: dict | None = None
    schedule_kind: str | None = Field(default=None, pattern="^(cron|interval)$")
    cron: str | None = Field(default=None, max_length=120)
    interval_seconds: int | None = Field(default=None, ge=1, le=31 * 24 * 3600)
    timezone: str | None = Field(default=None, max_length=64)
    misfire_grace_seconds: int | None = Field(default=None, ge=1, le=24 * 3600)
    enabled: bool | None = None


class SchedulePreviewIn(CamelModel):
    """保存前预览未来触发时间：cron 最容易配错，让人先看见结果。"""

    schedule_kind: str = Field(default="cron", pattern="^(cron|interval)$")
    cron: str = Field(default="", max_length=120)
    interval_seconds: int | None = Field(default=None, ge=1, le=31 * 24 * 3600)
    timezone: str | None = Field(default=None, max_length=64)
    count: int = Field(default=3, ge=1, le=10)


class ScriptScheduleCreateIn(CamelModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    draft_code: str = Field(default="", max_length=262144)
    timeout_seconds: int = Field(default=60, ge=5, le=300)
    enabled: bool = True


class ScriptScheduleUpdateIn(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    draft_code: str | None = Field(default=None, max_length=262144)
    timeout_seconds: int | None = Field(default=None, ge=5, le=300)
    enabled: bool | None = None


class ScriptSchedulePublishIn(CamelModel):
    draft_code: str | None = Field(default=None, max_length=262144)


class ScriptScheduleRunIn(CamelModel):
    params: dict = Field(default_factory=dict)


class ScriptScheduleTestIn(CamelModel):
    """脚本测试验证：不落库回滚执行（测 draft 代码）。"""
    params: dict = Field(default_factory=dict)


class ScheduleTestRunIn(CamelModel):
    """定时任务统一测试验证入口：内置脚本与 user-script.* 都走这里。"""
    task_type: str
    params: dict = Field(default_factory=dict)


# ---------- RBAC ----------


class PermissionOut(CamelModel):
    code: str
    name: str
    module: str
    sort: int


class MenuNodeOut(CamelModel):
    """菜单节点。前端侧栏与后台菜单管理共用一种形状，差别只在字段是否填充。"""

    id: str
    parent_id: str | None = None
    key: str
    label: str
    short: str = ""
    icon: str = ""
    path: str = ""
    kind: str = "page"
    permission: str | None = None
    sort: int = 0
    visible: bool = True
    children: list["MenuNodeOut"] = Field(default_factory=list)


class NavItemOut(CamelModel):
    """侧栏项。`name` 就是前端路由名，`permission` 供按钮/页面二次校验。

    带 `children` 的是分类分组（纯容器，无路由）：侧栏据此多渲染一层折叠。
    """

    name: str
    label: str
    short: str = ""
    icon: str = ""
    path: str = ""
    permission: str | None = None
    children: list["NavItemOut"] = Field(default_factory=list)


class NavGroupOut(CamelModel):
    key: str
    label: str
    icon: str = ""
    path: str = ""
    items: list[NavItemOut] = Field(default_factory=list)


class RbacMeOut(CamelModel):
    is_superadmin: bool = False
    role_keys: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    menus: list[NavGroupOut] = Field(default_factory=list)


class RoleOut(CamelModel):
    id: str
    role_key: str
    name: str
    description: str = ""
    is_superadmin: bool = False
    is_system: bool = False
    sort: int = 0
    permission_codes: list[str] = Field(default_factory=list)
    menu_ids: list[str] = Field(default_factory=list)
    user_count: int = 0


class RoleIn(CamelModel):
    role_key: str = Field(min_length=2, max_length=48, pattern="^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=48)
    description: str = Field(default="", max_length=200)
    sort: int = Field(default=100, ge=0, le=9999)
    # 新建时直接给的权限；不传则按「只读」起步（不给任何权限）
    permission_codes: list[str] | None = None


class RolePatch(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=48)
    description: str | None = Field(default=None, max_length=200)
    sort: int | None = Field(default=None, ge=0, le=9999)


class RolePermissionIn(CamelModel):
    codes: list[str] = Field(default_factory=list)


class RoleMenuIn(CamelModel):
    menu_ids: list[str] = Field(default_factory=list)


class MenuIn(CamelModel):
    parent_id: str | None = None
    key: str = Field(min_length=1, max_length=64, pattern="^[A-Za-z0-9_-]+$")
    label: str = Field(min_length=1, max_length=48)
    short: str = Field(default="", max_length=24)
    icon: str = Field(default="", max_length=32)
    kind: str = Field(default="page", pattern="^(group|page|link)$")
    permission: str | None = Field(default=None, max_length=64)
    sort: int = Field(default=100, ge=0, le=9999)
    visible: bool = True


class MenuPatch(CamelModel):
    parent_id: str | None = None
    label: str | None = Field(default=None, min_length=1, max_length=48)
    short: str | None = Field(default=None, max_length=24)
    icon: str | None = Field(default=None, max_length=32)
    kind: str | None = Field(default=None, pattern="^(group|page|link)$")
    permission: str | None = Field(default=None, max_length=64)
    sort: int | None = Field(default=None, ge=0, le=9999)
    visible: bool | None = None


class UserRolesOut(CamelModel):
    id: str
    display_name: str
    email: str | None = None
    is_active: bool = True
    # 开发态账号没有邮箱，界面建号/重置密码都要求有邮箱
    has_password: bool = False
    role_ids: list[str] = Field(default_factory=list)
    role_keys: list[str] = Field(default_factory=list)


class UserRolesIn(CamelModel):
    role_ids: list[str] = Field(default_factory=list)


class ManagedUserIn(CamelModel):
    """管理员建号。密码由服务端生成，只在响应里出现一次。"""

    email: str = Field(min_length=3, max_length=180)
    display_name: str | None = Field(default=None, max_length=120)
    role_ids: list[str] = Field(min_length=1)


class ManagedUserPatch(CamelModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None


class ManagedUserCreatedOut(CamelModel):
    user: UserRolesOut
    # 一次性初始密码。列表接口永不返回这个字段。
    initial_password: str


class RbacAuditOut(CamelModel):
    id: str
    actor_user_id: str | None = None
    actor_name: str | None = None
    action: str
    target_type: str = ""
    target_id: str = ""
    detail: dict = Field(default_factory=dict)
    created_at: datetime


# ---------- originality analysis ----------
class OriginalityAnalysisRequest(CamelModel):
    """文章原创度检测请求。"""
    
    content_version_id: str
    compare_scope: str = "all"  # all | own | public
    detailed: bool = True  # 是否返回相似片段详情
    use_cache: bool = True  # 是否使用缓存


class ScoreboardOut(CamelModel):
    """五维度评分。

    口径：`similarity` **越高越像**（0-100，与语料库内最像的一篇），其余四维越高越好。
    `corpusSize` 为 0 时 similarity 无意义，前端应标「样本不足」而不是显示 0%。
    """

    similarity: int  # 0-100，越高越像
    originality: int  # 观点独特性
    structure: int  # 结构创新度
    data_accuracy: int  # 数据真实度
    style_uniqueness: int  # 语言风格独特性


class SimilarArticleOut(CamelModel):
    """相似文章摘要。"""

    id: str
    title: str
    similarity: float  # 0.0-1.0
    matching_snippets: list[dict] = Field(default_factory=list)


class KeyPointOut(CamelModel):
    """关键观点分析。"""

    point: str
    novelty: int  # 创新度 0-100
    sources: int  # 已有多少篇稿件提过


class RiskFlagOut(CamelModel):
    """风险标记。"""

    type: str  # high_similarity_paragraph | missing_data | etc
    position: str  # 位置描述
    severity: str  # warning | error
    reason: str


class AnalysisDetailsOut(CamelModel):
    """检测详情。"""

    similar_articles: list[SimilarArticleOut] = Field(default_factory=list)
    key_points: list[KeyPointOut] = Field(default_factory=list)
    risk_flags: list[RiskFlagOut] = Field(default_factory=list)


class OriginalityAnalysisOut(CamelModel):
    """文章原创度检测结果。"""

    analysis_id: str
    content_version_id: str
    analyzed_at: datetime
    total_score: int  # 0-100
    level: str  # excellent | good | fair | poor
    scoreboard: ScoreboardOut
    details: AnalysisDetailsOut
    recommendations: list[str] = Field(default_factory=list)
    model_version: str
    processing_time_ms: int = 0
    # v3.0：一句话结论 + 语料规模 + 是否命中缓存
    summary: str = ""
    corpus_size: int = 0
    self_version_count: int = 0
    cached: bool = False


# ═══════════════════════════════════════════════════════════════
# Phase 2 & 3：高级功能 Schema
# ═══════════════════════════════════════════════════════════════


class BatchAnalysisRequest(CamelModel):
    """批量检测请求 (Phase 3)"""
    
    content_version_ids: list[str] = Field(min_items=1, max_items=100)
    compare_scope: str = Field(default="all", pattern="^(all|own|public)$")
    use_advanced_analysis: bool = Field(default=True)
    priority: str = Field(default="normal", pattern="^(low|normal|high)$")


class TextEmbeddingOut(CamelModel):
    """文本向量输出 (Phase 3)"""
    
    content_version_id: str
    embedding_dim: int
    model_version: str
    created_at: datetime
    is_deprecated: bool = False


class KeywordAnalysisOut(CamelModel):
    """关键词分析结果 (Phase 2)"""
    
    keyword: str
    weight: float  # TF-IDF 权重
    frequency: int
    is_novel: bool  # 是否为原创词


class StructureAnalysisOut(CamelModel):
    """结构分析结果 (Phase 2)"""
    
    outline_depth: int  # 大纲深度
    section_count: int  # 章节数
    diversity_score: float  # 结构多样性 (0-100)
    balance_score: float  # 结构平衡度 (0-100)
    suggestions: list[str] = Field(default_factory=list)


class SemanticSimilarityOut(CamelModel):
    """语义相似度 (Phase 3)"""
    
    text1_id: str
    text2_id: str
    similarity: float  # 0-1
    similarity_type: str  # "semantic" | "tfidf"
    matching_concepts: list[str] = Field(default_factory=list)


class AnalysisReportOut(CamelModel):
    """检测报告 (Phase 3)"""
    
    report_id: str
    analysis_id: str
    title: str
    export_formats: dict = Field(default_factory=dict)  # {"pdf": "url", "excel": "url"}
    status: str  # "generating" | "ready" | "failed"
    generated_at: datetime
    expires_at: datetime | None = None


class BatchTaskStatusOut(CamelModel):
    """批量任务状态 (Phase 3)"""
    
    task_id: str
    status: str  # "pending" | "running" | "completed" | "failed"
    progress: int  # 0-100
    total_items: int
    completed_items: int
    failed_items: int = 0
    estimated_completion_at: datetime | None = None
    completed_at: datetime | None = None


class AnalysisHistoryItemOut(CamelModel):
    """检测历史记录 (Phase 3)"""
    
    revision: int
    score: int
    score_change: int | None = None
    improvements: dict = Field(default_factory=dict)
    created_at: datetime



# ---------- 成稿台：段落级编辑编排 ----------
#
# 结构与 dsh-wechat-studio 的 article/block 契约保持一致（前端照搬时可少改），
# 只是把整篇 JSON 拆成了数据库行：article 是稿件头，blocks 是有稳定 id 的段落。


class StudioBlockOut(CamelModel):
    id: str
    ordinal: int = 0
    text: str = ""
    kind: str = "para"
    check: dict | None = None
    prev_text: str | None = None
    inserted_diagram_id: str | None = None


class StudioJobOut(CamelModel):
    """正在跑的流水线任务。kind: draft|titles|chat|fix|fixall|banner|report。"""

    kind: str
    label: str = ""
    text: str = ""
    target: str | None = None
    targets: list[str] = Field(default_factory=list)
    progress: dict | None = None
    stopping: bool = False


class StudioArticleOut(CamelModel):
    id: str
    topic: str = ""
    material: str = ""
    writer_style: str = "tutorial"
    front: str = ""
    title: str = ""
    hook: str | None = None
    titles: list = Field(default_factory=list)
    summary: str = ""
    banner_style: str = "poster"
    banners: dict = Field(default_factory=dict)
    layout_options: dict = Field(default_factory=dict)
    follow_card: bool = False
    zhuque: dict = Field(default_factory=dict)
    chat: list = Field(default_factory=list)
    notice: dict | None = None
    cover: dict = Field(default_factory=dict)
    inline_diagrams: list = Field(default_factory=list)
    scheduled_for: datetime | None = None
    published_at: datetime | None = None
    publish_marked_at: datetime | None = None
    archived: bool = False
    last_view: str = "paper"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    blocks: list[StudioBlockOut] = Field(default_factory=list)
    job: StudioJobOut | None = None


class StudioMetaOut(CamelModel):
    id: str
    title: str = ""
    topic: str = ""
    updated_at: datetime | None = None
    chars: int = 0
    blocks: int = 0
    hook: str | None = None
    last_view: str = "paper"
    archived_at: datetime | None = None
    cover_id: str | None = None
    review: int = 0
    checked: bool = False
    scheduled_for: datetime | None = None
    published_at: datetime | None = None
    publish_marked_at: datetime | None = None
    readiness: dict = Field(default_factory=dict)


class StudioStateOut(CamelModel):
    articles: list[StudioMetaOut] = Field(default_factory=list)
    settings: dict = Field(default_factory=dict)
    # 可选写作风格档：[{value, label, hint}]，由后端的 styles 目录扫描得出
    writer_styles: list = Field(default_factory=list)


class StudioNewIn(CamelModel):
    topic: str = ""
    material: str = ""
    markdown: str = ""
    # 写正文用的风格档；不传按实战教程
    writer_style: str = "tutorial"
    # 建好就写正文；给了 markdown 时按导入处理，不再让模型写
    draft: bool = False
    titles: bool = True


class StudioStyleIn(CamelModel):
    style: str = Field(min_length=1, max_length=32)


class StudioDraftIn(CamelModel):
    """写正文。带上 style 就是「按这个风格重写一遍」，不带就用稿件当前风格。"""

    style: str | None = None


class StudioChatIn(CamelModel):
    message: str = Field(min_length=1, max_length=4000)


class StudioBlockPatchIn(CamelModel):
    text: str = Field(max_length=40000)
    # 乐观锁：带上读到的原文，服务端发现被改过就拒绝，避免覆盖别人的修改
    expected_text: str | None = None


class StudioTitlePickIn(CamelModel):
    text: str = Field(min_length=1, max_length=80)


class StudioBannerIn(CamelModel):
    style: str = "poster"


class StudioScheduleIn(CamelModel):
    at: datetime | None = None


class StudioDetectIn(CamelModel):
    """质检检测。blockIds 只检指定段落（缺省=全文）；source 缺省=自动。"""

    block_ids: list[str] | None = None
    source: str | None = None


class StudioSettingsIn(CamelModel):
    """朱雀质检凭据。key 为空表示不配置，检测自动降级为本地启发式。"""

    zhuque_key: str | None = None
    zhuque_endpoint: str | None = None


class StudioRevisionOut(CamelModel):
    index: int
    label: str = ""
    at: datetime | None = None
    rows: list = Field(default_factory=list)


class StudioRevisionsOut(CamelModel):
    revisions: list[StudioRevisionOut] = Field(default_factory=list)


class StudioMarkdownOut(CamelModel):
    markdown: str


# ---------- 宠物创作系统：Skill 蒸馏与对话 ----------


class SkillDistillationStartIn(CamelModel):
    """开始 Skill 蒸馏"""

    skill_entry_id: str
    distillation_type: Literal["writing", "persona", "knowledge"]
    tier: Literal["quick", "standard", "deep"] = "standard"
    # 源数据
    source_data: dict[str, Any] = Field(default_factory=dict)


class SkillDistillationOut(CamelModel):
    """蒸馏任务状态"""

    model_config = {"populate_by_name": True, "alias_generator": camel, "from_attributes": True}

    id: str
    skill_entry_id: str
    distillation_type: str
    status: str  # pending | processing | completed | failed
    progress: int  # 0-100
    error_message: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class PetMessage(CamelModel):
    """宠物对话中的一条消息"""

    role: Literal["user", "pet", "assistant"]
    content: str
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class PetMessageIn(CamelModel):
    message: str = Field(min_length=1, max_length=12000)
    active_skill_ids: list[str] | None = Field(default=None, max_length=5)


class QuickRefineIn(CamelModel):
    paragraph: str = Field(min_length=1, max_length=20000)
    refine_type: Literal["polish", "rewrite", "remove_ai_taste"] = "polish"
    skill_id: str | None = None


class QuickCardIn(CamelModel):
    text: str = Field(min_length=1, max_length=12000)
    title: str | None = Field(default=None, max_length=40)
    theme: Literal["blue", "purple", "green", "orange", "red"] = "blue"


class PetConversationIn(CamelModel):
    """发起对话"""

    studio_article_id: str
    message: str = Field(min_length=1, max_length=12000)
    active_skill_ids: list[str] = Field(default_factory=list, max_length=5)


class PetConversationOut(CamelModel):
    """对话会话状态"""

    model_config = {"populate_by_name": True, "alias_generator": camel, "from_attributes": True}

    id: str
    studio_article_id: str
    messages: list[PetMessage] = Field(default_factory=list)
    active_skill_ids: list[str] = Field(default_factory=list)
    last_generation: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class SkillExecutionIn(CamelModel):
    """执行 Skill"""

    skill_id: str
    context: dict[str, Any] = Field(default_factory=dict)


class SkillExecutionOut(CamelModel):
    """Skill 执行结果"""

    result: str
    metadata: dict[str, Any] = Field(
        default_factory=dict
    )  # {skill_id, skill_type, execution_time_ms}


# ---------- 技能中心（独立技能库） ----------
class AuthorSkillIn(CamelModel):
    name: str = Field(min_length=1, max_length=80)
    skill_type: Literal["writing", "persona", "knowledge"]
    description: str = Field(default="", max_length=4000)
    summary: str = Field(default="", max_length=400)
    config: dict[str, Any] = Field(default_factory=dict)


class AuthorSkillUpdateIn(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=4000)
    summary: str | None = Field(default=None, max_length=400)
    config: dict[str, Any] | None = None
    note: str = Field(default="", max_length=200)


class AuthorSkillOut(CamelModel):
    model_config = {"populate_by_name": True, "alias_generator": camel, "from_attributes": True}

    id: str
    name: str
    skill_type: str
    description: str
    summary: str
    config: dict[str, Any]
    source: str
    status: str
    version: int
    usage_count: int
    created_at: datetime
    updated_at: datetime


class AuthorSkillDistillIn(CamelModel):
    materials: list[Any] = Field(default_factory=list, max_length=50)
    urls: list[str] = Field(default_factory=list, max_length=20)
    person_name: str = Field(default="", max_length=80)
    tier: Literal["quick", "standard", "deep"] = "standard"


class AuthorSkillDistillOut(CamelModel):
    model_config = {"populate_by_name": True, "alias_generator": camel, "from_attributes": True}

    id: str
    skill_id: str
    skill_type: str
    status: str
    progress: int
    material_stats: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AuthorSkillImportIn(CamelModel):
    """导入单个技能或整包；结构由服务层统一校验。"""

    payload: dict[str, Any]
