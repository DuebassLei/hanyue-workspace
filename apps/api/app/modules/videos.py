"""视频创作（图文成片）：项目 CRUD、AI 拆分镜、镜头编辑、成片任务。

原则对齐 AGENT.md：模型/生图/TTS/ffmpeg 任一环节不可用都明确报错，
绝不产出占位分镜或残缺视频；耗时操作全部走 background_jobs。
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Query, Response
from sqlalchemy import func, or_, select

from .. import models, schemas
from ..core import pagination, queue
from ..core.config import get_settings
from ..core.deps import ApiError, CurrentUser, DbSession, default_workspace, not_found
from ..core.security import sign_asset_url
from ..services import tts_provider, video_render, video_script

router = APIRouter(tags=["videos"])

MOTIONS = ("zoom_in", "zoom_out", "pan_left", "pan_right")


def _url(asset_id: str | None) -> str | None:
    return f"{get_settings().api_prefix}/assets/{asset_id}/raw?{sign_asset_url(asset_id)}" if asset_id else None


def _project(db, user, project_id, *, include_deleted=False):
    row = db.get(models.VideoProject, project_id)
    if not row or row.owner_user_id != user.id or (row.deleted_at and not include_deleted):
        raise not_found("视频项目")
    return row


def _shot(db, user, shot_id, *, include_deleted=False):
    row = db.get(models.VideoShot, shot_id)
    if not row or (row.deleted_at and not include_deleted):
        raise not_found("镜头")
    _project(db, user, row.project_id, include_deleted=include_deleted)
    return row


def _shots_of(db, project_id):
    return list(
        db.scalars(
            select(models.VideoShot)
            .where(models.VideoShot.project_id == project_id, models.VideoShot.deleted_at.is_(None))
            .order_by(models.VideoShot.sort_order, models.VideoShot.id)
        ).all()
    )


def _shot_out(row: models.VideoShot) -> schemas.VideoShotOut:
    return schemas.VideoShotOut(
        id=row.id,
        sort_order=row.sort_order,
        narration=row.narration,
        image_prompt=row.image_prompt,
        image_asset_id=row.image_asset_id,
        image_url=_url(row.image_asset_id),
        audio_asset_id=row.audio_asset_id,
        audio_url=_url(row.audio_asset_id),
        duration_ms=row.duration_ms,
        motion=row.motion,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _project_out(db, row: models.VideoProject, shots=None) -> schemas.VideoProjectOut:
    rows = shots if shots is not None else _shots_of(db, row.id)
    return schemas.VideoProjectOut(
        id=row.id,
        workspace_id=row.workspace_id,
        title=row.title,
        source_text=row.source_text,
        source_content_id=row.source_content_id,
        settings=row.settings or {},
        status=row.status,
        render_asset_id=row.render_asset_id,
        render_job_id=row.render_job_id,
        render_error=row.render_error,
        rendered_at=row.rendered_at,
        render_url=_url(row.render_asset_id),
        total_duration_ms=sum(s.duration_ms for s in rows),
        created_at=row.created_at,
        updated_at=row.updated_at,
        shots=[_shot_out(s) for s in rows],
    )


def _normalize_settings(raw: dict) -> dict:
    merged = {
        "aspectRatio": "9:16",
        "voice": tts_provider.DEFAULT_VOICE,
        "rate": "+0%",
        "subtitles": True,
        "bgmAssetId": None,
        "bgmVolume": 0.18,
        **(raw or {}),
    }
    if merged["aspectRatio"] not in video_render.RESOLUTIONS:
        raise ApiError("invalid_settings", "画幅只支持 9:16 / 16:9")
    try:
        merged["voice"] = tts_provider.normalize_voice(merged["voice"])
        merged["rate"] = tts_provider.normalize_rate(merged["rate"])
    except tts_provider.TTSUnavailable as exc:
        raise ApiError(exc.code, exc.message)
    volume = merged.get("bgmVolume")
    if not isinstance(volume, (int, float)) or not 0.02 <= float(volume) <= 0.6:
        merged["bgmVolume"] = 0.18
    return merged


def _touch_shots(project: models.VideoProject, shot: models.VideoShot, *, narration_changed: bool) -> None:
    """旁白变了旧配音作废；配图描述变了旧配图保留但提示重生成由用户触发。"""
    if narration_changed:
        shot.audio_asset_id = None
        shot.duration_ms = 0
        project.status = "dirty" if project.status == "rendered" else project.status


@router.get("/video-provider/status")
def provider_status(user: CurrentUser, db: DbSession) -> dict:
    from ..services import image_provider

    ffmpeg_ok = True
    ffmpeg_note = ""
    try:
        video_render.ffmpeg_bin()
    except video_render.RenderError as exc:
        ffmpeg_ok = False
        ffmpeg_note = exc.message
    return {
        "tts": tts_provider.status(),
        "images": image_provider.status(db, user.id),
        "ffmpeg": {"available": ffmpeg_ok, "note": ffmpeg_note},
    }


@router.get("/videos", response_model=list[schemas.VideoProjectListItem])
def list_projects(
    user: CurrentUser,
    db: DbSession,
    response: Response,
    limit: int = Query(default=50, le=200),
    cursor: str | None = Query(default=None),
) -> list[schemas.VideoProjectListItem]:
    stmt = select(models.VideoProject).where(
        models.VideoProject.owner_user_id == user.id,
        models.VideoProject.deleted_at.is_(None),
    )
    rows, next_cursor = pagination.paginate(
        db, stmt, models.VideoProject,
        sort_column=models.VideoProject.updated_at, cursor=cursor, limit=limit,
    )
    pagination.set_cursor_header(response, next_cursor)
    items = []
    for row in rows:
        shots = _shots_of(db, row.id)
        items.append(
            schemas.VideoProjectListItem(
                id=row.id,
                title=row.title,
                status=row.status,
                shot_count=len(shots),
                total_duration_ms=sum(s.duration_ms for s in shots),
                render_asset_id=row.render_asset_id,
                updated_at=row.updated_at,
            )
        )
    return items


@router.post("/videos", response_model=schemas.VideoProjectOut, status_code=201)
def create_project(body: schemas.VideoProjectCreate, user: CurrentUser, db: DbSession):
    source_text = body.source_text.strip()
    content_id = body.content_item_id
    if content_id:
        content = db.get(models.ContentItem, content_id)
        if not content or content.owner_user_id != user.id:
            raise not_found("稿件")
        version_body = ""
        if content.current_version_id:
            version = db.get(models.ContentVersion, content.current_version_id)
            version_body = (version.body if version else "") or ""
        source_text = source_text or version_body or (content.draft_body or "")
        if not source_text.strip():
            raise ApiError("empty_source", "稿件正文为空，无法转视频")
    if not source_text:
        raise ApiError("empty_source", "请粘贴文案或选择一篇稿件")
    project = models.VideoProject(
        owner_user_id=user.id,
        workspace_id=default_workspace(db, user).id,
        title=(body.title.strip() or source_text[:30] or "未命名视频")[:200],
        source_text=source_text,
        source_content_id=content_id,
        settings=_normalize_settings(body.settings.dump() if body.settings else None),
        status="draft",
    )
    db.add(project)
    db.commit()
    return _project_out(db, project)


@router.get("/videos/{project_id}", response_model=schemas.VideoProjectOut)
def get_project(project_id: str, user: CurrentUser, db: DbSession):
    return _project_out(db, _project(db, user, project_id))


@router.patch("/videos/{project_id}", response_model=schemas.VideoProjectOut)
def patch_project(project_id: str, body: schemas.VideoProjectPatch, user: CurrentUser, db: DbSession):
    project = _project(db, user, project_id)
    if body.title is not None:
        project.title = body.title.strip()[:200]
    if body.source_text is not None and body.source_text.strip():
        project.source_text = body.source_text
    if body.settings is not None:
        old = project.settings or {}
        new = _normalize_settings(body.settings.dump())
        # 音色/语速变化使既有配音全部过期
        if (old.get("voice"), old.get("rate")) != (new.get("voice"), new.get("rate")):
            for shot in _shots_of(db, project.id):
                shot.audio_asset_id = None
                shot.duration_ms = 0
            project.status = "dirty" if project.status == "rendered" else project.status
        project.settings = new
    db.commit()
    return _project_out(db, project)


@router.delete("/videos/{project_id}", status_code=204)
def delete_project(project_id: str, user: CurrentUser, db: DbSession):
    project = _project(db, user, project_id)
    project.deleted_at = datetime.now(timezone.utc)
    for shot in _shots_of(db, project.id):
        shot.deleted_at = project.deleted_at
    db.commit()
    shutil.rmtree(Path(get_settings().storage_dir) / "video_tmp" / project.id, ignore_errors=True)
    return Response(status_code=204)


@router.post("/videos/{project_id}/breakdown", response_model=schemas.VideoProjectOut)
def breakdown(project_id: str, body: schemas.VideoBreakdownRequest, user: CurrentUser, db: DbSession):
    """AI 拆分镜：覆盖现有镜头草稿（渲染中的项目拒绝，防止半途换轨）。"""
    project = _project(db, user, project_id)
    running = _running_job(db, user, project)
    if running:
        raise ApiError("video_busy", "成片任务进行中，请先取消或等待完成")
    try:
        result = video_script.breakdown(
            project.source_text, db=db, user_id=user.id, model_id=body.model_id
        )
    except video_script.ScriptError as exc:
        raise ApiError(exc.code, exc.message)
    now = datetime.now(timezone.utc)
    for shot in _shots_of(db, project.id):
        shot.deleted_at = now
    for index, item in enumerate(result["shots"]):
        db.add(
            models.VideoShot(
                project_id=project.id,
                sort_order=index,
                narration=item["narration"],
                image_prompt=item["imagePrompt"],
                motion=item["motion"],
            )
        )
    if result.get("title") and project.title in ("", "未命名视频"):
        project.title = result["title"]
    project.status = "scripted"
    project.render_error = None
    db.commit()
    return _project_out(db, project)


@router.post("/videos/{project_id}/shots", response_model=schemas.VideoShotOut, status_code=201)
def add_shot(project_id: str, body: schemas.VideoShotIn, user: CurrentUser, db: DbSession):
    project = _project(db, user, project_id)
    shots = _shots_of(db, project.id)
    shot = models.VideoShot(
        project_id=project.id,
        sort_order=(shots[-1].sort_order + 1) if shots else 0,
        narration=body.narration,
        image_prompt=body.image_prompt,
        motion=body.motion,
    )
    db.add(shot)
    if project.status == "rendered":
        project.status = "dirty"
    db.commit()
    return _shot_out(shot)


@router.patch("/videos/shots/{shot_id}", response_model=schemas.VideoShotOut)
def patch_shot(shot_id: str, body: schemas.VideoShotPatch, user: CurrentUser, db: DbSession):
    shot = _shot(db, user, shot_id)
    project = _project(db, user, shot.project_id)
    if body.narration is not None:
        changed = body.narration.strip() != (shot.narration or "").strip()
        shot.narration = body.narration
        _touch_shots(project, shot, narration_changed=changed)
    if body.image_prompt is not None:
        shot.image_prompt = body.image_prompt
    if body.motion is not None:
        shot.motion = body.motion
    if body.image_asset_id is not None:
        asset = db.get(models.Asset, body.image_asset_id)
        if not asset or asset.owner_user_id != user.id:
            raise not_found("素材")
        if not asset.mime_type.startswith("image/"):
            raise ApiError("invalid_image", "只能指定图片素材")
        shot.image_asset_id = asset.id
    if project.status == "rendered":
        project.status = "dirty"
    db.commit()
    return _shot_out(shot)


@router.delete("/videos/shots/{shot_id}", status_code=204)
def delete_shot(shot_id: str, user: CurrentUser, db: DbSession):
    shot = _shot(db, user, shot_id)
    project = _project(db, user, shot.project_id)
    shot.deleted_at = datetime.now(timezone.utc)
    db.flush()  # 让软删对后续查询可见，再重排序号
    remaining = _shots_of(db, project.id)
    for index, row in enumerate(remaining):
        row.sort_order = index
    if project.status == "rendered":
        project.status = "dirty"
    db.commit()
    return Response(status_code=204)


@router.post("/videos/{project_id}/shots/reorder", response_model=schemas.VideoProjectOut)
def reorder_shots(project_id: str, shot_ids: list[str], user: CurrentUser, db: DbSession):
    project = _project(db, user, project_id)
    rows = {row.id: row for row in _shots_of(db, project.id)}
    if sorted(shot_ids) != sorted(rows):
        raise ApiError("invalid_order", "镜头列表与项目现有镜头不一致，请刷新后重试")
    for index, shot_id in enumerate(shot_ids):
        rows[shot_id].sort_order = index
    db.commit()
    return _project_out(db, project)


def _running_job(db, user, project) -> models.BackgroundJob | None:
    return db.scalar(
        select(models.BackgroundJob).where(
            models.BackgroundJob.payload["videoProjectId"].as_string() == project.id,
            models.BackgroundJob.owner_user_id == user.id,
            models.BackgroundJob.queue == "video-render",
            models.BackgroundJob.status.in_(("queued", "running")),
        )
    )


@router.post("/videos/{project_id}/render", status_code=202)
def render_project(project_id: str, body: schemas.VideoRenderRequest, user: CurrentUser, db: DbSession):
    """提交成片任务。reset=true 时清掉缓存的配图/配音强制全量重做。"""
    project = _project(db, user, project_id)
    if _running_job(db, user, project):
        raise ApiError("video_busy", "该项目已有成片任务进行中")
    shots = _shots_of(db, project.id)
    if not shots:
        raise ApiError("no_shots", "还没有镜头：请先「AI 拆分镜」或手动添加")
    silent = all(not (s.narration or "").strip() for s in shots)
    if silent:
        raise ApiError("no_narration", "所有镜头旁白为空：无旁白的成片暂不支持")
    try:
        video_render.ffmpeg_bin()
    except video_render.RenderError as exc:
        raise ApiError(exc.code, exc.message)
    if body.reset:
        for shot in shots:
            shot.image_asset_id = None
            shot.audio_asset_id = None
            shot.duration_ms = 0
    project.status = "rendering"
    project.render_error = None
    db.commit()
    job_id = queue.enqueue(
        db,
        owner_user_id=user.id,
        queue="video-render",
        kind="video.render",
        payload={"videoProjectId": project.id},
    )
    project.render_job_id = job_id
    db.commit()
    return {"jobId": job_id, "shotCount": len(shots)}


@router.get("/videos/{project_id}/render-status")
def render_status(project_id: str, user: CurrentUser, db: DbSession) -> dict:
    project = _project(db, user, project_id)
    job = (
        db.get(models.BackgroundJob, project.render_job_id) if project.render_job_id else None
    )
    out: dict = {
        "status": project.status,
        "renderAssetId": project.render_asset_id,
        "renderUrl": _url(project.render_asset_id),
        "renderError": project.render_error,
        "renderedAt": project.rendered_at.isoformat() if project.rendered_at else None,
    }
    if job:
        out["job"] = {
            "id": job.id,
            "status": job.status,
            "progress": job.progress,
            "stage": (job.result or {}).get("stage") if job.status == "running" else None,
            "errorCode": job.error_code,
            "errorMessage": job.error_message,
        }
    return out
