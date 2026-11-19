"""全局风格库：基础/封面/正文配图提示词，供稿件锁定快照。"""

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

#: 出厂封面模板：Q版女仆抱桶 + 右侧双色块大标题。中文段直给国产生图模型，
#: 英文段供 Midjourney/DALL-E 3 取用，负向词内嵌（生图接口没有独立 negative 字段）。
#: 【】内是可替换占位：Studio 套用模板后由用户填当期主题与标题。
_FACTORY_COVER_PROMPT = """生成一张微信公众号封面图，横版 2.35:1 构图，自媒体爆款封面风格。画面左侧/中央放置一个超大的Q版二次元动漫女孩，蓝发，身穿女仆装，表情可爱且自信。她双手抱着一个巨大的红白条纹桶，桶里装满了【填写你的主题物品，例如：科技感十足的AI工具图标、放大镜、麦克风等】。人物和桶占据画面60%以上。背景是阳光明媚的乡村街道，景深虚化处理。
画面右侧留出空间，放置极具视觉冲击力的超大立体文字排版：上方是蓝底白字的倾斜标题框（代表【主标题，如：DeepSeek全家桶】），下方是黄底红字的粗体大字（代表【副标题，如：实测】），文字带有粗描边和动感线条。
整体色彩高饱和、高对比度，光影明亮干净，轮廓线清晰。确保缩小成正方形小图时，Q版人物和右侧的彩色大字号依然极其醒目、清晰可见。

EN (Midjourney / DALL-E 3): WeChat official account cover image, 2.35:1 horizontal composition, YouTube thumbnail style. Left side: A giant chibi anime girl with blue hair and a maid outfit, cute and confident expression, holding a large red-and-white striped bucket filled with 【your topic items, e.g., tech gadgets, magnifying glass, microphone】. The character and bucket take up 60% of the frame. Background: a sunny countryside street with blurred depth-of-field effect. Right side: Leave space for highly impactful, giant 3D typography blocks. Top block: blue background with white text, tilted. Bottom block: yellow background with red text, bold and dynamic, with thick outlines and action lines. High saturation, high contrast, bright lighting, crisp edges. Designed so the chibi character and the bold colorful text blocks remain highly recognizable in a small square thumbnail crop.

画面避免出现：小字、细节过多的背景、主体太小、人物靠边、低对比、暗淡的色彩、写实画风、文字排版混乱、没有色块背景的文字。"""

#: 启动时播种的出厂风格。sort_order=0 保证排在各用途列表最前。
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
    # 长度上限交给字段；空串与纯空白留给路由，统一报 invalid_name / invalid_prompt
    name: str | None = Field(default=None, max_length=40)
    purpose: _Purpose | None = None
    prompt: str | None = Field(default=None, max_length=2000)
    summary: str | None = Field(default=None, max_length=120)
    sort_order: int | None = Field(default=None, ge=0, le=9999)
    enabled: bool | None = None


class ImageStyleLockIn(schemas.CamelModel):
    style_id: str | None


def _out(row: models.ImageStyle) -> ImageStyleOut:
    return ImageStyleOut(
        id=row.id,
        name=row.name,
        purpose=row.purpose,
        prompt=row.prompt,
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


@router.get("", response_model=list[ImageStyleOut])
def list_image_styles(
    user: CurrentUser,
    db: DbSession,
    purpose: str = Query(),
    include_disabled: str | None = Query(default=None, alias="includeDisabled"),
) -> list[ImageStyleOut]:
    """任何登录用户。默认只返回启用项；includeDisabled 只有字面量 "1" 才含停用项。"""
    _require_purpose(purpose)
    stmt = select(models.ImageStyle).where(models.ImageStyle.purpose == purpose)
    if include_disabled == "1":
        _require_manage(user, db)
    else:
        stmt = stmt.where(models.ImageStyle.enabled.is_(True))
    rows = db.scalars(
        stmt.order_by(models.ImageStyle.sort_order.asc(), models.ImageStyle.id.asc())
    ).all()
    return [_out(row) for row in rows]


@router.post(
    "",
    response_model=ImageStyleOut,
    status_code=201,
    dependencies=[require_permission("image_styles.manage")],
)
def create_image_style(payload: ImageStyleIn, db: DbSession) -> ImageStyleOut:
    name = payload.name.strip()
    if not name:
        raise ApiError("invalid_name", "风格名称不能为空")
    prompt = payload.prompt.strip()
    if not prompt:
        raise ApiError("invalid_prompt", "风格提示词不能为空")
    row = models.ImageStyle(
        name=name,
        purpose=payload.purpose,
        prompt=prompt,
        summary=payload.summary.strip(),
        sort_order=payload.sort_order,
        enabled=payload.enabled,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _out(row)


@router.patch(
    "/{style_id}",
    response_model=ImageStyleOut,
    dependencies=[require_permission("image_styles.manage")],
)
def update_image_style(style_id: str, payload: ImageStylePatch, db: DbSession) -> ImageStyleOut:
    row = db.get(models.ImageStyle, style_id)
    if row is None:
        raise not_found("风格")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            raise ApiError("invalid_name", "风格名称不能为空")
        row.name = name
    if "prompt" in data:
        prompt = (data["prompt"] or "").strip()
        if not prompt:
            raise ApiError("invalid_prompt", "风格提示词不能为空")
        row.prompt = prompt
    if "purpose" in data:
        row.purpose = data["purpose"]
    if "summary" in data:
        row.summary = (data["summary"] or "").strip()
    if "sort_order" in data:
        row.sort_order = data["sort_order"]
    if "enabled" in data:
        row.enabled = data["enabled"]
    # 不回写已有稿件快照：名称与提示词只存在风格行上
    db.commit()
    db.refresh(row)
    return _out(row)


@router.delete(
    "/{style_id}",
    status_code=204,
    dependencies=[require_permission("image_styles.manage")],
)
def delete_image_style(style_id: str, db: DbSession) -> None:
    row = db.get(models.ImageStyle, style_id)
    if row is None:
        raise not_found("风格")
    db.execute(
        update(models.ContentItem)
        .where(models.ContentItem.base_style_id == style_id)
        .values(base_style_deleted=True)
    )
    db.delete(row)
    db.commit()


def seed_factory_styles(db) -> None:
    """启动时播种出厂风格：按 (purpose, name) 补缺，存在即跳过，绝不覆盖已有行。

    与内置 Skill 同语义——删了会在下次重启恢复。管理员想永久去掉某条出厂风格，
    应当停用（播种见同名行存在就不再插）或改名（视同自定义行）；改提示词不会被回滚。
    """
    inserted = False
    for spec in FACTORY_STYLES:
        exists = db.scalar(
            select(models.ImageStyle.id).where(
                models.ImageStyle.purpose == spec["purpose"],
                models.ImageStyle.name == spec["name"],
            )
        )
        if exists is not None:
            continue
        db.add(models.ImageStyle(**spec))
        inserted = True
    if inserted:
        db.commit()
