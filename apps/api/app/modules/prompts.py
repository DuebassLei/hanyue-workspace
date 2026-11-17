"""提示词库：用户维护的生图提示词模板，供图片生成页快速套用。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import select

from .. import models, schemas
from ..core.deps import ApiError, CurrentUser, DbSession, not_found

from ..core.rbac import require_permission

router = APIRouter(prefix="/prompts", tags=["prompts"], dependencies=[require_permission("prompts.use")])

DEFAULT_CATEGORY = "通用"


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


@router.get("", response_model=list[schemas.PromptTemplateOut])
def list_prompts(user: CurrentUser, db: DbSession) -> list[models.PromptTemplate]:
    return list(
        db.scalars(
            select(models.PromptTemplate)
            .where(models.PromptTemplate.owner_user_id == user.id)
            .order_by(models.PromptTemplate.updated_at.desc())
        )
    )


@router.post(
    "", response_model=schemas.PromptTemplateOut, status_code=status.HTTP_201_CREATED
)
def create_prompt(
    payload: schemas.PromptTemplateIn, user: CurrentUser, db: DbSession
) -> models.PromptTemplate:
    title = payload.title.strip()
    if not title:
        raise ApiError("invalid_title", "提示词名称不能为空")
    if not payload.content.strip():
        raise ApiError("invalid_prompt", "提示词内容不能为空")
    prompt = models.PromptTemplate(
        owner_user_id=user.id,
        title=title,
        category=_clean_category(payload.category),
        content=payload.content.strip(),
        tags=_clean_tags(payload.tags),
    )
    db.add(prompt)
    db.commit()
    return prompt


@router.patch("/{prompt_id}", response_model=schemas.PromptTemplateOut)
def update_prompt(
    prompt_id: str,
    payload: schemas.PromptTemplatePatch,
    user: CurrentUser,
    db: DbSession,
) -> models.PromptTemplate:
    prompt = db.get(models.PromptTemplate, prompt_id)
    if not prompt or prompt.owner_user_id != user.id:
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
    db.commit()
    return prompt


@router.delete("/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_prompt(prompt_id: str, user: CurrentUser, db: DbSession) -> None:
    prompt = db.get(models.PromptTemplate, prompt_id)
    if not prompt or prompt.owner_user_id != user.id:
        raise not_found("提示词")
    db.delete(prompt)
    db.commit()
