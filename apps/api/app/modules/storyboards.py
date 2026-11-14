"""Persistent storyboard projects, ordered scenes/shots, generation and exports."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Response
from sqlalchemy import func, select

from .. import models, schemas
from ..core import queue
from ..core.config import get_settings
from ..core.deps import ApiError, CurrentUser, DbSession, default_workspace, not_found
from ..core.security import sign_asset_url
from ..services.image_service import storage_path

router = APIRouter(tags=["storyboards"])


def _url(asset_id: str | None) -> str | None:
    return f"{get_settings().api_prefix}/assets/{asset_id}/raw?{sign_asset_url(asset_id)}" if asset_id else None


def _project(db, user, project_id, *, include_deleted=False):
    row = db.get(models.StoryboardProject, project_id)
    if not row or row.owner_user_id != user.id or (row.deleted_at and not include_deleted):
        raise not_found("分镜项目")
    return row


def _scene(db, user, scene_id, *, include_deleted=False):
    row = db.get(models.StoryboardScene, scene_id)
    if not row or (row.deleted_at and not include_deleted):
        raise not_found("分镜场景")
    project = _project(db, user, row.project_id, include_deleted=include_deleted)
    return row, project


def _shot(db, user, shot_id, *, include_deleted=False):
    row = db.get(models.StoryboardShot, shot_id)
    if not row or (row.deleted_at and not include_deleted):
        raise not_found("分镜镜头")
    scene, project = _scene(db, user, row.scene_id, include_deleted=include_deleted)
    return row, scene, project


def _cancel_storyboard_jobs(db, user_id: str, *, project_id: str | None = None, shot_ids: set[str] | None = None) -> None:
    shot_ids = shot_ids or set()
    jobs = db.scalars(select(models.BackgroundJob).where(
        models.BackgroundJob.owner_user_id == user_id,
        models.BackgroundJob.status.in_(("queued", "running")),
    )).all()
    for job in jobs:
        payload = job.payload or {}
        if project_id and payload.get("storyboardProjectId") == project_id:
            job.status = "cancelled"
            job.completed_at = datetime.now(timezone.utc)
        elif payload.get("storyboardShotId") in shot_ids:
            job.status = "cancelled"
            job.completed_at = datetime.now(timezone.utc)


def _asset_ids_for_shots(shots) -> set[str]:
    ids: set[str] = set()
    for shot in shots:
        if shot.sketch_asset_id:
            ids.add(shot.sketch_asset_id)
        ids.update(shot.final_asset_ids or [])
    return ids


def _purge_generated_assets(db, asset_ids: set[str], project) -> list:
    referenced = _asset_lookup(project) if project else {}
    paths = []
    for asset_id in asset_ids - set(referenced):
        asset = db.get(models.Asset, asset_id)
        if not asset or asset.source_type != "generated":
            continue
        content_ref = db.scalar(select(func.count()).select_from(models.ContentAsset).where(
            models.ContentAsset.asset_id == asset_id
        )) or 0
        child_ref = db.scalar(select(func.count()).select_from(models.Asset).where(
            models.Asset.parent_asset_id == asset_id
        )) or 0
        if content_ref or child_ref:
            continue
        paths.append(storage_path(get_settings().storage_dir, asset.storage_key))
        db.delete(asset)
    return paths


def _unlink_assets(paths) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _validate_assets(db, user, assets):
    payload = assets.model_dump() if hasattr(assets, "model_dump") else assets
    for category in ("characters", "locations", "styles"):
        values = payload.get(category) or []
        if not isinstance(values, list):
            raise ApiError("invalid_assets", f"{category} 必须是素材列表")
        seen = set()
        for item in values:
            if not isinstance(item, dict):
                raise ApiError("invalid_assets", "视觉资产格式不正确")
            name = str(item.get("name") or "").strip()
            description = str(item.get("description") or "").strip()
            asset_id = item.get("asset_id") or item.get("assetId")
            if not name or not (description or asset_id):
                raise ApiError("invalid_assets", "视觉资产需要名字，以及参考图或文字描述")
            item_id = str(item.get("id") or "").strip()
            if not item_id or item_id in seen:
                raise ApiError("invalid_assets", "视觉资产编号不能为空或重复")
            seen.add(item_id)
            if not asset_id:
                continue
            asset = db.get(models.Asset, asset_id)
            if not asset or asset.owner_user_id != user.id:
                raise not_found("项目素材")
    return payload


def _project_assets(project) -> dict:
    raw = project.assets or {}
    return {
        "characters": raw.get("characters") or [],
        "locations": raw.get("locations") or [],
        "styles": raw.get("styles") or [],
    }


def _asset_lookup(project) -> dict:
    found = {}
    for items in _project_assets(project).values():
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                found[item["id"]] = item
    return found


def _selected_assets(project, refs: list[str]) -> list[dict]:
    lookup = _asset_lookup(project)
    unknown = [item for item in refs if item not in lookup]
    if unknown:
        raise ApiError("invalid_asset_refs", "镜头引用了项目里没有的视觉资产")
    return [lookup[item] for item in refs]


def _category_of(project, ref: str) -> str:
    assets = _project_assets(project)
    return next((key for key, values in assets.items() if any(
        isinstance(row, dict) and row.get("id") == ref for row in values
    )), "")


def _item_asset_id(item: dict) -> str | None:
    return item.get("asset_id") or item.get("assetId") or None


def _named_characters(project) -> list[dict]:
    return [
        item for item in _project_assets(project)["characters"]
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]


def _mask_span(mask: list[bool], start: int, end: int) -> None:
    for index in range(start, end):
        mask[index] = True


def _present_characters(project, shot) -> list[dict]:
    """在场角色：按画面、动作、对白、手写提示词的出现顺序点名；长名字先占位。

    长名字先扫完整段并遮住命中区间，短名字不能落在已被占用的字上。
    正文一个名字都对不上时，才回退到本镜勾选的角色，并保持 asset_refs 顺序。
    勾了但正文没提到的角色，在已经点到名字时不混进来。
    """
    characters = _named_characters(project)
    by_length = sorted(characters, key=lambda item: len(str(item["name"]).strip()), reverse=True)
    chunks = (
        shot.frame_description or "",
        shot.action or "",
        shot.dialogue or "",
        shot.prompt or "",
    )
    found: list[dict] = []
    seen: set[str] = set()
    for text in chunks:
        mask = [False] * len(text)
        hits: list[tuple[int, dict]] = []
        for item in by_length:
            name = str(item["name"]).strip()
            if not name or item["id"] in seen:
                continue
            start = 0
            while True:
                index = text.find(name, start)
                if index < 0:
                    break
                end = index + len(name)
                if not any(mask[index:end]):
                    _mask_span(mask, index, end)
                    hits.append((index, item))
                    seen.add(item["id"])
                start = index + 1
        hits.sort(key=lambda pair: pair[0])
        found.extend(item for _, item in hits if item["id"] not in {row["id"] for row in found})
    if found:
        return found
    lookup = _asset_lookup(project)
    fallback = []
    for ref in shot.asset_refs or []:
        item = lookup.get(ref) or {}
        if _category_of(project, ref) == "characters" and item.get("id") not in {row["id"] for row in fallback}:
            fallback.append(item)
    return fallback


def _first_pictured(project, refs: list[str], category: str) -> dict | None:
    lookup = _asset_lookup(project)
    for ref in refs:
        if _category_of(project, ref) != category:
            continue
        item = lookup.get(ref) or {}
        if _item_asset_id(item):
            return item
    return None


def _storyboard_references(project, shot, stage: str) -> list[tuple[str, dict | None]]:
    """最多 4 张：在场角色图 ≤3，地点 ≤1，风格 ≤1。名额不够先丢风格再丢地点。

    正式图最后一张固定为已成功草图，因此角色+地点+风格先截到 3 张。
    草图阶段不带草图自己。请求体里的 reference_asset_ids 不并入。
    """
    slots: list[tuple[str, dict | None]] = []
    for item in _present_characters(project, shot):
        if len([row for row in slots if row[0] == "character"]) >= 3:
            break
        asset_id = _item_asset_id(item)
        if asset_id:
            slots.append(("character", item))
    location = _first_pictured(project, shot.asset_refs or [], "locations")
    if location:
        slots.append(("location", location))
    style = _first_pictured(project, shot.asset_refs or [], "styles")
    if style:
        slots.append(("style", style))
    cap = 3 if stage == "final" else 4
    while len(slots) > cap:
        kinds = [kind for kind, _ in slots]
        drop = "style" if "style" in kinds else "location"
        index = max(i for i, (kind, _) in enumerate(slots) if kind == drop)
        slots.pop(index)
    if stage == "final":
        slots.append(("sketch", None))
    return slots


def _sync_shot(db, shot):
    changed = False
    for stage in ("sketch", "final"):
        job_id = getattr(shot, f"{stage}_job_id")
        status = getattr(shot, f"{stage}_status")
        if not job_id or status in ("succeeded", "failed"):
            continue
        job = db.get(models.BackgroundJob, job_id)
        if not job:
            continue
        if job.status == "running":
            if status != "generating":
                setattr(shot, f"{stage}_status", "generating")
                changed = True
        elif job.status == "succeeded":
            result = job.result or {}
            ids = result.get("assetIds") or []
            if int((job.payload or {}).get("generationVersion") or 0) != shot.generation_version:
                continue
            if getattr(shot, f"{stage}_job_id") != job.id:
                continue
            if stage == "sketch" and ids:
                shot.sketch_asset_id = ids[0]
            elif stage == "final":
                shot.final_asset_ids = ids
            setattr(shot, f"{stage}_status", "succeeded")
            setattr(shot, f"{stage}_error", None)
            changed = True
        elif job.status == "failed":
            setattr(shot, f"{stage}_status", "failed")
            setattr(shot, f"{stage}_error", job.error_message or job.error_code or "生成失败")
            changed = True
    return changed


def _generation_prompt(db, job_id: str | None) -> str | None:
    """读取该阶段实际入队的完整提示词：包含镜头描述、参考图标注和角色一致性文案。

    提示词在入队时按当时的画面/资产拼装，只有 job payload 保留了这份最终文本；
    直接读 job 行无需加列或迁移，也不会与镜头后来被编辑过的 prompt 混淆。
    """
    if not job_id:
        return None
    job = db.get(models.BackgroundJob, job_id)
    prompt = (job.payload or {}).get("prompt") if job else None
    return prompt or None


def _shot_out(db, shot):
    return schemas.StoryboardShotOut(
        id=shot.id, sort_order=shot.sort_order, deleted_at=shot.deleted_at, frame_description=shot.frame_description,
        action=shot.action, dialogue=shot.dialogue, shot_size=shot.shot_size,
        view_angle=shot.view_angle, camera_motion=shot.camera_motion,
        duration_seconds=shot.duration_seconds, prompt=shot.prompt, asset_refs=shot.asset_refs or [],
        sketch_asset_id=shot.sketch_asset_id, sketch_url=_url(shot.sketch_asset_id),
        sketch_job_id=shot.sketch_job_id, sketch_status=shot.sketch_status,
        sketch_error=shot.sketch_error, sketch_prompt=_generation_prompt(db, shot.sketch_job_id),
        final_asset_ids=shot.final_asset_ids or [],
        final_urls=[_url(asset_id) for asset_id in (shot.final_asset_ids or [])],
        final_job_id=shot.final_job_id, final_status=shot.final_status,
        final_error=shot.final_error, final_prompt=_generation_prompt(db, shot.final_job_id),
        generation_version=shot.generation_version,
    )


def _project_out(db, project, *, scenes=True):
    scene_out = []
    if scenes:
        rows = db.scalars(select(models.StoryboardScene).where(
            models.StoryboardScene.project_id == project.id,
            models.StoryboardScene.deleted_at.is_(None),
        ).order_by(models.StoryboardScene.sort_order)).all()
        shots = db.scalars(select(models.StoryboardShot).join(
            models.StoryboardScene, models.StoryboardShot.scene_id == models.StoryboardScene.id
        ).where(
            models.StoryboardScene.project_id == project.id,
            models.StoryboardScene.deleted_at.is_(None),
            models.StoryboardShot.deleted_at.is_(None),
        ).order_by(models.StoryboardShot.sort_order)).all()
        for shot in shots:
            _sync_shot(db, shot)
        db.commit()
        by_scene = {}
        for shot in shots:
            by_scene.setdefault(shot.scene_id, []).append(_shot_out(db, shot))
        scene_out = [schemas.StoryboardSceneOut(
            id=row.id, sort_order=row.sort_order, deleted_at=row.deleted_at, title=row.title, summary=row.summary,
            chapter=row.chapter, shots=by_scene.get(row.id, []),
        ) for row in rows]
    return schemas.StoryboardProjectOut(
        id=project.id, workspace_id=project.workspace_id, title=project.title,
        deleted_at=project.deleted_at, summary=project.summary, assets=_project_assets(project), status=project.status,
        created_at=project.created_at, updated_at=project.updated_at, scenes=scene_out,
    )


@router.get("/storyboards/trash")
def list_trash(user: CurrentUser, db: DbSession):
    projects = db.scalars(select(models.StoryboardProject).where(
        models.StoryboardProject.owner_user_id == user.id,
        models.StoryboardProject.deleted_at.is_not(None),
    ).order_by(models.StoryboardProject.deleted_at.desc())).all()
    standalone_scenes = db.scalars(select(models.StoryboardScene).join(
        models.StoryboardProject, models.StoryboardScene.project_id == models.StoryboardProject.id
    ).where(
        models.StoryboardProject.owner_user_id == user.id,
        models.StoryboardProject.deleted_at.is_(None),
        models.StoryboardScene.deleted_at.is_not(None),
    ).order_by(models.StoryboardScene.deleted_at.desc())).all()
    standalone_shots = db.scalars(select(models.StoryboardShot).join(
        models.StoryboardScene, models.StoryboardShot.scene_id == models.StoryboardScene.id
    ).join(models.StoryboardProject, models.StoryboardScene.project_id == models.StoryboardProject.id).where(
        models.StoryboardProject.owner_user_id == user.id,
        models.StoryboardProject.deleted_at.is_(None),
        models.StoryboardScene.deleted_at.is_(None),
        models.StoryboardShot.deleted_at.is_not(None),
    ).order_by(models.StoryboardShot.deleted_at.desc())).all()
    return {
        "projects": [_project_out(db, row, scenes=True) for row in projects],
        "scenes": [{"id": row.id, "projectId": row.project_id, "title": row.title, "deletedAt": row.deleted_at} for row in standalone_scenes],
        "shots": [{"id": row.id, "sceneId": row.scene_id, "frameDescription": row.frame_description, "deletedAt": row.deleted_at} for row in standalone_shots],
    }


@router.get("/storyboards", response_model=list[schemas.StoryboardProjectOut])
def list_projects(user: CurrentUser, db: DbSession):
    projects = db.scalars(select(models.StoryboardProject).where(
        models.StoryboardProject.owner_user_id == user.id,
        models.StoryboardProject.deleted_at.is_(None),
    ).order_by(models.StoryboardProject.updated_at.desc())).all()
    return [_project_out(db, row, scenes=False) for row in projects]


@router.post("/storyboards", response_model=schemas.StoryboardProjectOut, status_code=201)
def create_project(payload: schemas.StoryboardProjectCreate, user: CurrentUser, db: DbSession):
    stored = _validate_assets(db, user, payload.assets)
    row = models.StoryboardProject(
        owner_user_id=user.id, workspace_id=default_workspace(db, user).id,
        title=payload.title.strip(), summary=payload.summary, assets=stored,
    )
    db.add(row)
    db.commit()
    return _project_out(db, row)


@router.get("/storyboards/{project_id}", response_model=schemas.StoryboardProjectOut)
def get_project(project_id: str, user: CurrentUser, db: DbSession):
    return _project_out(db, _project(db, user, project_id))


@router.patch("/storyboards/{project_id}", response_model=schemas.StoryboardProjectOut)
def patch_project(project_id: str, payload: schemas.StoryboardProjectPatch, user: CurrentUser, db: DbSession):
    row = _project(db, user, project_id)
    data = payload.model_dump(exclude_unset=True)
    if "assets" in data and data["assets"] is not None:
        data["assets"] = _validate_assets(db, user, data["assets"])
    for key, value in data.items():
        if value is not None:
            setattr(row, key, value.strip() if key == "title" else value)
    db.commit()
    return _project_out(db, row)


@router.delete("/storyboards/{project_id}", status_code=204)
def delete_project(project_id: str, user: CurrentUser, db: DbSession):
    row = _project(db, user, project_id)
    now = datetime.now(timezone.utc)
    _cancel_storyboard_jobs(db, user.id, project_id=row.id)
    for scene in db.scalars(select(models.StoryboardScene).where(models.StoryboardScene.project_id == row.id)).all():
        scene.deleted_at = scene.deleted_at or now
        for shot in db.scalars(select(models.StoryboardShot).where(models.StoryboardShot.scene_id == scene.id)).all():
            shot.deleted_at = shot.deleted_at or now
            shot.generation_version += 1
    row.deleted_at = now
    db.commit()
    return Response(status_code=204)


@router.post("/storyboards/{project_id}/restore", status_code=204)
def restore_project(project_id: str, user: CurrentUser, db: DbSession):
    row = _project(db, user, project_id, include_deleted=True)
    if row.deleted_at is None:
        raise ApiError("not_deleted", "项目不在回收站中", status_code=409)
    row.deleted_at = None
    for scene in db.scalars(select(models.StoryboardScene).where(models.StoryboardScene.project_id == row.id)).all():
        scene.deleted_at = None
        for shot in db.scalars(select(models.StoryboardShot).where(models.StoryboardShot.scene_id == scene.id)).all():
            shot.deleted_at = None
    db.commit()
    return Response(status_code=204)


@router.delete("/storyboards/{project_id}/permanent", status_code=204)
def permanently_delete_project(project_id: str, user: CurrentUser, db: DbSession):
    row = _project(db, user, project_id, include_deleted=True)
    if row.deleted_at is None:
        raise ApiError("not_deleted", "请先将项目移入回收站", status_code=409)
    _cancel_storyboard_jobs(db, user.id, project_id=row.id)
    shots = db.scalars(select(models.StoryboardShot).join(
        models.StoryboardScene, models.StoryboardShot.scene_id == models.StoryboardScene.id
    ).where(models.StoryboardScene.project_id == row.id)).all()
    asset_ids = _asset_ids_for_shots(shots)
    db.execute(models.StoryboardShot.__table__.delete().where(models.StoryboardShot.scene_id.in_(
        select(models.StoryboardScene.id).where(models.StoryboardScene.project_id == row.id)
    )))
    db.execute(models.StoryboardScene.__table__.delete().where(models.StoryboardScene.project_id == row.id))
    db.delete(row)
    paths = _purge_generated_assets(db, asset_ids, row)
    db.commit()
    _unlink_assets(paths)
    return Response(status_code=204)


@router.post("/storyboards/scenes/{scene_id}/restore", status_code=204)
def restore_scene(scene_id: str, user: CurrentUser, db: DbSession):
    scene, project = _scene(db, user, scene_id, include_deleted=True)
    if scene.deleted_at is None:
        raise ApiError("not_deleted", "场景不在回收站中", status_code=409)
    if project.deleted_at is not None:
        raise ApiError("restore_parent_first", "请先恢复所属项目", status_code=409)
    scene.deleted_at = None
    for shot in db.scalars(select(models.StoryboardShot).where(models.StoryboardShot.scene_id == scene.id)).all():
        shot.deleted_at = None
    db.commit()
    return Response(status_code=204)


@router.delete("/storyboards/scenes/{scene_id}/permanent", status_code=204)
def permanently_delete_scene(scene_id: str, user: CurrentUser, db: DbSession):
    scene, project = _scene(db, user, scene_id, include_deleted=True)
    if project.deleted_at is not None:
        raise ApiError("restore_parent_first", "请先永久删除所属项目", status_code=409)
    if scene.deleted_at is None:
        raise ApiError("not_deleted", "请先将场景移入回收站", status_code=409)
    shots = db.scalars(select(models.StoryboardShot).where(models.StoryboardShot.scene_id == scene.id)).all()
    _cancel_storyboard_jobs(db, user.id, shot_ids={shot.id for shot in shots})
    asset_ids = _asset_ids_for_shots(shots)
    db.execute(models.StoryboardShot.__table__.delete().where(models.StoryboardShot.scene_id == scene.id))
    db.delete(scene)
    paths = _purge_generated_assets(db, asset_ids, project)
    db.commit()
    _unlink_assets(paths)
    return Response(status_code=204)


@router.post("/storyboards/shots/{shot_id}/restore", status_code=204)
def restore_shot(shot_id: str, user: CurrentUser, db: DbSession):
    shot, scene, _ = _shot(db, user, shot_id, include_deleted=True)
    if shot.deleted_at is None or scene.deleted_at is not None:
        raise ApiError("restore_parent_first", "请先恢复所属项目和场景", status_code=409)
    shot.deleted_at = None
    db.commit()
    return Response(status_code=204)


@router.delete("/storyboards/shots/{shot_id}/permanent", status_code=204)
def permanently_delete_shot(shot_id: str, user: CurrentUser, db: DbSession):
    shot, _, project = _shot(db, user, shot_id, include_deleted=True)
    if shot.deleted_at is None:
        raise ApiError("not_deleted", "请先将镜头移入回收站", status_code=409)
    _cancel_storyboard_jobs(db, user.id, shot_ids={shot.id})
    asset_ids = _asset_ids_for_shots([shot])
    db.delete(shot)
    paths = _purge_generated_assets(db, asset_ids, project)
    db.commit()
    _unlink_assets(paths)
    return Response(status_code=204)


@router.post("/storyboards/{project_id}/scenes", response_model=schemas.StoryboardSceneOut, status_code=201)
def create_scene(project_id: str, payload: schemas.StoryboardSceneCreate, user: CurrentUser, db: DbSession):
    project = _project(db, user, project_id)
    order = len(db.scalars(select(models.StoryboardScene.id).where(models.StoryboardScene.project_id == project.id)).all())
    row = models.StoryboardScene(project_id=project.id, sort_order=order, **payload.model_dump())
    db.add(row)
    db.commit()
    return schemas.StoryboardSceneOut(id=row.id, sort_order=row.sort_order, title=row.title, summary=row.summary, chapter=row.chapter, shots=[])


@router.patch("/storyboards/scenes/{scene_id}", response_model=schemas.StoryboardSceneOut)
def patch_scene(scene_id: str, payload: schemas.StoryboardScenePatch, user: CurrentUser, db: DbSession):
    row, _ = _scene(db, user, scene_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(row, key, value)
    db.commit()
    return schemas.StoryboardSceneOut(id=row.id, sort_order=row.sort_order, title=row.title, summary=row.summary, chapter=row.chapter, shots=[])


@router.delete("/storyboards/scenes/{scene_id}", status_code=204)
def delete_scene(scene_id: str, user: CurrentUser, db: DbSession):
    row, _ = _scene(db, user, scene_id)
    now = datetime.now(timezone.utc)
    shots = db.scalars(select(models.StoryboardShot).where(models.StoryboardShot.scene_id == row.id)).all()
    _cancel_storyboard_jobs(db, user.id, shot_ids={shot.id for shot in shots})
    row.deleted_at = now
    for shot in shots:
        shot.deleted_at = shot.deleted_at or now
        shot.generation_version += 1
    db.commit()
    return Response(status_code=204)


@router.put("/storyboards/{project_id}/scenes/order")
def reorder_scenes(project_id: str, payload: schemas.StoryboardOrderIn, user: CurrentUser, db: DbSession):
    project = _project(db, user, project_id)
    rows = db.scalars(select(models.StoryboardScene).where(models.StoryboardScene.project_id == project.id)).all()
    if len(rows) != len(payload.ids) or set(payload.ids) != {row.id for row in rows}:
        raise ApiError("invalid_order", "场景顺序必须包含当前项目的全部场景")
    by_id = {row.id: row for row in rows}
    for index, item_id in enumerate(payload.ids):
        by_id[item_id].sort_order = index
    db.commit()
    return {"ok": True}


@router.post("/storyboards/scenes/{scene_id}/shots", response_model=schemas.StoryboardShotOut, status_code=201)
def create_shot(scene_id: str, payload: schemas.StoryboardShotCreate, user: CurrentUser, db: DbSession):
    scene, project = _scene(db, user, scene_id)
    _selected_assets(project, payload.asset_refs)
    order = len(db.scalars(select(models.StoryboardShot.id).where(models.StoryboardShot.scene_id == scene.id)).all())
    row = models.StoryboardShot(scene_id=scene.id, sort_order=order, **payload.model_dump())
    db.add(row)
    db.commit()
    return _shot_out(db, row)


@router.patch("/storyboards/shots/{shot_id}", response_model=schemas.StoryboardShotOut)
def patch_shot(shot_id: str, payload: schemas.StoryboardShotPatch, user: CurrentUser, db: DbSession):
    row, _, project = _shot(db, user, shot_id)
    if row.sketch_status in ("queued", "generating") or row.final_status in ("queued", "generating"):
        raise ApiError("shot_busy", "镜头生成中，完成后再编辑", status_code=409)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if key == "asset_refs":
            _selected_assets(project, value or [])
            row.asset_refs = value or []
            continue
        if value is not None:
            setattr(row, key, value)
    db.commit()
    return _shot_out(db, row)


@router.delete("/storyboards/shots/{shot_id}", status_code=204)
def delete_shot(shot_id: str, user: CurrentUser, db: DbSession):
    row, _, _ = _shot(db, user, shot_id)
    _cancel_storyboard_jobs(db, user.id, shot_ids={row.id})
    row.deleted_at = datetime.now(timezone.utc)
    row.generation_version += 1
    db.commit()
    return Response(status_code=204)


@router.put("/storyboards/scenes/{scene_id}/shots/order")
def reorder_shots(scene_id: str, payload: schemas.StoryboardOrderIn, user: CurrentUser, db: DbSession):
    scene, _ = _scene(db, user, scene_id)
    rows = db.scalars(select(models.StoryboardShot).where(models.StoryboardShot.scene_id == scene.id)).all()
    if len(rows) != len(payload.ids) or set(payload.ids) != {row.id for row in rows}:
        raise ApiError("invalid_order", "镜头顺序必须包含当前场景的全部镜头")
    by_id = {row.id: row for row in rows}
    for index, item_id in enumerate(payload.ids):
        by_id[item_id].sort_order = index
    db.commit()
    return {"ok": True}


def _queue_generation(db, user, shot, project, stage, payload):
    _selected_assets(project, shot.asset_refs or [])
    present = _present_characters(project, shot)
    slots = _storyboard_references(project, shot, stage)
    refs = [
        shot.sketch_asset_id if kind == "sketch" else _item_asset_id(item)
        for kind, item in slots
    ]
    for asset_id in refs:
        asset = db.get(models.Asset, asset_id)
        if not asset or asset.owner_user_id != user.id:
            raise not_found("参考素材")
    shot.generation_version += 1
    version = shot.generation_version
    subject = shot.prompt.strip() or ", ".join(part for part in (
        shot.frame_description, shot.action, shot.dialogue,
        f"{shot.shot_size} shot", f"{shot.view_angle} view", f"{shot.camera_motion} camera motion",
    ) if part)
    if not shot.prompt.strip():
        selected = _selected_assets(project, shot.asset_refs or [])
        names = [
            str(item.get("name") or "").strip()
            for item in selected
            if _category_of(project, item["id"]) != "characters" and str(item.get("name") or "").strip()
        ]
        if names:
            subject = subject + " " + " ".join(names)
    labels = []
    pictured = {}
    for index, (kind, item) in enumerate(slots, start=1):
        if kind == "sketch":
            labels.append(
                f"reference image {index} is the approved sketch. "
                "Keep this camera, framing and character placement. "
                "Raise finish only. Do not move characters or change the shot."
            )
            continue
        note = f": {item['description']}" if item.get("description") else ""
        labels.append(f"reference image {index} is {kind} {item['name']}{note}")
        pictured.setdefault(item["id"], index)
    consistency = ""
    if present:
        listed = []
        for item in present:
            name = str(item["name"]).strip()
            number = pictured.get(item["id"])
            if number:
                listed.append(f"{name} (reference image {number})")
            else:
                description = str(item.get("description") or "").strip()
                listed.append(f"{name}: {description}" if description else name)
        consistency = (
            "Keep every named character identical across shots: same face, hair, "
            "outfit, age and body. Do not redesign them. "
            + ", ".join(listed) + "."
        )
    prompt = "\n".join(part for part in (subject, *labels, consistency) if part)
    job_id = queue.enqueue(
        db, owner_user_id=user.id, queue="ai-image", kind="image-generation",
        payload={
            "prompt": prompt, "purpose": f"storyboard-{stage}", "aspectRatio": "3:4",
            "count": 1 if stage == "sketch" else 4, "modelId": payload.model_id,
            "referenceAssetIds": refs, "storyboardShotId": shot.id,
            "storyboardProjectId": project.id, "workspaceId": project.workspace_id,
            "generationStage": stage, "generationVersion": version,
        },
    )
    setattr(shot, f"{stage}_job_id", job_id)
    setattr(shot, f"{stage}_status", "queued")
    setattr(shot, f"{stage}_error", None)
    if stage == "final":
        shot.final_asset_ids = []
    db.commit()
    return job_id


@router.post("/storyboards/shots/{shot_id}/generate-sketch", status_code=202)
def generate_sketch(shot_id: str, payload: schemas.StoryboardGenerateIn, user: CurrentUser, db: DbSession):
    shot, _, project = _shot(db, user, shot_id)
    if shot.sketch_status in ("queued", "generating") or shot.final_status in ("queued", "generating"):
        raise ApiError("shot_busy", "该镜头已有生成任务在运行", status_code=409)
    return {"jobId": _queue_generation(db, user, shot, project, "sketch", payload), "status": "queued"}


@router.post("/storyboards/shots/{shot_id}/generate-final", status_code=202)
def generate_final(shot_id: str, payload: schemas.StoryboardGenerateIn, user: CurrentUser, db: DbSession):
    shot, _, project = _shot(db, user, shot_id)
    if shot.final_status in ("queued", "generating"):
        raise ApiError("shot_busy", "正式候选正在生成", status_code=409)
    if not shot.sketch_asset_id:
        raise ApiError("sketch_required", "先生成草图，正式图要锁住构图。", status_code=409)
    return {"jobId": _queue_generation(db, user, shot, project, "final", payload), "status": "queued"}


@router.post("/storyboards/shots/{shot_id}/retry", status_code=202)
def retry_shot(shot_id: str, payload: schemas.StoryboardGenerateIn, user: CurrentUser, db: DbSession):
    shot, _, project = _shot(db, user, shot_id)
    stage = "sketch" if shot.sketch_status == "failed" else "final"
    if stage == "final" and not shot.sketch_asset_id:
        raise ApiError("sketch_required", "先生成草图，正式图要锁住构图。", status_code=409)
    return {"jobId": _queue_generation(db, user, shot, project, stage, payload), "status": "queued", "stage": stage}


def _ordered_project(db, user, project_id):
    project = _project(db, user, project_id)
    data = _project_out(db, project)
    return project, data


@router.get("/storyboards/{project_id}/exports/board")
def export_board(project_id: str, user: CurrentUser, db: DbSession):
    _, data = _ordered_project(db, user, project_id)
    return data.model_dump(by_alias=True)


@router.get("/storyboards/{project_id}/exports/prompt-pack")
def export_prompt_pack(project_id: str, user: CurrentUser, db: DbSession):
    _, data = _ordered_project(db, user, project_id)
    lines = [f"# {data.title}", ""]
    for scene in data.scenes:
        lines.extend([f"## {scene.chapter} / {scene.title}", ""])
        for shot in scene.shots:
            # 优先导出实际出图用的完整提示词；没生成过才回退到手写的画面提示词
            full_prompt = shot.final_prompt or shot.sketch_prompt or shot.prompt or shot.frame_description
            lines.extend([f"### 镜头 {shot.sort_order + 1}", full_prompt, ""])
    return Response("\n".join(lines), media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": "attachment; filename=storyboard-prompts.md"})


def _append_breakdown(db, project, scenes: list[dict]) -> int:
    order = len(db.scalars(select(models.StoryboardScene.id).where(
        models.StoryboardScene.project_id == project.id
    )).all())
    added = 0
    for item in scenes:
        scene = models.StoryboardScene(
            project_id=project.id, sort_order=order + added,
            title=item["title"], summary=item["summary"], chapter=item["chapter"],
        )
        db.add(scene)
        db.flush()
        for index, shot in enumerate(item["shots"]):
            db.add(models.StoryboardShot(scene_id=scene.id, sort_order=index, **shot))
        added += 1
    db.commit()
    return added


@router.post("/storyboards/{project_id}/breakdown")
async def breakdown_project(project_id: str, payload: schemas.StoryboardBreakdownIn, user: CurrentUser, db: DbSession):
    from ..services import storyboard_breakdown

    project = _project(db, user, project_id)
    try:
        scenes = await storyboard_breakdown.break_down(db, user.id, payload.source_text, payload.model_id)
    except storyboard_breakdown.BreakdownError as exc:
        raise ApiError(exc.code, exc.message, status_code=422) from exc
    added = _append_breakdown(db, project, scenes)
    return {"addedScenes": added, "project": _project_out(db, project)}


@router.post("/storyboards/scenes/{scene_id}/breakdown")
async def breakdown_scene(scene_id: str, payload: schemas.StoryboardBreakdownIn, user: CurrentUser, db: DbSession):
    from ..services import storyboard_breakdown

    scene, project = _scene(db, user, scene_id)
    existing = db.scalars(select(models.StoryboardShot.id).where(models.StoryboardShot.scene_id == scene.id)).all()
    if existing:
        raise ApiError("scene_has_shots", "这一场已有镜头，拆分不会覆盖。请先清空或直接改镜头。", status_code=409)
    try:
        scenes = await storyboard_breakdown.break_down(db, user.id, payload.source_text, payload.model_id)
    except storyboard_breakdown.BreakdownError as exc:
        raise ApiError(exc.code, exc.message, status_code=422) from exc
    shots = scenes[0]["shots"]
    for index, shot in enumerate(shots):
        db.add(models.StoryboardShot(scene_id=scene.id, sort_order=index, **shot))
    if not scene.summary:
        scene.summary = scenes[0]["summary"]
    db.commit()
    return {"addedShots": len(shots), "project": _project_out(db, project)}
