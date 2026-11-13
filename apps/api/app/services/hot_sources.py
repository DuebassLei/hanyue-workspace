"""热点采集：多路公开榜单 → 统一的行结构。

参考 DSH 工作台的 `tools/gen-materials.py`：

    直取（能给原文链接）：今日头条、百度、抖音
    聚合兜底（拿不到原文链接）：微博、知乎 走 60s
    AI/IT 频道：AIHOT 的 JSON 接口

和原实现的差别只有一处：所有出站请求必须走 `core/ssrf.safe_request`
（项目硬约束：禁止自行创建 httpx client 直连外部域名）。因此：

* 只允许 HTTPS（`validate_base_url` 的规则），上面的源都满足；
* 不自动跟随重定向，3xx 按失败处理并记进 notes；
* 单路失败不影响其它路——热点页宁可少一个平台，也不能整页打不开。

返回的行结构：`{platform, rank, title, hot, url}`。
`hot` 统一成 int 或 None（原始接口有的是字符串、有的缺失）。
"""

from __future__ import annotations

import os

import httpx

from ..core import ssrf

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# (平台名, URL, 取条目的路径, 标题键, 热度键, 链接键)
DIRECT_SOURCES: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...],
                        tuple[str, ...]], ...] = (
    (
        "今日头条",
        "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc",
        ("data",),
        ("Title", "title"),
        ("HotValue", "hot_value"),
        ("Url", "url"),
    ),
    (
        "百度",
        "https://top.baidu.com/api/board?platform=wise&tab=realtime",
        ("data", "cards", "0", "content", "0", "content"),
        ("word", "query", "title"),
        ("hotScore", "hot"),
        ("url", "rawUrl"),
    ),
    (
        "抖音",
        "https://www.douyin.com/aweme/v1/web/hot/search/list/",
        ("data", "word_list"),
        ("word", "sentence"),
        ("hot_value", "hot"),
        ("url", "share_url"),
    ),
)

AGG_SOURCES: tuple[tuple[str, str], ...] = (
    ("微博", "https://60s.viki.moe/v2/weibo"),
    ("知乎", "https://60s.viki.moe/v2/zhihu"),
)

# AIHOT 是多源聚合，覆盖 IT之家 / Hacker News / TechCrunch / X / 技术博客等，
# 一条接入同时补上 AI 与 IT 两类频道。用 JSON 接口，UA 按对方要求标注。
AIHOT_UA = "aihot-api/2.0.0"
AIHOT_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("AI·热点", "https://aihot.news/api/v1/hot-topics", "hot"),
    ("AI·资讯", "https://aihot.news/api/v1/items?limit=50&window=24h", "feed"),
)

ALL_PLATFORMS = tuple(
    [name for name, *_ in DIRECT_SOURCES]
    + [name for name, _ in AGG_SOURCES]
    + [name for name, _, _ in AIHOT_SOURCES]
)


class HotSourcesError(RuntimeError):
    """所有源都失败时抛出；单源失败只记 notes，不抛。"""

    def __init__(self, message: str, notes: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.notes = notes or []


def enabled_platforms() -> list[str]:
    """启用的平台。`HOT_TOPICS_PLATFORMS=微博,知乎` 可只跑一部分（调试/降噪）。"""
    raw = os.getenv("HOT_TOPICS_PLATFORMS", "").strip()
    if not raw:
        return list(ALL_PLATFORMS)
    wanted = {part.strip() for part in raw.split(",") if part.strip()}
    return [name for name in ALL_PLATFORMS if name in wanted]


def _timeout() -> float:
    try:
        return max(3.0, float(os.getenv("HOT_TOPICS_TIMEOUT", "12")))
    except ValueError:
        return 12.0


def _fetch_json(url: str, headers: dict[str, str], retries: int = 1):
    """取 JSON。抖音等站偶发瞬时失败，重试一次。"""
    last: Exception | None = None
    for _ in range(retries + 1):
        try:
            response = ssrf.safe_request(
                "GET",
                url,
                headers={"Accept": "application/json,text/html,*/*", **headers},
                timeout=_timeout(),
            )
        except (ssrf.UnsafeUrl, httpx.HTTPError) as exc:
            last = exc
            continue
        # safe_request 不跟随重定向，所以 3xx 也算失败，而不是当成功解析空 body
        if response.status_code >= 300:
            last = HotSourcesError(f"HTTP {response.status_code}")
            continue
        try:
            return response.json()
        except ValueError as exc:
            last = exc
            continue
    raise last or HotSourcesError("请求失败")


def _dig(obj, path: tuple[str, ...]):
    cur = obj
    for key in path:
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif isinstance(cur, list) and key.isdigit():
            index = int(key)
            cur = cur[index] if index < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


def _pick(item: dict, keys: tuple[str, ...]):
    lowered = {str(k).lower(): v for k, v in item.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, ""):
            return value
    return ""


def _to_int(value) -> int | None:
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def _clean_url(value) -> str:
    """平台给的是搜索页而不是原文时按「拿不到」处理，留空。"""
    url = str(value or "").strip()
    if not url:
        return ""
    lowered = url.lower()
    if any(token in lowered for token in ("/s?word=", "/search?", "/search/", "s?wd=")):
        return ""
    return url


def _rows_direct(
    platform: str,
    payload,
    path: tuple[str, ...],
    title_keys: tuple[str, ...],
    hot_keys: tuple[str, ...],
    url_keys: tuple[str, ...],
) -> list[dict]:
    items = _dig(payload, path) or []
    rows: list[dict] = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        title = str(_pick(item, title_keys)).strip()
        if not title:
            continue
        rows.append({
            "platform": platform,
            "rank": index,
            "title": title,
            "hot": _to_int(_pick(item, hot_keys)),
            "url": _clean_url(_pick(item, url_keys)),
        })
    return rows


def _rows_agg(platform: str, payload) -> list[dict]:
    items = (payload or {}).get("data") or []
    rows: list[dict] = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        if not title:
            continue
        # 聚合接口不给原文链接，留空，不用搜索链接冒充
        rows.append({
            "platform": platform,
            "rank": index,
            "title": title,
            "hot": _to_int(item.get("hot_value") or item.get("hot")),
            "url": "",
        })
    return rows


def _rows_aihot(platform: str, payload, kind: str) -> list[dict]:
    items = (payload or {}).get("items") or []
    rows: list[dict] = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        links = item.get("links") or {}
        # 热点用「几个来源同时报道」当热度；资讯用分数
        hot = item.get("sourceCount") if kind == "hot" else item.get("score")
        rows.append({
            "platform": platform,
            "rank": index,
            "title": title,
            "hot": _to_int(hot),
            "url": str(links.get("original") or links.get("aihot") or ""),
        })
    return rows


def collect(platforms: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """采集所有启用的平台。

    返回 `(rows, notes)`。notes 每个平台一行，说明成功条数或失败原因，
    前端原样展示——「微博今天没抓到」比「悄悄少一个平台」诚实得多。
    """
    wanted = platforms if platforms is not None else enabled_platforms()
    rows: list[dict] = []
    notes: list[str] = []
    ok = 0

    for name, url, path, title_keys, hot_keys, url_keys in DIRECT_SOURCES:
        if name not in wanted:
            continue
        try:
            payload = _fetch_json(url, {"User-Agent": UA})
            got = _rows_direct(name, payload, path, title_keys, hot_keys, url_keys)
            rows.extend(got)
            notes.append(f"{name}：{len(got)} 条")
            ok += 1
        except Exception as exc:  # noqa: BLE001 - 单源失败不能拖垮整页
            notes.append(f"{name}：失败（{type(exc).__name__}）")

    for name, url in AGG_SOURCES:
        if name not in wanted:
            continue
        try:
            payload = _fetch_json(url, {"User-Agent": UA}, retries=2)
            got = _rows_agg(name, payload)
            rows.extend(got)
            notes.append(f"{name}：{len(got)} 条")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{name}：失败（{type(exc).__name__}）")

    for name, url, kind in AIHOT_SOURCES:
        if name not in wanted:
            continue
        try:
            payload = _fetch_json(url, {"User-Agent": AIHOT_UA})
            got = _rows_aihot(name, payload, kind)
            rows.extend(got)
            notes.append(f"{name}：{len(got)} 条")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{name}：失败（{type(exc).__name__}）")

    if ok == 0:
        raise HotSourcesError("所有热点源都不可用：" + "；".join(notes), notes)
    return rows, notes


def source_catalog() -> list[dict]:
    """给界面用的源清单（哪些平台会被采集）。"""
    return [
        {"platform": name, "kind": "direct"} for name, *_ in DIRECT_SOURCES
    ] + [
        {"platform": name, "kind": "aggregate"} for name, _ in AGG_SOURCES
    ] + [
        {"platform": name, "kind": "aihot"} for name, _, _ in AIHOT_SOURCES
    ]
