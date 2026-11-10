from __future__ import annotations

from fastapi import APIRouter, Query, Response
from sqlalchemy import func, select

from .. import models, schemas
from ..core import pagination
from ..core.deps import CurrentUser, DbSession, default_workspace, not_found

router = APIRouter(tags=["content"])


def _version_out(version: models.ContentVersion) -> schemas.VersionOut:
    return schemas.VersionOut(
        id=version.id,
        version_no=version.version_no,
        title=version.title,
        body=version.body,
        summary=version.summary,
        source_type=version.source_type,
        created_at=version.created_at,
    )


def _item_out(item: models.ContentItem, db: DbSession | None = None) -> schemas.ContentOut:
    latest = None
    if db is not None:
        version = db.scalar(
            select(models.ContentVersion)
            .where(models.ContentVersion.content_item_id == item.id)
            .order_by(models.ContentVersion.version_no.desc())
            .limit(1)
        )
        latest = _version_out(version) if version else None
    return schemas.ContentOut(
        id=item.id,
        title=item.title,
        status=item.status,
        type=item.type,
        current_version_id=item.current_version_id,
        updated_at=item.updated_at,
        latest_version=latest,
        draft_title=item.draft_title,
        draft_body=item.draft_body,
    )


@router.get("/content", response_model=list[schemas.ContentOut])
def list_content(
    user: CurrentUser,
    db: DbSession,
    response: Response,
    limit: int = Query(default=50, le=200),
    cursor: str | None = Query(default=None),
) -> list[schemas.ContentOut]:
    workspace = default_workspace(db, user)
    stmt = select(models.ContentItem).where(
        models.ContentItem.workspace_id == workspace.id
    )
    rows, next_cursor = pagination.paginate(
        db, stmt, models.ContentItem,
        sort_column=models.ContentItem.updated_at, cursor=cursor, limit=limit,
    )
    pagination.set_cursor_header(response, next_cursor)
    return [_item_out(row, db) for row in rows]


@router.post("/content", response_model=schemas.ContentOut, status_code=201)
def create_content(
    payload: schemas.ContentIn, user: CurrentUser, db: DbSession
) -> schemas.ContentOut:
    workspace = default_workspace(db, user)
    item = models.ContentItem(
        workspace_id=workspace.id,
        owner_user_id=user.id,
        title=payload.title,
        type=payload.type,
    )
    db.add(item)
    db.flush()
    version = models.ContentVersion(
        content_item_id=item.id, version_no=1, title=payload.title, body=""
    )
    db.add(version)
    db.flush()
    item.current_version_id = version.id
    db.commit()
    return _item_out(item, db)


@router.get("/content/{content_id}", response_model=schemas.ContentDetail)
def get_content(content_id: str, user: CurrentUser, db: DbSession) -> schemas.ContentDetail:
    item = db.get(models.ContentItem, content_id)
    if not item or item.owner_user_id != user.id:
        raise not_found("稿件")
    versions = db.scalars(
        select(models.ContentVersion)
        .where(models.ContentVersion.content_item_id == item.id)
        .order_by(models.ContentVersion.version_no.desc())
    ).all()
    variants = db.scalars(
        select(models.PlatformVariant)
        .where(models.PlatformVariant.content_item_id == item.id)
        .order_by(models.PlatformVariant.created_at.desc())
    ).all()
    return schemas.ContentDetail(
        item=_item_out(item, db),
        versions=[_version_out(v) for v in versions],
        variants=[
            schemas.VariantOut(
                id=v.id,
                platform=v.platform,
                title=v.title,
                body=v.body,
                tags=v.tags or [],
                status=v.status,
                revision_no=v.revision_no,
            )
            for v in variants
        ],
    )


@router.patch("/content/{content_id}/draft", response_model=schemas.ContentOut)
def save_draft(
    content_id: str, payload: schemas.DraftPatch, user: CurrentUser, db: DbSession
) -> schemas.ContentOut:
    """自动保存草稿。只更新 draft_title / draft_body，不创建版本记录。
    前端 debounce 后调用，用于防丢失；手动「存为版本」才调用 POST .../versions。
    """
    item = db.get(models.ContentItem, content_id)
    if not item or item.owner_user_id != user.id:
        raise not_found("稿件")
    if payload.title is not None:
        item.draft_title = payload.title
    if payload.body is not None:
        item.draft_body = payload.body
    db.commit()
    return _item_out(item, db)


@router.post("/content/{content_id}/versions", response_model=schemas.VersionOut, status_code=201)
def create_version(
    content_id: str, payload: schemas.VersionIn, user: CurrentUser, db: DbSession
) -> schemas.VersionOut:
    item = db.get(models.ContentItem, content_id)
    if not item or item.owner_user_id != user.id:
        raise not_found("稿件")
    max_no = db.scalar(
        select(func.max(models.ContentVersion.version_no)).where(
            models.ContentVersion.content_item_id == item.id
        )
    )
    version = models.ContentVersion(
        content_item_id=item.id,
        version_no=(max_no or 0) + 1,
        parent_version_id=item.current_version_id,
        source_type=payload.source_type,
        model_id=payload.model_id,
        title=payload.title or item.title,
        body=payload.body,
        summary=payload.summary,
    )
    db.add(version)
    db.flush()
    item.current_version_id = version.id
    if payload.title:
        item.title = payload.title
    item.status = "draft"
    db.commit()
    return _version_out(version)


@router.post(
    "/content/{content_id}/platform-variants",
    response_model=schemas.VariantOut,
    status_code=201,
)
def create_variant(
    content_id: str, payload: schemas.VariantIn, user: CurrentUser, db: DbSession
) -> schemas.VariantOut:
    item = db.get(models.ContentItem, content_id)
    if not item or item.owner_user_id != user.id:
        raise not_found("稿件")
    max_rev = db.scalar(
        select(func.max(models.PlatformVariant.revision_no)).where(
            models.PlatformVariant.content_item_id == item.id,
            models.PlatformVariant.platform == payload.platform,
        )
    )
    variant = models.PlatformVariant(
        content_item_id=item.id,
        base_version_id=payload.base_version_id or item.current_version_id,
        platform=payload.platform,
        revision_no=(max_rev or 0) + 1,
        title=payload.title or item.title,
        body=payload.body,
        tags=payload.tags,
    )
    db.add(variant)
    db.commit()
    return schemas.VariantOut(
        id=variant.id,
        platform=variant.platform,
        title=variant.title,
        body=variant.body,
        tags=variant.tags or [],
        status=variant.status,
        revision_no=variant.revision_no,
    )


@router.delete("/content/{content_id}", status_code=204)
def delete_content(content_id: str, user: CurrentUser, db: DbSession) -> None:
    """删除稿件及其全部下游数据。

    级联范围：版本、平台版本、审阅评论、发布任务、发布记录、指标快照、日历排期。
    指标快照通过发布任务/记录的 remote_content_id 反查，一并清理。
    """
    item = db.get(models.ContentItem, content_id)
    if not item or item.owner_user_id != user.id:
        raise not_found("稿件")

    # 1. 平台版本 → 发布任务 → 发布记录 + 指标快照
    variants = db.scalars(
        select(models.PlatformVariant).where(
            models.PlatformVariant.content_item_id == item.id
        )
    ).all()
    variant_ids = [v.id for v in variants]
    publish_jobs = (
        db.scalars(
            select(models.PublishJob).where(
                models.PublishJob.platform_variant_id.in_(variant_ids)
            )
        ).all()
        if variant_ids
        else []
    )
    remote_ids: set[str] = set()
    for job in publish_jobs:
        if job.remote_content_id:
            remote_ids.add(job.remote_content_id)
        for record in db.scalars(
            select(models.PublishRecord).where(
                models.PublishRecord.publish_job_id == job.id
            )
        ).all():
            if record.remote_content_id:
                remote_ids.add(record.remote_content_id)
            db.delete(record)
        db.delete(job)
    if remote_ids:
        for snapshot in db.scalars(
            select(models.MetricSnapshot).where(
                models.MetricSnapshot.owner_user_id == user.id,
                models.MetricSnapshot.remote_content_id.in_(remote_ids),
            )
        ).all():
            db.delete(snapshot)

    # 2. 版本、平台版本、审阅评论、日历排期
    for version in db.scalars(
        select(models.ContentVersion).where(
            models.ContentVersion.content_item_id == item.id
        )
    ).all():
        db.delete(version)
    for variant in variants:
        db.delete(variant)
    for comment in db.scalars(
        select(models.ReviewComment).where(
            models.ReviewComment.content_item_id == item.id
        )
    ).all():
        db.delete(comment)
    for entry in db.scalars(
        select(models.CalendarEntry).where(
            models.CalendarEntry.content_item_id == item.id
        )
    ).all():
        db.delete(entry)

    db.delete(item)
    db.commit()


# ---------- calendar ----------
@router.get("/calendar", response_model=list[schemas.CalendarOut])
def list_calendar(user: CurrentUser, db: DbSession) -> list[schemas.CalendarOut]:
    workspace = default_workspace(db, user)
    rows = db.scalars(
        select(models.CalendarEntry)
        .where(models.CalendarEntry.workspace_id == workspace.id)
        .order_by(models.CalendarEntry.planned_at)
    ).all()
    return [
        schemas.CalendarOut(
            id=row.id,
            title=row.title,
            planned_at=row.planned_at,
            platform=row.platform,
            status=row.status,
            content_item_id=row.content_item_id,
        )
        for row in rows
    ]


@router.post("/calendar", response_model=schemas.CalendarOut, status_code=201)
def create_calendar(
    payload: schemas.CalendarIn, user: CurrentUser, db: DbSession
) -> schemas.CalendarOut:
    workspace = default_workspace(db, user)
    entry = models.CalendarEntry(
        workspace_id=workspace.id,
        owner_user_id=user.id,
        title=payload.title,
        planned_at=payload.planned_at,
        platform=payload.platform,
        content_item_id=payload.content_item_id,
    )
    db.add(entry)
    db.commit()
    return schemas.CalendarOut(
        id=entry.id,
        title=entry.title,
        planned_at=entry.planned_at,
        platform=entry.platform,
        status=entry.status,
        content_item_id=entry.content_item_id,
    )


# ---------- week review ----------
@router.get("/reviews", response_model=list[schemas.WeekReviewOut])
def list_reviews(user: CurrentUser, db: DbSession) -> list[schemas.WeekReviewOut]:
    workspace = default_workspace(db, user)
    rows = db.scalars(
        select(models.WeekReview)
        .where(models.WeekReview.workspace_id == workspace.id)
        .order_by(models.WeekReview.week_start.desc())
    ).all()
    return [
        schemas.WeekReviewOut(
            id=row.id,
            week_start=row.week_start,
            planned_count=row.planned_count,
            published_count=row.published_count,
            learnings=row.learnings,
            blockers=row.blockers,
            next_week_focus=row.next_week_focus,
        )
        for row in rows
    ]


@router.post("/reviews", response_model=schemas.WeekReviewOut, status_code=201)
def create_review(
    payload: schemas.WeekReviewIn, user: CurrentUser, db: DbSession
) -> schemas.WeekReviewOut:
    workspace = default_workspace(db, user)
    review = models.WeekReview(
        workspace_id=workspace.id, owner_user_id=user.id, **payload.model_dump()
    )
    db.add(review)
    db.commit()
    return schemas.WeekReviewOut(
        id=review.id,
        week_start=review.week_start,
        planned_count=review.planned_count,
        published_count=review.published_count,
        learnings=review.learnings,
        blockers=review.blockers,
        next_week_focus=review.next_week_focus,
    )
