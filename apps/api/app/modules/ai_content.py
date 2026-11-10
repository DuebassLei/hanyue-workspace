"""热点灵感接口。

- GET /hot-topics：拉取热搜列表（未配置源时返回空）
- GET /hot-topics/status：热点源配置状态

热点只作为创作空间的灵感来源，不再写入选题库；
AI 成文由创作空间的 AI 写作面板直接完成，不依赖选题实体。
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import schemas
from ..core.deps import ApiError
from ..services import hot_topics

router = APIRouter(tags=["ai-content"])


@router.get("/hot-topics/status", response_model=schemas.HotTopicsStatusOut)
def hot_topics_status() -> schemas.HotTopicsStatusOut:
    status = hot_topics.source_status()
    return schemas.HotTopicsStatusOut(**status)


@router.get("/hot-topics", response_model=list[schemas.HotTopicOut])
def list_hot_topics(limit: int = 20) -> list[schemas.HotTopicOut]:
    try:
        items = hot_topics.fetch_hot_topics(limit=limit)
    except hot_topics.HotTopicsError as exc:
        raise ApiError(exc.code, exc.message, status_code=502) from exc
    return [schemas.HotTopicOut(**item) for item in items]
