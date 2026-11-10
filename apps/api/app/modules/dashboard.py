from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from sqlalchemy import func, select

from .. import models, schemas
from ..connectors import base as connectors
from ..core.deps import CurrentUser, DbSession, default_workspace
from ..services import publish_state

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=schemas.DashboardOut)
def dashboard(user: CurrentUser, db: DbSession) -> schemas.DashboardOut:
    workspace = default_workspace(db, user)
    now = datetime.now(timezone.utc)
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_end = week_start + timedelta(days=7)

    planned = db.scalar(
        select(func.count())
        .select_from(models.CalendarEntry)
        .where(
            models.CalendarEntry.workspace_id == workspace.id,
            models.CalendarEntry.planned_at >= week_start,
            models.CalendarEntry.planned_at < week_end,
        )
    ) or 0
    published = db.scalar(
        select(func.count())
        .select_from(models.PublishJob)
        .where(
            models.PublishJob.workspace_id == workspace.id,
            models.PublishJob.status == publish_state.SUCCEEDED,
            models.PublishJob.created_at >= week_start,
        )
    ) or 0
    pending_review = db.scalar(
        select(func.count())
        .select_from(models.PublishJob)
        .where(
            models.PublishJob.workspace_id == workspace.id,
            models.PublishJob.status.in_([publish_state.PENDING_REVIEW, publish_state.APPROVED]),
        )
    ) or 0
    failed = db.scalar(
        select(func.count())
        .select_from(models.PublishJob)
        .where(
            models.PublishJob.workspace_id == workspace.id,
            models.PublishJob.status.in_([publish_state.FAILED, publish_state.NEEDS_ATTENTION]),
        )
    ) or 0
    accounts = db.scalars(
        select(models.ConnectedAccount).where(
            models.ConnectedAccount.workspace_id == workspace.id
        )
    ).all()
    unsynced = sum(1 for a in accounts if a.last_synced_at is None)
    recent = db.scalars(
        select(models.ContentItem)
        .where(models.ContentItem.workspace_id == workspace.id)
        .order_by(models.ContentItem.updated_at.desc())
        .limit(5)
    ).all()

    return schemas.DashboardOut(
        publish_progress={
            "planned": planned,
            "published": published,
            "ratio": round(published / planned, 2) if planned else None,
        },
        pending_review=pending_review,
        failed_jobs=failed,
        unsynced_accounts=unsynced,
        platform_status=[
            {
                "platform": a.platform,
                "label": connectors.get_capability(a.platform).label,
                "status": a.status,
                "autoPublishEnabled": a.auto_publish_enabled,
                "lastSyncedAt": a.last_synced_at.isoformat() if a.last_synced_at else None,
            }
            for a in accounts
        ],
        recent_content=[
            {
                "id": c.id,
                "title": c.title,
                "status": c.status,
                "updatedAt": c.updated_at.isoformat(),
            }
            for c in recent
        ],
        ai_status={
            "modelConnections": db.scalar(
                select(func.count())
                .select_from(models.ModelConnection)
                .where(models.ModelConnection.owner_user_id == user.id)
            )
            or 0,
            "note": "未配置供应商凭据时生成请求会明确返回 provider_unavailable，不产生占位内容",
        },
    )
