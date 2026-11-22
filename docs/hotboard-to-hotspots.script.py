"""采集 hotboard（今日热榜）榜单 → 写入热点选题库（通用定时任务 / 用户脚本）。

数据源背景
----------
https://yiktt.github.io/hotboard/ 是纯静态聚合页，HTML 里没有数据；页面 JS
直接请求各平台公开接口，共 46 个榜单，分两类：

1. Codelife 主源（31 个）：https://api.codelife.cc/api/top/list?lang=cn&id=<cid>
   返回 {code:200, data:[{index, title, hotValue, link}]}，结构统一、最稳。
2. 直连源（15 个）：HN / dev.to / 豆瓣 / X 趋势 / App Store / 7 路 RSS /
   新浪热榜 / 网易 / 澎湃。页面上的 cors.redoc.ly 是浏览器专用的 CORS 代理，
   本脚本在服务端运行、没有同源限制，一律直连源站（更稳、少一层依赖）。

平台 cid / feed / 解析字段取自 hotboard 仓库 hot.js 的 SOURCES 与 loadSource。
详见 docs/hotboard-collector.md。

落库链路
--------
每个榜单各自调用公开入库入口，一个平台一轮：
    hot_topics.ingest(platform, source_url, items)
        → 写 hot_items → 用当天全部已入库条目聚簇+评分 → commit
聚簇阈值、100 分评分、item_uid/fingerprint 幂等与其它平台任务完全一致；
`source_url` 记的是该榜这一轮真正请求的地址，便于核对「这条从哪个榜来」。

脚本契约：run(params, ctx) -> dict
    ctx.db            真实 DB 会话
    ctx.owner_user_id 任务所属用户
    params            定时任务里配的 JSON 参数（见 PARAMS 说明）

PARAMS 可选项：
    platforms  要采的榜单：省略/[] = 默认精选源；"all" = 全部 46 个；
               ["weibo", "知乎热榜", "hackernews"] = 按 id 或中文名指定
    limit      每个榜单取前 N 条，1~50，默认 30
    timeout    单源超时秒数，3~30，默认 12
    date       归档日期 YYYY-MM-DD，默认今天
    region     地区覆盖，如 {"x": "united-states", "appstore": "us"}
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import quote

from app.core import ssrf
from app.services import hot_topics

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# ── 平台注册表（唯一真相：id 稳定，name 即落库的 platform）──────────────
# kind=codelife 走主源；其余为直连源。stable=False 的是易失效源（HTML 抓取 /
# App Store 分区接口），默认精选源不含它们，需要时用 platforms 显式点名。
SOURCES = [
    # 综合热搜
    dict(id="weibo", name="微博热搜", cat="综合热搜", kind="codelife", cid="KqndgxeLl9"),
    dict(id="zhihu", name="知乎热榜", cat="综合热搜", kind="codelife", cid="mproPpoq6O"),
    dict(id="baidu", name="百度热点", cat="综合热搜", kind="codelife", cid="Jb0vmloB1G"),
    dict(id="toutiao", name="头条热榜", cat="综合热搜", kind="codelife", cid="toutiao"),
    dict(id="wechat", name="微信热文", cat="综合热搜", kind="codelife", cid="WnBe01o371"),
    dict(id="smzdm", name="什么值得买", cat="综合热搜", kind="codelife", cid="K7GdagpoQy"),
    dict(id="tieba", name="百度贴吧", cat="综合热搜", kind="codelife", cid="Om4ejxvxEN"),
    dict(id="sinahot", name="新浪热榜", cat="综合热搜", kind="sina-hot"),
    dict(id="netease", name="网易新闻", cat="综合热搜", kind="netease"),
    # 科技开发
    dict(id="github", name="GitHub 趋势", cat="科技开发", kind="codelife", cid="rYqoXQ8vOD"),
    dict(id="juejin", name="掘金", cat="科技开发", kind="codelife", cid="QaqeEaVe9R"),
    dict(id="sspai", name="少数派", cat="科技开发", kind="codelife", cid="Y2KeDGQdNP"),
    dict(id="ithome", name="IT之家", cat="科技开发", kind="codelife", cid="74Kvx59dkx"),
    dict(id="kr36", name="36氪", cat="科技开发", kind="codelife", cid="Q1Vd5Ko85R"),
    dict(id="csdn", name="CSDN", cat="科技开发", kind="codelife", cid="DgeyrgMoZq"),
    dict(id="zcool", name="站酷", cat="科技开发", kind="codelife", cid="VaobJ88oAj"),
    dict(id="huxiu", name="虎嗅", cat="科技开发", kind="codelife", cid="5VaobgvAj1"),
    dict(id="oschina", name="开源中国", cat="科技开发", kind="codelife", cid="rYqoXZzdOD"),
    dict(id="renren", name="人人都是产品经理", cat="科技开发", kind="codelife", cid="20MdKx4ew1"),
    # 海外视野
    dict(id="phunt", name="Product Hunt", cat="海外视野", kind="codelife", cid="LBwdG0jePx"),
    dict(id="appstore", name="App Store", cat="海外视野", kind="appstore", stable=False),
    dict(id="hackernews", name="Hacker News", cat="海外视野", kind="hn"),
    dict(id="devto", name="dev.to", cat="海外视野", kind="devto"),
    dict(id="x", name="X 趋势", cat="海外视野", kind="x", stable=False),
    dict(id="bbc", name="BBC 新闻", cat="海外视野", kind="rss",
         feed="https://feeds.bbci.co.uk/news/world/rss.xml"),
    dict(id="nyt", name="纽约时报", cat="海外视野", kind="rss",
         feed="https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    dict(id="guardian", name="卫报", cat="海外视野", kind="rss",
         feed="https://www.theguardian.com/world/rss"),
    dict(id="aljazeera", name="半岛电视台", cat="海外视野", kind="rss",
         feed="https://www.aljazeera.com/xml/rss/all.xml"),
    dict(id="npr", name="NPR", cat="海外视野", kind="rss",
         feed="https://feeds.npr.org/1001/rss.xml"),
    dict(id="dw", name="德国之声", cat="海外视野", kind="rss",
         feed="https://rss.dw.com/rdf/rss-en-world"),
    # 新闻资讯
    dict(id="txnews", name="腾讯新闻·热问", cat="新闻资讯", kind="codelife", cid="12owgX0oNV"),
    dict(id="sina", name="新浪财经", cat="新闻资讯", kind="codelife", cid="rx9ozj7oXb"),
    dict(id="cbn", name="第一财经", cat="新闻资讯", kind="codelife", cid="0MdKam4ow1"),
    dict(id="xueqiu", name="雪球", cat="新闻资讯", kind="codelife", cid="X12owXzvNV"),
    dict(id="thepaper", name="澎湃新闻", cat="新闻资讯", kind="thepaper"),
    dict(id="chinanews", name="中国新闻网", cat="新闻资讯", kind="rss",
         feed="https://www.chinanews.com.cn/rss/scroll-news.xml"),
    dict(id="wallstreetcn", name="华尔街见闻", cat="新闻资讯", kind="codelife", cid="G2me3ndwjq"),
    # 影音娱乐
    dict(id="bili", name="哔哩哔哩", cat="影音娱乐", kind="codelife", cid="74KvxwokxM"),
    dict(id="douyin", name="抖音热点", cat="影音娱乐", kind="codelife", cid="DpQvNABoNE"),
    dict(id="douban", name="豆瓣电影", cat="影音娱乐", kind="douban", stable=False),
    dict(id="hupu", name="虎扑步行街", cat="影音娱乐", kind="codelife", cid="G47o8weMmN"),
    dict(id="weread", name="微信读书", cat="影音娱乐", kind="codelife", cid="4KvxEX0dkx"),
    dict(id="dongqiudi", name="懂球帝", cat="影音娱乐", kind="codelife", cid="n3moBE1eN5"),
    dict(id="kuaishou", name="快手", cat="影音娱乐", kind="codelife", cid="MZd7PrPerO"),
    dict(id="maoyan", name="猫眼电影", cat="影音娱乐", kind="codelife", cid="9JndkpJe3V"),
    dict(id="imdb", name="IMDb", cat="影音娱乐", kind="codelife", cid="VaobbkqoAj"),
]

# 默认精选：结构稳定的主源 + 稳定的直连 JSON/API 源。
# 易失效源（appstore / x / douban）不在默认内，用 platforms 显式点名。
DEFAULT_IDS = [
    "weibo", "zhihu", "baidu", "toutiao", "github", "juejin", "ithome",
    "kr36", "huxiu", "bili", "douyin", "hackernews",
]

# ── 通用工具 ──────────────────────────────────────────────────────────


def _to_int(value):
    """热度值归一：'1939万' / '1.2亿' / '1,234' / 1234 / '' → int 或 None。"""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    match = re.match(r"^([\d.]+)\s*万$", text)
    if match:
        return int(float(match.group(1)) * 10000)
    match = re.match(r"^([\d.]+)\s*亿$", text)
    if match:
        return int(float(match.group(1)) * 100000000)
    try:
        return int(float(text))
    except ValueError:
        return None


def _to_rank(value, fallback):
    try:
        number = int(value)
        return number if number > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def _get(url, timeout, accept="application/json, text/plain, */*"):
    """所有出站请求统一走项目 SSRF 防护（HTTPS / 公网 IP / 逐跳校验）。"""
    response = ssrf.safe_request(
        "GET", url,
        headers={"Accept": accept, "User-Agent": UA},
        timeout=float(timeout),
    )
    if response.status_code >= 400:
        raise ValueError(f"HTTP {response.status_code}")
    return response


def _row(title, rank, hot, url):
    return {"title": str(title or "").strip(), "rank": rank, "hot": hot, "url": str(url or "")}


# ── 各数据源 loader（返回 [_row, ...]）────────────────────────────────


def _load_codelife(src, p):
    stamp = int(datetime.now().timestamp() * 1000)
    url = f"https://api.codelife.cc/api/top/list?lang=cn&id={quote(src['cid'])}&_={stamp}"
    payload = _get(url, p["timeout"]).json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if payload.get("code") != 200 or not isinstance(data, list) or not data:
        raise ValueError("Codelife 主源数据异常")
    return [
        _row(it.get("title"), _to_rank(it.get("index"), i),
             _to_int(it.get("hotValue")), it.get("link"))
        for i, it in enumerate(data[:p["limit"]], 1)
        if isinstance(it, dict) and str(it.get("title") or "").strip()
    ]


def _load_hn(src, p):
    url = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=20"
    hits = (_get(url, p["timeout"]).json() or {}).get("hits") or []
    if not hits:
        raise ValueError("Hacker News 数据异常")
    return [
        _row(h.get("title"), i, _to_int(h.get("points")),
             h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}")
        for i, h in enumerate(hits[:p["limit"]], 1)
        if isinstance(h, dict) and str(h.get("title") or "").strip()
    ]


def _load_devto(src, p):
    data = _get("https://dev.to/api/articles?top=7&per_page=20", p["timeout"]).json()
    if not isinstance(data, list) or not data:
        raise ValueError("dev.to 数据异常")
    return [
        _row(a.get("title"), i, _to_int(a.get("positive_reactions_count")),
             a.get("url") or ("https://dev.to" + str(a.get("path") or "")))
        for i, a in enumerate(data[:p["limit"]], 1)
        if isinstance(a, dict) and str(a.get("title") or "").strip()
    ]


def _load_douban(src, p):
    # 用官方搜索 JSON 接口（比 HTML 页面解析稳），只展示标题与链接，不展示热度。
    url = ("https://movie.douban.com/j/search_subjects?type=movie"
           "&tag=%E7%83%AD%E9%97%A8&page_limit=20&page_start=0")
    subjects = (_get(url, p["timeout"], accept="application/json, */*").json() or {}).get("subjects") or []
    if not subjects:
        raise ValueError("豆瓣数据异常")
    return [
        _row(s.get("title"), i, None, s.get("url"))
        for i, s in enumerate(subjects[:p["limit"]], 1)
        if isinstance(s, dict) and str(s.get("title") or "").strip()
    ]


def _load_appstore(src, p):
    cc = p["region"].get("appstore", "us")
    url = f"https://itunes.apple.com/{quote(cc)}/rss/topfreeapplications/limit={p['limit']}/json"
    entries = ((_get(url, p["timeout"]).json() or {}).get("feed") or {}).get("entry") or []
    if not entries:
        raise ValueError("App Store 数据异常")
    rows = []
    for i, e in enumerate(entries[:p["limit"]], 1):
        if not isinstance(e, dict):
            continue
        title = str((e.get("im:name") or {}).get("label") or "").strip()
        links = e.get("link") or []
        if isinstance(links, dict):
            links = [links]
        alt = next(
            (l for l in links if isinstance(l, dict)
             and (l.get("attributes") or {}).get("rel") == "alternate"),
            links[0] if links else {},
        )
        url_value = (e.get("id") or {}).get("label") or (alt.get("attributes") or {}).get("href") or ""
        if title:
            rows.append(_row(title, i, None, url_value))
    return rows


def _load_x(src, p):
    region = p["region"].get("x", "united-states")
    slug = "" if region == "worldwide" else region + "/"
    text = _get("https://getdaytrends.com/" + slug, p["timeout"],
                accept="text/html, */*").text
    rows = []
    pattern = r'<td[^>]*class="[^"]*\bmain\b[^"]*"[^>]*>\s*<a[^>]*>(.*?)</a>'
    for match in re.finditer(pattern, text, re.S | re.I):
        title = " ".join(re.sub(r"<[^>]+>", "", match.group(1)).split()).strip()
        if not title:
            continue
        rows.append(_row(
            title, len(rows) + 1, None,
            "https://x.com/search?q=" + quote(title) + "&src=trend_click&f=top",
        ))
        if len(rows) >= p["limit"]:
            break
    if not rows:
        raise ValueError("X 趋势数据异常")
    return rows


def _load_rss(src, p):
    text = _get(src["feed"], p["timeout"],
                accept="application/rss+xml, application/xml, text/xml, */*").text
    root = ET.fromstring(text)
    rows = []
    for node in root.iter():
        if node.tag.split("}")[-1].lower() not in ("item", "entry"):
            continue
        title = link = ""
        for child in node:
            tag = child.tag.split("}")[-1].lower()
            if tag == "title" and not title:
                title = " ".join((child.text or "").split()).strip()
            elif tag == "link" and not link:
                link = child.get("href") or " ".join((child.text or "").split())
        if title and link:
            rows.append(_row(title, len(rows) + 1, None, link))
        if len(rows) >= p["limit"]:
            break
    if not rows:
        raise ValueError("RSS 数据异常")
    return rows


def _load_sina(src, p):
    day = datetime.now().strftime("%Y%m%d")
    url = ("https://top.news.sina.com.cn/ws/GetTopDataList.php?top_type=day"
           "&top_cat=www_www_all_suda_suda&top_time=" + day +
           "&top_show_num=50&top_order=DESC&js_var=all_1_data")
    text = _get(url, p["timeout"],
                accept="application/javascript, text/plain, */*").text
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("新浪热榜数据异常")
    data = json.loads(text[start:end + 1]).get("data") or []
    if not data:
        raise ValueError("新浪热榜数据异常")
    return [
        _row(it.get("title"), i, _to_int(it.get("top_num")), it.get("url"))
        for i, it in enumerate(data[:p["limit"]], 1)
        if isinstance(it, dict) and str(it.get("title") or "").strip()
    ]


def _load_netease(src, p):
    data = ((_get("https://m.163.com/fe/api/hot/news/flow", p["timeout"]).json() or {})
            .get("data") or {}).get("list") or []
    if not data:
        raise ValueError("网易新闻数据异常")
    return [
        _row(it.get("title"), i, None, it.get("url"))
        for i, it in enumerate(data[:p["limit"]], 1)
        if isinstance(it, dict) and str(it.get("title") or "").strip()
    ]


def _load_thepaper(src, p):
    data = ((_get("https://cache.thepaper.cn/contentapi/wwwIndex/rightSidebar", p["timeout"]).json() or {})
            .get("data") or {}).get("hotNews") or []
    if not data:
        raise ValueError("澎湃新闻数据异常")
    rows = []
    for i, it in enumerate(data[:p["limit"]], 1):
        if not isinstance(it, dict):
            continue
        title = str(it.get("name") or "").strip()
        link = it.get("link") or (
            f"https://www.thepaper.cn/newsDetail_forward_{it.get('contId')}"
            if it.get("contId") else ""
        )
        if title:
            rows.append(_row(title, i, None, link))
    return rows


LOADERS = {
    "codelife": _load_codelife,
    "hn": _load_hn,
    "devto": _load_devto,
    "douban": _load_douban,
    "appstore": _load_appstore,
    "x": _load_x,
    "rss": _load_rss,
    "sina-hot": _load_sina,
    "netease": _load_netease,
    "thepaper": _load_thepaper,
}


def _choose_sources(raw):
    if raw in (None, "", []):
        return [s for s in SOURCES if s["id"] in DEFAULT_IDS]
    if isinstance(raw, str) and raw.strip().lower() == "all":
        return list(SOURCES)
    if isinstance(raw, list):
        keys = {str(x).strip() for x in raw if str(x).strip()}
        chosen = [s for s in SOURCES if s["id"] in keys or s["name"] in keys]
        known = {s["id"] for s in chosen} | {s["name"] for s in chosen}
        unknown = keys - known
        if unknown:
            raise ValueError(
                "未知平台：" + "、".join(sorted(unknown))
                + "；可选 id：" + "、".join(s["id"] for s in SOURCES)
            )
        return chosen
    raise ValueError('platforms 必须是 id/名称数组或 "all"')


def _source_url(src):
    """该榜这一轮真正请求的地址（记进 hot_items.source_url，不参与评分）。

    与各 loader 里的请求地址保持一致；RSS 直接用 feed，Codelife 用榜单接口。
    """
    if src.get("feed"):
        return src["feed"]
    if src["kind"] == "codelife":
        return f"https://api.codelife.cc/api/top/list?lang=cn&id={src['cid']}"
    return {
        "hn": "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=20",
        "devto": "https://dev.to/api/articles?top=7&per_page=20",
        "douban": "https://movie.douban.com/j/search_subjects?type=movie&tag=热门",
        "appstore": "https://itunes.apple.com/cn/rss/topfreeapplications/limit=20/json",
        "x": "https://getdaytrends.com/",
        "sina-hot": "https://top.news.sina.com.cn/ws/GetTopDataList.php?top_type=day",
        "netease": "https://m.163.com/fe/api/hot/news/flow",
        "thepaper": "https://cache.thepaper.cn/contentapi/wwwIndex/rightSidebar",
    }.get(src["kind"], "https://yiktt.github.io/hotboard/")


def run(params, ctx):
    params = params or {}
    limit = max(1, min(int(params.get("limit") or 30), 50))
    timeout = max(3, min(int(params.get("timeout") or 12), 30))
    region = params.get("region") if isinstance(params.get("region"), dict) else {}
    day = str(params.get("date") or hot_topics.today_str())
    captured = hot_topics.now_stamp()

    sources = _choose_sources(params.get("platforms"))
    p = {"limit": limit, "timeout": timeout, "region": region}

    notes, ok_sources = [], 0
    ingested = created = matched = 0
    clusters = []
    for src in sources:
        try:
            items = LOADERS[src["kind"]](src, p)
            # 一个平台一轮入库：本批只写这个榜，聚簇仍按当天全部条目重算
            result = hot_topics.ingest(
                ctx.db,
                platform=src["name"],
                source_url=_source_url(src),
                items=items,
                day=day,
                captured_at=captured,
            )
        except Exception as exc:  # 单源失败只记 notes，不影响其它源
            notes.append(f"{src['name']}：抓取失败（{type(exc).__name__}: {str(exc)[:60]}）")
            continue
        ok_sources += 1
        ingested += result["items"]
        created += result["newClusters"]
        matched += result["matchedClusters"]
        clusters = result["clusters"]
        notes.append(f"{src['name']}：{len(items)} 条")

    if ok_sources == 0:
        raise RuntimeError("全部来源抓取失败：" + "；".join(notes))

    cluster_rows = hot_topics.list_clusters(ctx.db, day=day, limit=500)
    total_clusters = len(cluster_rows) if cluster_rows else clusters
    recommended = sum(1 for c in cluster_rows if (c.get("score") or 0) >= 70)
    degraded = [n for n in notes if "失败" in n]

    summary = (
        f"{day} {captured} hotboard 采集 {ok_sources}/{len(sources)} 榜 · {ingested} 条 → "
        f"{total_clusters} 个事件（新 {created} / 并入 {matched}，≥70 分 {recommended}）"
    )
    if degraded:
        summary += f" · {len(degraded)} 个源降级"
    return {
        "summary": summary,
        "day": day,
        "capturedAt": captured,
        "items": ingested,
        "sources": ok_sources,
        "sourcesTotal": len(sources),
        "clusters": total_clusters,
        "newClusters": created,
        "matchedClusters": matched,
        "recommended": recommended,
        "degraded": degraded,
        "notes": notes,
    }
