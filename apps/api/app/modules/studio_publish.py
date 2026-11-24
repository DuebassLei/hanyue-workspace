"""成稿台的微信草稿箱推送和复盘路由。"""

from datetime import datetime

from fastapi import APIRouter
from sqlalchemy import select

from .. import models
from ..core.deps import ApiError, CurrentUser, DbSession, not_found
from ..core.rbac import AccessDep, require_permission
from ..schemas import CamelModel
from ..services import studio_publish as SPUB, studio_repo
from . import studio, wechat_mp

router = APIRouter(tags=["studio"], dependencies=[require_permission("studio.use")])


class StudioPublishIn(CamelModel):
    platforms: list[str] | None = None
    account_id: str | None = None
    scheduled_at: datetime | None = None
    dry_run: bool = False


def _load(db, user, article_id: str) -> models.StudioArticle:
    row = studio_repo.get_article(db, article_id, owner_user_id=user.id)
    if not row:
        raise not_found("稿件")
    return row


@router.post("/studio/articles/{article_id}/publish", response_model=dict)
def publish_article(article_id: str, payload: StudioPublishIn, db: DbSession, user: CurrentUser, access: AccessDep) -> dict:
    article = _load(db, user, article_id)
    SPUB.normalize_platforms(payload.platforms)
    SPUB.check_schedule(payload.scheduled_at)
    if payload.dry_run:
        archive = studio.export_to_content(article_id, db, user)
        return {
            "contentItemId": archive.get("contentId") or archive.get("contentItemId"),
            "versionId": archive["versionId"],
            "publishJobIds": [],
            "scheduledFor": None,
        }
    if not access.can("publishing.use"):
        raise ApiError("permission_denied", "缺少权限：publishing.use", status_code=403)
    account_id = payload.account_id or db.scalar(
        select(models.ConnectedAccount.id).where(
            models.ConnectedAccount.owner_user_id == user.id,
            models.ConnectedAccount.platform == wechat_mp.PLATFORM,
            models.ConnectedAccount.auth_source == "app_secret",
            models.ConnectedAccount.status == "connected",
        ).order_by(models.ConnectedAccount.created_at.desc(), models.ConnectedAccount.id.desc()).limit(1)
    )
    if not account_id:
        raise ApiError("account_not_bound", "请先在发布中心绑定并验证公众号账号", status_code=409)
    draft = SPUB.draft_payload(db, article, account_id)
    return SPUB.result(wechat_mp.enqueue_draft(db, user, draft, studio_article_id=article.id))


@router.post(
    "/studio/articles/{article_id}/publish/{job_id}/retry",
    response_model=dict,
    dependencies=[require_permission("publishing.use")],
)
def retry_draft(article_id: str, job_id: str, db: DbSession, user: CurrentUser) -> dict:
    job = SPUB.studio_draft_job(db, _load(db, user, article_id), job_id)
    return SPUB.result(wechat_mp.retry_studio_draft(db, job))


@router.get("/studio/articles/{article_id}/review", response_model=dict)
def review_article(article_id: str, db: DbSession, user: CurrentUser, access: AccessDep) -> dict:
    article = _load(db, user, article_id)
    return SPUB.review(
        db, article, studio_repo.row_to_dict(article),
        can_retry=lambda job: access.can("publishing.use") and wechat_mp.can_retry_draft(job),
    )
