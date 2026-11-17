"""认证：邮箱密码登录（正式）+ 开发态 subject 登录（便利入口）。

设计要点：
- **auth_subject 是稳定身份键，与登录方式解耦**。密码账号固定为
  `password:<email>`，所以换登录方式/接 IdP 时 workspace 与内容归属不断。
- **登录失败要花掉同样的时间**：账号不存在时也跑一次 scrypt，
  否则可以用响应时间枚举哪些邮箱注册过。
- **改密即全端下线**：令牌带 `pwdv`（密码修改时间的 epoch），
  早于它的旧令牌在 core/deps.py 里被拒绝，不需要维护黑名单。
- 连续失败达到阈值后锁定账号一段时间，挡在线爆破；成功登录清零。
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header
from sqlalchemy import func, select

from .. import models, schemas
from ..core.config import get_settings
from ..core.deps import ApiError, CurrentUser, DbSession, default_workspace
from ..services import rbac_service
from ..core.logging import get_logger
from ..core.security import (
    create_token,
    decode_token,
    epoch_seconds,
    hash_password,
    password_needs_rehash,
    to_utc,
    validate_password,
    verify_password,
)

router = APIRouter(tags=["auth"])
settings = get_settings()
logger = get_logger(__name__)

# 形如 Asia/Shanghai、America/Argentina/Buenos_Aires，或 UTC；不校验该时区在当前系统是否可解析
# （Windows 上缺 tzdata 时 zoneinfo 会抛错，不能因此把一个合法时区判成非法）。
_TZ_PATTERN = re.compile(r"^(?:UTC|[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+)*)$")

# 刻意保守：只要求「有 @、有域名、域名带点」，不做 RFC 5322 全量校验。
# 过严的正则会误杀合法地址，而真正的有效性只能靠发信验证。
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# 邮箱列宽 190，而 auth_subject 也是 190；加 `password:` 前缀后必须留出余量
_EMAIL_MAX_LENGTH = 180


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _provider(user: models.User) -> str:
    """身份来源，只描述「这个账号能怎么登录」。

    有密码 → password；没有密码 → 开发态账号（历史数据或免密入口创建的）。
    将来接 IdP 时在这里加第三个分支即可，前端按字符串渲染。
    """
    return "password" if user.password_hash else "local-dev"


def _user_out(user: models.User) -> schemas.UserOut:
    return schemas.UserOut(
        id=user.id,
        display_name=user.display_name,
        timezone=user.timezone,
        auth_subject=user.auth_subject,
        auth_provider=_provider(user),
        email=user.email,
        has_password=bool(user.password_hash),
        created_at=user.created_at,
    )


def _token_for(user: models.User) -> str:
    return create_token(user.id, pwd_epoch=epoch_seconds(user.password_changed_at))


def _normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


def _has_password_user(db) -> bool:
    return (
        db.scalar(
            select(func.count())
            .select_from(models.User)
            .where(models.User.password_hash.is_not(None))
        )
        or 0
    ) > 0


def _seed_system_tasks(user: models.User, db) -> None:
    """系统级定时任务（拉热点等）需要一个真实 owner 才能落 background_jobs
    （owner_user_id 是必填外键），所以绑到首个登录用户。多用户下只有第一条会留下。
    失败不能影响登录：调度器出问题只记日志，不把用户挡在门外。
    """
    try:
        from ..services import scheduled_tasks

        scheduled_tasks.ensure_system_tasks(db, user.id, user.timezone)
    except Exception as exc:  # pragma: no cover - 兜底不影响主流程
        logger.warning("播种系统定时任务失败（不影响登录）：%s", exc)


def _session_response(user: models.User, db) -> schemas.LoginResponse:
    workspace = default_workspace(db, user)
    db.commit()
    _seed_system_tasks(user, db)
    return schemas.LoginResponse(
        token=_token_for(user), user=_user_out(user), workspace_id=workspace.id
    )


@router.get("/auth/config", response_model=schemas.AuthConfigOut)
def auth_config(db: DbSession) -> schemas.AuthConfigOut:
    """登录页需要知道的能力开关。未登录可访问，不暴露任何账号信息。"""
    bootstrap = not _has_password_user(db)
    return schemas.AuthConfigOut(
        password_login=True,
        allow_dev_login=settings.allow_dev_login,
        # 首个密码账号永远允许创建：否则新部署会卡在「没账号可登录」
        allow_registration=settings.allow_registration or bootstrap,
        bootstrap_available=bootstrap,
        password_min_length=settings.password_min_length,
    )


@router.post(
    "/auth/register", response_model=schemas.LoginResponse, status_code=201
)
def register(payload: schemas.RegisterRequest, db: DbSession) -> schemas.LoginResponse:
    """邮箱密码注册。

    注册成功后直接返回会话（不用再登录一次）。开放条件见 /auth/config：
    显式打开 ALLOW_REGISTRATION，或库里还没有任何密码账号（首次部署）。
    """
    email = _normalize_email(payload.email)
    if len(email) > _EMAIL_MAX_LENGTH or not _EMAIL_PATTERN.match(email):
        raise ApiError("invalid_email", "请输入有效的邮箱地址")
    problem = validate_password(
        payload.password, min_length=settings.password_min_length
    )
    if problem:
        raise ApiError("weak_password", problem)
    if db.scalar(select(models.User).where(func.lower(models.User.email) == email)):
        raise ApiError("email_taken", "该邮箱已注册，请直接登录", status_code=409)
    if _has_password_user(db) and not settings.allow_registration:
        raise ApiError(
            "registration_disabled",
            "注册已关闭，请联系管理员开通账号",
            status_code=403,
        )

    now = _now()
    user = models.User(
        auth_subject=f"password:{email}",
        email=email,
        display_name=(payload.display_name or "").strip() or email.split("@", 1)[0],
        password_hash=hash_password(payload.password),
        password_changed_at=now,
        last_login_at=now,
        failed_login_attempts=0,
        is_active=True,
    )
    db.add(user)
    db.flush()
    # 新账号立刻拿到默认角色，否则登录后权限为空、侧栏空白
    rbac_service.assign_default_role(db, user.id)
    logger.info("新账号注册：%s", email)
    return _session_response(user, db)


@router.post("/auth/login", response_model=schemas.LoginResponse)
def login(payload: schemas.LoginRequest, db: DbSession) -> schemas.LoginResponse:
    """登录入口。

    带 `password` 走正式认证；只带 `subject` 走开发态便利入口
    （仅 ALLOW_DEV_LOGIN=true 时可用，生产门禁会强制关闭）。
    """
    if payload.password:
        return _login_with_password(payload, db)
    if not settings.allow_dev_login:
        raise ApiError(
            "dev_login_disabled",
            "开发态登录已关闭，请使用邮箱密码登录",
            status_code=403,
        )
    subject = (payload.subject or "").strip()
    if not subject:
        raise ApiError("missing_credentials", "请提供邮箱与密码")

    user = db.scalar(
        select(models.User).where(models.User.auth_subject == subject)
    )
    if user is None:
        user = models.User(
            auth_subject=subject, display_name=payload.display_name or "创作者"
        )
        db.add(user)
        db.flush()
        rbac_service.assign_default_role(db, user.id)
    elif not user.is_active:
        raise ApiError("account_disabled", "该账号已停用", status_code=403)
    return _session_response(user, db)


def _login_with_password(
    payload: schemas.LoginRequest, db
) -> schemas.LoginResponse:
    identifier = _normalize_email(
        payload.identifier or payload.email or payload.subject or ""
    )
    password = payload.password or ""
    if not identifier:
        raise ApiError("missing_credentials", "请提供邮箱与密码")
    if not password:
        raise ApiError("missing_credentials", "请提供密码")

    user = db.scalar(
        select(models.User).where(func.lower(models.User.email) == identifier)
    )
    # 账号不存在 / 该账号没有密码：同样跑一次哈希再拒绝，避免时间侧信道
    if user is None or not user.password_hash:
        verify_password(password, None)
        raise ApiError("invalid_credentials", "邮箱或密码不正确", status_code=401)

    now = _now()
    locked_until = to_utc(user.locked_until)
    if locked_until and locked_until > now:
        minutes = max(1, math.ceil((locked_until - now).total_seconds() / 60))
        raise ApiError(
            "account_locked",
            f"密码错误次数过多，请 {minutes} 分钟后再试",
            status_code=429,
        )
    if not user.is_active:
        raise ApiError("account_disabled", "该账号已停用", status_code=403)

    if not verify_password(password, user.password_hash):
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        if user.failed_login_attempts >= settings.auth_lock_threshold:
            user.locked_until = now + timedelta(minutes=settings.auth_lock_minutes)
            user.failed_login_attempts = 0
            logger.warning(
                "账号 %s 连续登录失败达到阈值，锁定 %s 分钟",
                identifier,
                settings.auth_lock_minutes,
            )
        db.commit()
        raise ApiError("invalid_credentials", "邮箱或密码不正确", status_code=401)

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    # 哈希参数升级时静默重算；注意不要动 password_changed_at，否则会把
    # 用户自己其它设备上的有效会话一起踢下线。
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return _session_response(user, db)


@router.post("/auth/password")
def change_password(
    payload: schemas.PasswordChangeRequest, user: CurrentUser, db: DbSession
) -> dict:
    """设置 / 修改密码。

    已有密码时必须验证当前密码——令牌被盗时不能让对方直接换掉密码。
    成功后签发新令牌：旧令牌因为 pwdv 落后会立即失效，前端需要换用新令牌。
    """
    if user.password_hash:
        if not payload.current_password:
            raise ApiError("current_password_required", "请先输入当前密码")
        if not verify_password(payload.current_password, user.password_hash):
            raise ApiError("invalid_current_password", "当前密码不正确")
        if payload.current_password == payload.new_password:
            raise ApiError("password_reused", "新密码不能与当前密码相同")

    problem = validate_password(
        payload.new_password, min_length=settings.password_min_length
    )
    if problem:
        raise ApiError("weak_password", problem)

    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = _now()
    user.failed_login_attempts = 0
    user.locked_until = None
    # 开发态账号补齐邮箱后才算正式账号；没有邮箱就沿用原 auth_subject
    db.commit()
    db.refresh(user)
    return {
        "status": "password_changed",
        "token": _token_for(user),
        "user": _user_out(user).dump(),
    }


@router.get("/auth/me", response_model=schemas.UserOut)
def me(user: CurrentUser) -> schemas.UserOut:
    return _user_out(user)


@router.patch("/auth/me", response_model=schemas.UserOut)
def update_me(
    payload: schemas.ProfilePatch, user: CurrentUser, db: DbSession
) -> schemas.UserOut:
    """更新本人资料。

    身份字段（auth_subject / 令牌 / 邮箱）不在可写范围内：邮箱是登录凭据，
    允许在这里改等于允许绕过「改密需旧密码」的校验链。
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
