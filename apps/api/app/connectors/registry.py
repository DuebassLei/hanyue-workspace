"""平台连接器注册表（设计 §7.1 / §7.3 / §12.1）。

平台收敛说明：内容发布平台仅保留**微信公众号**。
- 公众号没有面向第三方创作工具的 OAuth 授权码流程，凭据是 AppID + AppSecret，
  走 `connectors/wechat_mp.py` 的草稿箱实现（stable_token + 真实探针）。
- 未配置凭据 → `UnconfiguredConnector`，所有动作明确抛 `ConnectorUnavailable`，
  绝不伪造结果。
"""

from __future__ import annotations

import hashlib

from . import base as connectors
from . import wechat_mp


class ConnectorUnavailable(RuntimeError):
    """平台未配置或接口不可用；调用方必须如实报错，不得伪造结果。"""


class UnconfiguredConnector:
    platform: str
    configured = False

    def __init__(self, platform: str, reason: str = "未配置官方应用凭据") -> None:
        self.platform = platform
        self.reason = reason

    def _fail(self) -> None:
        raise ConnectorUnavailable(
            f"{connectors.PLATFORM_LABELS.get(self.platform, self.platform)}：{self.reason}"
        )

    def authorization_url(self, **_kwargs) -> str:
        self._fail()
        return ""

    def exchange_code(self, **_kwargs) -> dict:
        self._fail()
        return {}

    def verify_token(self, **_kwargs) -> dict:
        self._fail()
        return {}

    def publish(self, **_kwargs) -> dict:
        self._fail()
        return {}

    def get_publish_status(self, **_kwargs) -> dict:
        self._fail()
        return {}

    def sync_account_metrics(self, **_kwargs) -> list[dict]:
        self._fail()
        return []

    def sync_content_metrics(self, **_kwargs) -> list[dict]:
        self._fail()
        return []


# 值 = (配置指纹, 连接器实例)；环境变量变更后自动重建，避免缓存让用户改配置不生效
_CACHE: dict[str, tuple[str, object]] = {}


def get_connector(platform: str, db=None):
    """公众号走 AppID/AppSecret + 草稿箱实现（凭据：界面设置优先，环境变量兜底）；其余平台一律不可用。"""
    if platform != "wechat_official":
        return UnconfiguredConnector(platform, "该平台未接入，系统仅支持微信公众号")
    config = wechat_mp.config_from_source(db)
    fingerprint = wechat_mp.config_fingerprint(config)
    cached = _CACHE.get(platform)
    if cached and cached[0] == fingerprint:
        return cached[1]
    connector: object = (
        wechat_mp.WeChatMPConnector(config)
        if config
        else UnconfiguredConnector(
            platform, "未配置公众号 AppID/AppSecret：可在「发布中心 → 公众号草稿箱」填写，或配置 WECHAT_MP_APPID / WECHAT_MP_APPSECRET"
        )
    )
    _CACHE[platform] = (fingerprint, connector)
    return connector


def reset_connector(platform: str = "wechat_official") -> None:
    """凭据变更后清空缓存，下次取用时按最新配置重建。"""
    _CACHE.pop(platform, None)
    for key in [k for k in _ACCOUNT_CACHE if k.startswith(f"{platform}:")]:
        _ACCOUNT_CACHE.pop(key, None)


_ACCOUNT_CACHE: dict[str, tuple[str, object]] = {}


def get_account_connector(account, db=None):
    """按账号取连接器：账号绑定了自己的凭据就用它，否则回落部署级默认凭据。"""
    platform = account.platform
    if platform != "wechat_official":
        return UnconfiguredConnector(platform, "该平台未接入，系统仅支持微信公众号")
    config = wechat_mp.config_from_account(account, db)
    fingerprint = wechat_mp.config_fingerprint(config)
    key = f"{platform}:{account.id}"
    cached = _ACCOUNT_CACHE.get(key)
    if cached and cached[0] == fingerprint:
        return cached[1]
    if config is None:
        return UnconfiguredConnector(
            platform,
            "该账号未绑定凭据，且未配置默认 AppID/AppSecret",
        )
    connector = wechat_mp.WeChatMPConnector(config)
    _ACCOUNT_CACHE[key] = (fingerprint, connector)
    return connector


def connector_status(platform: str, db=None) -> dict:
    connector = get_connector(platform, db)
    cap = connectors.get_capability(platform)
    status = {
        "platform": platform,
        "configured": bool(getattr(connector, "configured", False)),
        "capabilityConfirmed": cap.confirmed,
        "reason": getattr(connector, "reason", "") if not getattr(connector, "configured", False) else "",
        "credentialSource": wechat_mp.config_source_label(db) if platform == "wechat_official" else "none",
    }
    if isinstance(connector, wechat_mp.WeChatMPConnector):
        status["mode"] = "app_secret"
        status["appId"] = connector.config.app_id_masked
        status["draftCapable"] = True
        status["verifyCapable"] = True
        status["verifyNote"] = "绑定即真实验证（draft/batchget 探针）"
    else:
        status["mode"] = "none"
        status["draftCapable"] = False
        status["verifyCapable"] = False
        status["verifyNote"] = "未配置 WECHAT_MP_APPID / WECHAT_MP_APPSECRET，验证授权会明确报错"
    return status
