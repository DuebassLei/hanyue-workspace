"""用户偏好服务端持久化（设计 §5.4）。

主题、全局默认文本模型与全局默认生图模型都按用户存服务端，换设备/换浏览器登录后
自动同步。无记录时返回 theme=system、source=default，不假装服务端已有值。

PATCH 是部分更新：只改传了的字段。主题、默认文本模型、默认生图模型互不覆盖。
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from .. import models, schemas
from ..core.deps import ApiError, CurrentUser, DbSession
from ..services import ai_gateway, image_provider

router = APIRouter(tags=["preferences"])


def _preference_out(
    db, user_id: str, row: models.UserPreference | None
) -> schemas.PreferenceOut:
    info = ai_gateway.describe_default_model(db, user_id)
    image_info = image_provider.describe_default_image_model(db, user_id)
    image_preferences = schemas.StudioImagePreferences.model_validate(
        (row.studio_image_preferences if row else {}) or {}
    )
    return schemas.PreferenceOut(
        theme=row.theme if row else "system",
        source="server" if row else "default",
        default_text_model_id=row.default_text_model_id if row else None,
        default_text_model_label=info["label"],
        default_text_model_ignored=info["ignored"],
        default_image_model_id=row.default_image_model_id if row else None,
        default_image_model_label=image_info["label"],
        default_image_model_ignored=image_info["ignored"],
        studio_image_preferences=image_preferences,
    )


@router.get("/preferences", response_model=schemas.PreferenceOut)
def get_preferences(user: CurrentUser, db: DbSession) -> schemas.PreferenceOut:
    row = db.scalar(
        select(models.UserPreference).where(
            models.UserPreference.owner_user_id == user.id
        )
    )
    return _preference_out(db, user.id, row)


@router.patch("/preferences", response_model=schemas.PreferenceOut)
def update_preferences(
    payload: schemas.PreferencePatch, user: CurrentUser, db: DbSession
) -> schemas.PreferenceOut:
    data = payload.model_dump(exclude_unset=True)
    row = db.scalar(
        select(models.UserPreference).where(
            models.UserPreference.owner_user_id == user.id
        )
    )
    if row is None:
        row = models.UserPreference(
            owner_user_id=user.id, theme=data.get("theme") or "system"
        )
        db.add(row)

    if data.get("theme") is not None:
        row.theme = data["theme"]

    if "default_text_model_id" in data:
        model_id = str(data["default_text_model_id"] or "").strip() or None
        if model_id:
            # 显式「设为默认」必须指向当前用户可用的文本模型；越权一律拒绝。
            try:
                ai_gateway.resolve_selection(db, user.id, model_id=model_id)
            except ai_gateway.InvalidModelSelection as exc:
                raise ApiError(exc.code, exc.message, status_code=400) from exc
        row.default_text_model_id = model_id

    if "default_image_model_id" in data:
        image_model_id = str(data["default_image_model_id"] or "").strip() or None
        if image_model_id:
            # 同口径：必须是本人的、启用的、登记了 image.generate 且凭据齐全的模型。
            reason = image_provider.default_image_model_reason(db, user.id, image_model_id)
            if reason:
                detail = image_provider.DEFAULT_IMAGE_IGNORE_MESSAGES.get(reason, reason)
                raise ApiError(
                    "model_not_found",
                    f"指定的生图模型不可用（{detail}）；请重新选择或清空后按自动顺序选择",
                    status_code=400,
                )
        row.default_image_model_id = image_model_id

    if "studio_image_preferences" in data:
        image_preferences = data["studio_image_preferences"]
        row.studio_image_preferences = (
            image_preferences.model_dump(exclude_unset=False)
            if hasattr(image_preferences, "model_dump")
            else dict(image_preferences or {})
        )

    db.commit()
    db.refresh(row)
    return _preference_out(db, user.id, row)
