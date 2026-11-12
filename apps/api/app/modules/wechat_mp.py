"""微信公众号草稿箱接入（官方接口）。

能力边界：
- 做：把稿件推送到公众号**草稿箱**（含封面永久素材、正文内图片上传）。
- 不做：正式群发 / 发布。那需要账号认证与群发权限，本系统只做草稿，避免误发。
- 不伪造：凭据缺失、IP 未加白名单、接口无权限、内容超限等，全部返回真实错误码与排查指引。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from sqlalchemy import select

from .. import models, schemas
from ..connectors import registry
from ..connectors import wechat_mp as wechat
from ..core import queue
from ..core.deps import ApiError, CurrentUser, DbSession, default_workspace, not_found
from ..core.security import decrypt_secret, encrypt_secret
from ..services import egress_ip, publish_state, wechat_article, wechat_layout
from .publishing import _job_out

router = APIRouter(tags=["wechat-mp"])

DRAFT_SCOPES = ["draft.add", "draft.batchget", "material.add_material", "media.uploadimg"]
PLATFORM = "wechat_official"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _connector(db=None) -> wechat.WeChatMPConnector:
    connector = registry.get_connector(PLATFORM, db)
    if not isinstance(connector, wechat.WeChatMPConnector):
        raise ApiError(
            "connector_unavailable",
            "公众号未配置：请在「发布中心 → 公众号草稿箱」填写 AppID/AppSecret，或在后端配置环境变量",
            status_code=409,
        )
    return connector


def _as_api_error(exc: wechat.WeChatMPError) -> ApiError:
    return ApiError(exc.code, exc.message, status_code=409, retryable=exc.retryable)


def _bound_account(db, user) -> models.ConnectedAccount | None:
    return db.scalar(
        select(models.ConnectedAccount)
        .where(
            models.ConnectedAccount.owner_user_id == user.id,
            models.ConnectedAccount.platform == PLATFORM,
            models.ConnectedAccount.auth_source == "app_secret",
        )
        .order_by(models.ConnectedAccount.created_at.desc())
    )


def _require_bound(db, user) -> models.ConnectedAccount:
    account = _bound_account(db, user)
    if not account or account.status != "connected":
        raise ApiError(
            "authorization_required",
            "公众号尚未完成凭据绑定：请先在「公众号草稿箱」卡片里点「绑定并验证」",
            status_code=409,
        )
    return account


def _target_account(db, user, account_id: str | None) -> models.ConnectedAccount:
    """多账号支持：显式指定 accountId 时必须属于当前用户且是公众号；否则取首个已绑定账号。"""
    if account_id:
        account = db.get(models.ConnectedAccount, account_id)
        if not account or account.owner_user_id != user.id:
            raise not_found("平台账号")
        if account.platform != PLATFORM:
            raise ApiError("platform_mismatch", "该账号不是公众号账号", status_code=409)
        return account
    return _require_bound(db, user)


def _account_connector(account: models.ConnectedAccount, db):
    """按账号取连接器：账号自有凭据优先，否则回落部署级默认凭据。"""
    connector = registry.get_account_connector(account, db)
    if not isinstance(connector, wechat.WeChatMPConnector):
        raise ApiError(
            "connector_unavailable",
            "该账号未绑定凭据，且未配置默认 AppID/AppSecret：请在账号上录入凭据或设置默认凭据",
            status_code=409,
        )
    return connector


def _resolve_assets(db, user, asset_ids: list[str]) -> list[models.Asset]:
    assets: list[models.Asset] = []
    for asset_id in asset_ids:
        asset = db.get(models.Asset, asset_id)
        if not asset or asset.owner_user_id != user.id:
            raise ApiError("asset_not_found", f"素材 {asset_id} 不存在或无权访问", status_code=404)
        if not (asset.mime_type or "").startswith("image/"):
            raise ApiError("unsupported_asset", "公众号草稿只支持图片素材", status_code=422)
        assets.append(asset)
    return assets


def _theme_out(preset: dict) -> schemas.WeChatThemeOut:
    return schemas.WeChatThemeOut(
        id=preset["id"],
        name=preset["name"],
        is_preset=True,
        description=preset.get("description", ""),
        tokens=preset["tokens"],
        editable=False,
    )


def _custom_theme_out(row: models.WeChatTheme) -> schemas.WeChatThemeOut:
    return schemas.WeChatThemeOut(
        id=row.id,
        name=row.name,
        is_preset=False,
        description="",
        tokens=row.tokens or {},
        editable=True,
    )


def _theme_tokens(db, user, theme_id: str) -> tuple[str, dict]:
    """解析主题 → (themeId, tokens)。未指定用默认预设；自定义主题必须属于当前用户。"""
    if not theme_id or theme_id in wechat_layout.PRESET_THEMES:
        preset = wechat_layout.theme_by_id(theme_id)
        return preset["id"], dict(preset["tokens"])
    row = db.get(models.WeChatTheme, theme_id)
    if not row or row.owner_user_id != user.id:
        raise ApiError("theme_not_found", "排版主题不存在或无权访问", status_code=404)
    try:
        return row.id, wechat_layout.normalize_tokens(row.tokens)
    except wechat_layout.ThemeError as exc:
        raise ApiError("invalid_theme", f"主题配置已损坏：{exc}", status_code=409) from exc


# ---------------- 排版主题 ----------------


@router.get("/wechat-mp/themes")
def list_themes(user: CurrentUser, db: DbSession) -> dict:
    presets = [_theme_out(item) for item in wechat_layout.PRESET_THEMES.values()]
    rows = db.scalars(
        select(models.WeChatTheme)
        .where(models.WeChatTheme.owner_user_id == user.id)
        .order_by(models.WeChatTheme.created_at.asc())
    ).all()
    return {
        "defaultThemeId": wechat_layout.DEFAULT_THEME_ID,
        "presets": [item.model_dump(by_alias=True) for item in presets],
        "custom": [
            _custom_theme_out(row).model_dump(by_alias=True) for row in rows
        ],
    }


@router.post("/wechat-mp/themes", status_code=201)
def create_theme(
    payload: schemas.WeChatThemeIn, user: CurrentUser, db: DbSession
) -> dict:
    """基于预设复制一份自定义主题；tokens 越界直接 422，不落库。"""
    base = wechat_layout.theme_by_id(payload.base_theme_id)
    merged = {**base["tokens"], **(payload.tokens or {})}
    try:
        tokens = wechat_layout.normalize_tokens(merged)
    except wechat_layout.ThemeError as exc:
        raise ApiError("invalid_theme", str(exc), status_code=422) from exc
    row = models.WeChatTheme(
        owner_user_id=user.id,
        name=(payload.name or "").strip() or f"{base['name']} 副本",
        tokens=tokens,
        is_preset=False,
    )
    db.add(row)
    db.commit()
    return _custom_theme_out(row).model_dump(by_alias=True)


@router.patch("/wechat-mp/themes/{theme_id}")
def update_theme(
    theme_id: str, payload: schemas.WeChatThemeIn, user: CurrentUser, db: DbSession
) -> dict:
    row = db.get(models.WeChatTheme, theme_id)
    if not row or row.owner_user_id != user.id:
        raise ApiError("theme_not_found", "主题不存在或无权修改（系统预设不可改）", status_code=404)
    merged = {**(row.tokens or {}), **(payload.tokens or {})}
    try:
        row.tokens = wechat_layout.normalize_tokens(merged)
    except wechat_layout.ThemeError as exc:
        raise ApiError("invalid_theme", str(exc), status_code=422) from exc
    if (payload.name or "").strip():
        row.name = payload.name.strip()[:60]
    db.commit()
    return _custom_theme_out(row).model_dump(by_alias=True)


@router.delete("/wechat-mp/themes/{theme_id}", status_code=204)
def delete_theme(theme_id: str, user: CurrentUser, db: DbSession) -> None:
    row = db.get(models.WeChatTheme, theme_id)
    if not row or row.owner_user_id != user.id:
        raise ApiError("theme_not_found", "主题不存在或无权删除（系统预设不可删）", status_code=404)
    db.delete(row)
    db.commit()


@router.post("/wechat-mp/drafts/render")
def render_draft(
    payload: schemas.WeChatRenderIn, user: CurrentUser, db: DbSession
) -> dict:
    """渲染预览：纯本地计算，不调微信、不落库。预览与推送共用同一渲染函数。"""
    theme_id, tokens = _theme_tokens(db, user, payload.theme_id)
    urls = [url for url in payload.image_urls if url]
    if not urls and payload.asset_ids:
        assets = _resolve_assets(db, user, payload.asset_ids)
        # 素材还没上传到微信，先用 asset:// 占位，前端用本地 object URL 替换后预览
        urls = [f"asset://{asset.id}" for asset in assets]
    result = wechat_layout.render_document(payload.body, tokens, urls)
    issues = wechat_layout.lint_html(result["html"], tokens)
    return {
        "platform": PLATFORM,
        "themeId": theme_id,
        "themeName": wechat_layout.theme_by_id(theme_id)["name"] if theme_id in wechat_layout.PRESET_THEMES else "",
        "tokens": tokens,
        "html": result["html"],
        "warnings": result["warnings"],
        "issues": issues,
        "stats": result["stats"],
    }



@router.get("/wechat-mp/egress-ip")
def get_egress_ip(user: CurrentUser, force: bool = Query(default=False)) -> dict:
    """探测本服务器的公网出口 IP，供用户直接填进公众号 IP 白名单。

    微信 40164 不一定在 errmsg 里给出 IP，用户无从配置，所以这里主动问外部接口。
    结果缓存 5 分钟；`?force=true` 强制刷新。
    """
    result = egress_ip.detect_egress_ip(force=force)
    result["hint"] = (
        f"请把 {result['ip']} 加入公众号后台「设置与开发 → 基本配置 → IP 白名单」"
        if result["ip"]
        else "自动探测失败，请在该服务器上执行 curl https://myip.ipip.net 手动获取出口 IP"
    )
    return result


@router.get("/wechat-mp/credentials")
def get_credentials(user: CurrentUser, db: DbSession) -> dict:
    """查看当前凭据状态：AppID 打码展示，Secret 永不回传。"""
    setting = db.get(models.AppSetting, wechat.CREDENTIAL_SETTING_KEY)
    connector = registry.get_connector(PLATFORM, db)
    return {
        "configured": isinstance(connector, wechat.WeChatMPConnector),
        "source": wechat.config_source_label(db),
        "appIdMasked": connector.config.app_id_masked
        if isinstance(connector, wechat.WeChatMPConnector)
        else "",
        "savedAt": setting.updated_at.isoformat() if setting else None,
        "hint": "AppID / AppSecret 在公众号后台「设置与开发 → 基本配置」获取；IP 白名单需包含本机出口 IP",
    }


@router.put("/wechat-mp/credentials")
def save_credentials(payload: schemas.WeChatCredentialsIn, user: CurrentUser, db: DbSession) -> dict:
    """保存凭据到数据库（Secret 加密存储），保存后立即做真实验证探针。

    验证失败也会保留凭据（常见原因是 IP 白名单未加），返回真实 errcode 说明。
    """
    app_id = payload.app_id.strip()
    app_secret = payload.app_secret.strip()
    if not app_id or not app_secret:
        raise ApiError("invalid_credentials", "AppID 与 AppSecret 均不能为空", status_code=422)

    setting = db.get(models.AppSetting, wechat.CREDENTIAL_SETTING_KEY)
    if setting is None:
        setting = models.AppSetting(
            key=wechat.CREDENTIAL_SETTING_KEY,
            value={
                "app_id": app_id,
                "app_secret": encrypt_secret(app_secret),
            },
        )
        db.add(setting)
    else:
        setting.value = {
            "app_id": app_id,
            "app_secret": encrypt_secret(app_secret),
        }
    db.commit()
    registry.reset_connector(PLATFORM)

    connector = _connector(db)
    masked = connector.config.app_id_masked
    probe = None
    probe_error = None
    try:
        probe = connector.ping(db)
    except wechat.WeChatMPError as exc:
        probe_error = {"code": exc.code, "message": exc.message}
    return {
        "configured": probe_error is None,
        "source": "db",
        "appIdMasked": masked,
        "probe": probe,
        "probeError": probe_error,
        "note": (
            "验证通过：可以「绑定并验证」公众号账号后推送草稿"
            if probe
            else "凭据已保存，但真实验证未通过（最常见原因：公众号后台 IP 白名单未包含本机出口 IP）；修正后重新验证即可"
        ),
    }


@router.delete("/wechat-mp/credentials", status_code=204)
def clear_credentials(user: CurrentUser, db: DbSession) -> None:
    setting = db.get(models.AppSetting, wechat.CREDENTIAL_SETTING_KEY)
    if setting:
        db.delete(setting)
        db.commit()
    registry.reset_connector(PLATFORM)


@router.get("/wechat-mp/status")
def draft_status(user: CurrentUser, db: DbSession) -> dict:
    connector = registry.get_connector(PLATFORM, db)
    account = _bound_account(db, user)
    # 账号自带 AppID/AppSecret 时，即使部署级没配也要算「已配置」：
    # 推送走的是账号凭据，否则这里报未配置会把已经能用的账号挡住
    account_level = False
    if account is not None and not isinstance(connector, wechat.WeChatMPConnector):
        account_connector = registry.get_account_connector(account, db)
        if isinstance(account_connector, wechat.WeChatMPConnector):
            connector = account_connector
            account_level = True
    base = {
        "platform": PLATFORM,
        "draftCapable": True,
        "publishSupported": False,
        "publishNote": "本接入只推送到草稿箱；正式群发请在公众号后台完成",
        "credentialSource": "account" if account_level else wechat.config_source_label(db),
        "limits": {
            "titleChars": wechat_article.MAX_TITLE_CHARS,
            "digestChars": wechat_article.MAX_DIGEST_CHARS,
            "bodyChars": wechat_article.MAX_BODY_CHARS,
            "inlineImageBytes": wechat_article.MAX_INLINE_BYTES,
            "thumbBytes": wechat_article.MAX_THUMB_BYTES,
        },
        "account": {
            "id": account.id,
            "displayName": account.display_name,
            "status": account.status,
            "expiresAt": account.expires_at.isoformat() if account and account.expires_at else None,
        }
        if account
        else None,
    }
    if isinstance(connector, wechat.WeChatMPConnector):
        base.update(
            {
                "configured": True,
                "appId": connector.config.app_id_masked,
                "apiBase": connector.config.api_base,
                "authorDefault": connector.config.author,
                "token": connector.token_state(db),
                "reason": "",
            }
        )
    else:
        base.update(
            {
                "configured": False,
                "appId": "",
                "apiBase": "",
                "authorDefault": "",
                "token": {"cached": False, "expiresAt": None, "refreshedAt": None, "lastError": None},
                "reason": getattr(
                    connector,
                    "reason",
                    "未配置公众号 AppID/AppSecret（WECHAT_MP_APPID / WECHAT_MP_APPSECRET）",
                ),
            }
        )
    return base


@router.post("/wechat-mp/bind")
def bind_account(payload: schemas.WeChatBindIn, user: CurrentUser, db: DbSession) -> dict:
    """绑定并**真实验证**：调用公众号只读接口确认凭据、IP 白名单与草稿权限都可用。

    多账号：payload.credentials 提供时，该账号持有自己的 AppID/AppSecret（验证通过后加密存到账号上）；
    不提供时使用部署级默认凭据（界面保存的或环境变量）。同一平台可绑定多个账号。
    """
    if payload.credentials:
        config = wechat.WeChatMPConfig(
            app_id=payload.credentials.app_id.strip(),
            app_secret=payload.credentials.app_secret.strip(),
            api_base=(wechat.config_from_source(db) or wechat.WeChatMPConfig(app_id="x", app_secret="x")).api_base,
            author="",
        )
        if not config.app_id or not config.app_secret:
            raise ApiError("invalid_credentials", "AppID 与 AppSecret 均不能为空", status_code=422)
        connector = wechat.WeChatMPConnector(config)
    else:
        connector = _connector(db)
    try:
        probe = connector.ping(db)
    except wechat.WeChatMPError as exc:
        raise _as_api_error(exc) from exc

    workspace = default_workspace(db, user)
    # 多账号语义：同一 AppID 已绑定则更新该账号；不同 AppID 新建账号，互不覆盖。
    # platform_subject 必须存完整 AppID：打码值会把 wxAAA***01 / wxBBB***01 误判成同一账号。
    app_id = connector.config.app_id if isinstance(connector, wechat.WeChatMPConnector) else probe["appId"]
    account = db.scalar(
        select(models.ConnectedAccount).where(
            models.ConnectedAccount.owner_user_id == user.id,
            models.ConnectedAccount.platform == PLATFORM,
            models.ConnectedAccount.platform_subject == app_id,
        )
    )
    if account is None:
        # 仅接管多账号改造前、尚未记录 platform_subject 的旧账号；
        # 其他账号即使同为 app_secret 也各持不同 AppID，不能互相覆盖。
        account = db.scalar(
            select(models.ConnectedAccount).where(
                models.ConnectedAccount.owner_user_id == user.id,
                models.ConnectedAccount.platform == PLATFORM,
                models.ConnectedAccount.platform_subject == "",
            )
        )
    display_name = (payload.display_name or "").strip() or f"公众号 {probe['appId']}"
    if account is None:
        account = models.ConnectedAccount(
            owner_user_id=user.id,
            workspace_id=workspace.id,
            platform=PLATFORM,
            display_name=display_name,
            status="connected",
            scopes=list(DRAFT_SCOPES),
            auth_source="app_secret",
        )
        db.add(account)
    else:
        account.display_name = display_name
        account.status = "connected"
        account.scopes = list(DRAFT_SCOPES)
        account.workspace_id = workspace.id
    if payload.credentials:
        # 多账号：凭据跟随账号保存（Secret 加密），后续推送/验证都用账号自己的凭据
        account.app_id = config.app_id
        account.app_secret_ciphertext = encrypt_secret(config.app_secret)
    account.platform_subject = app_id
    expires_at = (probe.get("token") or {}).get("expiresAt")
    if expires_at:
        try:
            account.expires_at = datetime.fromisoformat(expires_at)
        except ValueError:
            account.expires_at = None
    db.commit()
    return {
        "account": {
            "id": account.id,
            "displayName": account.display_name,
            "status": account.status,
            "authSource": account.auth_source,
            "scopes": account.scopes,
            "expiresAt": account.expires_at.isoformat() if account.expires_at else None,
        },
        "probe": probe,
        "note": "验证通过：可推送草稿到公众号后台；正式群发仍需在公众号后台操作",
    }


@router.put("/wechat-mp/accounts/{account_id}/credentials")
def update_account_credentials(
    account_id: str, payload: schemas.WeChatCredentialsIn, user: CurrentUser, db: DbSession
) -> dict:
    """为已绑定账号修改自有凭据：真实验证后加密保存（Secret 永不回传），并刷新该账号连接器缓存。

    与部署级默认凭据独立：账号绑定了自有凭据后，推送/验证都以账号凭据为准。
    """
    account = db.get(models.ConnectedAccount, account_id)
    if not account or account.owner_user_id != user.id:
        raise not_found("平台账号")
    if account.platform != PLATFORM:
        raise ApiError("platform_mismatch", "该账号不是公众号账号", status_code=409)

    app_id = payload.app_id.strip()
    app_secret = payload.app_secret.strip()
    if not app_id or not app_secret:
        raise ApiError("invalid_credentials", "AppID 与 AppSecret 均不能为空", status_code=422)

    base = wechat.config_from_source(db)
    config = wechat.WeChatMPConfig(
        app_id=app_id,
        app_secret=app_secret,
        api_base=base.api_base if base else wechat.DEFAULT_API_BASE,
        author=base.author if base else "",
    )
    connector = wechat.WeChatMPConnector(config)
    probe = None
    probe_error = None
    try:
        probe = connector.ping(db)
    except wechat.WeChatMPError as exc:
        # 常见原因是 IP 白名单未放行；凭据仍保存，用户修正环境后重新验证即可
        probe_error = {"code": exc.code, "message": exc.message}

    account.app_id = app_id
    account.app_secret_ciphertext = encrypt_secret(app_secret)
    if probe:
        account.platform_subject = app_id
        account.status = "connected"
    db.commit()
    registry.reset_connector(PLATFORM)
    return {
        "accountId": account.id,
        "appIdMasked": connector.config.app_id_masked,
        "probe": probe,
        "probeError": probe_error,
        "note": (
            "验证通过：该账号后续推送将使用自己的凭据"
            if probe
            else "凭据已保存到账号，但真实验证未通过（最常见原因：公众号后台 IP 白名单未包含本机出口 IP）"
        ),
    }


@router.post("/wechat-mp/drafts/preview")
def preview_draft(
    payload: schemas.WeChatDraftPreviewIn, user: CurrentUser, db: DbSession
) -> dict:
    issues = wechat_article.validate_article(
        title=payload.title,
        body=payload.body,
        digest=payload.digest,
        author=payload.author,
        asset_count=len(payload.asset_ids),
        content_source_url=payload.content_source_url,
    )
    theme_id, tokens = _theme_tokens(db, user, payload.theme_id)
    assets = _resolve_assets(db, user, payload.asset_ids) if payload.asset_ids else []
    rendered = wechat_layout.render_document(
        payload.body, tokens, [f"asset://{asset.id}" for asset in assets[1:]]
    )
    issues.extend(rendered["warnings"])
    issues.extend(wechat_layout.lint_html(rendered["html"], tokens))
    blocking = [item for item in issues if item["level"] == "error"]
    if payload.account_id:
        # 多账号：预览按指定账号的自有凭据判断可推送性
        target = _target_account(db, user, payload.account_id)
        connector = registry.get_account_connector(target, db)
        configured = isinstance(connector, wechat.WeChatMPConnector)
        bound = target if target.status == "connected" else None
        unconfigured_msg = "该账号未绑定凭据，且未配置默认 AppID/AppSecret"
    else:
        connector = registry.get_connector(PLATFORM, db)
        configured = isinstance(connector, wechat.WeChatMPConnector)
        bound = _bound_account(db, user)
        unconfigured_msg = "后端未配置公众号 AppID/AppSecret，无法推送草稿"
    if not configured:
        issues.append(
            {
                "field": "connector",
                "level": "error",
                "message": unconfigured_msg,
            }
        )
    if not bound:
        issues.append(
            {
                "field": "account",
                "level": "error",
                "message": "尚未绑定并验证公众号，请先点「绑定并验证」",
            }
        )
    preset = wechat_layout.PRESET_THEMES.get(theme_id)
    return {
        "platform": PLATFORM,
        "canPush": configured and bool(bound) and not blocking and not any(
            item["level"] == "error" for item in issues
        ),
        "issues": issues,
        "thumbAssetId": assets[0].id if assets else None,
        "thumbName": assets[0].original_name if assets else "",
        "inlineCount": max(0, len(assets) - 1),
        "renderedChars": rendered["stats"]["renderedChars"],
        "themeId": theme_id,
        "themeName": preset["name"] if preset else "",
        "stats": rendered["stats"],
        "html": rendered["html"],
        "note": "第一张素材作为封面（thumb_media_id），其余作为正文内图片",
    }


@router.post("/wechat-mp/drafts", response_model=schemas.PublishJobOut, status_code=201)
def push_draft(
    payload: schemas.WeChatDraftIn, user: CurrentUser, db: DbSession
) -> schemas.PublishJobOut:
    """创建草稿推送任务；真正的平台调用在任务队列里执行，失败会带真实微信错误码。"""
    existing = db.scalar(
        select(models.PublishJob).where(
            models.PublishJob.owner_user_id == user.id,
            models.PublishJob.idempotency_key == payload.idempotency_key,
        )
    )
    if existing:
        return _job_out(existing)

    account = _target_account(db, user, payload.account_id)
    connector = _account_connector(account, db)
    if not payload.asset_ids:
        raise ApiError(
            "thumb_required", "微信草稿必须有封面图，请至少选择一张素材", status_code=422
        )
    issues = wechat_article.validate_article(
        title=payload.title,
        body=payload.body,
        digest=payload.digest,
        author=payload.author,
        asset_count=len(payload.asset_ids),
        content_source_url=payload.content_source_url,
    )
    blocking = [item["message"] for item in issues if item["level"] == "error"]
    if blocking:
        raise ApiError("draft_invalid", "；".join(blocking), status_code=422)
    _resolve_assets(db, user, payload.asset_ids)
    theme_id, theme_tokens = _theme_tokens(db, user, payload.theme_id)
    # 内联样式会显著增加 content 长度，而微信上限按 content 字符数计
    estimated = wechat_layout.render_document(
        payload.body, theme_tokens, [f"asset://{i}" for i in range(len(payload.asset_ids))]
    )
    if estimated["stats"]["htmlChars"] > wechat_article.MAX_BODY_CHARS:
        raise ApiError(
            "draft_too_long",
            f"套用排版后正文 HTML 长度 {estimated['stats']['htmlChars']}，超过微信 {wechat_article.MAX_BODY_CHARS} 字符上限；请精简正文或减少强调标记",
            status_code=422,
        )

    workspace = default_workspace(db, user)
    content_item_id = None
    if payload.content_item_id:
        item = db.get(models.ContentItem, payload.content_item_id)
        # 只允许关联本人稿件，跨用户传入按未关联处理
        if item and item.owner_user_id == user.id:
            content_item_id = item.id
    job = models.PublishJob(
        owner_user_id=user.id,
        workspace_id=workspace.id,
        content_item_id=content_item_id,
        connected_account_id=account.id,
        platform=PLATFORM,
        target="draft",
        idempotency_key=payload.idempotency_key,
        status=publish_state.QUEUED,
        request_id=uuid.uuid4().hex,
        reviewed_by=user.id,
        theme_id=theme_id,
        layout_snapshot=theme_tokens,
        payload_snapshot={
            "mode": "draft",
            # 草稿要推全文，不能像发布预览那样截断
            "title": payload.title.strip(),
            "body": payload.body,
            "digest": payload.digest.strip(),
            "author": (payload.author or connector.config.author).strip(),
            "contentSourceUrl": payload.content_source_url.strip(),
            "assetIds": list(payload.asset_ids),
            "needOpenComment": bool(payload.need_open_comment),
            "onlyFansCanComment": bool(payload.only_fans_can_comment),
            # 排版快照：主题后续被改也能复现当时的排版
            "themeId": theme_id,
            "layoutTokens": theme_tokens,
        },
    )
    db.add(job)
    try:
        db.commit()
    except Exception as exc:  # 并发同键：回查已存在任务
        db.rollback()
        raced = db.scalar(
            select(models.PublishJob).where(
                models.PublishJob.owner_user_id == user.id,
                models.PublishJob.idempotency_key == payload.idempotency_key,
            )
        )
        if raced:
            return _job_out(raced)
        raise exc

    queue.enqueue(
        db,
        owner_user_id=user.id,
        queue="platform-publish",
        kind="wechat_draft",
        payload={"publishJobId": job.id, "accountId": account.id, "target": "draft"},
    )
    return _job_out(job)


@router.get("/wechat-mp/drafts")
def list_remote_drafts(
    user: CurrentUser,
    db: DbSession,
    offset: int = Query(default=0, ge=0, le=10000),
    count: int = Query(default=20, ge=1, le=20),
    account_id: str = Query(default=""),
) -> dict:
    """读取公众号后台真实的草稿列表（用于确认推送结果，不是本地缓存）。

    多账号：accountId 指定目标账号（用其自有凭据）；缺省取首个已绑定账号。
    """
    account = _target_account(db, user, account_id)
    connector = _account_connector(account, db)
    try:
        return connector.list_drafts(db, offset=offset, count=count)
    except wechat.WeChatMPError as exc:
        raise _as_api_error(exc) from exc


@router.get("/wechat-mp/drafts/{media_id}")
def get_remote_draft(
    media_id: str, user: CurrentUser, db: DbSession, account_id: str = Query(default="")
) -> dict:
    account = _target_account(db, user, account_id)
    connector = _account_connector(account, db)
    try:
        return connector.get_draft(db, media_id)
    except wechat.WeChatMPError as exc:
        raise _as_api_error(exc) from exc
