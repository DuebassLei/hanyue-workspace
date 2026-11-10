"""探测本服务器的公网出口 IP。

为什么需要：微信接口按「调用来源 IP」校验白名单，未加白名单时返回 40164。
微信的 errmsg 不一定带 IP，用户就无从知道该去后台填什么。
这里主动问外部「我是谁」接口，把出口 IP 直接给到前端，省掉猜的环节。

安全：所有请求都走 `core.ssrf.safe_request`，域名照样解析并拦截内网地址。
"""

from __future__ import annotations

import ipaddress
import os
import re
import time

from ..core import ssrf

# 按顺序尝试，命中即返回。国内可达性与稳定性优先，海外接口作为兜底。
DEFAULT_PROBE_URLS = (
    "https://myip.ipip.net",
    "https://api.ip.sb/ip",
    "https://ipinfo.io/ip",
    "https://api.ipify.org",
)

# 结果缓存：出口 IP 不会频繁变化，避免每次打开页面都打外部接口
_CACHE_TTL_SECONDS = 300
_cache: dict[str, object] = {"ip": "", "source": "", "at": 0.0}

_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6_RE = re.compile(r"\b(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}\b")

def _probe_urls() -> list[str]:
    """探测源地址：允许用 EGRESS_IP_PROBE_URLS 覆盖（逗号分隔）。"""
    raw = os.environ.get("EGRESS_IP_PROBE_URLS", "").strip()
    if raw:
        return [item.strip() for item in raw.split(",") if item.strip()]
    return list(DEFAULT_PROBE_URLS)


def _is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _pick_ip(text: str) -> str:
    for candidate in _IPV4_RE.findall(text or ""):
        if _is_public(candidate):
            return candidate
    for candidate in _IPV6_RE.findall(text or ""):
        if _IPV4_RE.search(candidate):
            continue
        if _is_public(candidate):
            return candidate
    return ""


def detect_egress_ip(*, force: bool = False) -> dict:
    """返回 {ip, source, checkedAt, cached, attempts}。

    全部探测失败时 ip 为空字符串，attempts 里带上每一条的真实失败原因，
    前端据此提示用户手动查，而不是编一个 IP 出来。
    """
    now = time.time()
    if not force and _cache["ip"] and now - float(_cache["at"]) < _CACHE_TTL_SECONDS:
        return {
            "ip": _cache["ip"],
            "source": _cache["source"],
            "checkedAt": int(_cache["at"]),
            "cached": True,
            "attempts": [],
        }

    attempts: list[dict] = []
    for url in _probe_urls():
        try:
            response = ssrf.safe_request("GET", url, timeout=6.0)
        except ssrf.UnsafeUrl as exc:
            attempts.append({"url": url, "ok": False, "error": str(exc)})
            continue
        except Exception as exc:  # 网络类异常：记下换个源继续试
            attempts.append(
                {"url": url, "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:80]}"}
            )
            continue
        if response.status_code >= 400:
            attempts.append(
                {"url": url, "ok": False, "error": f"HTTP {response.status_code}"}
            )
            continue
        ip = _pick_ip(response.text[:2048])
        if not ip:
            attempts.append({"url": url, "ok": False, "error": "响应里没有公网 IP"})
            continue
        attempts.append({"url": url, "ok": True, "error": ""})
        _cache.update({"ip": ip, "source": url, "at": now})
        return {
            "ip": ip,
            "source": url,
            "checkedAt": int(now),
            "cached": False,
            "attempts": attempts,
        }

    return {"ip": "", "source": "", "checkedAt": int(now), "cached": False, "attempts": attempts}
