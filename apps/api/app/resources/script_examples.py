"""常用定时脚本示例（单一真相源）。

统一契约：`def run(params, ctx) -> dict`，与用户脚本同一执行通道（进程内全依赖）
（进程内、真实 DB、项目全部依赖可用）。ctx 是 ScriptContext：
    ctx.db             当前数据库会话
    ctx.owner_user_id  任务所属用户
    ctx.task_id / ctx.run_id / ctx.job_id / ctx.task_type  触发账本信息
返回 dict，界面摘要优先取 result['summary']。

这里的每份代码都会被测试逐个「编译 + 契约 + 回滚执行」验证（见
tests/test_script_examples.py），改示例请同步跑测试。
"""

from __future__ import annotations

CONTENT_STATS = '''"""创作数据统计：按状态统计我的稿件数量，产出一句摘要。"""
from sqlalchemy import func, select

from app import models


def run(params, ctx):
    db = ctx.db
    rows = db.execute(
        select(models.ContentItem.status, func.count(models.ContentItem.id))
        .where(models.ContentItem.owner_user_id == ctx.owner_user_id)
        .group_by(models.ContentItem.status)
    ).all()
    counts = {status: int(count) for status, count in rows}
    total = sum(counts.values())
    drafts = counts.get("draft", 0)
    return {
        "summary": f"共 {total} 篇稿件：草稿 {drafts}、"
        + "、".join(f"{k} {v}" for k, v in counts.items() if k != "draft"),
        "counts": counts,
    }
'''

CONTENT_WEEKLY = '''"""草稿库周报：列出最近 7 天有更新的稿件标题。"""
from datetime import timedelta

from sqlalchemy import select

from app import models
from app.services.scheduled_tasks import now_utc


def run(params, ctx):
    db = ctx.db
    days = int(params.get("days") or 7)
    since = now_utc() - timedelta(days=days)
    items = db.scalars(
        select(models.ContentItem)
        .where(
            models.ContentItem.owner_user_id == ctx.owner_user_id,
            models.ContentItem.updated_at >= since,
        )
        .order_by(models.ContentItem.updated_at.desc())
        .limit(50)
    ).all()
    titles = [item.title for item in items]
    summary = f"最近 {days} 天更新 {len(titles)} 篇" + (
        "：" + "、".join(titles[:5]) if titles else ""
    )
    return {
        "summary": summary,
        "days": days,
        "titles": titles,
    }
'''

HOT_SNAPSHOT = '''"""热点榜快照：抓当前榜单热度最高的 N 条，汇总成一句话。"""
from sqlalchemy import select

from app import models


def run(params, ctx):
    db = ctx.db
    limit = max(1, min(int(params.get("limit") or 10), 50))
    rows = db.scalars(
        select(models.HotItem)
        .order_by(models.HotItem.hot.desc().nullslast(), models.HotItem.rank.asc().nullslast())
        .limit(limit)
    ).all()
    titles = [row.title for row in rows]
    platforms = sorted({row.platform for row in rows})
    return {
        "summary": f"当前热度 Top {len(titles)}（来源：{'、'.join(platforms) or '暂无数据'}）"
        + ("，榜首：" + titles[0] if titles else ""),
        "titles": titles,
    }
'''

HTTP_FETCH = '''"""外部接口采集示例：GET 一个 JSON 接口，把关键字段带回结果（不落库）。"""
import requests


def run(params, ctx):
    url = str(params.get("url") or "").strip()
    if not url:
        raise ValueError("请在任务参数里配置 url，例如 https://api.github.com/zen")
    timeout = max(1, min(int(params.get("timeout") or 15), 60))
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "hanyue-scheduled-script"})
    response.raise_for_status()
    payload = response.json() if "json" in (response.headers.get("content-type") or "") else response.text[:500]
    return {
        "summary": f"GET {url} → {response.status_code}",
        "status": response.status_code,
        "payload": payload,
    }
'''

# 官方脚本（不是玩具示例）：热点选题页的数据就靠它定时刷新。
# 采集口径的唯一真相是 app/hot_sources/catalog.yaml，链路真相是 services.hot_topics.refresh；
# 脚本本身只做「参数拆包 + 调用」，所以加平台、改评分都不用动它。
HOT_BOARD_REFRESH = '''"""热点榜单采集（官方脚本）：抓一轮默认数据源，写入/更新热点库。

链路：catalog.yaml 里启用的源 → 采集 → hot_items(新增/更新) → 聚簇 → 评分。
具体逻辑全在 app.services.hot_topics.refresh，这里只负责参数与摘要。

参数（都可省略）：
    platforms  平台名数组，如 ["微博", "知乎", "酷安"]；留空 = catalog 里全部启用源

想加/减平台、调采集细节：改 app/hot_sources/catalog.yaml 或 hot_topics 服务，
不用改这个脚本；只想临时少跑几个平台，就在任务参数里写 platforms。
"""
from app.hot_sources import load_catalog
from app.services import hot_topics


def run(params, ctx):
    raw = params.get("platforms")
    if raw in (None, "", []):
        platforms = None  # None = catalog 里全部启用源
    elif isinstance(raw, list):
        platforms = [str(name).strip() for name in raw if str(name).strip()]
    else:
        raise ValueError('platforms 必须是平台名数组，例如 ["微博", "知乎"]')

    if platforms:
        known = {source["name"] for source in load_catalog()["sources"]}
        unknown = [name for name in platforms if name not in known]
        if unknown:
            raise ValueError(
                "平台名不在 catalog 里：" + "、".join(unknown)
                + "；可选：" + "、".join(sorted(known))
            )

    result = hot_topics.refresh(ctx.db, platforms=platforms)
    notes = result.get("notes") or []
    # notes 每个源都会有一条（「微博：50 条」是成功计数），摘要里只挑真降级的那些，
    # 否则界面上会写「22 条降级」，把正常采集说成故障。
    degraded = [
        note for note in notes
        if any(mark in note for mark in ("失败", "selector_may_changed", "已跳过", "未知采集类型"))
    ]
    summary = (
        f"{result['day']} {result['capturedAt']} 采集 {result['items']} 条 · "
        f"归并 {result['clusters']} 个事件"
        f"（新 {result['newClusters']} / 并入 {result['matchedClusters']}）"
    )
    if degraded:
        summary += f" · {len(degraded)} 个源降级：" + "；".join(degraded[:3])
    return {
        "summary": summary,
        "day": result["day"],
        "capturedAt": result["capturedAt"],
        "items": result["items"],
        "clusters": result["clusters"],
        "newClusters": result["newClusters"],
        "matchedClusters": result["matchedClusters"],
        "degraded": degraded,
        "notes": notes,
    }
'''

BATCH_DRAFTS = '''"""批量建稿：为参数里的标题清单各建一篇草稿，同名已存在则跳过。"""
from sqlalchemy import select

from app import models
from app.core.deps import default_workspace


def run(params, ctx):
    db = ctx.db
    titles = params.get("titles") or []
    if not isinstance(titles, list):
        raise ValueError("titles 必须是字符串数组")
    user = db.get(models.User, ctx.owner_user_id)
    if user is None:
        raise ValueError("任务所属用户不存在")
    workspace = default_workspace(db, user)
    existing = {
        row.title
        for row in db.scalars(
            select(models.ContentItem).where(
                models.ContentItem.owner_user_id == ctx.owner_user_id
            )
        ).all()
    }
    created, skipped = [], []
    for title in titles:
        title = str(title).strip()[:200]
        if not title:
            continue
        if title in existing:
            skipped.append(title)
            continue
        item = models.ContentItem(
            workspace_id=workspace.id,
            owner_user_id=ctx.owner_user_id,
            title=title,
            type="article",
        )
        db.add(item)
        db.flush()
        version = models.ContentVersion(
            content_item_id=item.id, version_no=1, title=title, body=""
        )
        db.add(version)
        db.flush()
        item.current_version_id = version.id
        item.draft_title = title
        existing.add(title)
        created.append(title)
    db.commit()
    return {
        "summary": f"新建草稿 {len(created)} 篇，跳过 {len(skipped)} 篇（同名已存在）",
        "created": created,
        "skipped": skipped,
    }
'''

EXAMPLES: list[dict] = [
    {
        "key": "content_stats",
        "title": "创作数据统计",
        "description": "按状态统计我的稿件数量，产出一句摘要。演示：ctx.db + SQLAlchemy 聚合查询。",
        "code": CONTENT_STATS,
    },
    {
        "key": "content_weekly",
        "title": "草稿库周报",
        "description": "列出最近 N 天有更新的稿件标题，可配 days 参数。演示：时间窗口查询 + 摘要。",
        "code": CONTENT_WEEKLY,
    },
    {
        "key": "hot_snapshot",
        "title": "热点榜快照",
        "description": "抓当前热榜热度最高的 N 条并汇总。演示：读取系统级数据（hot_items）。",
        "code": HOT_SNAPSHOT,
    },
    {
        "key": "http_fetch",
        "title": "外部接口采集",
        "description": "GET 一个 JSON 接口带回结果，不落库。演示：requests 全依赖可用 + 参数校验。",
        "code": HTTP_FETCH,
    },
    {
        "key": "hot_board_refresh",
        "title": "热点榜单采集（官方）",
        "description": "抓一轮默认数据源写入热点库（新增/更新条目 → 聚簇 → 评分）。"
        "可配 platforms 只跑部分平台，留空跑 catalog.yaml 全部启用源。",
        "code": HOT_BOARD_REFRESH,
    },
    {
        "key": "batch_drafts",
        "title": "批量建稿",
        "description": "为标题清单各建一篇草稿，同名跳过。演示：写业务数据（ContentItem + 版本）。",
        "code": BATCH_DRAFTS,
    },
]
