"""异步任务执行器：把 queued 任务真实推进到终态（设计 §6.6 / §11.1）。

所有失败都带明确 errorCode，外部能力缺失时绝不写入 0 或占位数据。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select, update

from .. import models
from ..connectors import registry
from ..connectors.wechat_mp import WeChatMPConnector, WeChatMPError
from ..core import queue
from ..core.config import get_settings
from ..core.db import SessionLocal
from ..core.deps import default_workspace
from ..core.security import decrypt_secret, sign_asset_url
from . import handdraw, image_provider, image_service, publish_state, wechat_article

RETRYABLE_CODES = {"provider_unreachable", "provider_error", "provider_timeout", "rate_limited"}
MAX_ATTEMPTS = queue.MAX_ATTEMPTS
logger = logging.getLogger(__name__)


class JobFailure(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ext(mime: str) -> str:
    return {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(mime, ".bin")


def _current_status(job_id: str) -> str | None:
    """用独立会话读最新状态，用于协作式取消，不干扰当前执行会话。"""
    session = SessionLocal()
    try:
        row = session.get(models.BackgroundJob, job_id)
        return row.status if row else None
    finally:
        session.close()


def _write_terminal(db, job_id: str, **values) -> bool:
    """条件写回终态：cancelled 的任务不允许被成功/失败覆盖。"""
    changed = db.execute(
        update(models.BackgroundJob)
        .where(
            models.BackgroundJob.id == job_id,
            models.BackgroundJob.status.notin_(["cancelled", "succeeded", "failed"]),
        )
        .values(**values)
    )
    db.commit()
    return changed.rowcount == 1


def mark_failed(job_id: str, code: str, message: str) -> None:
    db = SessionLocal()
    try:
        _write_terminal(
            db,
            job_id,
            status="failed",
            error_code=code,
            error_message=message[:1000],
            completed_at=_utcnow(),
        )
    finally:
        db.close()


def run_job(job_id: str) -> None:
    db = SessionLocal()
    try:
        # 原子领取（CAS）：并发消费者里只有 rowcount==1 的那个能进入执行体，
        # 避免重复发布 / 重复生图入库。
        claimed = db.execute(
            update(models.BackgroundJob)
            .where(
                models.BackgroundJob.id == job_id,
                models.BackgroundJob.status == "queued",
            )
            .values(
                status="running",
                started_at=_utcnow(),
                attempt_count=models.BackgroundJob.attempt_count + 1,
            )
        )
        db.commit()
        if claimed.rowcount != 1:
            return
        queue.mark_dispatched_done(job_id)

        job = db.get(models.BackgroundJob, job_id)
        for attempt in range(job.attempt_count, MAX_ATTEMPTS + 1):
            if _current_status(job_id) == "cancelled":
                # 链成员被取消也要接力：否则同链 queued 成员无人投递，卡到下次重启
                queue.chain_next_if_done(payload_of(db, job_id), owner_user_id=job.owner_user_id)
                return
            try:
                result = _execute(db, job)
                if _current_status(job_id) == "cancelled":
                    queue.chain_next_if_done(payload_of(db, job_id), owner_user_id=job.owner_user_id)
                    return
                if _write_terminal(
                    db,
                    job_id,
                    status="succeeded",
                    progress=100,
                    result=result,
                    completed_at=_utcnow(),
                ):
                    db.refresh(job)
                queue.chain_next_if_done(payload_of(db, job_id), owner_user_id=job.owner_user_id)
                return
            except JobFailure as exc:
                if exc.retryable and attempt < MAX_ATTEMPTS:
                    db.execute(
                        update(models.BackgroundJob)
                        .where(models.BackgroundJob.id == job_id)
                        .values(attempt_count=attempt + 1, progress=min(90, 30 * attempt))
                    )
                    db.commit()
                    time.sleep(0.4 * (2 ** (attempt - 1)))  # 指数退避
                    if _current_status(job_id) == "cancelled":
                        return
                    continue
                _write_terminal(
                    db,
                    job_id,
                    status="failed",
                    error_code=exc.code,
                    error_message=exc.message[:1000],
                    completed_at=_utcnow(),
                )
                db.refresh(job)
                _on_failure(db, job)
                queue.chain_next_if_done(payload_of(db, job_id), owner_user_id=job.owner_user_id)
                return
            except Exception as exc:  # 兜底，保证任务不会永远 queued/running
                _write_terminal(
                    db,
                    job_id,
                    status="failed",
                    error_code="internal_error",
                    error_message=f"{type(exc).__name__}: {exc}"[:1000],
                    completed_at=_utcnow(),
                )
                db.refresh(job)
                _on_failure(db, job)
                queue.chain_next_if_done(payload_of(db, job_id), owner_user_id=job.owner_user_id)
                return
    finally:
        db.close()


def payload_of(db, job_id: str) -> dict:
    row = db.get(models.BackgroundJob, job_id)
    return (row.payload or {}) if row else {}


def _on_failure(db, job: models.BackgroundJob) -> None:
    """把关联的旧式任务表（同步任务/发布任务）一并推进到终态，避免状态悬空。"""
    payload = job.payload or {}
    if job.queue == "platform-sync":
        sync_job = db.get(models.PlatformSyncJob, payload.get("syncJobId"))
        if sync_job:
            sync_job.status = "failed"
            sync_job.error_code = job.error_code
            sync_job.started_at = sync_job.started_at or job.started_at
            sync_job.completed_at = job.completed_at
            db.commit()
    if job.queue == "platform-publish":
        publish_job = db.get(models.PublishJob, payload.get("publishJobId"))
        if publish_job:
            publish_job.error_code = job.error_code
            publish_job.error_message = job.error_message
            _advance_publish_job_on_failure(db, publish_job, job.error_code or "")
            db.commit()
    if payload.get("storyboardShotId"):
        _apply_storyboard_failure(db, job)
    if payload.get("characterId"):
        _apply_character_image_failure(db, job)


def _apply_character_image_failure(db, job: models.BackgroundJob) -> None:
    payload = job.payload or {}
    row = db.get(models.Character, payload.get("characterId"))
    if not row or row.owner_user_id != job.owner_user_id:
        return
    pending = [dict(item) for item in (row.pending_images or [])]
    hit = next((item for item in pending if item.get("jobId") == job.id), None)
    if hit is None:
        return
    hit["status"] = "failed"
    hit["error"] = (job.error_message or job.error_code or "生成失败")[:500]
    row.pending_images = pending
    db.commit()


def _advance_publish_job_on_failure(db, publish_job: models.PublishJob, error_code: str) -> None:
    """后台任务彻底失败（重试耗尽）后，把发布任务推进到可解释的终态。

    此前失败只写错误字段、状态悬在 publishing/queued，用户永远看不到失败。
    映射原则（对齐 AGENT.md）：
    - 远端已产生内容 → succeeded（绝不再重发）
    - 明确的致命错误（授权失效/内容被拒）→ failed
    - 超时/网络/限流等结果未知 → needs_attention，等用户核实后回填或重试
    """
    if publish_job.status in publish_state.TERMINAL:
        return
    code = (error_code or "").lower()
    if publish_job.remote_content_id:
        target = publish_state.SUCCEEDED
        note = f"后台执行失败但远端已有内容，视为成功（{code}）"
    elif code in {"auth_expired", "forbidden", "validation_failed", "content_rejected"}:
        target = publish_state.FAILED
        note = f"执行失败：{code}"
    else:
        target = publish_state.NEEDS_ATTENTION
        note = f"执行结果未知（{code}），请核实后回填或重试"
    if target == publish_job.status:
        publish_state.record_history(publish_job, target, actor="system", note=note)
        return
    try:
        publish_state.apply_transition(publish_job, target, actor="system", note=note)
    except publish_state.TransitionError:
        # 兜底：映射结果与状态机冲突时不吞错误，直接标记需人工核实
        publish_state.apply_transition(
            publish_job, publish_state.NEEDS_ATTENTION, actor="system", note=f"异常流转兜底：{note}"
        )


def _execute(db, job: models.BackgroundJob) -> dict:
    handlers = {
        "ai-image": _handle_ai_image,
        "image-processing": _handle_image_processing,
        "platform-sync": _handle_platform_sync,
        "platform-publish": _handle_platform_publish,
        "novel": _handle_novel,
    }
    handler = handlers.get(job.queue)
    if not handler:
        raise JobFailure("unknown_queue", f"未注册的任务队列：{job.queue}")
    return handler(db, job)


def _store_asset(db, user, workspace_id: str, raw: bytes, mime: str, name: str, **extra) -> models.Asset:
    settings = get_settings()
    key = f"{user.id}/{uuid.uuid4().hex}{_ext(mime)}"
    path = image_service.storage_path(settings.storage_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    from PIL import Image
    import io

    with Image.open(io.BytesIO(raw)) as image:
        width, height = image.size
    asset = models.Asset(
        owner_user_id=user.id,
        workspace_id=workspace_id,
        storage_key=key,
        original_name=name,
        mime_type=mime,
        size_bytes=len(raw),
        width=width,
        height=height,
        checksum=hashlib.sha256(raw).hexdigest(),
        **extra,
    )
    db.add(asset)
    db.flush()
    return asset


def _fill_late_anchor_reference(db, payload: dict) -> dict:
    """入队时锚点还没出图 → 到执行这张时再补一次参考图。

    retry-failed / generate-rest 在「锚点失败」时只能按当时状态发 payload（参考图为空）。
    如果锚点是同一条链的链头，等轮到后面几张时它往往已经成功了——此时补上参考图，
    整组才能保持同一画风，而不是集体退化为纯文生图。
    只对「贴图批量、非锚点、payload 里本来就没有参考图」的行生效，且必须仍是
    sort_order==0 的成功行，避免拿错基准。返回值是新的 payload 副本（不改 job 行）。
    """
    if payload.get("referenceAssetIds") or not payload.get("stickerSetId"):
        return payload
    image_id = payload.get("stickerImageId")
    if not image_id:
        return payload
    image = db.get(models.StickerImage, image_id)
    if image is None or image.sort_order == 0:
        return payload
    anchor = db.scalar(
        select(models.StickerImage)
        .where(
            models.StickerImage.set_id == image.set_id,
            models.StickerImage.sort_order == 0,
            models.StickerImage.status == "succeeded",
            models.StickerImage.asset_id.is_not(None),
        )
    )
    if anchor is None:
        return payload
    return {**payload, "referenceAssetIds": [anchor.asset_id]}


def _load_reference_images(db, job: models.BackgroundJob, payload: dict) -> list[tuple[bytes, str, str]]:
    """读取参考图素材；文件缺失或不属于任务创建者时明确失败。"""
    references: list[tuple[bytes, str, str]] = []
    for asset_id in payload.get("referenceAssetIds") or []:
        asset = db.get(models.Asset, asset_id)
        if not asset or asset.owner_user_id != job.owner_user_id:
            raise JobFailure("asset_not_found", "参考图素材不存在")
        path = image_service.storage_path(get_settings().storage_dir, asset.storage_key)
        if not path.exists():
            raise JobFailure("asset_not_found", "参考图文件已丢失")
        references.append((path.read_bytes(), asset.mime_type, f"{asset.id}{_ext(asset.mime_type)}"))
    return references


def _builtin_reference(number: str | None, model_code: str | None, room: int) -> tuple[bytes, str, str] | None:
    """按内置手绘风格补参考图；该风格不需要参考图时返回 None。

    选了风格却读不到参考图时明确失败，不静默降级成无参考图生成。
    """
    if not number:
        return None
    session = SessionLocal()
    try:
        style = session.scalar(
            select(models.HanddrawStyle).where(
                or_(
                    models.HanddrawStyle.style_number == str(number).strip().upper(),
                    models.HanddrawStyle.style_number == handdraw.normalize_style_number(number),
                )
            )
        )
        if style is None:
            raise JobFailure("style_not_found", f"手绘风格不存在：{number}")
        activation = handdraw.resolve_activation(style, model_code)
        if not activation.need_reference:
            return None
        path = handdraw.resolve_reference_file(style)
        if path is None:
            raise JobFailure("reference_missing", f"手绘风格 {style.style_number} 的参考图缺失")
        if room <= 0:
            raise JobFailure("too_many_references", "内置参考图与所选素材合计超出 4 张上限")
        return path.read_bytes(), "image/webp", f"{style.style_number}.webp"
    except ValueError as exc:
        raise JobFailure("style_not_found", str(exc)) from exc
    finally:
        session.close()


def _handle_ai_image(db, job: models.BackgroundJob) -> dict:
    payload = job.payload or {}
    prompt = payload.get("prompt") or ""
    # 贴图创作：风格句只在执行器里拼一次，模块层不再重复拼接。非贴纸任务无此键，行为零变化。
    sticker_style_id = payload.get("stickerStyleId")
    if sticker_style_id is not None:
        from . import sticker_styles as _sticker_styles

        style = _sticker_styles.STYLES.get(sticker_style_id)
        if style is None:
            raise JobFailure("sticker_style_unknown", f"未知贴图风格：{sticker_style_id}")
        prompt = f"{prompt} {style['prompt_suffix']}".strip()
    user = db.get(models.User, job.owner_user_id)
    workspace = default_workspace(db, user)
    if payload.get("storyboardProjectId"):
        project = db.get(models.StoryboardProject, payload["storyboardProjectId"])
        if not project or project.owner_user_id != job.owner_user_id:
            raise JobFailure("storyboard_not_found", "分镜项目不存在")
        workspace = db.get(models.Workspace, project.workspace_id)
        if not workspace or workspace.owner_user_id != job.owner_user_id:
            raise JobFailure("workspace_not_found", "分镜工作区不存在")
    if payload.get("characterId"):
        # 角色是用户级的，没有所属工作区；入队时已解析好默认工作区
        if payload.get("workspaceId"):
            candidate = db.get(models.Workspace, payload["workspaceId"])
            if candidate and candidate.owner_user_id == job.owner_user_id:
                workspace = candidate
    payload = _fill_late_anchor_reference(db, payload)
    references = _load_reference_images(db, job, payload)
    provider = image_provider.resolve_image_provider(db, job.owner_user_id, payload.get("modelId"))
    model_code = provider.get("modelCode") if provider else None
    model_id = payload.get("modelId")
    # 该供应商已被证实不支持 /images/edits 时直接走文生图，不再白烧一发注定失败的请求。
    # 探测结果记在模型 catalog 的 options.supportsEdits 上，图片生成与贴图共用一份。
    # 环境变量供应商没有 catalog 行，provider_supports_edits 返回 None，每次现探。
    edits_known_unsupported = (
        bool(references)
        and image_provider.provider_supports_edits(db, job.owner_user_id, model_id) is False
    )
    if edits_known_unsupported and payload.get("stickerSetId"):
        anchor_desc = _sticker_anchor_desc(db, payload)
        if anchor_desc:
            prompt = f"{prompt}, consistent with: {anchor_desc}"
    if edits_known_unsupported:
        references = []
    builtin = _builtin_reference(
        payload.get("builtinReferenceStyleNumber"),
        model_code,
        handdraw.MAX_REFERENCES - len(references),
    )
    if builtin is not None:
        references = [builtin, *references]
    degraded_to_text = edits_known_unsupported
    try:
        images = image_provider.generate_images(
            db,
            job.owner_user_id,
            prompt=prompt,
            aspect_ratio=payload.get("aspectRatio") or "3:4",
            count=int(payload.get("count") or 1),
            model_id=model_id,
            reference_images=references or None,
        )
    except image_provider.ImageProviderUnavailable as exc:
        # 供应商不支持 /images/edits（典型表现 404）时降级为纯文生图重试，
        # 图片生成与贴图批量都适用：丢弃参考图不该让整次任务失败。
        # 贴图的一致性走 Q10-B：把锚点图反推成英文描述拼进 prompt（每集合只反推一次，
        # 结果缓存在集合行上）；反推不可用时退回仅风格句。其他错误照常上抛。
        if not (
            references
            and (
                exc.code == "edits_unsupported"
                # 兼容旧路径：网关吞掉状态码只回通用错误时仍按文案兜底识别
                or (exc.code == "provider_error" and "/images/edits" in exc.message)
            )
        ):
            raise JobFailure(exc.code, exc.message, exc.code in RETRYABLE_CODES) from exc
        # 记住这家供应商不支持 edits，后续任务直接跳过白烧的一发
        image_provider.mark_edits_unsupported(db, model_id)
        if payload.get("stickerSetId"):
            anchor_desc = _sticker_anchor_desc(db, payload)
            if anchor_desc:
                prompt = f"{prompt}, consistent with: {anchor_desc}"
        degraded_to_text = True
        images = image_provider.generate_images(
            db,
            job.owner_user_id,
            prompt=prompt,
            aspect_ratio=payload.get("aspectRatio") or "3:4",
            count=int(payload.get("count") or 1),
            model_id=model_id,
            reference_images=None,
        )
    asset_ids: list[str] = []
    for index, (raw, mime) in enumerate(images, start=1):
        asset = _store_asset(
            db,
            user,
            workspace.id,
            raw,
            mime,
            f"ai-{index}-{uuid.uuid4().hex[:6]}",
            source_type="generated",
            prompt=prompt,
            license_note="AI 生成，发布前请确认平台内容规范与商用授权",
        )
        asset_ids.append(asset.id)
    if not asset_ids:
        # 供应商路径正常时会抛错而不是返回空列表；这里是最后一道闸：
        # 没有 asset 就绝不能把图行标成 succeeded（会出现「成功但没图」的格子）。
        raise JobFailure("empty_provider_response", "图像供应商未返回任何图片")
    db.commit()
    # 贴图创作：把首张 asset 幂等回写到对应图行，并按集合终态推进集合状态。
    # 按 payload.stickerImageId 定位而不是 job_id——enqueue_chain 在任务开始执行前
    # 不会把 job_id 写回图行，用 job_id 匹配会永远找不到行（真实批量路径踩过的坑）。
    if payload.get("stickerImageId"):
        _apply_sticker_success(db, job, asset_ids, payload["stickerImageId"])
    if payload.get("storyboardShotId"):
        _apply_storyboard_success(db, job, asset_ids, payload)
    if payload.get("characterId"):
        _apply_character_image_success(db, job, asset_ids, payload, degraded_to_text, prompt)
    return {
        "assetIds": asset_ids,
        "count": len(asset_ids),
        # 前端要把结果直接写进正文，这里顺带给出可匿名访问的签名直链
        "assetUrls": [_signed_asset_url(aid) for aid in asset_ids],
        # 参考图被丢弃时如实告知：画风只剩文字描述，不是静默降级
        "degradedToText": degraded_to_text,
        "notice": (
            "该供应商不支持参考图编辑，已按纯文生图生成，画风仅由文字描述决定"
            if degraded_to_text
            else ""
        ),
    }


def _apply_storyboard_success(db, job, asset_ids: list[str], payload: dict) -> None:
    shot = db.get(models.StoryboardShot, payload.get("storyboardShotId"))
    stage = payload.get("generationStage")
    if not shot or shot.deleted_at is not None or stage not in ("sketch", "final"):
        return
    scene = db.get(models.StoryboardScene, shot.scene_id)
    project = db.get(models.StoryboardProject, scene.project_id) if scene else None
    if not scene or scene.deleted_at is not None or not project or project.deleted_at is not None:
        return
    if shot.generation_version != payload.get("generationVersion"):
        return
    if getattr(shot, f"{stage}_job_id") != job.id:
        return
    if stage == "sketch":
        shot.sketch_asset_id = asset_ids[0] if asset_ids else None
        shot.sketch_status = "succeeded"
        shot.sketch_error = None
    else:
        shot.final_asset_ids = asset_ids
        shot.final_status = "succeeded"
        shot.final_error = None
    db.commit()


def _apply_character_image_success(
    db, job: models.BackgroundJob, asset_ids: list[str], payload: dict,
    degraded_to_text: bool, prompt: str,
) -> None:
    """角色参考图出图后回写候选位：只认 jobId 匹配的占位，任务被替换则静默跳过。"""
    row = db.get(models.Character, payload.get("characterId"))
    if not row or row.owner_user_id != job.owner_user_id or row.deleted_at is not None:
        return
    pending = [dict(item) for item in (row.pending_images or [])]
    hit = next((item for item in pending if item.get("jobId") == job.id), None)
    if hit is None:
        return
    hit["status"] = "succeeded"
    hit["assetId"] = asset_ids[0] if asset_ids else None
    hit["degraded"] = bool(degraded_to_text)
    hit["prompt"] = prompt[:4000]
    row.pending_images = pending
    db.commit()


def _apply_storyboard_failure(db, job: models.BackgroundJob) -> None:
    payload = job.payload or {}
    shot = db.get(models.StoryboardShot, payload.get("storyboardShotId"))
    if not shot or shot.deleted_at is not None or shot.generation_version != payload.get("generationVersion"):
        return
    scene = db.get(models.StoryboardScene, shot.scene_id)
    project = db.get(models.StoryboardProject, scene.project_id) if scene else None
    if not scene or scene.deleted_at is not None or not project or project.deleted_at is not None:
        return
    stage = payload.get("generationStage")
    if stage in ("sketch", "final") and getattr(shot, f"{stage}_job_id") == job.id:
        setattr(shot, f"{stage}_status", "failed")
        setattr(shot, f"{stage}_error", job.error_message or job.error_code or "生成失败")
        db.commit()


def _merge_set_meta(db, set_id: str, **updates) -> dict:
    """原子合并贴图集 meta_json 的若干键。

    直接 `row.meta_json = {**old, ...}` 是整列覆盖：另一个会话若持着旧快照
    （如先取集合、再写 anchor_desc），会把对方刚写的键抹掉。这里在写之前
    强制从库里重读一次（populate_existing 覆盖 identity map 里的旧对象），
    把丢更新窗口压到同一事务内。
    """
    fresh = db.execute(
        select(models.StickerSet)
        .where(models.StickerSet.id == set_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if fresh is None:
        return {}
    merged = {**(fresh.meta_json or {}), **updates}
    fresh.meta_json = merged
    db.commit()
    return merged


def _merge_set_meta_remove(db, set_id: str, *keys: str) -> None:
    """从集合 meta_json 删掉若干键（同样先重读再合并，避免覆盖并发写入）。"""
    fresh = db.execute(
        select(models.StickerSet)
        .where(models.StickerSet.id == set_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if fresh is None:
        return
    meta = dict(fresh.meta_json or {})
    if not any(key in meta for key in keys):
        return
    for key in keys:
        meta.pop(key, None)
    fresh.meta_json = meta


def _sticker_anchor_desc(db, payload: dict) -> str:
    """锚点图的「画风签名」（Q10-B 一致性兜底），缓存在集合 meta_json.anchor_desc。

    用 STYLE_SIGNATURE_SYSTEM 只提取可迁移画法（技法/色板/光线/质感/构图），
    刻意排除主体——主体由各张画面描述负责，避免锚点内容污染整组。
    每个集合只调一次视觉模型；失败记哨兵值不再重试，降级为仅风格句。

    锚点必须是 sort_order==0 的那张（图序第一张），不能「取第一个有 asset 的行」——
    done 集合重排、或锚点尚未成功而中间张先出图时，那样会拿中间张的画风去锚定全组。
    """
    sticker_set = db.get(models.StickerSet, payload.get("stickerSetId") or "")
    if sticker_set is None:
        return ""
    meta = sticker_set.meta_json or {}
    if "anchor_desc" in meta:
        return str(meta["anchor_desc"])
    anchor = db.scalar(
        select(models.StickerImage)
        .where(
            models.StickerImage.set_id == sticker_set.id,
            models.StickerImage.sort_order == 0,
            models.StickerImage.asset_id.is_not(None),
        )
    )
    if anchor is None:
        # 正常情况下锚点未就绪时批量生成不会开跑；拿不到就退回仅风格句，
        # 宁可少一层一致性，也不要拿错误基准污染整组。
        return ""
    asset = db.get(models.Asset, anchor.asset_id)
    desc = ""
    if asset is not None:
        try:
            path = image_service.storage_path(get_settings().storage_dir, asset.storage_key)
            if path.is_file():
                desc = image_provider.interrogate_image(
                    db,
                    sticker_set.owner_user_id,
                    image_bytes=path.read_bytes(),
                    mime_type=asset.mime_type,
                    system_prompt=image_provider.STYLE_SIGNATURE_SYSTEM,
                    user_text="Extract this image's style signature.",
                ).strip()
        except Exception as exc:  # noqa: BLE001 —— 反推失败不阻塞生成，降级为仅风格句
            from ..core.logging import get_logger

            get_logger("job_runner.sticker").warning("锚点画风提取失败，降级为仅风格句：%s", exc)
            desc = ""
    desc = _style_signature_guard(desc)[:400] if desc else ""
    _merge_set_meta(db, sticker_set.id, anchor_desc=desc)
    return desc


# VLM 偶尔无视指令把锚点主体带进画风签名（典型：a man walking / the Eiffel Tower）。
# 这些词会污染其余各张的画面描述：先按逗号切段，整段含主体词的丢弃（风格词与主体词
# 很少同段）；只剩冠词等碎片的段也丢掉。剩余太短说明签名基本被主体占满，整体弃用。
_SUBJECT_TOKEN_RE = re.compile(
    r"\b(?:man|men|woman|women|person|people|person's|child|children|boy|girl|crowd|"
    r"building|tower|bridge|street|cityscape|skyline|room|interior|table|chair|car|"
    r"vehicle|cat|dog|bird|horse|tree|forest|mountain|beach|ocean|sea|lake|food|dish|"
    r"meal|cup|mug|bottle|book|phone|laptop|screen|logo|sign|poster|banner|"
    r"eiffel|statue|monument|temple|church|mosque|castle)\b",
    re.IGNORECASE,
)
_STOPWORD_ONLY_RE = re.compile(r"^(?:the|a|an|this|that|with|and|of|in|on|to|its|their)?[\s.]*$", re.IGNORECASE)


def _style_signature_guard(text: str) -> str:
    kept = []
    for segment in re.split(r"[,;\n]", text):
        seg = segment.strip(" .")
        if not seg or _STOPWORD_ONLY_RE.match(seg):
            continue
        if _SUBJECT_TOKEN_RE.search(seg):
            continue
        kept.append(seg)
    cleaned = ", ".join(kept)
    return cleaned if len(cleaned) >= 20 else ""


def _apply_sticker_success(
    db, job: models.BackgroundJob, asset_ids: list[str], image_id: str | None = None
) -> None:
    """生图成功后回写贴图集：图行 → succeeded + asset；全成功则集合 → review。

    定位优先用 payload 里的 stickerImageId（enqueue_chain 路径不回写 job_id），
    兜底按 job_id 匹配（单发 enqueue 路径）；两者都查不到即任务已被替换，静默跳过。
    有 failed 行时集合保持 generating，让单张 retry 按钮可用。
    """
    image = None
    if image_id:
        image = db.get(models.StickerImage, image_id)
        # 该行已被新一轮重试接管（job_id 指向别的任务）时不再回写
        if image is not None and image.job_id not in (None, job.id):
            image = None
    if image is None:
        image = db.scalar(
            select(models.StickerImage).where(models.StickerImage.job_id == job.id)
        )
    if image is None:
        return
    new_asset_id = asset_ids[0] if asset_ids else image.asset_id
    db.execute(
        update(models.StickerImage)
        .where(models.StickerImage.id == image.id)
        .values(
            asset_id=new_asset_id,
            status="succeeded",
            error_code=None,
        )
    )
    # done 集合的单张重抽：finalize 不会再跑，新 asset 必须当场补盖 sticker 标记，
    # 否则素材库会把它归到「AI 生成」破坏归档不变量。
    sticker_set = db.get(models.StickerSet, image.set_id)
    if sticker_set is not None and sticker_set.status == "done" and asset_ids:
        asset = db.get(models.Asset, new_asset_id)
        if asset is not None and asset.owner_user_id == job.owner_user_id:
            asset.source_type = "sticker"
    # 锚点换图即作废画风签名缓存：旧签名描述的是上一张锚点，继续用它对齐会让
    # 这一轮其余几张往已经不在的基准上靠。下一张需要时按新锚点重新提取。
    # 用 _merge_set_meta_remove 重读后合并，避免拿本会话的旧快照把并发写入的键抹掉。
    if image.sort_order == 0:
        _merge_set_meta_remove(db, image.set_id, "anchor_desc")
    siblings = db.scalars(
        select(models.StickerImage).where(models.StickerImage.set_id == image.set_id)
    ).all()
    if siblings and all(row.status == "succeeded" for row in siblings):
        db.execute(
            update(models.StickerSet)
            .where(
                models.StickerSet.id == image.set_id,
                models.StickerSet.status != "done",
            )
            .values(status="review")
        )
    db.commit()


def _signed_asset_url(asset_id: str) -> str:
    """素材的签名直链（相对路径）。<img src> 带不了 Authorization，只能靠签名。"""
    return f"{get_settings().api_prefix}/assets/{asset_id}/raw?{sign_asset_url(asset_id)}"


def _handle_image_processing(db, job: models.BackgroundJob) -> dict:
    payload = job.payload or {}
    source = db.get(models.Asset, payload.get("assetId"))
    if not source or source.owner_user_id != job.owner_user_id:
        raise JobFailure("asset_not_found", "指定的素材不存在")
    settings = get_settings()
    path = image_service.storage_path(settings.storage_dir, source.storage_key)
    if not path.is_file():
        raise JobFailure("asset_file_missing", "素材原文件不存在")
    operation = payload.get("operation")
    if operation not in image_service.OPERATIONS:
        raise JobFailure("unsupported_operation", f"不支持的操作：{operation}")
    try:
        outputs = image_service.apply_operation(
            path.read_bytes(), operation, payload.get("options") or {}
        )
    except image_service.ImageValidationError as exc:
        raise JobFailure("invalid_image", str(exc)) from exc
    user = db.get(models.User, job.owner_user_id)
    asset_ids: list[str] = []
    for index, (output, mime, _size) in enumerate(outputs, start=1):
        suffix = f"-{index}" if len(outputs) > 1 else ""
        asset = _store_asset(
            db,
            user,
            source.workspace_id,
            output,
            mime,
            f"{source.original_name}-{operation}{suffix}",
            source_type="derived",
            parent_asset_id=source.id,
        )
        asset_ids.append(asset.id)
    image_job = db.get(models.ImageJob, payload.get("imageJobId"))
    if image_job:
        image_job.status = "succeeded"
        image_job.progress = 100
        image_job.output_asset_ids = asset_ids
        image_job.completed_at = _utcnow()
    db.commit()
    return {
        "assetIds": asset_ids,
        "operation": operation,
        "panelCount": len(asset_ids),
        "assetUrls": [_signed_asset_url(aid) for aid in asset_ids],
    }


def _handle_platform_sync(db, job: models.BackgroundJob) -> dict:
    payload = job.payload or {}
    account = db.get(models.ConnectedAccount, payload.get("accountId"))
    if not account:
        raise JobFailure("account_not_found", "平台账号不存在")
    sync_job = db.get(models.PlatformSyncJob, payload.get("syncJobId"))
    if sync_job:
        sync_job.status = "running"
        sync_job.started_at = _utcnow()
        db.commit()

    connector = registry.get_connector(account.platform, db)
    access_token = decrypt_secret(account.access_token_ciphertext)
    if not getattr(connector, "configured", False):
        raise JobFailure("connector_unavailable", "该平台尚未配置官方应用凭据，无法采集数据")
    if not access_token:
        raise JobFailure("authorization_required", "账号未完成官方 OAuth 授权，无法采集数据")

    try:
        if (payload.get("syncType") or "account") == "account":
            records = connector.sync_account_metrics(
                access_token=access_token, cursor=(sync_job.cursor if sync_job else None)
            )
        else:
            records = connector.sync_content_metrics(
                access_token=access_token, cursor=(sync_job.cursor if sync_job else None)
            )
    except registry.ConnectorUnavailable as exc:
        raise JobFailure("connector_unavailable", str(exc)) from exc
    if not isinstance(records, list):
        raise JobFailure("invalid_platform_response", "平台返回的数据格式不符合适配器契约")

    written = 0
    for record in records:
        metrics = record.get("metrics")
        if not isinstance(metrics, dict) or not metrics:
            continue  # 缺失指标不填 0
        db.add(
            models.MetricSnapshot(
                owner_user_id=account.owner_user_id,
                connected_account_id=account.id,
                platform=account.platform,
                remote_content_id=record.get("remoteContentId"),
                normalized_metrics=metrics,
                raw_metrics=record.get("raw") or {},
                source="api",
            )
        )
        written += 1
    account.last_synced_at = _utcnow()
    if sync_job:
        sync_job.status = "succeeded"
        sync_job.completed_at = _utcnow()
        sync_job.cursor = payload.get("nextCursor") or sync_job.cursor
    db.commit()
    return {"records": written, "syncedAt": account.last_synced_at.isoformat()}


def _handle_platform_publish(db, job: models.BackgroundJob) -> dict:
    payload = job.payload or {}
    publish_job = db.get(models.PublishJob, payload.get("publishJobId"))
    if not publish_job:
        raise JobFailure("publish_job_not_found", "发布任务不存在")
    account = db.get(models.ConnectedAccount, publish_job.connected_account_id)
    if not account:
        raise JobFailure("account_not_found", "平台账号不存在")
    if (publish_job.target or "publish") == "draft" or payload.get("target") == "draft":
        return _handle_wechat_draft(db, job, publish_job, account)
    connector = registry.get_connector(account.platform, db)
    access_token = decrypt_secret(account.access_token_ciphertext)
    try:
        publish_state.apply_transition(
            publish_job, publish_state.PUBLISHING, actor="system", note="开始提交平台发布"
        )
        db.commit()
        result = connector.publish(
            access_token=access_token, payload=publish_job.payload_snapshot or {}
        )
    except registry.ConnectorUnavailable as exc:
        raise JobFailure("connector_unavailable", str(exc)) from exc
    remote_id = (result or {}).get("remoteContentId")
    if not remote_id:
        raise JobFailure("invalid_platform_response", "平台未返回内容 ID，需人工核实是否已发布")
    publish_job.remote_content_id = remote_id
    publish_state.apply_transition(
        publish_job, publish_state.SUCCEEDED, actor="system", note="平台发布成功"
    )
    db.commit()
    return {"remoteContentId": remote_id}


def _novel_complete(
    db,
    user_id: str,
    prompt: str,
    *,
    model_id: str | None = None,
    apply_default: bool = True,
    explicit: bool = False,
    meta: dict | None = None,
) -> str:
    """同步拼一次模型文本。测试 monkeypatch 这个函数，避免打网。

    - 不传 model_id：默认模型排候选链首位，失败逐个降级；
    - 显式指定 / 入队快照：钉死该模型，失败明确报错并带供应商原因，绝不偷偷换模型；
    - apply_default=False：队列任务严格按入队快照执行，不受之后修改默认模型影响；
    - meta（可变 dict）：回填实际使用的模型信息，供 job.result 如实回报。
    """
    from . import ai_gateway, novel_rewrite

    try:
        selection = novel_rewrite.resolve_novel_selection(
            db,
            user_id,
            model_id=model_id,
            apply_default=apply_default,
            explicit=explicit,
        )
    except ai_gateway.InvalidModelSelection as exc:
        raise JobFailure(exc.code, exc.message, retryable=False) from exc

    candidates = [
        (connection, model_code, entry_id)
        for (connection, model_code), entry_id in zip(
            selection["candidates"], selection["entryIds"]
        )
        if connection is not None and model_code and model_code != "unconfigured"
    ]
    if not candidates:
        raise JobFailure(
            "provider_unavailable",
            "尚未配置可用的文本模型连接；请在「设置 → 模型连接」登记并测试通过后重试",
            retryable=False,
        )

    async def _collect() -> str:
        failures: list[str] = []
        for index, (connection, model_code, entry_id) in enumerate(candidates):
            parts: list[str] = []
            error: dict | None = None
            async for event in ai_gateway.stream_text(
                db,
                connection=connection,
                model_code=model_code,
                task_type="novel",
                prompt=prompt,
                system_prompt=novel_rewrite.NOVEL_SYSTEM_RULES,
            ):
                name = event.get("event")
                data = event.get("data") or {}
                if name == "generation.delta":
                    parts.append(data.get("text") or "")
                elif name == "generation.error":
                    error = data
            # 出错时整个候选作废（即使已吐了几个字），换下一个重新生成，
            # 避免把两个模型的半截正文拼成一章。
            text = "".join(parts)
            if error is None and text.strip():
                if meta is not None:
                    meta.update(
                        {
                            "modelCode": model_code,
                            "modelId": entry_id,
                            "provider": connection.provider_type,
                            "fallbackUsed": index > 0,
                            "defaultModelIgnored": selection["defaultModelIgnored"],
                            "pinned": selection["pinned"],
                        }
                    )
                return text
            reason = (error or {}).get("message") or "模型没有返回正文"
            failures.append(f"{model_code}：{reason}")
            logger.warning(
                "[novel] 模型 %s 调用失败，尝试下一个候选：%s", model_code, reason[:200]
            )
            if selection["pinned"]:
                # 显式选中 / 快照钉死：不降级，让错误原样冒出去
                break
        raise JobFailure(
            "provider_unavailable",
            "所有文本模型均不可用 → " + "；".join(failures)[:500],
            retryable=False,
        )

    return asyncio.run(_collect())


def _handle_novel(db, job: models.BackgroundJob) -> dict:
    kind = job.kind
    if kind == "novel.extract":
        return _handle_novel_extract(db, job)
    if kind == "novel.rewrite":
        return _handle_novel_rewrite(db, job)
    if kind == "novel.continue":
        return _handle_novel_continue(db, job)
    raise JobFailure("unknown_kind", f"未注册的小说任务：{kind}")


def _handle_novel_extract(db, job: models.BackgroundJob) -> dict:
    from . import novel_rewrite

    payload = job.payload or {}
    project = db.get(models.NovelProject, payload.get("novelProjectId"))
    if not project or project.owner_user_id != job.owner_user_id:
        raise JobFailure("not_found", "小说项目不存在", retryable=False)
    meta: dict = {}
    raw = _novel_complete(
        db,
        job.owner_user_id,
        novel_rewrite.build_extract_prompt(project.source_text),
        model_id=payload.get("modelId"),
        explicit=bool(payload.get("modelExplicit")),
        apply_default=False,
        meta=meta,
    )
    characters, aliases = novel_rewrite.parse_extract(raw)
    novel_rewrite.apply_extract(db, project, characters, aliases)
    db.commit()
    return {"characters": len(characters), "aliases": len(aliases), **meta}


def _handle_novel_rewrite(db, job: models.BackgroundJob) -> dict:
    from . import novel_rewrite

    payload = job.payload or {}
    chapter = db.get(models.NovelChapter, payload.get("novelChapterId"))
    if not chapter:
        raise JobFailure("not_found", "章节不存在", retryable=False)
    project = db.get(models.NovelProject, chapter.project_id)
    if not project or project.owner_user_id != job.owner_user_id:
        raise JobFailure("not_found", "小说项目不存在", retryable=False)
    aliases = db.scalars(
        select(models.NovelAlias).where(models.NovelAlias.project_id == project.id)
    ).all()
    characters = db.scalars(
        select(models.NovelCharacter).where(models.NovelCharacter.project_id == project.id)
    ).all()
    prompt = novel_rewrite.build_rewrite_prompt(chapter, aliases, characters, project.rules or {})
    meta: dict = {}
    text = _novel_complete(
        db,
        job.owner_user_id,
        prompt,
        model_id=payload.get("modelId"),
        explicit=bool(payload.get("modelExplicit")),
        apply_default=False,
        meta=meta,
    )
    if not str(text or "").strip():
        raise JobFailure("empty_model_output", "模型没有返回正文", retryable=False)
    outcome = novel_rewrite.apply_rewrite_result(
        db, chapter, text, expected_version=payload["expectedVersion"]
    )
    db.commit()
    return {"outcome": outcome, **meta}


def _handle_novel_continue(db, job: models.BackgroundJob) -> dict:
    from . import novel_continue, novel_rewrite

    payload = job.payload or {}
    chapter = db.get(models.NovelChapter, payload.get("novelChapterId"))
    if not chapter:
        raise JobFailure("not_found", "章节不存在", retryable=False)
    project = db.get(models.NovelProject, chapter.project_id)
    if not project or project.owner_user_id != job.owner_user_id:
        raise JobFailure("not_found", "小说项目不存在", retryable=False)
    accepted = db.scalars(
        select(models.NovelChapter)
        .where(
            models.NovelChapter.project_id == project.id,
            models.NovelChapter.status == "accepted",
            models.NovelChapter.id != chapter.id,
        )
        .order_by(models.NovelChapter.sort_order)
    ).all()
    previous = accepted[-1] if accepted else None
    aliases = db.scalars(
        select(models.NovelAlias).where(models.NovelAlias.project_id == project.id)
    ).all()
    characters = db.scalars(
        select(models.NovelCharacter).where(models.NovelCharacter.project_id == project.id)
    ).all()
    prompt = novel_continue.build_continue_prompt(
        accepted,
        previous.accepted_text if previous else "",
        characters,
        aliases,
        project.rules or {},
    )
    meta: dict = {}
    text = _novel_complete(
        db,
        job.owner_user_id,
        prompt,
        model_id=payload.get("modelId"),
        explicit=bool(payload.get("modelExplicit")),
        apply_default=False,
        meta=meta,
    )
    if not str(text or "").strip():
        raise JobFailure("empty_model_output", "模型没有返回正文", retryable=False)
    outcome = novel_rewrite.apply_rewrite_result(
        db, chapter, text, expected_version=payload["expectedVersion"]
    )
    scanned = chapter.draft_text if outcome == "applied" else ""
    warnings = novel_continue.scan_warnings(scanned, characters, aliases)
    db.commit()
    return {"outcome": outcome, "warnings": warnings, **meta}


def _read_asset_bytes(asset: models.Asset) -> bytes:
    path = image_service.storage_path(get_settings().storage_dir, asset.storage_key)
    if not path.is_file():
        raise JobFailure("asset_file_missing", f"素材文件缺失：{asset.original_name}")
    return path.read_bytes()


def _handle_wechat_draft(
    db,
    job: models.BackgroundJob,
    publish_job: models.PublishJob,
    account: models.ConnectedAccount,
) -> dict:
    """把稿件推到公众号草稿箱：封面永久素材 → 正文图片 → draft/add。

    中间任何一步失败都带微信真实 errcode 说明，绝不写入假 media_id。
    """
    # 多账号：按任务所属账号取连接器（账号自有凭据优先，部署级默认凭据兜底）
    connector = registry.get_account_connector(account, db)
    if not isinstance(connector, WeChatMPConnector):
        raise JobFailure(
            "connector_unavailable",
            "该账号未绑定凭据，且未配置默认 AppID/AppSecret：请在账号上录入凭据或设置默认凭据",
        )
    if account.auth_source != "app_secret" or account.status != "connected":
        raise JobFailure(
            "authorization_required", "公众号账号未完成凭据绑定与验证，请重新「绑定并验证」"
        )

    snapshot = publish_job.payload_snapshot or {}
    assets: list[models.Asset] = []
    for asset_id in snapshot.get("assetIds") or []:
        asset = db.get(models.Asset, asset_id)
        if not asset or asset.owner_user_id != job.owner_user_id:
            raise JobFailure("asset_not_found", f"素材 {asset_id} 不存在或无权访问")
        assets.append(asset)
    if not assets:
        raise JobFailure("thumb_required", "微信草稿必须有封面图（第一张素材作为 thumb_media_id）")

    publish_state.apply_transition(
        publish_job, publish_state.PUBLISHING, actor="system", note="开始推送公众号草稿箱"
    )
    db.commit()

    try:
        thumb_data, thumb_mime, thumb_name = wechat_article.prepare_thumb(
            _read_asset_bytes(assets[0])
        )
        thumb = connector.upload_thumb(
            db, data=thumb_data, filename=thumb_name, mime=thumb_mime
        )
        inline_urls: list[str] = []
        for asset in assets[1:]:
            data, mime, name = wechat_article.prepare_inline(_read_asset_bytes(asset))
            inline_urls.append(
                connector.upload_inline_image(db, data=data, filename=name, mime=mime)
            )
        html = wechat_article.render_html(
            snapshot.get("body") or "",
            inline_urls,
            snapshot.get("layoutTokens") or None,
        )
        article = wechat_article.build_article(
            title=snapshot.get("title") or "",
            html=html,
            thumb_media_id=thumb["mediaId"],
            digest=snapshot.get("digest") or "",
            author=snapshot.get("author") or "",
            content_source_url=snapshot.get("contentSourceUrl") or "",
            need_open_comment=1 if snapshot.get("needOpenComment") else 0,
            only_fans_can_comment=1 if snapshot.get("onlyFansCanComment") else 0,
        )
        media_id = connector.add_draft(db, article=article)
    except WeChatMPError as exc:
        raise JobFailure(exc.code, exc.message, exc.retryable) from exc
    except wechat_article.ArticleError as exc:
        raise JobFailure("invalid_image", str(exc)) from exc

    pushed_at = _utcnow()
    publish_job.remote_content_id = media_id
    publish_state.apply_transition(
        publish_job, publish_state.SUCCEEDED, actor="system", note="草稿推送成功"
    )
    publish_job.payload_snapshot = {
        **snapshot,
        "draftMediaId": media_id,
        "thumbMediaId": thumb["mediaId"],
        "inlineImages": len(inline_urls),
        "pushedAt": pushed_at.isoformat(),
    }
    db.add(
        models.PublishRecord(
            publish_job_id=publish_job.id,
            platform=publish_job.platform,
            remote_content_id=media_id,
            published_at=None,
            permalink=None,
            result_status="draft_created",
        )
    )
    db.commit()
    return {
        "draftMediaId": media_id,
        "thumbMediaId": thumb["mediaId"],
        "inlineImages": len(inline_urls),
        "target": "draft",
    }

