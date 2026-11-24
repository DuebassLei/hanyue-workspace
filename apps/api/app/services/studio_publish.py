"""成稿台的归档版本 → 微信草稿任务，以及复盘状态。"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select

from .. import models, schemas
from ..connectors import base as connectors
from ..core.deps import ApiError, not_found
from . import article_blocks as AB, publish_state

WECHAT = "wechat_official"
PLATFORM_ALIASES = {"wechat-mp", "wechat_mp", "wechat", "mp"}
_ASSET_IMAGE = re.compile(r"!\[([^\]\n]*)\]\(<?asset://([\w-]+)>?\)")


def normalize_platforms(platforms: list[str] | None) -> None:
    if any(platform.strip() not in PLATFORM_ALIASES | {WECHAT} for platform in platforms or []):
        raise ApiError("unknown_platform", "成稿台目前仅支持微信公众号（wechat_official）草稿箱", status_code=400)


def check_schedule(value: datetime | None) -> None:
    if value is None:
        return
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    if value <= datetime.now(timezone.utc):
        raise ApiError("scheduled_at_past", "计划发布时间必须晚于当前时间", status_code=400)
    raise ApiError("schedule_separate", "请用「保存排期」记录计划；草稿箱推送不自动执行排期", status_code=422)


def archived_version(db, article: models.StudioArticle) -> models.ContentVersion | None:
    if not article.content_item_id:
        return None
    return db.scalar(
        select(models.ContentVersion)
        .join(models.ContentItem, models.ContentItem.id == models.ContentVersion.content_item_id)
        .where(
            models.ContentVersion.content_item_id == article.content_item_id,
            models.ContentVersion.source_type == "studio",
            models.ContentItem.owner_user_id == article.owner_user_id,
        )
        .order_by(models.ContentVersion.version_no.desc())
        .limit(1)
    )


def draft_payload(db, article, account_id: str) -> schemas.WeChatDraftIn:
    version = archived_version(db, article)
    if version is None:
        raise ApiError("archive_required", "请先用「文章存档」保存要推送的版本", status_code=409)
    cover_id = db.scalar(
        select(models.ContentAsset.asset_id)
        .where(models.ContentAsset.content_version_id == version.id, models.ContentAsset.usage_role == "cover")
        .order_by(models.ContentAsset.sort_order, models.ContentAsset.asset_id)
        .limit(1)
    )
    if not cover_id:
        raise ApiError("thumb_required", "归档版本缺少封面，请选好封面后重新存档", status_code=422)
    inline_ids: list[str] = []

    def placeholder(match: re.Match) -> str:
        caption, asset_id = match.groups()
        if asset_id not in inline_ids:
            inline_ids.append(asset_id)
        number = inline_ids.index(asset_id) + 1
        caption = caption.replace("}", "")
        return "{{image" + str(number) + (f"|{caption}" if caption else "") + "}}"

    body = _ASSET_IMAGE.sub(placeholder, version.body)
    return schemas.WeChatDraftIn(
        account_id=account_id,
        idempotency_key=f"studio-draft:{article.id}:{version.id}:{account_id}",
        content_item_id=version.content_item_id,
        version_id=version.id,
        title=version.title,
        body=body,
        asset_ids=[cover_id, *inline_ids],
    )


def result(job: models.PublishJob) -> dict:
    return {
        "contentItemId": job.content_item_id,
        "versionId": (job.payload_snapshot or {}).get("versionId"),
        "publishJobIds": [job.id],
        "scheduledFor": None,
        "status": public_status(job),
    }


def studio_draft_job(db, article: models.StudioArticle, job_id: str) -> models.PublishJob:
    job = db.get(models.PublishJob, job_id)
    if (
        not job or job.owner_user_id != article.owner_user_id or job.target != "draft"
        or (job.payload_snapshot or {}).get("studioArticleId") != article.id
    ):
        raise not_found("草稿推送任务")
    return job


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def public_status(job: models.PublishJob) -> str:
    if job.status == publish_state.SUCCEEDED:
        return "drafted" if job.target == "draft" else "succeeded"
    if job.status in {publish_state.FAILED, publish_state.NEEDS_ATTENTION, publish_state.CANCELLED}:
        return "failed"
    if (job.payload_snapshot or {}).get("mode") == "manual":
        return "manual"
    if job.status == publish_state.PUBLISHING:
        return "running"
    return "queued"


def review(db, article: models.StudioArticle, data: dict, *, can_retry) -> dict:
    def belongs(job: models.PublishJob) -> bool:
        source = (job.payload_snapshot or {}).get("studioArticleId")
        return source == article.id if source else (
            article.content_item_id is not None and job.content_item_id == article.content_item_id
        )

    owned = select(models.PublishJob).where(models.PublishJob.owner_user_id == article.owner_user_id)
    jobs = {}
    if article.content_item_id:
        for job in db.scalars(owned.where(models.PublishJob.content_item_id == article.content_item_id)):
            if belongs(job):
                jobs[job.id] = job
    for job in db.scalars(owned.order_by(models.PublishJob.created_at.desc(), models.PublishJob.id.desc()).limit(200)):
        if belongs(job):
            jobs[job.id] = job

    latest = {}
    for job in sorted(jobs.values(), key=lambda row: (row.created_at, row.id)):
        latest[(job.platform, job.connected_account_id, job.target)] = job
    accounts = {}
    records = {}
    if latest:
        account_ids = {job.connected_account_id for job in latest.values()}
        accounts = {row.id: row.display_name for row in db.scalars(select(models.ConnectedAccount).where(
            models.ConnectedAccount.id.in_(account_ids),
            models.ConnectedAccount.owner_user_id == article.owner_user_id,
        ))}
        for record in db.scalars(select(models.PublishRecord).where(
            models.PublishRecord.publish_job_id.in_([job.id for job in latest.values()])
        ).order_by(models.PublishRecord.created_at, models.PublishRecord.id)):
            records[record.publish_job_id] = record

    published_at = article.published_at
    platforms = []
    for job in latest.values():
        snapshot = job.payload_snapshot or {}
        record = records.get(job.id)
        status = public_status(job)
        at = _iso(job.updated_at or job.created_at)
        url = None
        draft_media_id = None
        if status == "drafted":
            draft_media_id = job.remote_content_id or snapshot.get("draftMediaId")
            at = snapshot.get("pushedAt") or at
        elif status == "succeeded":
            at = _iso((record.published_at if record else None) or job.updated_at or job.created_at)
            url = (record.permalink if record else None) or snapshot.get("permalink")
            if at:
                stamp = datetime.fromisoformat(at)
                if published_at is None or stamp > datetime.fromisoformat(_iso(published_at)):
                    published_at = stamp
        elif status in {"queued", "manual"} and job.scheduled_at:
            at = _iso(job.scheduled_at)
        error = None
        if status == "failed":
            error = (
                "任务已取消" if job.status == publish_state.CANCELLED
                else job.error_message or job.error_code or "任务失败，请到发布中心查看详情"
            )
        platforms.append({
            "jobId": job.id,
            "platform": job.platform,
            "platformLabel": connectors.get_capability(job.platform).label or job.platform,
            "accountId": job.connected_account_id,
            "accountLabel": accounts.get(job.connected_account_id),
            "versionId": snapshot.get("versionId"),
            "target": job.target,
            "status": status,
            "url": url,
            "at": at,
            "error": error,
            "draftMediaId": draft_media_id,
            "canRetry": snapshot.get("studioArticleId") == article.id and can_retry(job),
        })

    version = archived_version(db, article)
    meta = AB.list_meta(data)
    return {
        "articleId": article.id,
        "contentItemId": article.content_item_id,
        "versionId": version.id if version else None,
        "publishedAt": _iso(published_at),
        "platforms": platforms,
        "stats": {"blocks": meta["blocks"], "chars": meta["chars"], "flagged": meta["review"], "checked": meta["checked"]},
    }
