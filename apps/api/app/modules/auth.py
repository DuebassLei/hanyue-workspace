from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Header
from sqlalchemy import select

from .. import models, schemas
from ..core.config import get_settings
from ..core.deps import ApiError, CurrentUser, DbSession, default_workspace
from ..core.logging import get_logger
from ..core.security import create_token, decode_token

router = APIRouter(tags=["auth"])
settings = get_settings()
logger = get_logger(__name__)

# 形如 Asia/Shanghai、America/Argentina/Buenos_Aires，或 UTC；不校验该时区在当前系统是否可解析
# （Windows 上缺 tzdata 时 zoneinfo 会抛错，不能因此把一个合法时区判成非法）。
_TZ_PATTERN = re.compile(r"^(?:UTC|[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+)*)$")


def _provider() -> str:
    """身份来源：开发态登录开关打开时，账号是本地 subject 换来的；关闭时来自真实 IdP。"""
    return "local-dev" if settings.allow_dev_login else "idp"


def _user_out(user: models.User) -> schemas.UserOut:
    return schemas.UserOut(
        id=user.id,
        display_name=user.display_name,
        timezone=user.timezone,
        auth_subject=user.auth_subject,
        auth_provider=_provider(),
        created_at=user.created_at,
    )


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
    # 系统级定时任务（拉热点等）需要一个真实 owner 才能落 background_jobs
    # （owner_user_id 是必填外键），所以绑到首个登录用户。多用户下只有第一条会留下。
    # 失败不能影响登录：调度器出问题只记日志，不把用户挡在门外。
    try:
        from ..services import scheduled_tasks

        scheduled_tasks.ensure_system_tasks(db, user.id, user.timezone)
    except Exception as exc:  # pragma: no cover - 兜底不影响主流程
        logger.warning("播种系统定时任务失败（不影响登录）：%s", exc)
    return schemas.LoginResponse(
        token=create_token(user.id), user=_user_out(user), workspace_id=workspace.id
    )


@router.get("/auth/me", response_model=schemas.UserOut)
def me(user: CurrentUser) -> schemas.UserOut:
    return _user_out(user)


@router.patch("/auth/me", response_model=schemas.UserOut)
def update_me(
    payload: schemas.ProfilePatch, user: CurrentUser, db: DbSession
) -> schemas.UserOut:
    """更新本人资料。

    身份字段（auth_subject / 令牌）不在可写范围内：正式接入 IdP 后由断言决定，
    这里若允许改写就等于允许用户冒充别人的身份。
    """
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        raise ApiError("empty_patch", "没有需要更新的字段")

    if "display_name" in fields:
        name = fields["display_name"].strip()
        if not name:
            raise ApiError("invalid_display_name", "显示名不能为空")
        user.display_name = name

    if "timezone" in fields:
        tz = fields["timezone"].strip()
        if not _TZ_PATTERN.match(tz):
            raise ApiError("invalid_timezone", "时区格式不合法，应形如 Asia/Shanghai")
        user.timezone = tz

    db.commit()
    db.refresh(user)
    return _user_out(user)


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
