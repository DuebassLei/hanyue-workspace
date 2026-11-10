"""用户偏好服务端持久化（设计 §5.4）。

主题不再只存 localStorage：服务端按用户保存，换设备/换浏览器登录后自动同步。
无记录时返回 theme=system 且 source=default，不假装服务端已有值。
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from .. import models, schemas
from ..core.deps import CurrentUser, DbSession

router = APIRouter(tags=["preferences"])


def _preference_out(row: models.UserPreference | None) -> schemas.PreferenceOut:
    if row is None:
        return schemas.PreferenceOut(theme="system", source="default")
    return schemas.PreferenceOut(theme=row.theme, source="server")


@router.get("/preferences", response_model=schemas.PreferenceOut)
def get_preferences(user: CurrentUser, db: DbSession) -> schemas.PreferenceOut:
    row = db.scalar(
        select(models.UserPreference).where(
            models.UserPreference.owner_user_id == user.id
        )
    )
    return _preference_out(row)


@router.patch("/preferences", response_model=schemas.PreferenceOut)
def update_preferences(
    payload: schemas.PreferencePatch, user: CurrentUser, db: DbSession
) -> schemas.PreferenceOut:
    row = db.scalar(
        select(models.UserPreference).where(
            models.UserPreference.owner_user_id == user.id
        )
    )
    if row is None:
        row = models.UserPreference(owner_user_id=user.id, theme=payload.theme)
        db.add(row)
    else:
        row.theme = payload.theme
    db.commit()
    db.refresh(row)
    return _preference_out(row)
