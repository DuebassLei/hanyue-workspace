"""各平台的原始解析函数（从旧 services/hot_sources.py 原样迁入）。

这些函数是 Adapter 的实现基础，行为保持等价：输入原始 payload/text，
输出 `{platform, rank, title, hot, url}` 字典，不产出契约对象、不做写入。
单独成模块是为了让 Adapter 与编排器不依赖 services 层，避免循环 import。
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
AIHOT_UA = "aihot-api/2.0.0"
AIHOT_AGENT_UA = "aihot-skill/2.0.0 (+https://aihot.news/aihot-skill/)"
WECHAT_10W_UA = UA
WECHAT_SEARCH_UA = UA
WECHAT_10W_DEFAULT = "https://raw.githubusercontent.com/alchaincyf/wechat-article/main/articles.json"
OPENRANK_BASE = "https://kaiyuanbang.cn/zh-cn/rankings"
UAPI_HOTBOARD_URL = "https://uapis.cn/api/v1/misc/hotboard"

# hot_items.url 是 VARCHAR(500)；超长 URL 会触发 StringDataRightTruncation 让整轮采集 500。
URL_MAX_LENGTH = 500


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


def _tidy_url(url: str) -> str:
    """超长时才动刀：先去跟踪参数，仍超长再硬截到列宽以内。"""
    if len(url) <= URL_MAX_LENGTH:
        return url
    try:
        parts = urlsplit(url)
        trimmed = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except ValueError:
        trimmed = url
    if len(trimmed) < URL_MAX_LENGTH:
        return trimmed
    return url[:URL_MAX_LENGTH]


def _clean_url(value) -> str:
    """平台给的是搜索页而不是原文时按「拿不到」处理，留空。"""
    url = str(value or "").strip()
    if not url:
        return ""
    lowered = url.lower()
    if any(token in lowered for token in ("/s?word=", "/search?", "/search/", "s?wd=")):
        return ""
    return _tidy_url(url)


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
        hot = item.get("sourceCount") if kind == "hot" else item.get("score")
        rows.append({
            "platform": platform,
            "rank": index,
            "title": title,
            "hot": _to_int(hot),
            "url": _clean_url(links.get("original") or links.get("aihot")),
        })
    return rows


_AGENT_HOT_RE = re.compile(
    r"第\s*(\d+)\s*名：\[(.*?)\]\((.*?)\)\s*\n\s*信源：(.*?)(?:\n\s*来龙去脉：(.*?))?(?=\n第|\n\n|\Z)",
    re.DOTALL,
)
_AGENT_DAILY_RE = re.compile(
    r"(\d+)\.\s*\[(.*?)\]\((.*?)\)(?:\s*·\s*([^\n]+))?\n\s+([^\n]+)"
)


def _rows_aihot_agent(platform: str, text: str, kind: str) -> list[dict]:
    """解析 AIHOT agent 接口的 Markdown。解析不到就返回空列表，不算失败。"""
    rows: list[dict] = []
    if kind == "hot":
        for match in _AGENT_HOT_RE.finditer(text or ""):
            rank, title, url, sources, _story = match.groups()
            title = title.strip()
            if not title:
                continue
            source_count = len([part for part in sources.split("、") if part.strip()])
            rows.append({
                "platform": platform,
                "rank": int(rank),
                "title": title,
                "hot": source_count or None,
                "url": _clean_url(url),
            })
        return rows

    for section in re.split(r"【(.*?)】", text or "")[2::2]:
        for match in _AGENT_DAILY_RE.finditer(section):
            index, title, url, _source, _summary = match.groups()
            title = title.strip()
            if not title:
                continue
            rows.append({
                "platform": platform,
                "rank": int(index),
                "title": title,
                "hot": None,
                "url": _clean_url(url),
            })
    return rows


def _rows_wechat_10w(payload) -> list[dict]:
    """微信 10w+ 爆文。兼容两种快照结构：`{articles:[...]}` 或直接是数组。"""
    raw = payload.get("articles") if isinstance(payload, dict) else payload
    rows: list[dict] = []
    for index, item in enumerate(raw or [], 1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        account = str(item.get("userName") or item.get("account_name") or "").strip()
        shown = f"{account}｜{title}" if account and account not in title else title
        clicks = str(item.get("clicksCount") or item.get("clicks") or "").strip()
        rows.append({
            "platform": "微信 10w+ 爆文池",
            "rank": index,
            "title": shown[:200],
            "hot": _to_int(clicks.replace("w+", "0000").replace("+", "")),
            "url": _clean_url(item.get("oriUrl") or item.get("url")),
        })
    return rows


def _rows_wechat_search(html: str) -> list[dict]:
    """搜狗微信搜索结果页。页面改版导致解析为空时返回空列表，不抛。"""
    rows: list[dict] = []
    for index, match in enumerate(
        re.finditer(
            r'<a[^>]+href="(/link\?url=[^"]+)"[^>]*>(.*?)</a>', html or "", re.DOTALL
        ),
        1,
    ):
        href, inner = match.groups()
        title = re.sub(r"<[^>]+>", "", inner)
        title = re.sub(r"\s+", " ", title).strip()
        if len(title) < 6:
            continue
        rows.append({
            "platform": "微信文章搜索",
            "rank": index,
            "title": title[:200],
            "hot": None,
            "url": _clean_url("https://weixin.sogou.com" + href),
        })
        if len(rows) >= 20:
            break
    return rows


_OPENRANK_ITEM_RE = re.compile(
    r'<article class="ornav-repo[^"]*".*?</article>', re.DOTALL
)
_OPENRANK_LINK_RE = re.compile(r'data-orank-target-url="([^"]+)"')
_OPENRANK_RANK_RE = re.compile(r'<div class="ornav-rank">(\d+)</div>')
_OPENRANK_TITLE_RE = re.compile(r'<h3>\s*<a [^>]*>(.*?)</a>', re.DOTALL)
_OPENRANK_DELTA_RE = re.compile(
    r'<strong class="[^"]*"><bdi dir="ltr">([+\-]?[\d,]+)</bdi>\s*</strong>\s*<small>'
)
_OPENRANK_STARS_RE = re.compile(r'#orank-icon-star"/></svg></span><strong><bdi[^>]*>([\d,]+)')


def _rows_openrank(platform: str, html: str) -> list[dict]:
    """解析开源榜的 ornav-repo 条目。页面改版导致解析为空时返回空列表，不抛。"""
    rows: list[dict] = []
    for chunk in _OPENRANK_ITEM_RE.findall(html or ""):
        rank_match = _OPENRANK_RANK_RE.search(chunk)
        title_match = _OPENRANK_TITLE_RE.search(chunk)
        if not rank_match or not title_match:
            continue
        title = re.sub(r"<[^>]+>", "", title_match.group(1))
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            continue
        delta = _OPENRANK_DELTA_RE.search(chunk)
        stars = _OPENRANK_STARS_RE.search(chunk)
        hot = None
        if delta:
            hot = _to_int(delta.group(1).lstrip("+"))
        elif stars:
            hot = _to_int(stars.group(1))
        link_match = _OPENRANK_LINK_RE.search(chunk)
        link = link_match.group(1) if link_match else ""
        rows.append({
            "platform": platform,
            "rank": int(rank_match.group(1)),
            "title": title[:200],
            "hot": hot,
            "url": _clean_url(f"https://kaiyuanbang.cn{link}" if link.startswith("/") else link),
        })
    return rows


_UAPI_HOTNUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?[wk万亿]?)", re.IGNORECASE)


def _uapi_hot_value(value) -> int | None:
    """UAPI 的 hot_value 是自由格式字符串，取第一个数字段当热度；多指标宁缺毋滥。"""
    text = str(value or "").strip()
    if not text:
        return None
    rest = _UAPI_HOTNUM_RE.sub("", text, count=1)
    if _UAPI_HOTNUM_RE.search(rest):
        return None
    match = _UAPI_HOTNUM_RE.search(text)
    if not match:
        return None
    token = match.group(1).replace(",", "")
    multiplier = 1
    if token[-1] in "wW万":
        multiplier = 10000
    elif token[-1] == "亿":
        multiplier = 100000000
    elif token[-1] == "k":
        multiplier = 1000
    number = token.rstrip("wW万亿kK")
    try:
        return int(float(number) * multiplier)
    except ValueError:
        return None


def _rows_uapi(platform: str, payload) -> list[dict]:
    items = (payload or {}).get("list") or []
    rows: list[dict] = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        rank = _to_int(item.get("index")) or index
        rows.append({
            "platform": platform,
            "rank": rank,
            "title": title[:300],
            "hot": _uapi_hot_value(item.get("hot_value")),
            "url": _clean_url(item.get("url")),
        })
    return rows
