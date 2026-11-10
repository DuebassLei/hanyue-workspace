"""热点抓取服务。

适配器模式：支持通过环境变量 HOT_TOPICS_API_URL 配置热搜源。
未配置时返回空列表（不伪造热点），前端可手动输入关键词创建选题。

安全：所有出站请求走 ssrf.safe_request，校验过的 IP 固定连接层。
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx

from ..core import ssrf


class HotTopicsError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _configured_source() -> str | None:
    url = os.getenv("HOT_TOPICS_API_URL", "").strip()
    if not url:
        return None
    try:
        ssrf.validate_base_url(url)
    except ssrf.UnsafeUrl:
        return None
    return url


def fetch_hot_topics(limit: int = 20) -> list[dict]:
    """抓取热搜列表。未配置源时返回空列表，不伪造。

    返回格式：[{"title": str, "hot": str, "url": str|None, "source": str}]
    """
    source = _configured_source()
    if not source:
        return []

    try:
        response = ssrf.safe_request(
            "GET",
            source,
            headers={"Accept": "application/json"},
            timeout=15.0,
        )
    except (ssrf.UnsafeUrl, httpx.HTTPError) as exc:
        raise HotTopicsError("fetch_failed", f"热点源请求失败：{type(exc).__name__}") from exc

    if response.status_code >= 400:
        raise HotTopicsError(
            "fetch_failed", f"热点源返回 HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError:
        raise HotTopicsError("invalid_response", "热点源未返回 JSON")

    items = _normalize(payload, limit)
    return items


def _normalize(payload, limit: int) -> list[dict]:
    """把不同热搜源的返回格式统一成内部结构。

    支持两种常见格式：
    1. {"data": [{"title":..., "hot":..., "url":...}]}
    2. [{"word":..., "hot":..., "url":...}]  (微博热搜风格)
    """
    raw_list: list = []
    if isinstance(payload, dict):
        for key in ("data", "items", "list", "result"):
            val = payload.get(key)
            if isinstance(val, list):
                raw_list = val
                break
    elif isinstance(payload, list):
        raw_list = payload

    result: list[dict] = []
    for item in raw_list[:limit]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("word") or item.get("name") or "").strip()
        if not title:
            continue
        hot = str(item.get("hot") or item.get("hot_value") or item.get("num") or "")
        url = item.get("url") or item.get("link")
        result.append(
            {
                "title": title,
                "hot": hot,
                "url": url if isinstance(url, str) else None,
                "source": urlparse(source).netloc if (url := _configured_source()) else "hot-topics",
            }
        )
    return result


def source_status() -> dict:
    """热点源配置状态，供前端展示。"""
    source = _configured_source()
    return {
        "configured": bool(source),
        "source": source or "",
        "note": (
            "已配置热搜源，可拉取实时热点"
            if source
            else "未配置 HOT_TOPICS_API_URL，当前仅支持手动输入热点关键词"
        ),
    }
