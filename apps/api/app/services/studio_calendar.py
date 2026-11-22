"""成稿台排期 ↔ 内容日历的同步。

成稿台的「排期」写在稿件自己的 `scheduled_for` 上，内容日历读的是 `calendar_entries` 表：
两边原本各写各的——在成稿台排了期，内容日历里看不到；在日历里排了期，成稿台也不知道。

这里把成稿台的排期同步成一条日历条目（用 `studio_article_id` 反查回稿件），
取消排期时同步撤掉；日历里手工排的条目（没有 studio_article_id）不受影响。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from .. import models
from ..core.deps import default_workspace


def _parse_at(value) -> datetime | None:
    """稿件里的排期是 ISO 字符串；SQLite 读回的时间是 naive，统一补 UTC。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        at = value
    else:
        try:
            at = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at


def find_entry(db, user_id: str, article_id: str) -> models.CalendarEntry | None:
    """按成稿台稿件找它同步出去的那条日历条目。"""
    return db.scalar(
        select(models.CalendarEntry)
        .where(
            models.CalendarEntry.owner_user_id == user_id,
            models.CalendarEntry.studio_article_id == article_id,
        )
        .order_by(models.CalendarEntry.created_at.desc())
        .limit(1)
    )


def sync_schedule(db, user, article: models.StudioArticle, data: dict) -> models.CalendarEntry | None:
    """按稿件当前的排期同步日历条目；没有排期就把之前同步的那条撤掉。

    幂等：同一篇稿件反复改排期只维护一条条目，不会堆出一串旧期次。
    """
    at = _parse_at(data.get("scheduled_for"))
    entry = find_entry(db, user.id, article.id)
    if at is None:
        if entry is not None:
            db.delete(entry)
            db.flush()
        return None

    title = (data.get("title") or data.get("topic") or "未命名稿件")[:200]
    if entry is None:
        workspace_id = article.workspace_id or default_workspace(db, user).id
        entry = models.CalendarEntry(
            workspace_id=workspace_id,
            owner_user_id=user.id,
            content_item_id=article.content_item_id,
            studio_article_id=article.id,
            title=title,
            planned_at=at,
            status="planned",
        )
        db.add(entry)
    else:
        entry.title = title
        entry.planned_at = at
        entry.status = "planned"
        if article.content_item_id:
            entry.content_item_id = article.content_item_id
    db.flush()
    return entry
