"""手绘风格库：内置只读列表、参考图/色卡直出与提示词合成。"""

from __future__ import annotations

import math

from fastapi import APIRouter, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from .. import models
from ..core.deps import ApiError, CurrentUser, DbSession, not_found
from ..services import handdraw as handdraw_service
from ..services import image_provider

router = APIRouter(prefix="/handdraw", tags=["handdraw"])


class ComposeIn(BaseModel):
    styleNumber: str
    theme: str = ""
    colorId: str | None = None
    accentColorId: str | None = None
    modelId: str | None = None
    model: str | None = None
    language: str = "auto"


def _style_out(
    style: models.HanddrawStyle, activation: handdraw_service.Activation | None = None
) -> dict:
    return {
        "number": style.style_number,
        "legacyNumber": style.legacy_number,
        "groupKey": style.group_key,
        "groupName": style.group_name,
        "referenceAuthor": style.reference_author,
        "generationName": style.generation_name,
        "referenceAvailable": style.reference_available,
        "activationSource": activation.activation_source if activation else None,
        "needReference": activation.need_reference if activation else None,
    }


def _color_out(color: models.HanddrawColor) -> dict:
    return {
        "id": color.color_id,
        "category": color.category,
        "categoryZh": color.category_zh,
        "categoryEn": color.category_en,
        "nameZh": color.name_zh,
        "nameEn": color.name_en,
        "quoteZh": color.quote_zh,
        "quoteEn": color.quote_en,
        "swatchUrl": f"/handdraw/colors/{color.color_id}/swatch" if color.swatch_file else None,
    }


def _find_style(db: DbSession, number: str) -> models.HanddrawStyle:
    try:
        canonical = handdraw_service.normalize_style_number(number)
    except ValueError as exc:
        raise ApiError("invalid_style", str(exc)) from exc
    style = db.scalar(
        select(models.HanddrawStyle).where(models.HanddrawStyle.style_number == canonical)
    )
    if not style:
        raise not_found("手绘风格")
    return style


def _find_color(db: DbSession, color_id: str | None) -> models.HanddrawColor | None:
    if not color_id:
        return None
    text = color_id.strip().upper()
    if text.isdigit():
        text = f"C-{int(text):02}"
    elif not text.startswith("C-"):
        text = f"C-{text}"
    color = db.scalar(select(models.HanddrawColor).where(models.HanddrawColor.color_id == text))
    if not color:
        raise ApiError("invalid_color", f"主题色不存在：{color_id}")
    return color


def _model_code(db: DbSession, user: models.User, model_id: str | None, model: str | None) -> str:
    """优先用供应商登记的模型代号，其次用调用方显式传入的模型名。"""
    provider = image_provider.resolve_image_provider(db, user.id, model_id)
    if provider and provider.get("modelCode"):
        return str(provider["modelCode"])
    return handdraw_service.normalize_model_code(model or model_id)


@router.get("/meta")
def get_meta(user: CurrentUser, db: DbSession) -> dict:
    meta = db.scalar(
        select(models.HanddrawLibraryMeta).where(
            models.HanddrawLibraryMeta.library_key == handdraw_service.LIBRARY_KEY
        )
    )
    return {
        "version": meta.version if meta else "",
        "styleCount": meta.style_count if meta else 0,
        "colorCount": meta.color_count if meta else 0,
        "referenceCount": meta.reference_count if meta else 0,
        "missingReferences": meta.missing_references if meta else [],
        "attribution": {
            "author": "yang0",
            "repository": "https://github.com/yang0/handraw-style",
            "license": "MIT with Attribution Requirement",
        },
    }


@router.get("/styles")
def list_styles(
    user: CurrentUser,
    db: DbSession,
    q: str = "",
    group: str = "",
    hasReference: bool | None = None,
    page: int = Query(1, ge=1),
    pageSize: int = Query(24, ge=1, le=200),
    modelId: str | None = None,
) -> dict:
    stmt = select(models.HanddrawStyle)
    if q.strip():
        keyword = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                models.HanddrawStyle.generation_name.ilike(keyword),
                models.HanddrawStyle.reference_author.ilike(keyword),
                models.HanddrawStyle.traits_raw.ilike(keyword),
                models.HanddrawStyle.style_number.ilike(keyword),
                models.HanddrawStyle.legacy_number.ilike(keyword),
            )
        )
    if group.strip():
        stmt = stmt.where(models.HanddrawStyle.group_key == group.strip().upper())
    if hasReference is not None:
        stmt = stmt.where(models.HanddrawStyle.reference_available == hasReference)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        db.scalars(
            stmt.order_by(models.HanddrawStyle.sort_order)
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
    )
    model_code = _model_code(db, user, modelId, None)
    return {
        "items": [
            _style_out(row, handdraw_service.resolve_activation(row, model_code)) for row in rows
        ],
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "pages": math.ceil(total / pageSize) if total else 0,
        "groups": _groups(db),
    }


def _groups(db: DbSession) -> list[dict]:
    rows = db.execute(
        select(
            models.HanddrawStyle.group_key,
            models.HanddrawStyle.group_name,
            func.count(models.HanddrawStyle.id),
        )
        .group_by(models.HanddrawStyle.group_key, models.HanddrawStyle.group_name)
        .order_by(models.HanddrawStyle.group_key)
    ).all()
    return [{"key": key, "name": name, "count": count} for key, name, count in rows]


@router.get("/styles/{number}")
def get_style(number: str, user: CurrentUser, db: DbSession, modelId: str | None = None) -> dict:
    style = _find_style(db, number)
    model_code = _model_code(db, user, modelId, None)
    activation = handdraw_service.resolve_activation(style, model_code)
    data = _style_out(style, activation)
    data.update(
        {
            "traits": activation.traits,
            "referenceUrl": (
                f"/handdraw/styles/{style.style_number}/reference"
                if style.reference_available
                else None
            ),
        }
    )
    return data


@router.get("/styles/{number}/reference")
def get_style_reference(number: str, user: CurrentUser, db: DbSession) -> FileResponse:
    style = _find_style(db, number)
    path = handdraw_service.resolve_reference_file(style)
    if not path:
        raise ApiError("reference_missing", "该风格缺少参考图", status.HTTP_404_NOT_FOUND)
    return FileResponse(path, media_type="image/webp")


@router.get("/colors")
def list_colors(user: CurrentUser, db: DbSession) -> list[dict]:
    rows = db.scalars(select(models.HanddrawColor).order_by(models.HanddrawColor.sort_order))
    return [_color_out(row) for row in rows]


@router.get("/colors/{color_id}/swatch")
def get_color_swatch(color_id: str, user: CurrentUser, db: DbSession) -> FileResponse:
    color = _find_color(db, color_id)
    if not color or not color.swatch_file:
        raise not_found("色卡")
    path = handdraw_service.LIBRARY_DIR / color.swatch_file
    if not path.exists():
        raise not_found("色卡")
    return FileResponse(path, media_type="image/webp")


@router.post("/compose")
def compose(payload: ComposeIn, user: CurrentUser, db: DbSession) -> dict:
    style = _find_style(db, payload.styleNumber)
    theme = (payload.theme or "").strip()
    if not theme:
        raise ApiError("invalid_theme", "请填写画面主题")
    color = _find_color(db, payload.colorId)
    accent = _find_color(db, payload.accentColorId)
    model_code = _model_code(db, user, payload.modelId, payload.model)
    activation = handdraw_service.resolve_activation(style, model_code)
    # 供应商已确认不支持参考图时，提示词不能再写「随请求自动附加参考图」——
    # 那张图根本不会发出去，承诺会误导模型也误导用户。
    edits_supported = image_provider.provider_supports_edits(db, user.id, payload.modelId)
    attach_reference = activation.need_reference and edits_supported is not False
    prompts = handdraw_service.compose_prompt(
        style, activation, theme, color, accent, attach_reference=attach_reference
    )
    return {
        "style": _style_out(style, activation),
        "color": (
            {"id": color.color_id, "nameZh": color.name_zh, "nameEn": color.name_en}
            if color
            else None
        ),
        "accentColor": (
            {"id": accent.color_id, "nameZh": accent.name_zh, "nameEn": accent.name_en}
            if accent
            else None
        ),
        "modelCode": activation.model_code,
        "promptZh": prompts["promptZh"],
        "promptEn": prompts["promptEn"],
        "activationSource": activation.activation_source,
        "includeTraits": activation.include_traits,
        "needReference": activation.need_reference,
        "referenceAvailable": style.reference_available,
        "language": prompts["language"],
    }
