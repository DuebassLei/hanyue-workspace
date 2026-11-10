from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Header
from sqlalchemy import select

from .. import models, schemas
from ..core.config import get_settings
from ..core.deps import ApiError, CurrentUser, DbSession, default_workspace
from ..core.security import create_token, decode_token

router = APIRouter(tags=["auth"])
settings = get_settings()


@router.post("/auth/login", response_model=schemas.LoginResponse)
def login(payload: schemas.LoginRequest, db: DbSession) -> schemas.LoginResponse:
    """开发/单点登录入口：按 subject 查找或创建本地用户。

    这是**开发态便利入口**：任何人都能用任意 subject 换取该身份的令牌。
    生产必须设置 ENVIRONMENT=production 且 ALLOW_DEV_LOGIN=false（启动门禁会强制校验），
    并改接真实 IdP / OIDC，由可信断言提供 auth_subject，绝不能信任请求体。
    """
    if not settings.allow_dev_login:
        raise ApiError(
            "dev_login_disabled",
            "开发态登录已关闭，请联系管理员接入正式身份提供者",
            status_code=403,
        )
    user = db.scalar(select(models.User).where(models.User.auth_subject == payload.subject))
    if user is None:
        user = models.User(
            auth_subject=payload.subject, display_name=payload.display_name or "创作者"
        )
        db.add(user)
        db.flush()
    workspace = default_workspace(db, user)
    db.commit()
    return schemas.LoginResponse(
        token=create_token(user.id),
        user=schemas.UserOut(
            id=user.id, display_name=user.display_name, timezone=user.timezone
        ),
        workspace_id=workspace.id,
    )


@router.get("/auth/me", response_model=schemas.UserOut)
def me(user: CurrentUser) -> schemas.UserOut:
    return schemas.UserOut(id=user.id, display_name=user.display_name, timezone=user.timezone)


@router.post("/auth/logout")
def logout(
    user: CurrentUser,
    db: DbSession,
    authorization: str | None = Header(default=None),
) -> dict:
    """吊销当前令牌：jti 落库后任何携带该令牌的请求都会 401。"""
    body = decode_token((authorization or "").split(" ", 1)[-1].strip()) or {}
    jti = body.get("jti")
    if jti:
        expires_at = datetime.fromtimestamp(int(body.get("exp", 0)), tz=timezone.utc)
        if not db.get(models.RevokedToken, jti):
            db.add(
                models.RevokedToken(
                    jti=jti, owner_user_id=user.id, expires_at=expires_at
                )
            )
            db.commit()
    return {"status": "logged_out", "revoked": bool(jti)}
