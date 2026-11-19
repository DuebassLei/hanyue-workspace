"""提示词库：全局统一提示词模板（管理员维护，全员只读可用）。

历史 ``owner_user_id`` 仅作来源追溯，不再作为可见性依据；新记录恒为 null。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status
from pydantic import Field
from sqlalchemy import select, update

from .. import models, schemas
from ..core.deps import ApiError, CurrentUser, DbSession, not_found
from ..core.rbac import require_permission

router = APIRouter(prefix="/prompts", tags=["prompts"], dependencies=[require_permission("prompts.use")])

DEFAULT_CATEGORY = "通用"

VALID_PROMPT_TYPES = frozenset(
    {"normal", "cover", "illustration", "base", "comic_panel", "reference"}
)
VALID_SOURCES = frozenset({"manual", "ai", "factory"})

# 增删改统一沿用「管理全局提示词」权限码（与旧 image_styles.manage 同码）。
_MANAGE = require_permission("image_styles.manage")


class DraftDepositIn(schemas.CamelModel):
    """从 AI 成文草稿手动沉淀提示词的入参（只传文本，不依赖服务端草稿存储）。"""

    title: str = Field(default="", max_length=120)
    cover_prompt: str = Field(default="")
    inline_prompts: list[str] = Field(default_factory=list, max_length=12)


def _clean_tags(tags: list[str] | None) -> list[str]:
    """去空白、去重并限制数量，避免前端提交重复或超长标签。"""
    cleaned: list[str] = []
    for tag in tags or []:
        value = tag.strip()[:24]
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned[:12]


def _clean_category(category: str) -> str:
    return category.strip()[:32] or DEFAULT_CATEGORY


def _require_prompt_type(value: str | None, default: str = "normal") -> str:
    prompt_type = (value or default).strip()
    if prompt_type not in VALID_PROMPT_TYPES:
        raise ApiError("invalid_prompt_type", "提示词类型必须是 normal/cover/illustration/base/comic_panel/reference")
    return prompt_type


def _require_source(value: str | None, default: str = "manual") -> str:
    source = (value or default).strip()
    if source not in VALID_SOURCES:
        raise ApiError("invalid_source", "提示词来源必须是 manual/ai/factory")
    return source


@router.get("", response_model=list[schemas.PromptTemplateOut])
def list_prompts(
    user: CurrentUser,
    db: DbSession,
    prompt_type: str | None = Query(default=None, alias="promptType"),
    enabled: bool | None = Query(default=None),
) -> list[models.PromptTemplate]:
    """全部登录用户可读全部记录；可按类型与启停状态过滤。"""
    if prompt_type is not None:
        prompt_type = _require_prompt_type(prompt_type)
    stmt = select(models.PromptTemplate)
    if prompt_type:
        stmt = stmt.where(models.PromptTemplate.prompt_type == prompt_type)
    if enabled is not None:
        stmt = stmt.where(models.PromptTemplate.enabled.is_(enabled))
    return list(
        db.scalars(
            stmt.order_by(
                models.PromptTemplate.sort_order.asc(),
                models.PromptTemplate.created_at.asc(),
                models.PromptTemplate.id.asc(),
            )
        )
    )


@router.post(
    "", response_model=schemas.PromptTemplateOut, status_code=status.HTTP_201_CREATED,
    dependencies=[_MANAGE],
)
def create_prompt(payload: schemas.PromptTemplateIn, user: CurrentUser, db: DbSession) -> models.PromptTemplate:
    title = payload.title.strip()
    if not title:
        raise ApiError("invalid_title", "提示词名称不能为空")
    if not payload.content.strip():
        raise ApiError("invalid_prompt", "提示词内容不能为空")
    prompt = models.PromptTemplate(
        owner_user_id=None,  # 全局资源：owner 不再作归属或可见性依据
        title=title,
        category=_clean_category(payload.category),
        content=payload.content.strip(),
        tags=_clean_tags(payload.tags),
        prompt_type=_require_prompt_type(payload.prompt_type),
        negative_content=(payload.negative_content or "").strip(),
        summary=(payload.summary or "").strip(),
        sort_order=payload.sort_order,
        enabled=payload.enabled,
        preview_asset_ids=list(payload.preview_asset_ids or []),
        source=_require_source(payload.source),
    )
    db.add(prompt)
    db.commit()
    return prompt


@router.post(
    "/from-draft", response_model=list[schemas.PromptTemplateOut],
    status_code=status.HTTP_201_CREATED, dependencies=[_MANAGE],
)
def deposit_from_draft(payload: DraftDepositIn, user: CurrentUser, db: DbSession) -> list[models.PromptTemplate]:
    """手动把 AI 成文的封面/插图提示词沉淀进统一提示词库（source=ai）。

    默认不自动沉淀；StudioView 通过「存入提示词库」按钮显式调用。幂等：重复存入不产生副本。
    """
    from ..services import ai_article

    return ai_article.deposit_article_prompts(
        db,
        cover_prompt=payload.cover_prompt,
        inline_prompts=payload.inline_prompts,
        title_prefix=payload.title.strip(),
    )


@router.patch("/{prompt_id}", response_model=schemas.PromptTemplateOut, dependencies=[_MANAGE])
def update_prompt(
    prompt_id: str,
    payload: schemas.PromptTemplatePatch,
    user: CurrentUser,
    db: DbSession,
) -> models.PromptTemplate:
    prompt = db.get(models.PromptTemplate, prompt_id)
    if not prompt:
        raise not_found("提示词")
    data = payload.model_dump(exclude_unset=True)
    if "title" in data:
        title = (data["title"] or "").strip()
        if not title:
            raise ApiError("invalid_title", "提示词名称不能为空")
        prompt.title = title
    if "category" in data:
        prompt.category = _clean_category(data["category"] or "")
    if "content" in data:
        content = (data["content"] or "").strip()
        if not content:
            raise ApiError("invalid_prompt", "提示词内容不能为空")
        prompt.content = content
    if "tags" in data:
        prompt.tags = _clean_tags(data["tags"])
    if data.get("prompt_type") is not None:
        prompt.prompt_type = _require_prompt_type(data["prompt_type"])
    if data.get("source") is not None:
        prompt.source = _require_source(data["source"])
    if "negative_content" in data:
        prompt.negative_content = (data["negative_content"] or "").strip()
    if "summary" in data:
        prompt.summary = (data["summary"] or "").strip()
    if data.get("sort_order") is not None:
        prompt.sort_order = data["sort_order"]
    if data.get("enabled") is not None:
        prompt.enabled = data["enabled"]
    if data.get("preview_asset_ids") is not None:
        prompt.preview_asset_ids = list(data["preview_asset_ids"])
    db.commit()
    return prompt


@router.delete("/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_MANAGE])
def delete_prompt(prompt_id: str, user: CurrentUser, db: DbSession) -> None:
    prompt = db.get(models.PromptTemplate, prompt_id)
    if not prompt:
        raise not_found("提示词")
    # 删除基础风格时，把锁定它的稿件快照标记为"源已失效"（快照文本仍保留）。
    # 与历史 image_styles 删除语义一致，避免稿件侧静默引用已不存在的风格。
    if prompt.prompt_type == "base":
        db.execute(
            update(models.ContentItem)
            .where(models.ContentItem.base_style_id == prompt_id)
            .values(base_style_deleted=True)
        )
    db.delete(prompt)
    db.commit()
