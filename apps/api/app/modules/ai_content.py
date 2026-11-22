"""热点灵感接口。

- GET /hot-topics：读已评分热点簇（没有数据时返回空）
- GET /hot-topics/status：热点数据现状（当天入库了哪些平台）

热点只作为创作空间的灵感来源，不再写入选题库；
AI 成文由创作空间的 AI 写作面板直接完成，不依赖选题实体。
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import schemas
from ..core.deps import ApiError, DbSession
from ..services import hot_topics

router = APIRouter(tags=["ai-content"])


@router.get("/hot-topics/status", response_model=schemas.HotTopicsStatusOut)
def hot_topics_status(db: DbSession) -> schemas.HotTopicsStatusOut:
    status = hot_topics.source_status(db)
    return schemas.HotTopicsStatusOut(**status)


@router.get("/hot-topics", response_model=list[schemas.HotTopicOut])
def list_hot_topics(db: DbSession, limit: int = 20) -> list[schemas.HotTopicOut]:
    # 只读已评分簇：采集已经下放到平台定时任务，这里不再回落抓外网
    try:
        items = hot_topics.fetch_hot_topics(db, limit=limit)
    except hot_topics.HotTopicsError as exc:
        raise ApiError(exc.code, exc.message, status_code=502) from exc
    return [schemas.HotTopicOut(**item) for item in items]
