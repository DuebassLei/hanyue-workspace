"""AI 生图、上传会话与生成任务取消（设计 §10.2 / §6.6 / §8）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query

from .. import models, schemas
from ..core import queue
from ..core.config import get_settings
from ..core.deps import ApiError, CurrentUser, DbSession, not_found
from ..core.security import create_upload_session as sign_upload_session
from ..services import image_provider

from ..core.rbac import require_permission

router = APIRouter(tags=["ai-images"])

# 生图接口被「图片生成」页和「创作空间」共用（Studio 内嵌配图），
# 所以按 any 放行；状态探针与上传会话是跨页面基础设施，不挂权限。
_IMAGE_USE = require_permission("images.use", "studio.use", mode="any")


@router.get("/ai/image-provider/status")
def image_provider_status(user: CurrentUser, db: DbSession) -> dict:
    return image_provider.status(db, user.id)


@router.post("/ai/image-generations", status_code=202, dependencies=[_IMAGE_USE])
def create_image_generation(
    payload: schemas.ImageGenerationIn, user: CurrentUser, db: DbSession
) -> dict:
    """创建异步生图任务；没有供应商时任务会明确失败，不会产出假图。"""
    if payload.count < 1 or payload.count > 4:
        raise ApiError("invalid_count", "生成数量需在 1~4 之间")
    if len(payload.reference_asset_ids) > 4:
        raise ApiError("invalid_references", "参考图最多 4 张")
    for asset_id in payload.reference_asset_ids:
        asset = db.get(models.Asset, asset_id)
        if not asset or asset.owner_user_id != user.id:
            raise not_found("参考图素材")
    job_id = queue.enqueue(
        db,
        owner_user_id=user.id,
        queue="ai-image",
        kind="image-generation",
        payload={
            "prompt": payload.prompt,
            "purpose": payload.purpose,
            "aspectRatio": payload.aspect_ratio,
            "exactAspect": payload.exact_aspect,
            "count": payload.count,
            "modelId": payload.model_id,
            "referenceAssetIds": payload.reference_asset_ids,
            "builtinReferenceStyleNumber": payload.builtin_reference_style_number,
            # 负向词 / seed：地基已打通执行器与 provider，这里是从 HTTP 入队的最后一环。
            "negativePrompt": payload.negative_prompt,
            "seed": payload.seed,
        },
    )
    return {
        "jobId": job_id,
        "status": "queued",
        "queue": "ai-image",
        "backend": queue.backend_name(),
        "message": "已入队；请用 GET /jobs/{jobId} 查询结果，未配置供应商时会明确报错",
    }


@router.post("/ai/generations/{generation_id}/cancel", dependencies=[_IMAGE_USE])
def cancel_generation(
    generation_id: str, user: CurrentUser, db: DbSession
) -> dict:
    generation = db.get(models.AiGeneration, generation_id)
    if not generation or generation.owner_user_id != user.id:
        raise not_found("生成记录")
    if generation.status in ("succeeded", "failed", "cancelled"):
        raise ApiError("invalid_transition", f"当前状态 {generation.status} 不能取消", status_code=409)
    generation.status = "cancelled"
    db.commit()
    return {"id": generation.id, "status": generation.status}


@router.post("/ai/image-interrogate", dependencies=[_IMAGE_USE])
def interrogate_image(payload: schemas.ImageInterrogateIn, user: CurrentUser, db: DbSession) -> dict:
    """反推提示词：给定素材图片，调用视觉语言模型生成描述该图的提示词。

    若 saveToAsset=true 则同时更新素材的 prompt 字段。
    """
    asset = db.get(models.Asset, payload.asset_id)
    if not asset or asset.owner_user_id != user.id:
        raise not_found("素材")

    settings = get_settings()
    from ..services.image_service import storage_path

    path: Path = storage_path(settings.storage_dir, asset.storage_key)
    if not path.is_file():
        raise ApiError("asset_file_missing", f"素材文件缺失：{asset.original_name}")

    image_bytes = path.read_bytes()
    mime = asset.mime_type or "image/jpeg"

    try:
        prompt_text = image_provider.interrogate_image(
            db,
            user.id,
            image_bytes=image_bytes,
            mime_type=mime,
            model_id=payload.model_id,
        )
    except image_provider.ImageProviderUnavailable as exc:
        raise ApiError(exc.code, exc.message) from exc

    if payload.save_to_asset:
        asset.prompt = prompt_text
        db.commit()

    return {"prompt": prompt_text, "assetId": asset.id}


@router.post("/assets/upload-sessions")
def create_upload_session(
    user: CurrentUser,
    db: DbSession,
    max_bytes: int = Query(default=15 * 1024 * 1024, le=50 * 1024 * 1024, alias="maxBytes"),
) -> dict:
    """签发一次性上传会话；实际上传仍走 /assets 并携带 sessionId。"""
    session = sign_upload_session(user.id, max_bytes=max_bytes)
    return {
        "sessionId": session["token"],
        "uploadUrl": "/api/v1/assets",
        "expiresAt": session["expiresAt"],
        "maxBytes": max_bytes,
        "acceptedMimeTypes": ["image/jpeg", "image/png", "image/webp", "image/gif"],
        "note": "上传时在 /assets 上附带 sessionId 查询参数；会话过期后需重新签发",
    }
