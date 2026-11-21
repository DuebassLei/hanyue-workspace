"""成稿台 → 文章管理 / 发布队列的桥。

只做三件事，全部围绕「成稿台的稿件」这一侧：

- 把稿件导出后的结果（`modules.studio.export_to_content` 的返回值）接上发布任务；
- 按 platform 建 `publish_jobs`（组装口径照抄 `modules/publishing.create_job`），
  平台能力不足时只落「手动模式」任务，绝不假装发布成功；
- 按 `contentItemId` 反查发布任务与结果，聚合成前端复盘表。

平台常量与状态机都复用既有实现：平台清单来自 `connectors.PLATFORMS`，
状态常量来自 `publish_state`，不做第二套定义。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import models
from ..connectors import base as connectors
from ..core import queue
from ..core.deps import ApiError
from . import article_blocks as AB, publish_state, studio_repo

# 契约示例里写的是 `wechat-mp`，代码里的平台码是 `wechat_official`（connectors.PLATFORMS）。
# 两种写法都认，统一归一到平台码，避免前端按契约传值直接 400。
PLATFORM_ALIASES = {
    "wechat-mp": "wechat_official",
    "wechat_mp": "wechat_official",
    "wechat": "wechat_official",
    "mp": "wechat_official",
}

# 同一次「立即发布」在这段时间内重复提交视为同一次操作（防双击重复建任务）。
# 超过窗口再点，视为用户确实要再发一次。
IDEMPOTENCY_WINDOW_SECONDS = 300

# 复盘兜底扫描：同一用户最近多少条任务里找「同一篇稿件重新导出」的历史任务
REVIEW_LOOKBACK_JOBS = 200


def available_platforms() -> list[str]:
    """当前部署真正接入了的平台（未接入的渠道只作占位，不接受发布）。"""
    return list(connectors.PLATFORMS)


def _label(platform: str) -> str:
    return connectors.get_capability(platform).label or platform


def _platform_hint() -> str:
    platforms = available_platforms()
    if not platforms:
        return "当前部署还没有接入任何发布平台"
    return "、".join(f"{platform}（{_label(platform)}）" for platform in platforms)


def normalize_platforms(platforms: list[str] | None) -> list[str]:
    """校验并归一平台列表；为空时给默认（首个已接入平台）。"""
    available = available_platforms()
    if not available:
        raise ApiError("no_platform_available", "当前部署还没有接入任何发布平台", status_code=409)
    if not platforms:
        return [available[0]]
    picked: list[str] = []
    unknown: list[str] = []
    for raw in platforms:
        key = str(raw or "").strip()
        key = PLATFORM_ALIASES.get(key, key)
        if key not in available:
            unknown.append(str(raw or "").strip() or "(空)")
        elif key not in picked:
            picked.append(key)
    if unknown:
        raise ApiError(
            "unknown_platform",
            f"暂不支持这些平台：{'、'.join(unknown)}。可用平台：{_platform_hint()}",
            status_code=400,
        )
    if not picked:
        raise ApiError("unknown_platform", f"请选择发布平台，可用平台：{_platform_hint()}", status_code=400)
    return picked


def parse_scheduled_at(value: datetime | None) -> datetime | None:
    """排期时间统一成带时区的 UTC；不能早于现在。"""
    if value is None:
        return None
    at = value
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    if at <= datetime.now(timezone.utc):
        raise ApiError("scheduled_at_past", "计划发布时间必须晚于当前时间", status_code=400)
    return at


def _aware(value: datetime | None) -> datetime | None:
    """SQLite 读回的时间是 naive，统一补 UTC 再比较/序列化。"""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _iso(value: datetime | None) -> str | None:
    stamp = _aware(value)
    return stamp.isoformat() if stamp else None


def _accounts(db, user_id: str, platform: str) -> list[models.ConnectedAccount]:
    rows = db.scalars(
        select(models.ConnectedAccount)
        .where(
            models.ConnectedAccount.owner_user_id == user_id,
            models.ConnectedAccount.platform == platform,
        )
        .order_by(models.ConnectedAccount.created_at.asc())
    ).all()
    # 已连接的优先；pending/unbound 的账号仍可承接「手动模式」任务（用户自己到平台发）
    return sorted(rows, key=lambda row: 0 if row.status == "connected" else 1)


def _pick_account(db, user_id: str, platform: str) -> models.ConnectedAccount:
    accounts = _accounts(db, user_id, platform)
    if not accounts:
        raise ApiError(
            "account_not_bound",
            f"{_label(platform)}还没有绑定账号：请先到发布中心绑定并验证，再回来发布。",
            status_code=409,
        )
    return accounts[0]


def _idempotency_key(article_id: str, platform: str, scheduled_at: datetime | None) -> str:
    """按「稿件 + 平台 + 时间窗」做幂等。

    不能用导出版本号：`export_to_content` 每次都新建 ContentItem，
    双击「立即发布」会拿到两个版本号，防守就失效了。
    """
    if scheduled_at is not None:
        token = f"at{int(scheduled_at.timestamp())}"
    else:
        token = f"n{int(datetime.now(timezone.utc).timestamp()) // IDEMPOTENCY_WINDOW_SECONDS}"
    return f"studio:{article_id}:{platform}:{token}"


def _existing_jobs(db, user_id: str, keys: list[str]) -> dict[str, models.PublishJob]:
    rows = db.scalars(
        select(models.PublishJob).where(
            models.PublishJob.owner_user_id == user_id,
            models.PublishJob.idempotency_key.in_(keys),
        )
    ).all()
    return {row.idempotency_key: row for row in rows}


def _auto_capable(account: models.ConnectedAccount, platform: str) -> bool:
    """与 publishing 的自动发布门槛保持一致：能力已核验 + 官方 OAuth + 账号开关。"""
    cap = connectors.get_capability(platform)
    return bool(cap.confirmed and cap.publish and account.auto_publish_enabled and account.auth_source == "oauth")


def publish(
    db,
    user,
    article: models.StudioArticle,
    data: dict,
    *,
    export,
    platforms: list[str],
    scheduled_at: datetime | None,
    dry_run: bool,
) -> dict:
    """建发布任务（dry_run 只导出）；返回契约里的四个字段。

    `export` 是 `modules.studio.export_to_content` 的调用入口：幂等命中时不必再导出一遍，
    否则双击会凭空多出两条 ContentItem。
    """
    keys = {platform: _idempotency_key(article.id, platform, scheduled_at) for platform in platforms}
    found = {} if dry_run else _existing_jobs(db, user.id, list(keys.values()))
    missing = [platform for platform in platforms if keys[platform] not in found]

    exported = export() if (dry_run or missing) else None
    if exported is not None:
        content_item_id = exported.get("contentId") or exported.get("contentItemId")
        version_id = exported.get("versionId") or ""
        title = (exported.get("title") or data.get("title") or "未命名稿件")[:200]
    else:
        snapshot = (found[keys[platforms[0]]].payload_snapshot or {}) if platforms else {}
        content_item_id = snapshot.get("contentItemId") or None
        version_id = snapshot.get("versionId") or ""
        title = (snapshot.get("title") or data.get("title") or "未命名稿件")[:200]

    result = {
        "contentItemId": content_item_id,
        "versionId": version_id,
        "publishJobIds": [],
        "scheduledFor": _iso(scheduled_at),
    }
    if dry_run:
        return result

    job_ids = [found[keys[platform]].id for platform in platforms if keys[platform] in found]
    if not missing:
        result["publishJobIds"] = job_ids
        return result

    body = AB.to_markdown(data)

    # 先确认每个平台都有可承接任务的账号，避免建了一半再失败
    accounts = {platform: _pick_account(db, user.id, platform) for platform in missing}

    blocking: list[str] = []
    for platform in missing:
        issues = connectors.validate_draft(platform, title=title, body=body, asset_count=0)
        blocking.extend(
            f"{_label(platform)}：{item['message']}" for item in issues if item.get("level") == "error"
        )
    if blocking:
        raise ApiError("draft_incomplete", "；".join(blocking), status_code=422)

    workspace_id = article.workspace_id
    if not workspace_id:
        content_item = db.get(models.ContentItem, content_item_id) if content_item_id else None
        workspace_id = content_item.workspace_id if content_item else None
    if not workspace_id:
        raise ApiError("workspace_missing", "这篇稿件还没有归属工作区，无法创建发布任务。", status_code=400)

    auto_jobs: list[tuple[str, str]] = []
    for platform in missing:
        account = accounts[platform]
        key = keys[platform]
        auto = _auto_capable(account, platform) and scheduled_at is None
        # 排期任务先排队（queued），到点由 publishing 的到期扫描提升为 approved；
        # 立即发布：能力齐备走 approved + 真正入队，否则落 manual（只记录，不调平台）
        status = publish_state.QUEUED if scheduled_at else publish_state.APPROVED
        if scheduled_at:
            note = "创建排期任务（成稿台导出）"
        elif auto:
            note = "创建自动发布任务（成稿台导出）"
        else:
            note = "创建任务（成稿台导出，手动模式待发）"
        job = models.PublishJob(
            owner_user_id=user.id,
            workspace_id=workspace_id,
            content_item_id=content_item_id,
            connected_account_id=account.id,
            platform=platform,
            target="publish",
            scheduled_at=scheduled_at,
            idempotency_key=key,
            status=status,
            request_id=uuid.uuid4().hex,
            reviewed_by=user.id,
            payload_snapshot={
                "mode": "auto" if auto else "manual",
                "title": title,
                "body": body[:2000],
                "assetIds": [],
                "contentItemId": content_item_id or "",
                "versionId": version_id,
                "source": "studio",
                # 重新导出会换一条 ContentItem，复盘按这个 key 兜住同一篇稿件的历史任务
                "studioArticleId": article.id,
            },
            layout_snapshot=dict(data.get("layout_options") or {}),
            status_history=publish_state.initial_history(status, actor="user", note=note),
        )
        db.add(job)
        try:
            db.commit()
        except IntegrityError:
            # 并发同键：回查已存在任务，不重复建
            db.rollback()
            raced = db.scalar(
                select(models.PublishJob).where(
                    models.PublishJob.owner_user_id == user.id,
                    models.PublishJob.idempotency_key == key,
                )
            )
            if not raced:
                raise
            job_ids.append(raced.id)
            continue
        job_ids.append(job.id)
        if auto:
            auto_jobs.append((job.id, account.id))

    data["scheduled_for"] = _iso(scheduled_at)
    data["publish_marked_at"] = AB.now_iso()
    AB.touch(data)
    studio_repo.save(db, article, data)
    db.commit()

    for job_id, account_id in auto_jobs:
        queue.enqueue(
            db,
            owner_user_id=user.id,
            queue="platform-publish",
            kind="publish",
            payload={"publishJobId": job_id, "accountId": account_id},
        )

    result["publishJobIds"] = job_ids
    return result


# ---------------- 复盘 ----------------

_STATUS_MAP = {
    publish_state.PENDING_REVIEW: "queued",
    publish_state.APPROVED: "queued",
    publish_state.QUEUED: "queued",
    publish_state.PUBLISHING: "running",
    publish_state.SUCCEEDED: "succeeded",
    publish_state.FAILED: "failed",
    publish_state.NEEDS_ATTENTION: "failed",
    publish_state.CANCELLED: "failed",
}


def _public_status(status: str) -> str:
    return _STATUS_MAP.get(status, "queued")


def stats(data: dict) -> dict:
    """统计口径与 `article_blocks.list_meta` 一致：flag/fixed 都算待处理。"""
    meta = AB.list_meta(data)
    return {
        "blocks": meta["blocks"],
        "chars": meta["chars"],
        "flagged": meta["review"],
        "checked": meta["checked"],
    }


def review(db, user, article: models.StudioArticle, data: dict) -> dict:
    """按 contentItemId 反查发布任务与结果；没有记录就是空数组。"""
    content_item_id = article.content_item_id
    jobs: dict[str, models.PublishJob] = {}
    if content_item_id:
        for job in db.scalars(
            select(models.PublishJob).where(
                models.PublishJob.owner_user_id == user.id,
                models.PublishJob.content_item_id == content_item_id,
            )
        ).all():
            jobs[job.id] = job
    # 成稿台再次导出会换一条 ContentItem：靠任务快照里的 studioArticleId 兜住历史任务，
    # 否则「重新发布一次」后复盘会突然清空。扫描面按用户封顶，不翻全库。
    recent = db.scalars(
        select(models.PublishJob)
        .where(models.PublishJob.owner_user_id == user.id)
        .order_by(models.PublishJob.created_at.desc(), models.PublishJob.id.desc())
        .limit(REVIEW_LOOKBACK_JOBS)
    ).all()
    for job in recent:
        if job.id not in jobs and (job.payload_snapshot or {}).get("studioArticleId") == article.id:
            jobs[job.id] = job
    ordered = sorted(jobs.values(), key=lambda row: (row.created_at, row.id))
    records: dict[str, models.PublishRecord] = {}
    if ordered:
        rows = db.scalars(
            select(models.PublishRecord)
            .where(models.PublishRecord.publish_job_id.in_([job.id for job in ordered]))
            .order_by(models.PublishRecord.created_at.asc(), models.PublishRecord.id.asc())
        ).all()
        for row in rows:
            records[row.publish_job_id] = row  # 同一任务多次记录时保留最新一条

    latest: dict[str, models.PublishJob] = {}
    for job in ordered:
        latest[job.platform] = job  # 按时间升序遍历，最后一条即为该平台的最新任务

    platforms: list[dict] = []
    published_at: datetime | None = None
    for platform, job in latest.items():
        record = records.get(job.id)
        status = _public_status(job.status)
        url = (record.permalink if record else None) or (job.payload_snapshot or {}).get("permalink") or None
        at = None
        if status == "succeeded":
            at = (record.published_at if record else None) or job.updated_at or job.created_at
            if at and (published_at is None or _aware(at) > _aware(published_at)):
                published_at = at
        elif job.status == publish_state.QUEUED and job.scheduled_at:
            at = job.scheduled_at
        else:
            at = job.updated_at or job.created_at
        error = None
        if status == "failed":
            if job.status == publish_state.CANCELLED:
                error = "任务已取消"
            else:
                error = job.error_message or job.error_code or "发布失败，请到发布中心查看详情"
        platforms.append(
            {
                "platform": platform,
                "platformLabel": _label(platform),
                "status": status,
                "url": url,
                "at": _iso(at),
                "error": error,
            }
        )

    return {
        "articleId": article.id,
        "contentItemId": content_item_id,
        "publishedAt": _iso(article.published_at or published_at),
        "platforms": platforms,
        "stats": stats(data),
    }
