"""角色库：用户级、跨分镜项目复用的视觉角色卡。

CRUD、「从正文抽取草稿」、「AI 补全外貌」、「AI 生成参考图」。
抽取/补全都不落库，必须人工确认后写入；生成图先进 pending_images 候选位，
采用才进 images——和 extract 的草稿→确认模式同构。
分镜侧通过 `role:<id>` 引用这里的角色，见 modules/storyboards.py 的双来源解析。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Response, status
from sqlalchemy import select

from .. import models, schemas
from ..core import queue
from ..core.deps import ApiError, CurrentUser, DbSession, default_workspace, not_found
from ..core.security import sign_asset_url
from ..core.config import get_settings
from ..services import character_extract, character_images

router = APIRouter(prefix="/characters", tags=["characters"])


def _url(asset_id: str | None) -> str | None:
    return f"{get_settings().api_prefix}/assets/{asset_id}/raw?{sign_asset_url(asset_id)}" if asset_id else None


def _clean_aliases(values) -> list[str]:
    cleaned: list[str] = []
    for value in values or []:
        text = str(value or "").strip()[:120]
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned[:12]


def _clean_appearance(values) -> dict[str, str]:
    raw = values if isinstance(values, dict) else {}
    cleaned: dict[str, str] = {}
    for key in schemas.APPEARANCE_KEYS:
        text = str(raw.get(key) or "").strip()
        if text:
            cleaned[key] = text[:200]
    return cleaned


def _clean_images(db, user, values) -> list[dict]:
    """校验参考图归属，归一化角度标签并保证有且仅有一张主图。"""
    cleaned: list[dict] = []
    seen: set[str] = set()
    for value in (values or [])[:8]:
        item = value.model_dump() if hasattr(value, "model_dump") else dict(value or {})
        asset_id = str(item.get("asset_id") or item.get("assetId") or "").strip()
        if not asset_id or asset_id in seen:
            continue
        asset = db.get(models.Asset, asset_id)
        if not asset or asset.owner_user_id != user.id:
            raise not_found("角色参考图")
        angle = str(item.get("angle") or "front").strip()
        if angle not in schemas.CHARACTER_ANGLES:
            angle = "any"
        seen.add(asset_id)
        cleaned.append({
            "assetId": asset_id,
            "angle": angle,
            "isPrimary": bool(item.get("is_primary") or item.get("isPrimary")),
            "label": str(item.get("label") or "").strip()[:60],
        })
    primary = next((index for index, item in enumerate(cleaned) if item["isPrimary"]), None)
    for index, item in enumerate(cleaned):
        item["isPrimary"] = index == (primary if primary is not None else 0)
    return cleaned


def _alive(db, user_id: str, asset_id) -> bool:
    """素材存在且属于本人。悬空引用（asset 被删）必须跳过而不是 500。"""
    if not asset_id:
        return False
    asset = db.get(models.Asset, asset_id)
    return bool(asset and asset.owner_user_id == user_id)


def _pending_out(db, user_id: str, raw: dict) -> schemas.CharacterPendingImageOut:
    asset_id = raw.get("assetId") or raw.get("asset_id")
    state = str(raw.get("status") or "queued")
    # 已出图但 asset 后来被删：如实归为 failed，不谎报成功
    if state == "succeeded" and not _alive(db, user_id, asset_id):
        state, asset_id = "failed", None
    return schemas.CharacterPendingImageOut(
        asset_id=asset_id, angle=raw.get("angle") or "front",
        job_id=raw.get("jobId"), status=state, prompt=raw.get("prompt") or "",
        degraded=bool(raw.get("degraded")), error=raw.get("error"),
        url=_url(asset_id) if state == "succeeded" else None,
    )


def _character_out(db, row: models.Character) -> schemas.CharacterOut:
    images = []
    for item in row.images or []:
        asset_id = item.get("assetId") or item.get("asset_id")
        if not _alive(db, row.owner_user_id, asset_id):
            continue  # 素材已被删除的悬空引用：跳过，角色卡照常可读
        images.append(schemas.CharacterImageOut(
            asset_id=asset_id, angle=item.get("angle") or "front",
            is_primary=bool(item.get("isPrimary")), label=item.get("label") or "",
            url=_url(asset_id),
        ))
    pending = [_pending_out(db, row.owner_user_id, item) for item in (row.pending_images or [])]
    return schemas.CharacterOut(
        id=row.id, name=row.name, aliases=row.aliases or [],
        appearance=row.appearance or {}, images=images, pending_images=pending,
        notes=row.notes or "",
        created_at=row.created_at, updated_at=row.updated_at,
    )


def _owned(db, user, character_id: str) -> models.Character:
    row = db.get(models.Character, character_id)
    if not row or row.owner_user_id != user.id or row.deleted_at:
        raise not_found("角色")
    return row


@router.get("", response_model=list[schemas.CharacterOut])
def list_characters(user: CurrentUser, db: DbSession):
    rows = db.scalars(select(models.Character).where(
        models.Character.owner_user_id == user.id,
        models.Character.deleted_at.is_(None),
    ).order_by(models.Character.updated_at.desc())).all()
    return [_character_out(db, row) for row in rows]


@router.post("", response_model=schemas.CharacterOut, status_code=status.HTTP_201_CREATED)
def create_character(payload: schemas.CharacterIn, user: CurrentUser, db: DbSession):
    row = models.Character(
        owner_user_id=user.id,
        name=payload.name.strip(),
        aliases=_clean_aliases(payload.aliases),
        appearance=_clean_appearance(payload.appearance),
        images=_clean_images(db, user, payload.images),
        notes=payload.notes.strip(),
    )
    db.add(row)
    db.commit()
    return _character_out(db, row)


@router.post("/extract", response_model=schemas.CharacterExtractOut)
async def extract_characters(payload: schemas.CharacterExtractIn, user: CurrentUser, db: DbSession):
    """抽取草稿：不写库、不生成占位角色；失败时把可操作原因原样透传。"""
    try:
        result = await character_extract.extract(db, user.id, payload.source_text, payload.model_id)
    except character_extract.ExtractionError as exc:
        code_status = 503 if exc.code == "provider_unavailable" else (400 if exc.code == "model_not_found" else 502)
        raise ApiError(exc.code, exc.message, status_code=code_status) from exc
    existing = {
        str(name).strip().lower()
        for name in db.scalars(select(models.Character.name).where(
            models.Character.owner_user_id == user.id,
            models.Character.deleted_at.is_(None),
        ))
    }
    drafts = [
        schemas.CharacterDraftOut(
            **draft, exists=draft["name"].strip().lower() in existing,
        )
        for draft in result["drafts"]
    ]
    return schemas.CharacterExtractOut(
        drafts=drafts, model_code=result["model_code"], fallback_used=result["fallback_used"],
    )


def _anchor_asset(row: models.Character, db, user_id: str) -> str | None:
    """锚定用的参考图：front 优先，其次任意一张还活着的（Q11：不为标签洁癖挡路）。"""
    alive = [
        (item.get("angle") or "front", item.get("assetId") or item.get("asset_id"))
        for item in (row.images or [])
        if _alive(db, user_id, item.get("assetId") or item.get("asset_id"))
    ]
    if not alive:
        return None
    return next((asset_id for angle, asset_id in alive if angle == "front"), alive[0][1])


def _drop_pending_assets(db, row: models.Character, keep: list[dict]) -> None:
    """删除不再保留的候选 asset。先 commit DB 删行、成功后再清磁盘，同 assets.delete_asset 约定。"""
    kept = {item.get("assetId") for item in keep if item.get("assetId")}
    doomed: list[tuple[str, str]] = []
    for item in row.pending_images or []:
        asset_id = item.get("assetId")
        if not asset_id or asset_id in kept:
            continue
        asset = db.get(models.Asset, asset_id)
        if asset and asset.owner_user_id == row.owner_user_id:
            doomed.append((asset_id, asset.storage_key))
            db.delete(asset)
    if not doomed:
        return
    db.commit()
    from ..services import image_service

    settings = get_settings()
    for _asset_id, storage_key in doomed:
        try:
            image_service.storage_path(settings.storage_dir, storage_key).unlink(missing_ok=True)
        except OSError:
            pass  # 只留孤儿文件，不影响数据一致性


@router.get("/{character_id}", response_model=schemas.CharacterOut)
def get_character(character_id: str, user: CurrentUser, db: DbSession):
    return _character_out(db, _owned(db, user, character_id))


@router.post("/{character_id}/appearance/complete", response_model=schemas.CharacterAppearanceCompleteOut)
async def complete_appearance(
    character_id: str, payload: schemas.CharacterAppearanceCompleteIn, user: CurrentUser, db: DbSession
):
    """AI 补全外貌：只返回建议字段，不落库；前端勾选采纳，且只填空白字段。"""
    row = _owned(db, user, character_id)
    try:
        result = await character_extract.complete_appearance(
            db, user.id, row.name, payload.hint_text, payload.source_text, payload.model_id,
        )
    except character_extract.ExtractionError as exc:
        code_status = 503 if exc.code == "provider_unavailable" else (400 if exc.code == "model_not_found" else 502)
        raise ApiError(exc.code, exc.message, status_code=code_status) from exc
    return schemas.CharacterAppearanceCompleteOut(
        appearance=result["appearance"], model_code=result["model_code"],
        fallback_used=result["fallback_used"],
    )


@router.post("/{character_id}/images/generate", response_model=schemas.CharacterImageGenerateOut, status_code=202)
def generate_character_image(
    character_id: str, payload: schemas.CharacterImageGenerateIn, user: CurrentUser, db: DbSession
):
    """生成一张角度参考图：入队 ai-image（kind=character.image），结果先进候选位。"""
    row = _owned(db, user, character_id)
    angle = payload.angle if payload.angle in schemas.CHARACTER_ANGLES else "any"
    prompt = payload.prompt_override.strip()[:4000] or character_images.build_prompt(
        row.name, row.appearance, angle, payload.style
    )
    anchor = _anchor_asset(row, db, user.id)
    workspace = default_workspace(db, user)
    job_payload = {
        "prompt": prompt,
        "aspectRatio": "3:4",
        "count": 1,
        "characterId": row.id,
        "characterAngle": angle,
        "workspaceId": workspace.id if workspace else None,
    }
    if anchor:
        job_payload["referenceAssetIds"] = [anchor]
    if payload.model_id:
        job_payload["modelId"] = payload.model_id
    # 同角度重新生成：旧候选作废，其 asset 一并删除（Q6-B）
    keep = [item for item in (row.pending_images or []) if item.get("angle") != angle]
    _drop_pending_assets(db, row, keep)
    row.pending_images = keep + [{
        "assetId": None, "angle": angle, "jobId": None,
        "status": "queued", "prompt": prompt, "degraded": False,
    }]
    db.commit()
    job_id = queue.enqueue(
        db, owner_user_id=user.id, queue="ai-image", kind="character.image", payload=job_payload,
    )
    pending = [dict(item) for item in row.pending_images]
    for item in pending:
        if item.get("angle") == angle and not item.get("jobId"):
            item["jobId"] = job_id
    row.pending_images = pending
    db.commit()
    return schemas.CharacterImageGenerateOut(job_id=job_id, prompt=prompt, anchored=bool(anchor))


@router.post("/{character_id}/images/{job_id}/accept", response_model=schemas.CharacterOut)
def accept_character_image(character_id: str, job_id: str, user: CurrentUser, db: DbSession):
    """采用候选图进 images；该角色第一张被采用的图自动成为主图。"""
    row = _owned(db, user, character_id)
    hit = next(
        (item for item in (row.pending_images or []) if item.get("jobId") == job_id), None
    )
    asset_id = (hit or {}).get("assetId")
    if hit is None or hit.get("status") != "succeeded" or not _alive(db, user.id, asset_id):
        raise not_found("候选参考图")
    images = list(row.images or [])
    images.append({
        "assetId": asset_id, "angle": hit.get("angle") or "any",
        "isPrimary": not any(bool(item.get("isPrimary")) for item in images),
        "label": "",
    })
    row.images = images[-8:]
    row.pending_images = [item for item in row.pending_images if item.get("jobId") != job_id]
    db.commit()
    return _character_out(db, row)


@router.delete("/{character_id}/images/{job_id}", status_code=204)
def reject_character_image(character_id: str, job_id: str, user: CurrentUser, db: DbSession):
    """拒绝候选：移出候选位并删除其 asset。"""
    row = _owned(db, user, character_id)
    keep = [item for item in (row.pending_images or []) if item.get("jobId") != job_id]
    if len(keep) == len(row.pending_images or []):
        raise not_found("候选参考图")
    _drop_pending_assets(db, row, keep)
    row.pending_images = keep
    db.commit()
    return Response(status_code=204)


@router.patch("/{character_id}", response_model=schemas.CharacterOut)
def patch_character(character_id: str, payload: schemas.CharacterPatch, user: CurrentUser, db: DbSession):
    row = _owned(db, user, character_id)
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        row.name = data["name"].strip()
    if "aliases" in data and data["aliases"] is not None:
        row.aliases = _clean_aliases(data["aliases"])
    if "appearance" in data and data["appearance"] is not None:
        row.appearance = _clean_appearance(data["appearance"])
    if "images" in data and data["images"] is not None:
        row.images = _clean_images(db, user, data["images"])
    if "notes" in data and data["notes"] is not None:
        row.notes = data["notes"].strip()
    db.commit()
    return _character_out(db, row)


@router.delete("/{character_id}", status_code=204)
def delete_character(character_id: str, user: CurrentUser, db: DbSession):
    row = _owned(db, user, character_id)
    # 软删：已引用它的分镜项目仍要能生成，历史 prompt 由 job.payload 快照负责
    row.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return Response(status_code=204)
