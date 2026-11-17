"""图文卡片：分页 AST 接口 + 自定义模板持久化。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy import select

from .. import models, schemas
from ..core.deps import CurrentUser, DbSession, not_found
from ..services import card_paginator

from ..core.rbac import require_permission

router = APIRouter(tags=["cards"], dependencies=[require_permission("cards.use")])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@router.get("/cards/presets")
def list_presets(user: CurrentUser) -> dict:
    """平台画幅预设与内置模板清单（前端常量以后端为同一份契约）。"""
    return {
        "presets": card_paginator.PLATFORM_PRESETS,
        "templates": card_paginator.BUILTIN_TEMPLATES,
    }


@router.post("/cards/paginate")
def paginate_cards(payload: schemas.CardPaginateIn, user: CurrentUser) -> dict:
    preset = card_paginator.find_preset(payload.preset_id)
    if payload.custom_width:
        preset["width"] = payload.custom_width
    if payload.custom_height:
        preset["height"] = payload.custom_height

    template = card_paginator.resolve_template(payload.template_id, payload.custom_template)
    meta = card_paginator.resolve_meta(payload.meta)

    cards = card_paginator.paginate_markdown_to_cards(
        payload.markdown, preset, template, meta, payload.pagination_mode
    )

    return {
        "status": "ok",
        "timestamp": _now_iso(),
        "renderSpec": {
            "canvasWidth": preset["width"],
            "canvasHeight": preset["height"],
            "exportWidth": preset["width"] * preset["exportScale"],
            "exportHeight": preset["height"] * preset["exportScale"],
            "exportScale": preset["exportScale"],
            "ratioLabel": preset["ratioLabel"],
            "paginationMode": payload.pagination_mode,
        },
        "template": template,
        "meta": meta,
        "totalCards": len(cards),
        "cards": cards,
    }


@router.get("/cards/templates", response_model=list[schemas.CardTemplateOut])
def list_templates(user: CurrentUser, db: DbSession) -> list[models.CardTemplate]:
    return list(
        db.scalars(
            select(models.CardTemplate)
            .where(models.CardTemplate.owner_user_id == user.id)
            .order_by(models.CardTemplate.created_at.desc())
        )
    )


@router.post(
    "/cards/templates", response_model=schemas.CardTemplateOut, status_code=status.HTTP_201_CREATED
)
def create_template(
    payload: schemas.CardTemplateIn, user: CurrentUser, db: DbSession
) -> models.CardTemplate:
    template = models.CardTemplate(
        owner_user_id=user.id,
        name=payload.name,
        subtitle=payload.subtitle,
        config=payload.config,
    )
    db.add(template)
    db.commit()
    return template


@router.patch("/cards/templates/{template_id}", response_model=schemas.CardTemplateOut)
def update_template(
    template_id: str,
    payload: schemas.CardTemplatePatch,
    user: CurrentUser,
    db: DbSession,
) -> models.CardTemplate:
    template = db.get(models.CardTemplate, template_id)
    if not template or template.owner_user_id != user.id:
        raise not_found("模板")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(template, key, value)
    db.commit()
    return template


@router.delete("/cards/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: str, user: CurrentUser, db: DbSession) -> None:
    template = db.get(models.CardTemplate, template_id)
    if not template or template.owner_user_id != user.id:
        raise not_found("模板")
    db.delete(template)
    db.commit()
