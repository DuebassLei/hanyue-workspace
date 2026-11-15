"""用户偏好服务端持久化（设计 §5.4）。

主题与「全局默认文本模型」都按用户存服务端，换设备/换浏览器登录后自动同步。
无记录时返回 theme=system、source=default，不假装服务端已有值。

PATCH 是部分更新：只改传了的字段。主题与默认模型互不覆盖。
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from .. import models, schemas
from ..core.deps import ApiError, CurrentUser, DbSession
from ..services import ai_gateway

router = APIRouter(tags=["preferences"])


def _preference_out(
    db, user_id: str, row: models.UserPreference | None
) -> schemas.PreferenceOut:
    info = ai_gateway.describe_default_model(db, user_id)
    return schemas.PreferenceOut(
        theme=row.theme if row else "system",
        source="server" if row else "default",
        default_text_model_id=row.default_text_model_id if row else None,
        default_text_model_label=info["label"],
        default_text_model_ignored=info["ignored"],
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

    db.commit()
    db.refresh(row)
    return _preference_out(db, user.id, row)
