"""热榜采集兼容层。

真正的实现已迁到 `app.hot_sources` 包（契约 + catalog + 三类 Adapter + 编排）。
本模块只做两件事：
1. re-export 公共 API，保持 `hot_topics.refresh()` 与历史 import 路径不回归；
2. 保留 `_fetch_json` / `_rows_*` 等纯解析入口，测试可直接单测解析函数而不打外网。

新增平台请改 `app/hot_sources/catalog.yaml`，不要再往本文件加源常量。
"""

from __future__ import annotations

import os

from ..core import ssrf
from ..hot_sources import (
    CollectContext,
    CollectResult,
    HotRawItem,
    HotSourcesError,
    all_platforms,
    collect,
    enabled_platforms,
    enabled_sources,
    load_catalog,
    source_catalog,
)
from ..hot_sources import parsers as _parsers
from ..hot_sources.parsers import (
    AIHOT_AGENT_UA,
    AIHOT_UA,
    OPENRANK_BASE,
    UAPI_HOTBOARD_URL,
    URL_MAX_LENGTH,
    UA,
    WECHAT_10W_DEFAULT,
    WECHAT_10W_UA,
    WECHAT_SEARCH_UA,
    _clean_url,
    _dig,
    _pick,
    _rows_agg,
    _rows_aihot,
    _rows_aihot_agent,
    _rows_direct,
    _rows_openrank,
    _rows_uapi,
    _rows_wechat_10w,
    _rows_wechat_search,
    _tidy_url,
    _to_int,
    _uapi_hot_value,
)

# 兼容旧代码的派生清单；单一真相仍是 catalog.yaml
ALL_PLATFORMS = tuple(all_platforms())


def _timeout() -> float:
    try:
        return max(3.0, float(os.getenv("HOT_TOPICS_TIMEOUT", "12")))
    except ValueError:
        return 12.0


def _fetch_json(url: str, headers: dict[str, str], retries: int = 1):
    """旧取数入口的薄兼容实现；新代码请用 CollectContext.safe_get。"""
    ctx = CollectContext(source={}, timeout=_timeout(), retries=retries)
    return ctx.safe_get(url, headers).json()


def _wechat_10w_url() -> str:
    # 空串（例如 compose 的 ${VAR:-}）也要回落到默认源，否则微信源会被静默禁用。
    return (os.getenv("HOT_TOPICS_WECHAT_10W_URL") or WECHAT_10W_DEFAULT).strip()


def _wechat_search_keyword() -> str:
    return os.getenv("HOT_TOPICS_WECHAT_KEYWORD", "").strip()


__all__ = [
    "ALL_PLATFORMS",
    "AIHOT_AGENT_UA",
    "AIHOT_UA",
    "CollectContext",
    "CollectResult",
    "HotRawItem",
    "HotSourcesError",
    "OPENRANK_BASE",
    "UAPI_HOTBOARD_URL",
    "URL_MAX_LENGTH",
    "UA",
    "WECHAT_10W_DEFAULT",
    "WECHAT_10W_UA",
    "WECHAT_SEARCH_UA",
    "_clean_url",
    "_fetch_json",
    "_rows_agg",
    "_rows_aihot",
    "_rows_aihot_agent",
    "_rows_direct",
    "_rows_openrank",
    "_rows_uapi",
    "_rows_wechat_10w",
    "_rows_wechat_search",
    "_timeout",
    "_uapi_hot_value",
    "_wechat_10w_url",
    "_wechat_search_keyword",
    "all_platforms",
    "collect",
    "enabled_platforms",
    "enabled_sources",
    "load_catalog",
    "source_catalog",
    "ssrf",
]
