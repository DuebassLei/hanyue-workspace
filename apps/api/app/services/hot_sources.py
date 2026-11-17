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
import re
from urllib.parse import quote

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

# AIHOT 给 agent 的 Markdown 接口：Top 榜带信源与「来龙去脉」链接，日报带分类摘要。
# 和上面的 JSON 接口内容重叠但不相同——JSON 是 24h 资讯流，这里是编辑过的 Top 与早报，
# 所以单独成两个平台，不和「AI·热点 / AI·资讯」混进同一个榜。
AIHOT_AGENT_UA = "aihot-skill/2.0.0 (+https://aihot.news/aihot-skill/)"
AIHOT_AGENT_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("AIHOT 热点与早报", "https://aihot.news/api/v1/agent/hot", "hot"),
    ("AIHOT 日报", "https://aihot.news/api/v1/agent/daily", "daily"),
)

# 微信 10w+ 爆文池：公开的爆款文章榜（标题、公众号、阅读量、原文链接）。
# 数据是第三方维护的快照，地址可用环境变量替换；没配置时用默认公开地址。
WECHAT_10W_DEFAULT = "https://raw.githubusercontent.com/alchaincyf/wechat-article/main/articles.json"
WECHAT_10W_UA = UA

# 微信文章搜索：搜狗微信搜索的公开结果页。没有官方 JSON 接口，只能解析 HTML。
# 关键词来自环境变量，没配就不采集——不能替用户决定搜什么。
WECHAT_SEARCH_UA = UA

ALL_PLATFORMS = tuple(
    [name for name, *_ in DIRECT_SOURCES]
    + [name for name, _ in AGG_SOURCES]
    + [name for name, _, _ in AIHOT_SOURCES]
    + [name for name, _, _ in AIHOT_AGENT_SOURCES]
    + ["微信 10w+ 爆文池", "微信文章搜索"]
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
                "url": url.strip(),
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
                "url": url.strip(),
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
        # 榜单条目的身份是标题；公众号名放进标题前缀会让同一篇文章跨日无法合并，
        # 所以只在标题本身不含账号时补一个短前缀，方便人一眼看出是谁写的。
        shown = f"{account}｜{title}" if account and account not in title else title
        clicks = str(item.get("clicksCount") or item.get("clicks") or "").strip()
        rows.append({
            "platform": "微信 10w+ 爆文池",
            "rank": index,
            "title": shown[:200],
            "hot": _to_int(clicks.replace("w+", "0000").replace("+", "")),
            "url": str(item.get("oriUrl") or item.get("url") or "").strip(),
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
            "url": "https://weixin.sogou.com" + href,
        })
        if len(rows) >= 20:
            break
    return rows


def _wechat_10w_url() -> str:
    return os.getenv("HOT_TOPICS_WECHAT_10W_URL", WECHAT_10W_DEFAULT).strip()


def _wechat_search_keyword() -> str:
    return os.getenv("HOT_TOPICS_WECHAT_KEYWORD", "").strip()


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

    for name, url, kind in AIHOT_AGENT_SOURCES:
        if name not in wanted:
            continue
        try:
            response = ssrf.safe_request(
                "GET",
                url,
                headers={"User-Agent": AIHOT_AGENT_UA, "Accept": "text/plain,*/*"},
                timeout=_timeout(),
            )
            if response.status_code >= 300:
                raise HotSourcesError(f"HTTP {response.status_code}")
            got = _rows_aihot_agent(name, response.text, kind)
            rows.extend(got)
            notes.append(f"{name}：{len(got)} 条")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{name}：失败（{type(exc).__name__}）")

    if "微信 10w+ 爆文池" in wanted:
        url = _wechat_10w_url()
        try:
            payload = _fetch_json(url, {"User-Agent": WECHAT_10W_UA})
            got = _rows_wechat_10w(payload)
            rows.extend(got)
            notes.append(f"微信 10w+ 爆文池：{len(got)} 条")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            notes.append(f"微信 10w+ 爆文池：失败（{type(exc).__name__}）")

    if "微信文章搜索" in wanted:
        keyword = _wechat_search_keyword()
        if not keyword:
            notes.append("微信文章搜索：未配置关键词（HOT_TOPICS_WECHAT_KEYWORD），已跳过")
            ok += 1
        else:
            try:
                response = ssrf.safe_request(
                    "GET",
                    f"https://weixin.sogou.com/weixin?type=2&query={quote(keyword)}",
                    headers={"User-Agent": WECHAT_SEARCH_UA, "Accept": "text/html"},
                    timeout=_timeout(),
                )
                if response.status_code >= 300:
                    raise HotSourcesError(f"HTTP {response.status_code}")
                got = _rows_wechat_search(response.text)
                rows.extend(got)
                notes.append(f"微信文章搜索（{keyword}）：{len(got)} 条")
                ok += 1
            except Exception as exc:  # noqa: BLE001
                notes.append(f"微信文章搜索：失败（{type(exc).__name__}）")

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
    ] + [
        {"platform": name, "kind": "aihot-agent"} for name, _, _ in AIHOT_AGENT_SOURCES
    ] + [
        {"platform": "微信 10w+ 爆文池", "kind": "wechat-10w"},
        {"platform": "微信文章搜索", "kind": "wechat-search"},
    ]
