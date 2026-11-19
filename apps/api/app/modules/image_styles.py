"""全局风格库 —— 兼容壳（DEPRECATED）。

原 ``image_styles`` 表的能力已合并进统一的 ``prompt_templates``。
本模块保留 ``/image-styles`` 路由，以 ``prompt_templates`` 为准，同时向旧的
``image_styles`` 表做双写（两表共用同一 id），供尚未迁移的调用方（如 content.py
的基础风格锁定）过渡使用；新代码请直接使用 ``/prompts``。

purpose → prompt_type 映射：base→base、cover→cover、inline→illustration。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import Field
from sqlalchemy import select, update

from .. import models, schemas
from ..core.deps import ApiError, CurrentUser, DbSession, not_found
from ..core.rbac import require_permission, resolve_access

router = APIRouter(prefix="/image-styles", tags=["image-styles"])

_PURPOSES = ("base", "cover", "inline")
_Purpose = Literal["base", "cover", "inline"]
_PURPOSE_TO_TYPE = {"base": "base", "cover": "cover", "inline": "illustration"}
_TYPE_TO_PURPOSE = {value: key for key, value in _PURPOSE_TO_TYPE.items()}

#: 出厂封面模板中「画面避免出现：…」的负向标记；迁移与播种都据此切分正/负向。
NEGATIVE_MARKER = "画面避免出现："

#: 出厂封面模板：Q版女仆抱桶 + 右侧双色块大标题。中文段直给国产生图模型，
#: 英文段供 Midjourney/DALL-E 3 取用；【】内是可替换占位，由用户填当期主题与标题。
_FACTORY_COVER_PROMPT = """生成一张微信公众号封面图，横版 2.35:1 构图，自媒体爆款封面风格。画面左侧/中央放置一个超大的Q版二次元动漫女孩，蓝发，身穿女仆装，表情可爱且自信。她双手抱着一个巨大的红白条纹桶，桶里装满了【填写你的主题物品，例如：科技感十足的AI工具图标、放大镜、麦克风等】。人物和桶占据画面60%以上。背景是阳光明媚的乡村街道，景深虚化处理。
画面右侧留出空间，放置极具视觉冲击力的超大立体文字排版：上方是蓝底白字的倾斜标题框（代表【主标题，如：DeepSeek全家桶】），下方是黄底红字的粗体大字（代表【副标题，如：实测】），文字带有粗描边和动感线条。
整体色彩高饱和、高对比度，光影明亮干净，轮廓线清晰。确保缩小成正方形小图时，Q版人物和右侧的彩色大字号依然极其醒目、清晰可见。

EN (Midjourney / DALL-E 3): WeChat official account cover image, 2.35:1 horizontal composition, YouTube thumbnail style. Left side: A giant chibi anime girl with blue hair and a maid outfit, cute and confident expression, holding a large red-and-white striped bucket filled with 【your topic items, e.g., tech gadgets, magnifying glass, microphone】. The character and bucket take up 60% of the frame. Background: a sunny countryside street with blurred depth-of-field effect. Right side: Leave space for highly impactful, giant 3D typography blocks. Top block: blue background with white text, tilted. Bottom block: yellow background with red text, bold and dynamic, with thick outlines and action lines. High saturation, high contrast, bright lighting, crisp edges. Designed so the chibi character and the bold colorful text blocks remain highly recognizable in a small square thumbnail crop.

画面避免出现：小字、细节过多的背景、主体太小、人物靠边、低对比、暗淡的色彩、写实画风、文字排版混乱、没有色块背景的文字。"""

#: 启动时播种的出厂风格（now 写入 prompt_templates）。sort_order=0 保证排在最前。
FACTORY_STYLES: tuple[dict, ...] = (
    {
        "name": "公众号爆款封面·Q版女仆桶",
        "purpose": "cover",
        "prompt": _FACTORY_COVER_PROMPT,
        "summary": "Q版女仆抱桶占六成，右侧蓝/黄双色块大标题，缩到方图仍醒目",
        "sort_order": 0,
    },
)


class ImageStyleOut(schemas.CamelModel):
    id: str
    name: str
    purpose: str
    prompt: str
    # 拆分后的负向词（正向仍只含画面描述）。旧调用方可忽略该字段。
    negative_content: str = ""
    summary: str
    sort_order: int
    enabled: bool
    created_at: datetime
    updated_at: datetime


class ImageStyleIn(schemas.CamelModel):
    name: str = Field(min_length=1, max_length=40)
    purpose: _Purpose
    prompt: str = Field(min_length=1, max_length=2000)
    summary: str = Field(default="", max_length=120)
    sort_order: int = Field(default=0, ge=0, le=9999)
    enabled: bool = True


class ImageStylePatch(schemas.CamelModel):
    name: str | None = Field(default=None, max_length=40)
    purpose: _Purpose | None = None
    prompt: str | None = Field(default=None, max_length=2000)
    summary: str | None = Field(default=None, max_length=120)
    sort_order: int | None = Field(default=None, ge=0, le=9999)
    enabled: bool | None = None


class ImageStyleLockIn(schemas.CamelModel):
    style_id: str | None


def split_negative(prompt: str) -> tuple[str, str]:
    """把「…画面避免出现：…」拆成 (正向, 负向)；没有标记时负向为空。"""
    text = prompt or ""
    pos = text.find(NEGATIVE_MARKER)
    if pos < 0:
        return text.strip(), ""
    return text[:pos].strip(), text[pos + len(NEGATIVE_MARKER):].strip()


def _out(row: models.PromptTemplate) -> ImageStyleOut:
    return ImageStyleOut(
        id=row.id,
        name=row.title,
        purpose=_TYPE_TO_PURPOSE.get(row.prompt_type, "base"),
        prompt=row.content,
        negative_content=row.negative_content or "",
        summary=row.summary or "",
        sort_order=row.sort_order,
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _require_purpose(purpose: str) -> str:
    if purpose not in _PURPOSES:
        raise ApiError("invalid_purpose", "用途必须是 base、cover 或 inline")
    return purpose


def _require_manage(user: models.User, db: DbSession) -> None:
    """只在 includeDisabled=1 时调用，不能挂在路由上，否则普通用户连启用列表也 403。"""
    if not resolve_access(db, user).can("image_styles.manage"):
        raise ApiError("permission_denied", "缺少权限：image_styles.manage", status_code=403)


@router.get("", response_model=list[ImageStyleOut], deprecated=True)
def list_image_styles(
    user: CurrentUser,
    db: DbSession,
    purpose: str = Query(),
    include_disabled: str | None = Query(default=None, alias="includeDisabled"),
) -> list[ImageStyleOut]:
    """兼容壳：从统一提示词库按类型列风格；默认只返回启用项。"""
    _require_purpose(purpose)
    prompt_type = _PURPOSE_TO_TYPE[purpose]
    stmt = select(models.PromptTemplate).where(models.PromptTemplate.prompt_type == prompt_type)
    if include_disabled == "1":
        _require_manage(user, db)
    else:
        stmt = stmt.where(models.PromptTemplate.enabled.is_(True))
    rows = db.scalars(
        stmt.order_by(models.PromptTemplate.sort_order.asc(), models.PromptTemplate.id.asc())
    ).all()
    return [_out(row) for row in rows]


@router.post(
    "", response_model=ImageStyleOut, status_code=201,
    dependencies=[require_permission("image_styles.manage")], deprecated=True,
)
def create_image_style(payload: ImageStyleIn, db: DbSession) -> ImageStyleOut:
    name = payload.name.strip()
    if not name:
        raise ApiError("invalid_name", "风格名称不能为空")
    positive, negative = split_negative(payload.prompt.strip())
    if not positive:
        raise ApiError("invalid_prompt", "风格提示词不能为空")
    # 过渡双写：旧表保留完整 prompt（含负向标记），两表共用同一 id。
    legacy = models.ImageStyle(
        name=name,
        purpose=payload.purpose,
        prompt=payload.prompt.strip(),
        summary=payload.summary.strip(),
        sort_order=payload.sort_order,
        enabled=payload.enabled,
    )
    db.add(legacy)
    db.flush()
    row = models.PromptTemplate(
        id=legacy.id,
        owner_user_id=None,
        title=name,
        category="风格",
        content=positive,
        tags=["style", payload.purpose],
        prompt_type=_PURPOSE_TO_TYPE[payload.purpose],
        negative_content=negative,
        summary=payload.summary.strip(),
        sort_order=payload.sort_order,
        enabled=payload.enabled,
        source="manual",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _out(row)


@router.patch(
    "/{style_id}", response_model=ImageStyleOut,
    dependencies=[require_permission("image_styles.manage")], deprecated=True,
)
def update_image_style(style_id: str, payload: ImageStylePatch, db: DbSession) -> ImageStyleOut:
    row = db.get(models.PromptTemplate, style_id)
    if row is None:
        raise not_found("风格")
    legacy = db.get(models.ImageStyle, style_id)  # 过渡双写，旧表缺行时跳过
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            raise ApiError("invalid_name", "风格名称不能为空")
        row.title = name
        if legacy is not None:
            legacy.name = name
    if "prompt" in data:
        positive, negative = split_negative((data["prompt"] or "").strip())
        if not positive:
            raise ApiError("invalid_prompt", "风格提示词不能为空")
        row.content = positive
        row.negative_content = negative
        if legacy is not None:
            legacy.prompt = (data["prompt"] or "").strip()
    if "purpose" in data and data["purpose"]:
        row.prompt_type = _PURPOSE_TO_TYPE[data["purpose"]]
        if legacy is not None:
            legacy.purpose = data["purpose"]
    if "summary" in data:
        row.summary = (data["summary"] or "").strip()
        if legacy is not None:
            legacy.summary = (data["summary"] or "").strip()
    if data.get("sort_order") is not None:
        row.sort_order = data["sort_order"]
        if legacy is not None:
            legacy.sort_order = data["sort_order"]
    if data.get("enabled") is not None:
        row.enabled = data["enabled"]
        if legacy is not None:
            legacy.enabled = data["enabled"]
    # 不回写已有稿件快照：名称与提示词只存在提示词行上
    db.commit()
    db.refresh(row)
    return _out(row)


@router.delete(
    "/{style_id}", status_code=204,
    dependencies=[require_permission("image_styles.manage")], deprecated=True,
)
def delete_image_style(style_id: str, db: DbSession) -> None:
    row = db.get(models.PromptTemplate, style_id)
    if row is None:
        raise not_found("风格")
    db.execute(
        update(models.ContentItem)
        .where(models.ContentItem.base_style_id == style_id)
        .values(base_style_deleted=True)
    )
    legacy = db.get(models.ImageStyle, style_id)
    if legacy is not None:
        db.delete(legacy)
    db.delete(row)
    db.commit()


def seed_factory_styles(db) -> None:
    """启动播种出厂风格到统一提示词库：按 (prompt_type, title) 补缺，存在即跳过。

    与内置 Skill 同语义——删了会在下次重启恢复。管理员想永久去掉某条出厂风格，
    应当停用（播种见同名行存在就不再插）或改名（视同自定义行）；改提示词不会被回滚。
    出厂封面模板的「画面避免出现：…」在播种时即拆入 negative_content。
    """
    inserted = False
    for spec in FACTORY_STYLES:
        prompt_type = _PURPOSE_TO_TYPE[spec["purpose"]]
        exists = db.scalar(
            select(models.PromptTemplate.id).where(
                models.PromptTemplate.prompt_type == prompt_type,
                models.PromptTemplate.title == spec["name"],
            )
        )
        if exists is not None:
            continue
        positive, negative = split_negative(spec["prompt"])
        # 过渡双写：旧表保留完整 prompt（含负向标记），两表共用同一 id。
        legacy = models.ImageStyle(
            name=spec["name"],
            purpose=spec["purpose"],
            prompt=spec["prompt"],
            summary=spec["summary"],
            sort_order=spec["sort_order"],
            enabled=True,
        )
        db.add(legacy)
        db.flush()
        db.add(
            models.PromptTemplate(
                id=legacy.id,
                owner_user_id=None,
                title=spec["name"],
                category="封面",
                content=positive,
                tags=["style", "factory", spec["purpose"]],
                prompt_type=prompt_type,
                negative_content=negative,
                summary=spec["summary"],
                sort_order=spec["sort_order"],
                enabled=True,
                source="factory",
            )
        )
        inserted = True
    if inserted:
        db.commit()
