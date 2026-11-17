"""热点选题接口。

    GET    /hot-topics/sources                 采集源与启用状态
    POST   /hot-topics/refresh                 采集 → 聚簇 → 评分
    GET    /hot-topics/clusters                事件列表（按分数排序）
    GET    /hot-topics/clusters/{id}           事件详情（成员条目 + 名次采样）
    PATCH  /hot-topics/clusters/{id}           人工状态：watching / ignored / new
    POST   /hot-topics/clusters/{id}/draft     热点 → 稿件（直接成稿）
    GET    /hot-topics/summary                 概要条统计
    GET    /hot-topics/degraded                哪些维度因采样不足没算

创作空间的「灵感」面板仍用 `/hot-topics`（见 modules/ai_content.py），
它优先读这里已经算好分的簇，一条都没有时才回落到单源。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from .. import schemas
from ..core.deps import ApiError, CurrentUser, DbSession, default_workspace, not_found
from ..services import hot_topics

router = APIRouter(tags=["hot-topics"])


@router.get("/hot-topics/sources", response_model=schemas.HotSourcesOut)
def hot_topics_sources() -> schemas.HotSourcesOut:
    return schemas.HotSourcesOut(**hot_topics.sources_status())


@router.post("/hot-topics/refresh", response_model=schemas.HotRefreshOut)
def refresh_hot_topics(
    user: CurrentUser,
    db: DbSession,
    payload: schemas.HotRefreshIn | None = None,
) -> schemas.HotRefreshOut:
    day = (payload.date if payload else None) or hot_topics.today_str()
    try:
        # 冷却在服务层（services/hot_topics.reserve_refresh）：定时任务与手动刷新
        # 面对同一份限速状态，路由层不再自己维护一份进程内计数。
        hot_topics.reserve_refresh()
        result = hot_topics.refresh(db, day=day)
    except hot_topics.HotTopicsError as exc:
        if exc.code == "too_frequent":
            raise ApiError(exc.code, exc.message, status_code=429) from exc
        raise ApiError(exc.code, exc.message, status_code=502, retryable=True) from exc
    return schemas.HotRefreshOut(**result)


@router.get("/hot-topics/clusters", response_model=list[schemas.HotClusterOut])
def list_hot_clusters(
    user: CurrentUser,
    db: DbSession,
    date: str | None = Query(default=None),
    min_score: int = Query(default=0, alias="minScore", ge=0, le=100),
    track: str | None = Query(default=None),
    platform: str | None = Query(default=None),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    sort: str = Query(default="score", pattern="^(score|time|resonance)$"),
    limit: int = Query(default=60, ge=1, le=200),
) -> list[schemas.HotClusterOut]:
    rows = hot_topics.list_clusters(
        db, day=date, min_score=min_score, track=track, platform=platform,
        status=status, q=q, sort=sort, limit=limit,
    )
    return [schemas.HotClusterOut(**row) for row in rows]


@router.get("/hot-topics/clusters/{cluster_id}", response_model=schemas.HotClusterDetailOut)
def get_hot_cluster(
    cluster_id: str, user: CurrentUser, db: DbSession
) -> schemas.HotClusterDetailOut:
    row = hot_topics.get_cluster(db, cluster_id)
    if row is None:
        raise not_found("热点事件")
    return schemas.HotClusterDetailOut(**row)


@router.patch("/hot-topics/clusters/{cluster_id}", response_model=schemas.HotClusterOut)
def patch_hot_cluster(
    cluster_id: str, payload: schemas.HotStatusIn, user: CurrentUser, db: DbSession
) -> schemas.HotClusterOut:
    try:
        row = hot_topics.set_status(db, cluster_id, payload.status)
    except hot_topics.HotTopicsError as exc:
        raise ApiError(exc.code, exc.message) from exc
    if row is None:
        raise not_found("热点事件")
    return schemas.HotClusterOut(**row)


@router.post(
    "/hot-topics/clusters/{cluster_id}/draft",
    response_model=schemas.ContentOut,
    status_code=201,
)
def create_draft_from_cluster(
    cluster_id: str, user: CurrentUser, db: DbSession
) -> schemas.ContentOut:
    from .content import _item_out  # 复用稿件出参，避免两套字段名

    workspace = default_workspace(db, user)
    result = hot_topics.create_draft(db, cluster_id, user, workspace)
    if result is None:
        raise not_found("热点事件")
    item, _cluster = result
    return _item_out(item, db)


@router.get("/hot-topics/summary", response_model=schemas.HotSummaryOut)
def hot_topics_summary(
    user: CurrentUser,
    db: DbSession,
    date: str | None = Query(default=None),
) -> schemas.HotSummaryOut:
    return schemas.HotSummaryOut(**hot_topics.summary(db, day=date))


@router.get("/hot-topics/degraded", response_model=list[schemas.HotDegradedOut])
def hot_topics_degraded(
    user: CurrentUser,
    db: DbSession,
    date: str | None = Query(default=None),
) -> list[schemas.HotDegradedOut]:
    return [schemas.HotDegradedOut(**row) for row in hot_topics.degraded_reasons(db, day=date)]
