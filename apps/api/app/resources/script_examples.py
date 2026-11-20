"""常用定时脚本示例（单一真相源）。

统一契约：`def run(params, ctx) -> dict`，与内置 manifest 脚本同一执行通道
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
        "key": "batch_drafts",
        "title": "批量建稿",
        "description": "为标题清单各建一篇草稿，同名跳过。演示：写业务数据（ContentItem + 版本）。",
        "code": BATCH_DRAFTS,
    },
]
