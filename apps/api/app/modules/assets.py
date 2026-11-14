from __future__ import annotations

import hashlib
import mimetypes
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, File, Form, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select

from .. import models, schemas
from ..core import pagination, queue
from ..core.config import get_settings
from ..core.deps import ApiError, CurrentUser, DbSession, OptionalUser, default_workspace, not_found
from ..core.security import sign_asset_url, verify_asset_signature, verify_upload_session
from ..services import image_service

router = APIRouter(tags=["assets"])
settings = get_settings()

ALLOWED_MIME = set(image_service.ALLOWED_INPUT)

# 前端筛选用的格式别名 -> 存储的 MIME 类型
_FORMAT_MIME = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
}


def _asset_out(asset: models.Asset) -> schemas.AssetOut:
    return schemas.AssetOut(
        id=asset.id,
        original_name=asset.original_name,
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        width=asset.width,
        height=asset.height,
        source_type=asset.source_type,
        prompt=asset.prompt,
        created_at=asset.created_at,
        url=asset_raw_url(asset),
    )


def asset_raw_url(asset: models.Asset) -> str:
    """带签名的素材直链（相对路径 + 查询串），可被 <img src> 直接使用。"""
    return f"{settings.api_prefix}/assets/{asset.id}/raw?{sign_asset_url(asset.id)}"


def _asset_disk_path(asset: models.Asset):
    return image_service.storage_path(settings.storage_dir, asset.storage_key)


@router.get("/assets", response_model=list[schemas.AssetOut])
def list_assets(
    user: CurrentUser,
    db: DbSession,
    response: Response,
    limit: int = Query(default=100, le=200),
    cursor: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=100),
    source_type: str | None = Query(default=None, alias="sourceType"),
    fmt: str | None = Query(default=None, alias="format"),
    days: int | None = Query(default=None, ge=1, le=3650),
) -> list[schemas.AssetOut]:
    stmt = select(models.Asset).where(models.Asset.owner_user_id == user.id)
    keyword = (q or "").strip()
    if keyword:
        # 转义 LIKE 通配符，让用户输入的 % / _ 按字面匹配
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        stmt = stmt.where(
            or_(
                models.Asset.original_name.ilike(pattern, escape="\\"),
                models.Asset.prompt.ilike(pattern, escape="\\"),
            )
        )
    if source_type:
        stmt = stmt.where(models.Asset.source_type == source_type)
    mime = _FORMAT_MIME.get((fmt or "").lower())
    if mime:
        stmt = stmt.where(models.Asset.mime_type == mime)
    if days:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = stmt.where(models.Asset.created_at >= since)
    rows, next_cursor = pagination.paginate(
        db, stmt, models.Asset,
        sort_column=models.Asset.created_at, cursor=cursor, limit=limit,
    )
    pagination.set_cursor_header(response, next_cursor)
    return [_asset_out(row) for row in rows]


@router.post("/assets", response_model=schemas.AssetOut, status_code=201)
async def upload_asset(
    user: CurrentUser,
    db: DbSession,
    file: UploadFile = File(...),
    source_type: str = Form(default="upload"),
    session_id: str | None = Query(default=None, alias="sessionId"),
) -> schemas.AssetOut:
    # 上限始终生效；签名上传会话可选，用于把限额收紧到更小的值。
    max_bytes = settings.max_upload_bytes
    if session_id:
        session = verify_upload_session(session_id)
        if not session or session.get("sub") != user.id:
            raise ApiError("invalid_upload_session", "上传会话无效或已过期，请重新签发")
        max_bytes = min(max_bytes, int(session.get("maxBytes") or max_bytes))
    # 分块读取并累计校验，避免单个超大文件把整块内存吃满
    buffer = bytearray()
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise ApiError("payload_too_large", f"文件超过 {max_bytes // (1024 * 1024)} MB 上限")
    data = bytes(buffer)
    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    if mime not in ALLOWED_MIME:
        raise ApiError("unsupported_type", "仅支持 JPEG/PNG/WebP/GIF/BMP 图片")
    try:
        width, height = image_service.read_bounds(data)
        image_service.validate_upload(mime, len(data), width, height)
    except image_service.ImageValidationError as exc:
        raise ApiError("invalid_image", str(exc)) from exc

    workspace = default_workspace(db, user)
    key = f"{user.id}/{uuid.uuid4().hex}{_ext(mime)}"
    path = image_service.storage_path(settings.storage_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

    asset = models.Asset(
        owner_user_id=user.id,
        workspace_id=workspace.id,
        storage_key=key,
        original_name=file.filename or "upload",
        mime_type=mime,
        size_bytes=len(data),
        width=width,
        height=height,
        source_type=source_type,
        checksum=hashlib.sha256(data).hexdigest(),
    )
    db.add(asset)
    db.commit()
    return _asset_out(asset)


@router.get("/assets/{asset_id}/raw")
def get_asset_raw(
    asset_id: str,
    db: DbSession,
    user: OptionalUser = None,
    exp: int | None = Query(default=None),
    sig: str | None = Query(default=None),
    inline: int | None = Query(default=None),
):
    """素材原图。

    两条访问路径：
      1. 已登录 + 本人素材（编辑器、素材页走这条，保持原有行为）；
      2. 带签名直链（exp/sig），供正文里的 <img src> 使用——图片标签
         没法自己带 Authorization 头，否则预览全是裂图、复制出去的链接也打不开。
    """
    authed = user is not None
    signed = bool(exp and sig and verify_asset_signature(asset_id, exp, sig))
    if not authed and not signed:
        # 未登录且无有效签名：不暴露素材是否存在
        raise not_found("素材")
    asset = db.get(models.Asset, asset_id)
    if not asset:
        raise not_found("素材")
    if not signed and (not user or asset.owner_user_id != user.id):
        raise not_found("素材")
    path = _asset_disk_path(asset)
    if not path.exists():
        raise not_found("图片文件")
    return FileResponse(
        path,
        media_type=asset.mime_type,
        # 默认带 filename => attachment，链接可直接下载且保留原始文件名；
        # inline=1 时不声明下载，供「打开原图」在新标签里直接显示图片。
        filename=None if inline else asset.original_name,
        # 签名 URL 可长缓存；鉴权访问每次都要重新校验
        headers={"Cache-Control": "private, max-age=86400"} if signed else None,
    )


@router.delete("/assets/{asset_id}", status_code=204)
def delete_asset(asset_id: str, user: CurrentUser, db: DbSession) -> None:
    asset = db.get(models.Asset, asset_id)
    if not asset or asset.owner_user_id != user.id:
        raise not_found("素材")
    used_in_content = db.scalar(
        select(func.count()).select_from(models.ContentAsset).where(
            models.ContentAsset.asset_id == asset_id
        )
    ) or 0
    has_derived = db.scalar(
        select(func.count()).select_from(models.Asset).where(
            models.Asset.parent_asset_id == asset_id
        )
    ) or 0
    if used_in_content or has_derived:
        raise ApiError(
            "asset_in_use", "该素材已被稿件或派生图引用，请先解除引用再删除", status_code=409
        )
    path = _asset_disk_path(asset)
    db.delete(asset)
    db.commit()
    # DB 记录删除成功后才清理磁盘；删不掉只留孤儿文件，不影响数据一致性
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


@router.post("/image-jobs", response_model=schemas.ImageJobOut, status_code=201)
def create_image_job(
    payload: schemas.ImageJobIn, user: CurrentUser, db: DbSession
) -> schemas.ImageJobOut:
    asset = db.get(models.Asset, payload.asset_id)
    if not asset or asset.owner_user_id != user.id:
        raise not_found("素材")
    if payload.operation not in image_service.OPERATIONS:
        raise ApiError("unsupported_operation", f"不支持的操作：{payload.operation}")

    job = models.ImageJob(
        owner_user_id=user.id,
        asset_id=asset.id,
        operation=payload.operation,
        options=payload.options,
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.flush()

    # 同步端点保留给小图即时处理；大图走 /image-jobs/async 投递队列
    try:
        path = _asset_disk_path(asset)
        data = path.read_bytes()
        outputs = image_service.apply_operation(data, payload.operation, payload.options)
        derived_ids: list[str] = []
        for index, (output, mime, (width, height)) in enumerate(outputs, start=1):
            key = f"{user.id}/{uuid.uuid4().hex}{_ext(mime)}"
            out_path = image_service.storage_path(settings.storage_dir, key)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(output)
            suffix = f"-{index}" if len(outputs) > 1 else ""
            derived = models.Asset(
                owner_user_id=user.id,
                workspace_id=asset.workspace_id,
                storage_key=key,
                original_name=f"{asset.original_name}-{payload.operation}{suffix}",
                mime_type=mime,
                size_bytes=len(output),
                width=width,
                height=height,
                source_type="derived",
                parent_asset_id=asset.id,
                checksum=hashlib.sha256(output).hexdigest(),
            )
            db.add(derived)
            db.flush()
            derived_ids.append(derived.id)
        job.status = "succeeded"
        job.progress = 100
        job.output_asset_ids = derived_ids
        job.completed_at = datetime.now(timezone.utc)
    except image_service.ImageValidationError as exc:
        job.status = "failed"
        job.error_code = "invalid_image"
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        raise ApiError("image_job_failed", str(exc)) from exc

    db.commit()
    return schemas.ImageJobOut(
        id=job.id,
        asset_id=job.asset_id,
        operation=job.operation,
        status=job.status,
        progress=job.progress,
        output_asset_ids=job.output_asset_ids or [],
        error_code=job.error_code,
    )


@router.post("/image-jobs/async", status_code=202)
def create_image_job_async(
    payload: schemas.ImageJobIn, user: CurrentUser, db: DbSession
) -> dict:
    """大图处理走队列：立即返回 jobId，结果用 GET /jobs/{jobId} 查询。"""
    asset = db.get(models.Asset, payload.asset_id)
    if not asset or asset.owner_user_id != user.id:
        raise not_found("素材")
    if payload.operation not in image_service.OPERATIONS:
        raise ApiError("unsupported_operation", f"不支持的操作：{payload.operation}")
    image_job = models.ImageJob(
        owner_user_id=user.id,
        asset_id=asset.id,
        operation=payload.operation,
        options=payload.options,
        status="queued",
    )
    db.add(image_job)
    db.commit()
    job_id = queue.enqueue(
        db,
        owner_user_id=user.id,
        queue="image-processing",
        kind="image-processing",
        payload={
            "assetId": asset.id,
            "imageJobId": image_job.id,
            "operation": payload.operation,
            "options": payload.options,
        },
    )
    return {
        "jobId": job_id,
        "imageJobId": image_job.id,
        "status": "queued",
        "backend": queue.backend_name(),
        "message": "已入队；请用 GET /jobs/{jobId} 查询处理进度",
    }


@router.get("/image-jobs/{job_id}", response_model=schemas.ImageJobOut)
def get_image_job(job_id: str, user: CurrentUser, db: DbSession) -> schemas.ImageJobOut:
    job = db.get(models.ImageJob, job_id)
    if not job or job.owner_user_id != user.id:
        raise not_found("图像任务")
    return schemas.ImageJobOut(
        id=job.id,
        asset_id=job.asset_id,
        operation=job.operation,
        status=job.status,
        progress=job.progress,
        output_asset_ids=job.output_asset_ids or [],
        error_code=job.error_code,
    )


def _ext(mime: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
    }.get(mime, ".bin")
