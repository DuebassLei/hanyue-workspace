"""微信公众号「草稿箱」连接器（官方接口：新增草稿 / 永久素材 / 图文内图片上传）。

与其余四个平台的差异：
- 微信公众平台**没有**面向第三方创作工具的 OAuth 授权码流程，凭据是
  AppID + AppSecret（在「设置与开发 → 基本配置」获取），因此这里不走 OAuth，
  而是用 `cgi-bin/stable_token` 换取 access_token，并落库缓存供多进程共享。
- 草稿箱是**低风险动作**：只在公众号后台生成草稿，不会对外发布。
  所以它的门禁是「凭据可用 + 真实探测通过」，而不是 Phase 0 的发布能力核验。
- 正式群发（发布）仍需账号认证与群发权限，本模块**不实现**，调用会明确报错。

所有出站请求都经 `core.ssrf.safe_request`（校验过的 IP 固定连接层），
不允许在别处 new httpx client 直连微信域名。
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import models
from ..core import ssrf
from ..core.logging import get_logger
from ..core.security import decrypt_secret, encrypt_secret

logger = get_logger(__name__)

DEFAULT_API_BASE = "https://api.weixin.qq.com"
# 令牌提前刷新窗口：微信令牌 7200s，留 5 分钟余量避免临界期调用失败
TOKEN_REFRESH_MARGIN = 300
_TOKEN_INVALID_CODES = {"invalid_token", "token_expired"}


class WeChatMPError(RuntimeError):
    """微信接口错误；code 用于任务失败分类，message 直接面向用户排查。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        errcode: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.errcode = errcode
        self.retryable = retryable


# 常见 errcode → (内部错误码, 用户可执行的说明, 是否可重试)
# 只收录有明确官方含义的码；未收录的保留原始 errcode 与 errmsg，绝不猜测成功。
ERRCODE_MAP: dict[int, tuple[str, str, bool]] = {
    40001: ("invalid_token", "access_token 已失效（可能被其它系统刷新过），请重试", True),
    40014: ("invalid_token", "access_token 不合法，请重试", True),
    42001: ("token_expired", "access_token 已过期，请重试", True),
    40002: ("invalid_grant_type", "grant_type 不合法", False),
    40013: ("invalid_appid", "AppID 不合法，请检查 WECHAT_MP_APPID", False),
    40125: ("invalid_appsecret", "AppSecret 不正确，请检查 WECHAT_MP_APPSECRET", False),
    41002: ("missing_appid", "缺少 AppID", False),
    41004: ("missing_appsecret", "缺少 AppSecret", False),
    40164: (
        "ip_not_whitelisted",
        "调用来源 IP 不在公众号 IP 白名单内：请在「设置与开发 → 基本配置 → IP 白名单」"
        "加入本服务器的公网出口 IP 后重试",
        False,
    ),
    40243: ("appsecret_frozen", "AppSecret 已被冻结，请到公众号后台重置后更新配置", False),
    48001: (
        "api_unauthorized",
        "该接口未获授权：草稿箱需要已认证的公众号，请确认账号类型与认证状态",
        False,
    ),
    45009: ("rate_limited", "接口调用次数超过当日上限，请稍后或次日再试", True),
    45003: ("title_too_long", "标题超长（微信限制 64 字以内）", False),
    45004: ("digest_too_long", "摘要超长（微信限制 120 字以内）", False),
    45166: ("invalid_content", "正文包含非法内容（常见原因：使用了非微信域名的图片或非法标签）", False),
    40007: ("invalid_media_id", "thumb_media_id 无效或不是图片永久素材", False),
    40006: ("invalid_media_size", "素材大小超出微信限制", False),
    40005: ("invalid_file_type", "文件类型不支持（封面仅支持 jpg/png，正文图片仅支持 jpg/png）", False),
    44004: ("empty_content", "请求内容为空", False),
    44002: ("empty_post", "请求体为空", False),
    45047: ("out_of_limit", "接口调用超出限额", True),
}

# stable_token 在系统繁忙时返回 -1，退回普通 token 接口再试一次
_ERRCODE_SYSTEM_BUSY = -1

# 微信 40164 的 errmsg 形如：
#   invalid ip 1.2.3.4 ipv6 ::ffff:1.2.3.4, not in whitelist rid: 6512...
#   invalid ip 183.6.44.12, not in whitelist
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6_RE = re.compile(r"\b(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}\b")


def _extract_ips(errmsg: str) -> list[str]:
    """从微信 errmsg 里抠出它判定为非法的来源 IP，去重且保持出现顺序。"""
    found: list[str] = []
    for match in _IPV4_RE.findall(errmsg or ""):
        octets = match.split(".")
        if all(0 <= int(o) <= 255 for o in octets) and match not in found:
            found.append(match)
    for match in _IPV6_RE.findall(errmsg or ""):
        # 过滤 ::ffff:1.2.3.4 这种已被 IPv4 规则覆盖的写法，只保留真正的 IPv6
        if _IPV4_RE.search(match):
            continue
        if match.count(":") >= 2 and match not in found:
            found.append(match)
    return found


def describe_errcode(errcode: int, errmsg: str) -> tuple[str, str, bool]:
    mapped = ERRCODE_MAP.get(errcode)
    if mapped:
        code, message, retryable = mapped
        if errcode == 40164:
            # 把微信实际看到的来源 IP 带出来，用户才能直接去后台加白名单；
            # 之前这里丢掉了 errmsg，用户拿不到 IP 无从配置。
            ips = _extract_ips(errmsg)
            if ips:
                message = (
                    f"调用来源 IP 不在公众号 IP 白名单内：本次请求的出口 IP 是 "
                    f"{' / '.join(ips)}，请把它加入「设置与开发 → 基本配置 → IP 白名单」后重试"
                )
            else:
                message = (
                    f"{message}（微信未在返回中给出 IP，请用「检测出口 IP」获取后加入白名单）"
                )
        return code, f"{message}（微信 errcode={errcode}）", retryable
    return (
        "wechat_api_error",
        f"微信接口返回错误 errcode={errcode} errmsg={errmsg or '无'}",
        errcode in {45009, 45047, -1},
    )


@dataclass(frozen=True)
class WeChatMPConfig:
    app_id: str
    app_secret: str
    api_base: str = DEFAULT_API_BASE
    author: str = ""
    need_open_comment: int = 0
    only_fans_can_comment: int = 0

    @property
    def app_id_masked(self) -> str:
        if len(self.app_id) <= 8:
            return self.app_id[:2] + "***"
        return f"{self.app_id[:6]}***{self.app_id[-2:]}"


def config_from_env() -> WeChatMPConfig | None:
    """读取公众号凭据。两种写法都支持，方便与 PLATFORM_* 命名保持一致。"""
    app_id = (
        os.getenv("WECHAT_MP_APPID", "").strip()
        or os.getenv("PLATFORM_WECHAT_OFFICIAL_CLIENT_ID", "").strip()
    )
    app_secret = (
        os.getenv("WECHAT_MP_APPSECRET", "").strip()
        or os.getenv("PLATFORM_WECHAT_OFFICIAL_CLIENT_SECRET", "").strip()
    )
    if not (app_id and app_secret):
        return None
    api_base = (
        os.getenv("WECHAT_MP_API_BASE", "").strip()
        or os.getenv("PLATFORM_WECHAT_OFFICIAL_API_BASE", "").strip()
        or DEFAULT_API_BASE
    ).rstrip("/")
    try:
        ssrf.validate_base_url(api_base)
    except ssrf.UnsafeUrl as exc:
        logger.warning("API_BASE 非法，按未配置处理：%s", exc)
        return None
    return WeChatMPConfig(
        app_id=app_id,
        app_secret=app_secret,
        api_base=api_base,
        author=os.getenv("WECHAT_MP_AUTHOR", "").strip(),
        need_open_comment=int(os.getenv("WECHAT_MP_NEED_OPEN_COMMENT", "0") or 0),
        only_fans_can_comment=int(os.getenv("WECHAT_MP_ONLY_FANS_CAN_COMMENT", "0") or 0),
    )


CREDENTIAL_SETTING_KEY = "wechat_mp_credentials"


def config_from_setting(db) -> WeChatMPConfig | None:
    """从数据库 AppSetting 读取凭据（界面可编辑，优先级高于环境变量）。"""
    from .. import models
    from ..core.security import decrypt_secret

    setting = db.get(models.AppSetting, CREDENTIAL_SETTING_KEY)
    if not setting:
        return None
    value = setting.value or {}
    app_id = str(value.get("app_id") or "").strip()
    app_secret = str(value.get("app_secret") or "").strip()
    if app_secret:
        app_secret = decrypt_secret(app_secret)
    if not (app_id and app_secret):
        return None
    api_base = str(value.get("api_base") or DEFAULT_API_BASE).strip().rstrip("/") or DEFAULT_API_BASE
    try:
        ssrf.validate_base_url(api_base)
    except ssrf.UnsafeUrl as exc:
        logger.warning("API_BASE 非法，按未配置处理：%s", exc)
        return None
    return WeChatMPConfig(
        app_id=app_id,
        app_secret=app_secret,
        api_base=api_base,
        author=str(value.get("author") or ""),
        need_open_comment=int(value.get("need_open_comment") or 0),
        only_fans_can_comment=int(value.get("only_fans_can_comment") or 0),
    )


def config_from_source(db=None) -> WeChatMPConfig | None:
    """凭据来源优先级：界面设置（DB） > 环境变量。"""
    if db is not None:
        from_db = config_from_setting(db)
        if from_db:
            return from_db
    return config_from_env()


def config_from_account(account, db=None) -> WeChatMPConfig | None:
    """账号级凭据优先：账号自己绑定的 AppID/AppSecret；未绑定时回落部署级默认凭据。

    支持同一平台维护多个公众号账号，各账号推送时使用各自凭据（令牌互不干扰）。
    """
    from ..core.security import decrypt_secret

    app_id = str(getattr(account, "app_id", "") or "").strip()
    ciphertext = str(getattr(account, "app_secret_ciphertext", "") or "").strip()
    if app_id and ciphertext:
        base = config_from_source(db)
        return WeChatMPConfig(
            app_id=app_id,
            app_secret=decrypt_secret(ciphertext),
            api_base=base.api_base if base else DEFAULT_API_BASE,
            author=base.author if base else "",
        )
    return config_from_source(db)


def config_source_label(db=None) -> str:
    """当前凭据来源：db / env / none。"""
    if db is not None and config_from_setting(db) is not None:
        return "db"
    if config_from_env() is not None:
        return "env"
    return "none"


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class WeChatMPConnector:
    """公众号草稿箱能力实现。"""

    platform = "wechat_official"
    configured = True

    def __init__(self, config: WeChatMPConfig) -> None:
        self.config = config

    # ---------- 出站请求 ----------

    def _api(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        params: dict | None = None,
        json_body: dict | None = None,
        files: list[tuple] | None = None,
        timeout: float = 20.0,
    ) -> dict:
        url = f"{self.config.api_base}{path}"
        query = {k: v for k, v in (params or {}).items() if v is not None}
        if token:
            query["access_token"] = token
        if query:
            url = f"{url}{'&' if '?' in url else '?'}{urlencode(query)}"
        try:
            response = ssrf.safe_request(
                method,
                url,
                headers={"Accept": "application/json"},
                json_body=json_body,
                files=files,
                timeout=timeout,
            )
        except ssrf.UnsafeUrl as exc:
            raise WeChatMPError("ssrf_blocked", f"出站地址被安全策略拒绝：{exc}") from exc
        except httpx.HTTPError as exc:
            raise WeChatMPError(
                "network_error", f"请求微信接口失败：{type(exc).__name__}", retryable=True
            ) from exc
        if response.status_code >= 500:
            raise WeChatMPError(
                "provider_error", f"微信接口返回 HTTP {response.status_code}", retryable=True
            )
        if response.status_code >= 400:
            raise WeChatMPError(
                "http_error", f"微信接口返回 HTTP {response.status_code}", retryable=False
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise WeChatMPError("invalid_response", "微信接口未返回 JSON") from exc
        if not isinstance(payload, dict):
            raise WeChatMPError("invalid_response", "微信接口返回格式不符合预期")
        errcode = payload.get("errcode")
        if errcode not in (None, 0):
            code, message, retryable = describe_errcode(int(errcode), str(payload.get("errmsg") or ""))
            raise WeChatMPError(code, message, errcode=int(errcode), retryable=retryable)
        return payload

    # ---------- access_token ----------

    def _token_record(self, db):
        return db.scalar(
            select(models.PlatformAccessToken).where(
                models.PlatformAccessToken.platform == self.platform,
                models.PlatformAccessToken.app_id == self.config.app_id,
            )
        )

    def _fetch_token(self, *, force: bool) -> dict:
        body = {
            "grant_type": "client_credential",
            "appid": self.config.app_id,
            "secret": self.config.app_secret,
            # force_refresh=True 会让上一个令牌立刻失效，只有明确需要时才置真
            "force_refresh": bool(force),
        }
        try:
            return self._api("POST", "/cgi-bin/stable_token", json_body=body)
        except WeChatMPError as exc:
            if exc.errcode != _ERRCODE_SYSTEM_BUSY:
                raise
            # 系统繁忙时退回 legacy 接口；两者令牌互不兼容，拿到即覆盖缓存
            return self._api(
                "GET",
                "/cgi-bin/token",
                params={
                    "grant_type": "client_credential",
                    "appid": self.config.app_id,
                    "secret": self.config.app_secret,
                },
            )

    def ensure_token(self, db, *, force: bool = False) -> str:
        """取（或刷新）access_token，密文落库，多进程共享同一枚令牌。"""
        record = self._token_record(db)
        if record and not force:
            cached = decrypt_secret(record.access_token_ciphertext or "")
            expires = _as_utc(record.expires_at)
            if cached and expires and expires - datetime.now(timezone.utc) > timedelta(
                seconds=TOKEN_REFRESH_MARGIN
            ):
                return cached

        payload = self._fetch_token(force=force)
        token = str(payload.get("access_token") or "")
        if not token:
            raise WeChatMPError("invalid_response", "微信未返回 access_token")
        expires_in = int(payload.get("expires_in") or 7200)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in))

        if record is None:
            record = models.PlatformAccessToken(
                platform=self.platform,
                app_id=self.config.app_id,
                access_token_ciphertext=encrypt_secret(token),
                expires_at=expires_at,
                refreshed_at=datetime.now(timezone.utc),
                last_error=None,
            )
            db.add(record)
            try:
                db.commit()
            except IntegrityError:
                # 并发首次获取：另一进程已写入，改用它的缓存
                db.rollback()
                record = self._token_record(db)
                cached = decrypt_secret(record.access_token_ciphertext) if record else ""
                if cached:
                    return cached
                record = self._token_record(db)
                if record is None:
                    raise
                record.access_token_ciphertext = encrypt_secret(token)
                record.expires_at = expires_at
                record.refreshed_at = datetime.now(timezone.utc)
                record.last_error = None
                db.commit()
        else:
            record.access_token_ciphertext = encrypt_secret(token)
            record.expires_at = expires_at
            record.refreshed_at = datetime.now(timezone.utc)
            record.last_error = None
            db.commit()
        return token

    def token_state(self, db) -> dict:
        """只读缓存状态，不触发任何网络请求。"""
        record = self._token_record(db)
        expires = _as_utc(record.expires_at) if record else None
        return {
            "cached": bool(record and record.access_token_ciphertext),
            "expiresAt": expires.isoformat() if expires else None,
            "refreshedAt": _as_utc(record.refreshed_at).isoformat()
            if record and record.refreshed_at
            else None,
            "lastError": record.last_error if record else None,
        }

    def _with_token_retry(self, db, action):
        """令牌类错误（被其它系统刷新 / 过期）自动强刷一次再试。"""
        token = self.ensure_token(db)
        try:
            return action(token)
        except WeChatMPError as exc:
            if exc.code not in _TOKEN_INVALID_CODES:
                raise
            token = self.ensure_token(db, force=True)
            return action(token)

    # ---------- 真实探测 ----------

    def ping(self, db) -> dict:
        """真实调用一次只读接口，证明「凭据 + 白名单 + 接口权限」都通。"""
        data = self._with_token_retry(
            db,
            lambda token: self._api(
                "POST",
                "/cgi-bin/draft/batchget",
                token=token,
                json_body={"offset": 0, "count": 1, "no_content": 1},
            ),
        )
        return {
            "draftTotal": int(data.get("total_count") or 0),
            "appId": self.config.app_id_masked,
            "apiBase": self.config.api_base,
            "token": self.token_state(db),
        }

    # ---------- 素材与草稿 ----------

    def upload_thumb(self, db, *, data: bytes, filename: str, mime: str) -> dict:
        payload = self._with_token_retry(
            db,
            lambda token: self._api(
                "POST",
                "/cgi-bin/material/add_material",
                token=token,
                params={"type": "image"},
                files=[("media", (filename, data, mime))],
                timeout=30.0,
            ),
        )
        media_id = str(payload.get("media_id") or "")
        if not media_id:
            raise WeChatMPError("invalid_response", "永久素材接口未返回 media_id")
        return {"mediaId": media_id, "url": payload.get("url")}

    def upload_inline_image(self, db, *, data: bytes, filename: str, mime: str) -> str:
        payload = self._with_token_retry(
            db,
            lambda token: self._api(
                "POST",
                "/cgi-bin/media/uploadimg",
                token=token,
                files=[("media", (filename, data, mime))],
                timeout=30.0,
            ),
        )
        url = str(payload.get("url") or "")
        if not url:
            raise WeChatMPError("invalid_response", "图文内图片接口未返回 url")
        return url

    def add_draft(self, db, *, article: dict) -> str:
        payload = self._with_token_retry(
            db,
            lambda token: self._api(
                "POST", "/cgi-bin/draft/add", token=token, json_body={"articles": [article]}
            ),
        )
        media_id = str(payload.get("media_id") or "")
        if not media_id:
            raise WeChatMPError("invalid_response", "草稿接口未返回 media_id")
        return media_id

    def list_drafts(self, db, *, offset: int = 0, count: int = 20) -> dict:
        data = self._with_token_retry(
            db,
            lambda token: self._api(
                "POST",
                "/cgi-bin/draft/batchget",
                token=token,
                json_body={
                    "offset": max(0, offset),
                    "count": max(1, min(count, 20)),
                    "no_content": 1,
                },
            ),
        )
        items = []
        for entry in data.get("item") or []:
            news = (entry.get("content") or {}).get("news_item") or []
            first = news[0] if news else {}
            items.append(
                {
                    "mediaId": entry.get("media_id"),
                    "title": first.get("title", ""),
                    "digest": first.get("digest", ""),
                    "articleCount": len(news),
                    "updateTime": entry.get("update_time"),
                    "thumbMediaId": first.get("thumb_media_id", ""),
                    "url": first.get("url", ""),
                }
            )
        return {
            "items": items,
            "totalCount": int(data.get("total_count") or 0),
            "itemCount": int(data.get("item_count") or 0),
            "offset": offset,
        }

    def get_draft(self, db, media_id: str) -> dict:
        if not media_id or len(media_id) > 128:
            raise WeChatMPError("invalid_media_id", "草稿 media_id 不合法")
        data = self._with_token_retry(
            db,
            lambda token: self._api(
                "POST", "/cgi-bin/draft/get", token=token, json_body={"media_id": media_id}
            ),
        )
        news = data.get("news_item") or []
        return {
            "mediaId": media_id,
            "articleCount": len(news),
            "title": (news[0].get("title") if news else "") or "",
            "updateTime": data.get("update_time"),
        }

    # ---------- 与其它连接器保持接口一致（未实现项明确报错） ----------

    def authorization_url(self, **_kwargs) -> str:
        raise _no_oauth()

    def exchange_code(self, **_kwargs) -> dict:
        raise _no_oauth()

    def verify_token(self, *, access_token: str | None = None, db=None, **_kwargs) -> dict:
        if db is None:
            raise WeChatMPError("internal_error", "公众号校验需要数据库会话")
        info = self.ping(db)
        return {"valid": True, "subject": info["appId"], "draftTotal": info["draftTotal"]}

    def publish(self, **_kwargs) -> dict:
        from .registry import ConnectorUnavailable

        raise ConnectorUnavailable(
            "公众号正式群发需要已认证账号的群发权限，本系统只提供草稿箱推送；"
            "请在公众号后台完成群发"
        )

    def get_publish_status(self, **_kwargs) -> dict:
        from .registry import ConnectorUnavailable

        raise ConnectorUnavailable("公众号发布状态接口未接入（草稿箱不产生对外发布）")

    def sync_account_metrics(self, **_kwargs) -> list[dict]:
        from .registry import ConnectorUnavailable

        raise ConnectorUnavailable("公众号指标采集适配器尚未按官方文档实现")

    def sync_content_metrics(self, **_kwargs) -> list[dict]:
        from .registry import ConnectorUnavailable

        raise ConnectorUnavailable("公众号内容指标采集适配器尚未按官方文档实现")


def _no_oauth():
    from .registry import ConnectorUnavailable

    return ConnectorUnavailable(
        "公众号不使用 OAuth 授权码流程：请在后端配置 WECHAT_MP_APPID / WECHAT_MP_APPSECRET 后使用「绑定并验证」"
    )


def config_fingerprint(config: WeChatMPConfig | None) -> str:
    """凭据变更后需要重建连接器；指纹不含 secret 明文。"""
    if config is None:
        return "unconfigured"
    raw = f"{config.app_id}|{config.api_base}|{config.author}|{len(config.app_secret)}"
    return hashlib.sha256(raw.encode()).hexdigest()
