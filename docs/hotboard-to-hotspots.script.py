"""采集 hotboard 文章数据 → 接入热点选题（通用定时任务 / 用户脚本）。

数据源说明
----------
https://yiktt.github.io/hotboard/ 是纯前端聚合页，HTML 里没有数据；
页面 JS 从 https://api.codelife.cc/api/top/list?lang=cn&id=<cid> 拉取榜单。
因此本脚本直接打后者，返回结构：{code:200, data:[{index,title,hotValue,link}]}。

接入方式
--------
通用定时任务进程内执行，可 import app.services 并拿到真实 DB 会话（ctx.db）。
本脚本复用 services/hot_topics.py 中 refresh() 的同一条落库链路：
    _ingest(灌条目) → _upsert_clusters(聚簇+评分) → commit
这样聚簇阈值、100 分评分、item_uid/fingerprint 幂等都和手动刷新完全一致。

脚本契约：run(params, ctx) -> dict
    ctx.db            真实 DB 会话
    ctx.owner_user_id 任务所属用户
    params            定时任务里配的 JSON 参数（见下方 PARAMS 说明）

params 可选项：
    boards    dict  {平台名: cid}，覆盖默认榜单清单
    limit     int   每个榜单取前 N 条，1~50，默认 30
    timeout   int   单次请求超时秒数，1~60，默认 12
    date      str   指定归档日期 YYYY-MM-DD，默认今天
"""

import re

import requests

from app.services import hot_topics

# 榜单 cid 取自 hotboard 页面 hot.js 的 SOURCES 定义；platform 名即落库的 platform。
# 需要别的榜单：在 hot.js 里查对应 cid，往这里加一行，或用 params.boards 覆盖。
DEFAULT_BOARDS = {
    "知乎热榜": "mproPpoq6O",
    "百度热点": "Jb0vmloB1G",
    "微信热文": "WnBe01o371",
    "掘金": "QaqeEaVe9R",
    "虎嗅": "5VaobgvAj1",
    "少数派": "Y2KeDGQdNP",
    "IT之家": "74Kvx59dkx",
    "36氪": "Q1Vd5Ko85R",
}

_API = "https://api.codelife.cc/api/top/list"


def _to_int(value):
    """hotValue: '1939万' / '1.2万' / '5198' / '' → int 或 None。"""
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


def _to_rank(value):
    """index 在不同源里可能是 int 也可能是字符串数字。"""
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _fetch(cid, timeout):
    response = requests.get(
        _API,
        params={"lang": "cn", "id": cid},
        headers={
            "Accept": "application/json",
            "User-Agent": "hanyue-scheduled-script",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 200 or not isinstance(payload.get("data"), list):
        raise ValueError(f"异常响应 code={payload.get('code')}")
    return payload["data"]


def run(params, ctx):
    boards = params.get("boards") or DEFAULT_BOARDS
    if not isinstance(boards, dict) or not boards:
        raise ValueError("params.boards 必须是非空对象 {平台名: cid}")
    limit = max(1, min(int(params.get("limit") or 30), 50))
    timeout = max(1, min(int(params.get("timeout") or 12), 60))

    day = params.get("date") or hot_topics.today_str()
    captured_at = hot_topics.now_stamp()

    rows = []
    notes = []
    ok_sources = 0
    for platform, cid in boards.items():
        try:
            data = _fetch(cid, timeout)
        except Exception as exc:  # 单源失败只记 notes，不影响其它源
            notes.append(f"{platform}：抓取失败（{type(exc).__name__}）")
            continue
        ok_sources += 1
        for item in data[:limit]:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            rows.append({
                "platform": str(platform),
                "title": title,
                "rank": _to_rank(item.get("index")),
                "hot": _to_int(item.get("hotValue")),
                "url": str(item.get("link") or ""),
            })
        if not data:
            notes.append(f"{platform}：解析到 0 行（selector_may_changed）")

    if ok_sources == 0:
        raise RuntimeError("所有榜单源都抓取失败：" + "；".join(notes))

    # 与 hot_topics.refresh() 同一条落库链路：灌条目 → 聚簇评分 → 提交。
    members = hot_topics._ingest(ctx.db, day, rows, captured_at)
    created, matched = hot_topics._upsert_clusters(ctx.db, day, members, captured_at)
    ctx.db.commit()

    clusters = hot_topics.list_clusters(ctx.db, day=day, limit=500)
    recommended = sum(1 for c in clusters if c["score"] >= 70)

    return {
        "summary": (
            f"采集 {len(rows)} 条文章 → {len(clusters)} 个热点事件，"
            f"新建 {created} 个，≥70 分 {recommended} 个"
        ),
        "day": day,
        "capturedAt": captured_at,
        "items": len(rows),
        "clusters": len(clusters),
        "newClusters": created,
        "matchedClusters": matched,
        "recommended": recommended,
        "notes": notes,
    }
