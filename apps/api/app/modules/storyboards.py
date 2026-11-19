"""Persistent storyboard projects, ordered scenes/shots, generation and exports."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select

from .. import models, schemas
from ..core import queue
from ..core.config import get_settings
from ..core.deps import ApiError, CurrentUser, DbSession, default_workspace, not_found
from ..core.security import sign_asset_url
from ..services.image_service import storage_path
from ..services import handdraw as handdraw_service

from ..core.rbac import require_permission

router = APIRouter(tags=["storyboards"], dependencies=[require_permission("storyboards.use")])


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
    # 按素材 id 保护，而不是按条目 id：角色库的多张参考图也要算「仍被引用」
    referenced = _asset_ids_in_assets(_resolved_assets(db, project)) if project else set()
    paths = []
    for asset_id in asset_ids - referenced:
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


ROLE_PREFIX = "role:"
APPEARANCE_LABELS = {
    "hair": "发型", "hairColor": "发色", "eyes": "眼睛", "face": "面部",
    "outfit": "服装", "body": "体型", "age": "年龄", "extra": "其他",
}


def _role_id(item: dict) -> str | None:
    value = item.get("role_id") or item.get("roleId")
    if not value:
        raw = str(item.get("id") or "")
        if raw.startswith(ROLE_PREFIX):
            value = raw[len(ROLE_PREFIX):]
    if not value:
        return None
    return str(value).strip() or None


def _validate_assets(db, user, assets):
    """校验并精简项目视觉资产。

    角色允许两种来源：老格式本地条目，或 `role:` 前缀的全局角色库引用。
    引用条目的一切展示字段都由服务端按角色库实时回填，不采信前端传值。
    """
    payload = assets.model_dump() if hasattr(assets, "model_dump") else assets
    for category in ("characters", "locations", "styles"):
        values = payload.get(category) or []
        if not isinstance(values, list):
            raise ApiError("invalid_assets", f"{category} 必须是素材列表")
        seen = set()
        for item in values:
            if not isinstance(item, dict):
                raise ApiError("invalid_assets", "视觉资产格式不正确")
            role_id = _role_id(item) if category == "characters" else None
            if role_id:
                row = db.get(models.Character, role_id)
                # 软删角色仍允许引用：老项目不该因为角色下架而无法保存
                if not row or row.owner_user_id != user.id:
                    raise not_found("角色库角色")
                item_id = f"{ROLE_PREFIX}{role_id}"
                if item_id in seen:
                    raise ApiError("invalid_assets", "视觉资产编号不能为空或重复")
                seen.add(item_id)
                item.clear()
                item.update({
                    "id": item_id, "name": row.name, "asset_id": None,
                    "description": "", "role_id": role_id,
                })
                continue
            name = str(item.get("name") or "").strip()
            description = str(item.get("description") or "").strip()
            asset_id = item.get("asset_id") or item.get("assetId")
            if not name or not (description or asset_id):
                raise ApiError("invalid_assets", "视觉资产需要名字，以及参考图或文字描述")
            item_id = str(item.get("id") or "").strip()
            if not item_id or item_id in seen or item_id.startswith(ROLE_PREFIX):
                raise ApiError("invalid_assets", "视觉资产编号不能为空或重复")
            seen.add(item_id)
            if asset_id:
                asset = db.get(models.Asset, asset_id)
                if not asset or asset.owner_user_id != user.id:
                    raise not_found("项目素材")
            item.clear()
            item.update({
                "id": item_id, "name": name, "asset_id": asset_id,
                "description": description,
            })
    return payload


def _project_assets(project) -> dict:
    """项目里存下来的原始资产（角色库引用只存 id + roleId）。"""
    raw = project.assets or {}
    return {
        "characters": raw.get("characters") or [],
        "locations": raw.get("locations") or [],
        "styles": raw.get("styles") or [],
    }


def _appearance_text(appearance) -> str:
    raw = appearance if isinstance(appearance, dict) else {}
    return "，".join(
        f"{label}：{str(raw[key]).strip()}"
        for key, label in APPEARANCE_LABELS.items()
        if str(raw.get(key) or "").strip()
    )


def _primary_asset_id(images) -> str | None:
    rows = [item for item in (images or []) if isinstance(item, dict)]
    usable = [item for item in rows if item.get("assetId") or item.get("asset_id")]
    chosen = next((item for item in usable if item.get("isPrimary")), None) or (usable[0] if usable else None)
    return (chosen.get("assetId") or chosen.get("asset_id")) if chosen else None


def _resolved_assets(db, project) -> dict:
    """把角色库引用展开成生成/展示用的完整条目；老格式本地角色原样保留。

    软删的角色仍能解析：已经引用它的项目不该因为角色下架就静默失去参考图。
    """
    raw = _project_assets(project)
    role_ids = {
        rid for item in raw["characters"]
        if isinstance(item, dict) and (rid := _role_id(item))
    }
    rows = {}
    if role_ids:
        found = db.scalars(select(models.Character).where(
            models.Character.id.in_(role_ids),
            models.Character.owner_user_id == project.owner_user_id,
        )).all()
        rows = {row.id: row for row in found}
    characters = []
    for item in raw["characters"]:
        if not isinstance(item, dict):
            continue
        rid = _role_id(item)
        row = rows.get(rid) if rid else None
        if not row:
            characters.append(dict(item))
            continue
        images = [image for image in (row.images or []) if isinstance(image, dict)]
        appearance = row.appearance or {}
        characters.append({
            "id": f"{ROLE_PREFIX}{row.id}",
            "name": row.name,
            "description": (_appearance_text(appearance) or str(row.notes or ""))[:500],
            "asset_id": _primary_asset_id(images),
            "role_id": row.id,
            "aliases": row.aliases or [],
            "appearance": appearance,
            "images": images,
            "image_urls": [url for url in (_url(image.get("assetId")) for image in images) if url],
        })
    return {"characters": characters, "locations": raw["locations"], "styles": raw["styles"]}


def _asset_ids_in_assets(assets) -> set[str]:
    """项目正在使用的全部素材 id（含角色库多参考图），防止清理时误删。"""
    ids = set()
    for values in assets.values():
        for item in values:
            if not isinstance(item, dict):
                continue
            single = _item_asset_id(item)
            if single:
                ids.add(single)
            for image in item.get("images") or []:
                if isinstance(image, dict) and (image.get("assetId") or image.get("asset_id")):
                    ids.add(image.get("assetId") or image.get("asset_id"))
    return ids


def _pick_character_image(images, shot_size: str, view_angle: str) -> dict | None:
    """每个角色每个镜头只投 1 张：先按景别/视角匹配角度标签，匹配不到回落主图。"""
    rows = [
        image for image in (images or [])
        if isinstance(image, dict) and (image.get("assetId") or image.get("asset_id"))
    ]
    if not rows:
        return None
    size = str(shot_size or "").strip()
    if size in ("wide", "full"):
        targets = ("full", "any")
    elif size in ("close", "extreme_close"):
        targets = ("close", "front", "any")
    else:
        targets = ("front", "any")
    if any(word in str(view_angle or "") for word in ("high", "low", "over", "side", "tilt")):
        targets = ("side",) + targets
    matched = [image for image in rows if str(image.get("angle") or "any") in targets]
    if matched:
        matched.sort(key=lambda image: (
            targets.index(str(image.get("angle") or "any")),
            0 if image.get("isPrimary") else 1,
        ))
        return matched[0]
    return next((image for image in rows if image.get("isPrimary")), rows[0])


def _asset_lookup(assets) -> dict:
    found = {}
    for items in assets.values():
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                found[item["id"]] = item
    return found


def _selected_assets(assets, refs: list[str]) -> list[dict]:
    lookup = _asset_lookup(assets)
    unknown = [item for item in refs if item not in lookup]
    if unknown:
        raise ApiError("invalid_asset_refs", "镜头引用了项目里没有的视觉资产")
    return [lookup[item] for item in refs]


def _category_of(assets, ref: str) -> str:
    return next((key for key, values in assets.items() if any(
        isinstance(row, dict) and row.get("id") == ref for row in values
    )), "")


def _item_asset_id(item: dict) -> str | None:
    return item.get("asset_id") or item.get("assetId") or None


def _named_characters(assets) -> list[dict]:
    return [
        item for item in assets["characters"]
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]


def _mask_span(mask: list[bool], start: int, end: int) -> None:
    for index in range(start, end):
        mask[index] = True


def _present_characters(assets, shot) -> list[dict]:
    """在场角色：按画面、动作、对白、手写提示词的出现顺序点名；长名字先占位。

    角色库条目的 aliases 一并进入匹配词表，命中后统一返回规范名。
    长名字先扫完整段并遮住命中区间，短名字不能落在已被占用的字上。
    正文一个名字都对不上时，才回退到本镜勾选的角色，并保持 asset_refs 顺序。
    """
    terms: list[tuple[str, dict]] = []
    for item in _named_characters(assets):
        words = [str(item["name"]).strip()]
        for alias in item.get("aliases") or []:
            text = str(alias or "").strip()
            if text and text not in words:
                words.append(text)
        for word in words:
            if word:
                terms.append((word, item))
    by_length = sorted(terms, key=lambda pair: len(pair[0]), reverse=True)
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
        for name, item in by_length:
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
    lookup = _asset_lookup(assets)
    fallback = []
    for ref in shot.asset_refs or []:
        item = lookup.get(ref) or {}
        if _category_of(assets, ref) == "characters" and item.get("id") not in {row["id"] for row in fallback}:
            fallback.append(item)
    return fallback


def _first_pictured(assets, refs: list[str], category: str) -> dict | None:
    lookup = _asset_lookup(assets)
    for ref in refs:
        if _category_of(assets, ref) != category:
            continue
        item = lookup.get(ref) or {}
        if _item_asset_id(item):
            return item
    return None


def _normalize_style_config(db, config: dict | None) -> dict:
    """项目级统一风格：指向手绘风格库的 style_number。

    空配置合法（不强制选风格）；非空时 style_number 必须存在且达到可用性口径
    （有参考图，或特征词强到无需参考图），否则明确报错，不静默存一个出不了图的配置。
    """
    if not config:
        return {}
    number = str(config.get("style_number") or "").strip()
    if not number:
        raise ApiError("style_invalid", "统一风格缺少 style_number")
    style = db.scalar(
        select(models.HanddrawStyle).where(models.HanddrawStyle.style_number == number.upper())
    )
    if style is None:
        raise ApiError("style_not_found", f"手绘风格不存在：{number}", status_code=404)
    if not handdraw_service.style_usable(style):
        raise ApiError("style_unusable", f"风格 {number} 既无参考图也无可用特征词，无法保证组内统一")
    return {
        "style_number": style.style_number,
        "generation_name": style.generation_name,
        "group_key": style.group_key,
    }


def _style_context(db, style_config: dict | None, model_code: str | None, subject: str) -> dict:
    """把项目级 style_config 展开成入队用的手绘风格上下文。

    生效机制完整复用手绘库：resolve_activation 决定拼特征词还是带参考图，
    compose_style_segment 产出风格段，语言跟随镜头主体（detect_language）。
    snapshot 记入 payload.styleSnapshot，历史镜头可追溯当时的风格与激活方式。
    """
    config = style_config if isinstance(style_config, dict) else {}
    number = str(config.get("style_number") or "").strip()
    if not number:
        return {"segment": "", "need_reference": False, "snapshot": None}
    style = db.scalar(
        select(models.HanddrawStyle).where(models.HanddrawStyle.style_number == number.upper())
    )
    if style is None:
        # 配置在保存时已校验；库被重建导致编号消失时如实失败，不静默出无风格图。
        raise ApiError("style_not_found", f"手绘风格不存在：{number}", status_code=404)
    activation = handdraw_service.resolve_activation(style, model_code)
    zh, en = handdraw_service.compose_style_segment(style, activation, activation.need_reference)
    language = handdraw_service.detect_language(subject)
    segment = "".join(zh) if language == "zh" else " ".join(en)
    snapshot = {
        "styleNumber": style.style_number,
        "generationName": style.generation_name,
        "groupKey": style.group_key,
        "referenceAuthor": style.reference_author,
        "traits": activation.traits,
        "activationMode": activation.activation_source,
        "modelCode": activation.model_code,
        "language": language,
        "promptHead": segment,
        "needReference": activation.need_reference,
    }
    return {"segment": segment, "need_reference": activation.need_reference, "snapshot": snapshot}


def _storyboard_references(assets, shot, stage: str, *, style_reserved: bool = False) -> list[tuple[str, dict | None]]:
    """最多 4 张：在场角色图 ≤3，地点 ≤1，风格 ≤1。名额不够先丢地点，风格最后丢。

    角色库条目带多张参考图时，按镜头景别/视角挑 1 张，绝不让一个人占满名额。
    正式图最后一张固定为已成功草图，因此角色+地点+风格先截到 3 张。
    草图阶段不带草图自己。请求体里的 reference_asset_ids 不并入。

    项目选了手绘统一风格时（style_reserved），风格参考图由 job_runner 的
    _builtin_reference 按 builtinReferenceStyleNumber 补到第 1 槽，这里给它
    预扣 1 个名额：用户参考图上限从 4 降到 3，超额时按既有顺序丢弃，不报错。
    """
    slots: list[tuple[str, dict | None]] = []
    for item in _present_characters(assets, shot):
        if len([row for row in slots if row[0] == "character"]) >= 3:
            break
        chosen = dict(item)
        picked = _pick_character_image(item.get("images"), shot.shot_size, shot.view_angle)
        if picked:
            chosen["asset_id"] = picked.get("assetId") or picked.get("asset_id")
            chosen.pop("assetId", None)
            chosen["chosenAngle"] = picked.get("angle") or "any"
        asset_id = _item_asset_id(chosen)
        if asset_id:
            slots.append(("character", chosen))
    location = _first_pictured(assets, shot.asset_refs or [], "locations")
    if location:
        slots.append(("location", location))
    style = _first_pictured(assets, shot.asset_refs or [], "styles")
    if style:
        slots.append(("style", style))
    cap = 3 if stage == "final" else 4
    if style_reserved:
        cap -= 1
    while len(slots) > cap:
        kinds = [kind for kind, _ in slots]
        # 丢弃优先级：地点 → 角色 → 镜头风格资产。
        if "location" in kinds:
            drop_kind = "location"
        elif "character" in kinds:
            drop_kind = "character"
        elif "style" in kinds:
            drop_kind = "style"
        else:
            break
        candidates = [i for i, (kind, _) in enumerate(slots) if kind == drop_kind]
        if not candidates:
            break
        slots.pop(max(candidates))
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


def _generation_payload(db, job_id: str | None) -> dict:
    """读取入队时的 prompt 与风格快照；不存在 job 时返回空对象。"""
    if not job_id:
        return {}
    job = db.get(models.BackgroundJob, job_id)
    return job.payload if job and isinstance(job.payload, dict) else {}


def _generation_prompt(db, job_id: str | None) -> str | None:
    """读取入队时保存的完整提示词，不受镜头后续编辑影响。"""
    return _generation_payload(db, job_id).get("prompt") or None


def _generation_style_snapshot(db, job_ids: list[str | None]) -> dict | None:
    """优先取正式图阶段，其次草图阶段的风格快照。"""
    for job_id in job_ids:
        snapshot = _generation_payload(db, job_id).get("styleSnapshot")
        if isinstance(snapshot, dict):
            return snapshot
    return None


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
        style_snapshot=_generation_style_snapshot(db, [shot.final_job_id, shot.sketch_job_id]),
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
        deleted_at=project.deleted_at, summary=project.summary, assets=_resolved_assets(db, project), status=project.status,
        style_config=project.style_config if isinstance(project.style_config, dict) else {},
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
        style_config=_normalize_style_config(db, payload.style_config),
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
    if "style_config" in data and data["style_config"] is not None:
        data["style_config"] = _normalize_style_config(db, data["style_config"])
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
    _selected_assets(_resolved_assets(db, project), payload.asset_refs)
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
            _selected_assets(_resolved_assets(db, project), value or [])
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
    assets = _resolved_assets(db, project)
    _selected_assets(assets, shot.asset_refs or [])
    present = _present_characters(assets, shot)
    subject = shot.prompt.strip() or ", ".join(part for part in (
        shot.frame_description, shot.action, shot.dialogue,
        f"{shot.shot_size} shot", f"{shot.view_angle} view", f"{shot.camera_motion} camera motion",
    ) if part)
    if not shot.prompt.strip():
        selected = _selected_assets(assets, shot.asset_refs or [])
        names = [
            str(item.get("name") or "").strip()
            for item in selected
            if _category_of(assets, item["id"]) != "characters" and str(item.get("name") or "").strip()
        ]
        if names:
            subject = subject + " " + " ".join(names)
    style = _style_context(db, project.style_config, payload.model_id, subject)
    slots = _storyboard_references(assets, shot, stage, style_reserved=style["need_reference"])
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
    labels = []
    pictured = {}
    # 手绘风格参考图由 job_runner 插到参考图列表最前（第 1 槽），这里的标注从 2 起算。
    label_offset = 1 if style["need_reference"] else 0
    for index, (kind, item) in enumerate(slots, start=1 + label_offset):
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
    # 手绘风格段置于 prompt 最前，作为整组镜头的画风锚点；其后依次是
    # 画面主体、参考图标注、角色一致性句。风格来自项目 style_config，改风格后
    # 新入队的镜头即应用新风格，历史镜头靠 payload 快照追溯旧风格。
    prompt = "\n".join(part for part in (style["segment"], subject, *labels, consistency) if part)
    # 生成时把实际用到的角色数据快照进 payload：之后改全局角色或删角色，
    # 历史镜头的「实际出图提示词」与当时用的参考图仍可追溯。
    pictured_assets = {
        item["id"]: _item_asset_id(item)
        for kind, item in slots if kind == "character" and item
    }
    character_snapshot = [
        {
            "roleId": item.get("role_id"),
            "name": str(item.get("name") or "").strip(),
            "aliases": item.get("aliases") or [],
            "appearance": item.get("appearance") or {},
            "description": str(item.get("description") or ""),
            "allImageAssetIds": [
                image.get("assetId") for image in (item.get("images") or [])
                if isinstance(image, dict) and image.get("assetId")
            ],
            "referenceAssetId": pictured_assets.get(item.get("id")),
        }
        for item in present
    ]
    job_id = queue.enqueue(
        db, owner_user_id=user.id, queue="ai-image", kind="image-generation",
        payload={
            "prompt": prompt, "purpose": f"storyboard-{stage}", "aspectRatio": "3:4",
            "count": 1 if stage == "sketch" else 4, "modelId": payload.model_id,
            "referenceAssetIds": refs, "storyboardShotId": shot.id,
            "storyboardProjectId": project.id, "workspaceId": project.workspace_id,
            "generationStage": stage, "generationVersion": version,
            "characterSnapshot": character_snapshot,
            # 手绘统一风格：风格参考图沿用 job_runner 的 _builtin_reference 路径
            # （按 builtinReferenceStyleNumber 补到参考图第 1 槽，缺失即明确失败）；
            # styleSnapshot 原样透传进 job.result 供历史镜头追溯当时风格。
            "builtinReferenceStyleNumber": (
                style["snapshot"]["styleNumber"] if style["need_reference"] else None
            ),
            "styleSnapshot": style["snapshot"],
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
            # 优先导出实际出图用的完整提示词；没生成过才回退到手写的画面提示词。
            # 风格快照单独导出，保留锁定机制与当时生效的模型信息，便于追溯。
            full_prompt = shot.final_prompt or shot.sketch_prompt or shot.prompt or shot.frame_description
            lines.extend([f"### 镜头 {shot.sort_order + 1}", "", full_prompt, ""])
            if shot.style_snapshot:
                snapshot = shot.style_snapshot
                lines.extend([
                    "风格快照：",
                    f"- 风格：{snapshot.get('generationName') or snapshot.get('styleNumber')}",
                    f"- 编号：{snapshot.get('styleNumber')}",
                    f"- 锁定方式：{'参考图' if snapshot.get('needReference') else '文字特征'}",
                    f"- 模型：{snapshot.get('modelCode') or '未指定'}",
                    f"- 生效特征：{snapshot.get('traits') or '无'}",
                    "",
                ])
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
        # 显式选了不可用模型属于请求错误，与「供应商暂时不可用」区分开
        status = 400 if exc.code == "model_not_found" else 422
        raise ApiError(exc.code, exc.message, status_code=status) from exc
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
        # 显式选了不可用模型属于请求错误，与「供应商暂时不可用」区分开
        status = 400 if exc.code == "model_not_found" else 422
        raise ApiError(exc.code, exc.message, status_code=status) from exc
    shots = scenes[0]["shots"]
    for index, shot in enumerate(shots):
        db.add(models.StoryboardShot(scene_id=scene.id, sort_order=index, **shot))
    if not scene.summary:
        scene.summary = scenes[0]["summary"]
    db.commit()
    return {"addedShots": len(shots), "project": _project_out(db, project)}
