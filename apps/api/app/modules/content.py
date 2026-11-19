from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel
from sqlalchemy import exists, func, or_, select

from .. import models, schemas
from ..core import pagination
from ..models import utcnow
from ..core.deps import ApiError, CurrentUser, DbSession, default_workspace, not_found
from ..services import article_workflow

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
        created_at=item.created_at,
        archived=bool(item.archived),
        archived_at=item.archived_at,
        latest_version=latest,
        draft_title=item.draft_title,
        draft_body=item.draft_body,
        base_style_id=item.base_style_id,
        base_style_name=item.base_style_name or "",
        base_style_prompt=item.base_style_prompt or "",
        base_style_deleted=bool(item.base_style_deleted),
    )


class ArchivePatch(BaseModel):
    archived: bool


class ImageStyleLockIn(schemas.CamelModel):
    style_id: str | None


_SORTABLE = {
    "updatedAt": models.ContentItem.updated_at,
    "createdAt": models.ContentItem.created_at,
    "title": models.ContentItem.title,
}


@router.get("/content", response_model=list[schemas.ContentOut])
def list_content(
    user: CurrentUser,
    db: DbSession,
    response: Response,
    limit: int = Query(default=50, le=200),
    cursor: str | None = Query(default=None),
    fields: str | None = Query(default=None, description="list=瘦身模式，不返回正文"),
    q: str | None = Query(default=None, description="标题关键词"),
    status: str | None = Query(default=None),
    type: str | None = Query(default=None),
    platform: str | None = Query(default=None, description="按平台版本的平台筛"),
    updated_from: datetime | None = Query(default=None, alias="updatedFrom"),
    updated_to: datetime | None = Query(default=None, alias="updatedTo"),
    include_archived: bool = Query(default=False, alias="includeArchived"),
    sort: str = Query(default="updatedAt"),
) -> list[schemas.ContentOut]:
    """稿件列表。

    默认（创作空间侧栏）：未归档、updated_at 倒序；`fields=list` 时不返回
    draft_body / latest_version 正文，最新版本号与平台版本数改走两条聚合查询，
    消灭逐行 N+1——稿件几百篇时侧栏与稿件库首屏都靠它。
    """
    workspace = default_workspace(db, user)
    stmt = select(models.ContentItem).where(
        models.ContentItem.workspace_id == workspace.id
    )
    if not include_archived:
        # 旧库补列前写入的行 archived 为 NULL，一律按未归档处理
        stmt = stmt.where(
            or_(models.ContentItem.archived.is_(None), models.ContentItem.archived.is_(False))
        )
    if q and q.strip():
        stmt = stmt.where(models.ContentItem.title.ilike(f"%{q.strip()}%"))
    if status:
        stmt = stmt.where(models.ContentItem.status == status)
    if type:
        stmt = stmt.where(models.ContentItem.type == type)
    if platform:
        stmt = stmt.where(
            exists().where(
                models.PlatformVariant.content_item_id == models.ContentItem.id,
                models.PlatformVariant.platform == platform,
            )
        )
    if updated_from:
        stmt = stmt.where(models.ContentItem.updated_at >= updated_from)
    if updated_to:
        stmt = stmt.where(models.ContentItem.updated_at <= updated_to)
    # 标题按字母升序符合直觉；时间类仍倒序（新→旧）
    descending = sort != "title"
    sort_column = _SORTABLE.get(sort, models.ContentItem.updated_at)
    rows, next_cursor = pagination.paginate(
        db, stmt, models.ContentItem,
        sort_column=sort_column, cursor=cursor, limit=limit, descending=descending,
    )
    pagination.set_cursor_header(response, next_cursor)

    if fields != "list":
        return [_item_out(row, db) for row in rows]

    # 瘦身模式：一次聚合取最新版本号 + 平台版本数，替代每行两次子查询
    ids = [row.id for row in rows]
    version_no_by_item: dict[str, int] = {}
    variant_count_by_item: dict[str, int] = {}
    if ids:
        for item_id, version_no in db.execute(
            select(
                models.ContentVersion.content_item_id,
                func.max(models.ContentVersion.version_no),
            )
            .where(models.ContentVersion.content_item_id.in_(ids))
            .group_by(models.ContentVersion.content_item_id)
        ):
            version_no_by_item[item_id] = version_no
        for item_id, count in db.execute(
            select(
                models.PlatformVariant.content_item_id,
                func.count(models.PlatformVariant.id),
            )
            .where(models.PlatformVariant.content_item_id.in_(ids))
            .group_by(models.PlatformVariant.content_item_id)
        ):
            variant_count_by_item[item_id] = count
    return [
        schemas.ContentOut(
            id=row.id,
            title=row.title,
            status=row.status,
            type=row.type,
            current_version_id=row.current_version_id,
            updated_at=row.updated_at,
            created_at=row.created_at,
            archived=bool(row.archived),
            archived_at=row.archived_at,
            base_style_id=row.base_style_id,
            base_style_name=row.base_style_name or "",
            base_style_prompt=row.base_style_prompt or "",
            base_style_deleted=bool(row.base_style_deleted),
            version_no=version_no_by_item.get(row.id),
            variant_count=variant_count_by_item.get(row.id, 0),
        )
        for row in rows
    ]


@router.patch("/content/{content_id}/archive", response_model=schemas.ContentOut)
def set_content_archive(
    content_id: str, payload: ArchivePatch, user: CurrentUser, db: DbSession
) -> schemas.ContentOut:
    """归档/恢复稿件。软隐藏：不进侧栏与稿件库默认视图，详情接口照常可开。"""
    item = db.get(models.ContentItem, content_id)
    if not item or item.owner_user_id != user.id:
        raise not_found("稿件")
    item.archived = payload.archived
    item.archived_at = utcnow() if payload.archived else None
    db.commit()
    return _item_out(item, db)


@router.patch("/content/{content_id}/image-style", response_model=schemas.ContentOut)
def lock_content_image_style(
    content_id: str, payload: ImageStyleLockIn, user: CurrentUser, db: DbSession
) -> schemas.ContentOut:
    """把当时的基础风格快照写进稿件。登录即可，只改自己的稿件。"""
    item = db.get(models.ContentItem, content_id)
    if not item or item.owner_user_id != user.id:
        raise not_found("稿件")
    if payload.style_id is None:
        item.base_style_id = None
        item.base_style_name = ""
        item.base_style_prompt = ""
        item.base_style_deleted = False
    else:
        style = db.get(models.ImageStyle, payload.style_id)
        if style is None:
            raise ApiError("style_not_found", "风格不存在", status_code=404)
        if style.purpose != "base":
            raise ApiError("style_not_base", "只能锁定基础风格")
        if not style.enabled:
            raise ApiError("style_disabled", "该风格已停用")
        item.base_style_id = style.id
        item.base_style_name = style.name
        item.base_style_prompt = style.prompt
        item.base_style_deleted = False
    db.commit()
    return _item_out(item, db)


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


_FLOW_STEP_LABELS = {
    "create_outline": "列大纲",
    "generate_article": "生成文章",
    "review_article": "审稿",
    "check_ai_tone": "检查 AI 味",
    "extract_facts": "抽取事实",
    "plan_images": "规划配图",
    "retrieve_content": "检索稿件",
}


def _owned_item(content_id: str, user, db: DbSession) -> models.ContentItem:
    item = db.get(models.ContentItem, content_id)
    if not item or item.owner_user_id != user.id:
        raise not_found("稿件")
    return item


def _latest_version(item: models.ContentItem, db: DbSession) -> models.ContentVersion | None:
    return db.scalar(
        select(models.ContentVersion)
        .where(models.ContentVersion.content_item_id == item.id)
        .order_by(models.ContentVersion.version_no.desc())
        .limit(1)
    )


def _resolve_flow_skill(user, db: DbSession, skill_key: str | None):
    key = (skill_key or "article-drafting").strip() or "article-drafting"
    entry = db.scalar(
        select(models.SkillRegistryEntry).where(
            models.SkillRegistryEntry.owner_user_id == user.id,
            models.SkillRegistryEntry.source == "custom",
            models.SkillRegistryEntry.skill_key == key,
        )
    )
    if entry is None:
        entry = db.scalar(
            select(models.SkillRegistryEntry).where(
                models.SkillRegistryEntry.owner_user_id.is_(None),
                models.SkillRegistryEntry.source == "builtin",
                models.SkillRegistryEntry.skill_key == key,
            )
        )
    if entry is None or not (entry.manifest or {}).get("workflow"):
        raise not_found("写作流程")
    return entry


@router.get("/content/{content_id}/writing-flow")
def get_writing_flow(
    content_id: str, user: CurrentUser, db: DbSession, skillKey: str | None = None
) -> dict:
    """当前稿件对应的写作流程步骤，以及基于最新正文的检查结果。

    不调用模型、不改正文。生成类步骤只展示定义；检查类步骤直接跑本地规则。
    """
    item = _owned_item(content_id, user, db)
    entry = _resolve_flow_skill(user, db, skillKey)
    version = _latest_version(item, db)
    markdown = version.body if version else ""
    title = version.title if version else item.title
    summary = version.summary if version else ""
    manifest = entry.manifest or {}
    checks = {
        "review_article": article_workflow.review_article(
            {"title": title or "", "markdown": markdown or "", "summary": summary or ""},
            manifest.get("reviewRules") or [],
        ),
        "check_ai_tone": article_workflow.check_ai_tone(markdown or ""),
        "extract_facts": article_workflow.extract_facts(markdown or ""),
        "plan_images": article_workflow.plan_images(
            markdown or "", [], None, manifest.get("imagePolicy")
        ),
    }
    steps = []
    for index, step in enumerate(manifest.get("workflow") or [], start=1):
        tool = step.get("tool") if isinstance(step, dict) else None
        if not isinstance(tool, str):
            continue
        steps.append(
            {
                "index": index,
                "tool": tool,
                "label": _FLOW_STEP_LABELS.get(tool, tool),
                "kind": "check" if tool in checks else "generate",
                "result": checks.get(tool),
            }
        )
    return {
        "skillKey": entry.skill_key,
        "skillName": entry.display_name,
        "description": entry.description or "",
        "versionId": version.id if version else None,
        "hasBody": bool((markdown or "").strip()),
        "steps": steps,
    }


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
