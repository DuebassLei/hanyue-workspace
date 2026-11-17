"""贴图创作接口。

    GET    /sticker-styles                              风格库 / 赛道默认 / 叙事线
    POST   /stickers                                    新建集合（draft）
    GET    /stickers                                    列表（可按 status / clusterId 过滤）
    GET    /stickers/{id}                               详情（含逐张图 + 签名直链）
    PATCH  /stickers/{id}                               改标题/主题/风格/叙事
    DELETE /stickers/{id}                               软删
    PUT    /stickers/{id}/outline                       整体替换图序
    PATCH  /stickers/{id}/images/{img_id}               单张改画面描述/配文
    POST   /stickers/{id}/generate-anchor               生成锚点（第 1 张）
    POST   /stickers/{id}/generate-rest                 以锚点为参考生成其余
    POST   /stickers/{id}/images/{img_id}/retry         单张重试
    POST   /stickers/{id}/finalize                      全部成功 → done
    GET    /stickers/{id}/archive.zip                   打包下载

越权一律 not_found。job 失败不靠全局 hook，读取时对 queued/generating 的行按
job_id 惰性回写 BackgroundJob 的真实状态（见 _sync_images_from_jobs）。
"""

from __future__ import annotations

import io
import zipfile

from fastapi import APIRouter, Query, Response
from sqlalchemy import delete, select

from .. import models, schemas
from ..core import pagination, queue
from ..core.config import get_settings
from ..core.deps import ApiError, CurrentUser, DbSession, default_workspace, not_found
from ..core.security import sign_asset_url
from ..services import sticker_styles
from ..services.image_service import storage_path

router = APIRouter(tags=["stickers"])

# 可编辑图序的状态；generating/deleted 不允许改 outline。
# done 放行是给「再补一张」闭环用的——put_outline 内部会拒绝仍有在跑任务的归档集合。
_EDITABLE_OUTLINE_STATUS = {"draft", "outlined", "review", "done"}
# 单张改画面描述的状态：generating 期间图序与 job 已绑定，改描述会与在跑任务错位。
_EDITABLE_IMAGE_STATUS = {"draft", "outlined", "review", "done"}
# 可原地重画的图行状态：queued/generating 不许再入队，其余都能换一发。
_REGENERATABLE_IMAGE_STATUS = {"pending", "failed", "succeeded"}
# 已终结的图行状态（不再需要向 job 表对账）
_IMAGE_TERMINAL = {"succeeded", "failed"}


def _signed_url(asset_id: str) -> str:
    return f"{get_settings().api_prefix}/assets/{asset_id}/raw?{sign_asset_url(asset_id)}"


def _load_set(db, user, set_id) -> models.StickerSet:
    """取集合；不存在、非本人、或已软删都视为 not_found。"""
    row = db.get(models.StickerSet, set_id)
    if not row or row.owner_user_id != user.id or row.status == "deleted":
        raise not_found("贴图集")
    return row


def _images(db, set_id) -> list[models.StickerImage]:
    return list(
        db.scalars(
            select(models.StickerImage)
            .where(models.StickerImage.set_id == set_id)
            .order_by(models.StickerImage.sort_order)
        ).all()
    )


def _sync_images_from_jobs(db, images) -> bool:
    """惰性同步：把 queued/generating 的图行按其 job 的真实终态回写。

    成功但 asset_id 缺失（执行器写入与读请求竞态）时补写首张 asset；
    job 失败则置 failed 并透传 error_code。返回是否发生了改动。
    """
    pending = [img for img in images if img.status not in _IMAGE_TERMINAL and img.job_id]
    if not pending:
        return False
    changed = False
    jobs = {
        job.id: job
        for job in db.scalars(
            select(models.BackgroundJob).where(
                models.BackgroundJob.id.in_([img.job_id for img in pending])
            )
        ).all()
    }
    for img in pending:
        job = jobs.get(img.job_id)
        if job is None:
            continue
        if job.status == "failed":
            img.status = "failed"
            img.error_code = job.error_code or "job_failed"
            changed = True
        elif job.status == "succeeded":
            asset_ids = (job.result or {}).get("assetIds") or []
            if not img.asset_id and asset_ids:
                img.asset_id = asset_ids[0]
            img.status = "succeeded"
            img.error_code = None
            changed = True
    if changed:
        db.commit()
    return changed


def _reject_if_chain_active(db, user, set_id: str) -> None:
    """同一集合只允许一条在跑的串行链，否则会有两个 running 成员并发打供应商。

    除「有成员在跑」外，链上只要还留着排队成员也一并拒绝：这时再发一条新链，
    新旧两批会交替被接力投递，等于把串行链变成两条交错链。若发现链已停摆
    （有排队却没人跑，接力断掉或重启后没补投），就地补投链头再拒绝——
    否则用户会被永久 409 卡死，只能等下次重启。
    """
    group_key = f"sticker:{set_id}"
    if queue.chain_busy(db, owner_user_id=user.id, group_key=group_key):
        raise ApiError(
            "chain_active", "该贴图集还有未完成的任务，请等它们落定后再试", status_code=409
        )


def _set_out(
    db, row: models.StickerSet, images, *, with_images: bool = False
) -> schemas.StickerSetOut:
    succeeded = sum(1 for img in images if img.status == "succeeded")
    failed = sum(1 for img in images if img.status == "failed")
    cover = next((img.asset_id for img in images if img.asset_id), None)
    out = schemas.StickerSetOut(
        id=row.id,
        title=row.title,
        theme=row.theme,
        style_id=row.style_id,
        track=row.track,
        hot_cluster_id=row.hot_cluster_id,
        narrative=row.narrative,
        status=row.status,
        total=len(images),
        succeeded=succeeded,
        failed=failed,
        cover_url=_signed_url(cover) if cover else None,
        anchor_desc=str((row.meta_json or {}).get("anchor_desc") or ""),
        image_model_id=_stored_image_model_id(row),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
    if with_images:
        out.images = [_image_out(img) for img in images]
    return out


def _image_out(img: models.StickerImage) -> schemas.StickerImageOut:
    return schemas.StickerImageOut(
        id=img.id,
        sort_order=img.sort_order,
        caption=img.caption,
        frame_prompt=img.frame_prompt,
        prompt_applied=_prompt_applied(img),
        slot_type=img.slot_type,
        status=img.status,
        asset_id=img.asset_id,
        url=_signed_url(img.asset_id) if img.asset_id else None,
        job_id=img.job_id,
        error_code=img.error_code,
    )


def _reference_asset_ids(images, target, is_anchor: bool) -> list[str]:
    """重画时的参考图：锚点自己不带；其余张优先带已成功的锚点。"""
    if is_anchor:
        return []
    anchor = images[0] if images else None
    if anchor is not None and anchor.status == "succeeded" and anchor.asset_id:
        return [anchor.asset_id]
    return []


def _prompt_applied(img: models.StickerImage) -> bool:
    """是否没有「改了描述但还没重画」。

    generated_prompt 为空 = 没有入队记录（老数据/测试直写 asset），
    不能凭空值判定成未应用；有记录且与当前描述不同才算。
    """
    return not img.generated_prompt or img.frame_prompt == img.generated_prompt


def _requeue_image(db, user, row, images, target, *, clear_asset: bool) -> None:
    """把单张图重新入队（调用方负责状态/空闲校验）。

    clear_asset 用于「描述变了」：旧素材与新描述不对应，必须摘掉，
    否则 generate-rest 会把旧 asset 当复用结果秒过，用户等不到新图。
    锚点换图还会作废 anchor_desc 缓存——由执行器成功回写时统一处理。
    """
    _reject_if_chain_active(db, user, row.id)
    is_anchor = target.sort_order == 0
    references = _reference_asset_ids(images, target, is_anchor)
    if clear_asset:
        target.asset_id = None
    job_id = _enqueue_image_job(db, user, row, target, references)
    target.status = "queued"
    target.job_id = job_id
    target.error_code = None
    if row.status != "done":
        row.status = "generating"


@router.get("/sticker-styles", response_model=schemas.StickerStylesOut)
def list_sticker_styles(user: CurrentUser, db: DbSession) -> schemas.StickerStylesOut:
    styles = [
        {k: v for k, v in style.items() if k != "prompt_suffix"}
        for style in sticker_styles.STYLES.values()
    ]
    narratives = list(sticker_styles.NARRATIVES.values())
    return schemas.StickerStylesOut(
        styles=styles,
        track_defaults=dict(sticker_styles.TRACK_DEFAULTS),
        narratives=narratives,
    )


@router.post("/stickers", response_model=schemas.StickerSetOut, status_code=201)
def create_sticker_set(
    payload: schemas.StickerSetCreateIn, user: CurrentUser, db: DbSession
) -> schemas.StickerSetOut:
    if payload.style_id not in sticker_styles.STYLES:
        raise ApiError("invalid_style", f"未知风格：{payload.style_id}")
    if payload.narrative not in schemas.STICKER_NARRATIVES:
        raise ApiError("invalid_narrative", f"未知叙事线：{payload.narrative}")
    workspace = default_workspace(db, user)
    title = payload.title.strip() or payload.theme.strip()[:20]
    row = models.StickerSet(
        owner_user_id=user.id,
        workspace_id=workspace.id,
        title=title,
        theme=payload.theme,
        style_id=payload.style_id,
        track=payload.track,
        hot_cluster_id=payload.hot_cluster_id,
        narrative=payload.narrative,
        status="draft",
    )
    db.add(row)
    db.commit()
    return _set_out(db, row, [])


@router.get("/stickers", response_model=list[schemas.StickerSetOut])
def list_sticker_sets(
    response: Response,
    user: CurrentUser,
    db: DbSession,
    status: str | None = Query(default=None),
    cluster_id: str | None = Query(default=None, alias="clusterId"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
) -> list[schemas.StickerSetOut]:
    stmt = select(models.StickerSet).where(
        models.StickerSet.owner_user_id == user.id,
        models.StickerSet.status != "deleted",
    )
    if status:
        stmt = stmt.where(models.StickerSet.status == status)
    if cluster_id:
        stmt = stmt.where(models.StickerSet.hot_cluster_id == cluster_id)
    # keyset 分页 + X-Next-Cursor 头，与 assets/jobs 列表惯例一致
    rows, next_cursor = pagination.paginate(
        db, stmt, models.StickerSet,
        sort_column=models.StickerSet.updated_at, cursor=cursor, limit=limit,
    )
    pagination.set_cursor_header(response, next_cursor)
    out: list[schemas.StickerSetOut] = []
    for row in rows:
        images = _images(db, row.id)
        if _sync_images_from_jobs(db, images):
            db.refresh(row)
        out.append(_set_out(db, row, images))
    return out


@router.get("/stickers/{set_id}", response_model=schemas.StickerSetOut)
def get_sticker_set(set_id: str, user: CurrentUser, db: DbSession) -> schemas.StickerSetOut:
    row = _load_set(db, user, set_id)
    images = _images(db, row.id)
    if _sync_images_from_jobs(db, images):
        db.refresh(row)
    detail = _set_out(db, row, images)
    detail.images = [_image_out(img) for img in images]
    return detail


@router.patch("/stickers/{set_id}", response_model=schemas.StickerSetOut)
def patch_sticker_set(
    set_id: str,
    payload: schemas.StickerSetUpdateIn,
    user: CurrentUser,
    db: DbSession,
) -> schemas.StickerSetOut:
    row = _load_set(db, user, set_id)
    if row.status in ("done", "deleted"):
        raise ApiError("invalid_transition", f"当前状态 {row.status} 不能修改", status_code=409)
    data = payload.model_dump(exclude_unset=True)
    if "style_id" in data and data["style_id"] is not None:
        if data["style_id"] not in sticker_styles.STYLES:
            raise ApiError("invalid_style", f"未知风格：{data['style_id']}")
        row.style_id = data["style_id"]
    if "narrative" in data and data["narrative"] is not None:
        if data["narrative"] not in schemas.STICKER_NARRATIVES:
            raise ApiError("invalid_narrative", f"未知叙事线：{data['narrative']}")
        row.narrative = data["narrative"]
    if "title" in data and data["title"] is not None:
        row.title = data["title"]
    if "theme" in data and data["theme"] is not None:
        row.theme = data["theme"]
    db.commit()
    return _set_out(db, row, _images(db, row.id), with_images=True)


@router.patch("/stickers/{set_id}/images/{image_id}", response_model=schemas.StickerSetOut)
def patch_sticker_image(
    set_id: str,
    image_id: str,
    payload: schemas.StickerImagePatchIn,
    user: CurrentUser,
    db: DbSession,
) -> schemas.StickerSetOut:
    """第 3 步单张编辑：看/改画面描述，必要时只重画这一张。

    只改当前行，不重排整组——避免像 put_outline 那样删行重建，把别张的
    job 回写、复用池和归档标记一起打乱。
    """
    row = _load_set(db, user, set_id)
    if row.status not in _EDITABLE_IMAGE_STATUS:
        raise ApiError(
            "invalid_transition", f"当前状态 {row.status} 不能改画面描述", status_code=409
        )
    images = _images(db, row.id)
    if _sync_images_from_jobs(db, images):
        db.refresh(row)
        images = _images(db, row.id)
    target = next((img for img in images if img.id == image_id), None)
    if target is None:
        raise not_found("贴图")
    prompt_changed = (
        payload.frame_prompt is not None and payload.frame_prompt.strip() != target.frame_prompt
    )
    if target.status in ("queued", "generating") or (
        payload.regenerate and target.status not in _REGENERATABLE_IMAGE_STATUS
    ):
        raise ApiError(
            "invalid_transition", "这张还在生成中，等它落定后再改", status_code=409
        )
    if payload.regenerate:
        # 单张重画也不能与别的在跑任务并发：供应商配额与整组画风都要等前一张落定
        if any(
            img.id != target.id and img.status in ("queued", "generating") for img in images
        ):
            raise ApiError(
                "invalid_transition", "还有图片在生成中，等它们结束再重画这张", status_code=409
            )
        anchor = images[0] if images else None
        if (
            target.sort_order != 0
            and (anchor is None or anchor.status != "succeeded" or not anchor.asset_id)
        ):
            raise ApiError("invalid_transition", "需先生成锚点图", status_code=409)
    if payload.caption is not None:
        target.caption = payload.caption.strip()
    if payload.slot_type is not None:
        target.slot_type = "pace" if payload.slot_type == "pace" else "info"
    if payload.frame_prompt is not None:
        target.frame_prompt = payload.frame_prompt.strip()
    # 描述变了但暂不重画：有旧素材的 pending/failed 行会被 generate-rest 当成
    # 可复用秒过；succeeded 行保留当前图，等用户勾选重画。
    if prompt_changed and not payload.regenerate and target.status in ("pending", "failed"):
        target.asset_id = None
    if prompt_changed and payload.regenerate:
        _requeue_image(db, user, row, images, target, clear_asset=True)
    db.commit()
    detail = _set_out(db, row, _images(db, row.id), with_images=True)
    detail.images = [_image_out(img) for img in _images(db, row.id)]
    return detail


@router.delete("/stickers/{set_id}", status_code=204)
def delete_sticker_set(set_id: str, user: CurrentUser, db: DbSession) -> Response:
    row = _load_set(db, user, set_id)
    row.status = "deleted"
    row.deleted_at = models.utcnow()
    db.commit()
    return Response(status_code=204)


@router.put("/stickers/{set_id}/outline", response_model=schemas.StickerSetOut)
def put_outline(
    set_id: str,
    payload: schemas.StickerOutlineIn,
    user: CurrentUser,
    db: DbSession,
) -> schemas.StickerSetOut:
    row = _load_set(db, user, set_id)
    if row.status not in _EDITABLE_OUTLINE_STATUS:
        raise ApiError(
            "invalid_transition", f"当前状态 {row.status} 不能修改图序", status_code=409
        )
    count = len(payload.items)
    if count < 2 or count > 9:
        raise ApiError("invalid_outline_count", "图序张数需在 2~9 之间")
    previous = _images(db, row.id)
    # 按图序对齐旧 asset：done 重排/补一张时，位置未变且描述未改的行才能复用旧素材
    old_by_order = {img.sort_order: img for img in previous}
    archived: list[str | None] = []
    # 改图序会先删行再重建：任何在跑任务的回写都会落空（资产白烧 + 行状态错乱），
    # 所以要求既没有在跑的行、也没有活跃链。done 集合此前只看行状态，
    # 走过一遍链式入队后 job_id 可能已与行脱钩，这里用链状态兜住。
    if any(img.job_id and img.status not in _IMAGE_TERMINAL for img in previous):
        raise ApiError(
            "invalid_transition", "仍有任务在生成中，等它们结束后再调整图序", status_code=409
        )
    _reject_if_chain_active(db, user, row.id)
    # 已有图行先全删再重建；改图序意味着重新规划，旧生成结果作废
    db.execute(delete(models.StickerImage).where(models.StickerImage.set_id == row.id))
    for index, item in enumerate(payload.items):
        old = old_by_order.get(index)
        # 位置与画面描述都没变 → 复用旧素材；描述被改 → 旧 asset 作废（不进复用池）
        reusable = (
            old.asset_id
            if old is not None and old.asset_id and old.frame_prompt == item.frame_prompt
            else None
        )
        archived.append(reusable)
        # 复用池直接随行落库（status 仍 pending）：generate-rest 据此秒过，不重复烧钱
        db.add(
            models.StickerImage(
                set_id=row.id,
                sort_order=index,
                caption=item.caption,
                frame_prompt=item.frame_prompt,
                slot_type=item.slot_type,
                asset_id=reusable,
                # 复用的 asset 是按这份描述生成的；新行也需要记下来，
                # 否则「改了描述但没重画」的守卫会把它误判成未应用。
                generated_prompt=item.frame_prompt if reusable else None,
                status="pending",
            )
        )
    row.status = "outlined"
    # 重排即作废归档标记：未复用的旧 asset 改回 generated，finalize 重新盖章时才恢复 sticker
    replaced = {aid for aid in archived if aid}
    for old in previous:
        if old.asset_id and old.asset_id not in replaced:
            asset = db.get(models.Asset, old.asset_id)
            if asset and asset.owner_user_id == user.id and asset.source_type == "sticker":
                asset.source_type = "generated"
    db.commit()
    images = _images(db, row.id)
    if any(archived):
        # done 集合重排后自动回到 generating：新行确认方案时会复用这些旧 asset 秒过，
        # 全成功后执行器把集合推进 review，再 finalize 即可恢复归档不变量。
        # asset_id 只落库不回显（DB 仍 pending）——回显成功态图会诱导用户在生成中下载 zip。
        row.status = "generating"
        db.commit()
    return _set_out(db, row, _images(db, row.id), with_images=True)


def _stored_image_model_id(row: models.StickerSet) -> str | None:
    """集合选定的生图模型。空值表示走环境变量 / 首个可用连接。"""
    stored = (row.meta_json or {}).get("imageModelId")
    return stored.strip() if isinstance(stored, str) and stored.strip() else None


def _remember_image_model(row: models.StickerSet, model_id: str | None) -> None:
    """记住本次选择，重试、补图和改描述重画都沿用同一模型。"""
    meta = dict(row.meta_json or {})
    if model_id:
        meta["imageModelId"] = model_id
    else:
        meta.pop("imageModelId", None)
    row.meta_json = meta or None


def _job_payload(
    row: models.StickerSet, img: models.StickerImage, reference_asset_ids: list[str], chain: bool
) -> dict:
    payload = {
        "prompt": img.frame_prompt,
        "aspectRatio": "3:4",
        "count": 1,
        "stickerStyleId": row.style_id,
        "stickerSetId": row.id,
        "stickerImageId": img.id,
    }
    model_id = _stored_image_model_id(row)
    if model_id:
        payload["modelId"] = model_id
    if reference_asset_ids:
        payload["referenceAssetIds"] = reference_asset_ids
    if chain:
        # 同集合的批量任务串行执行，避免并发撞供应商限流（429）
        payload["stickerChainKey"] = f"sticker:{row.id}"
    return payload


def _enqueue_image_job(
    db, user, row: models.StickerSet, img: models.StickerImage, reference_asset_ids: list[str]
) -> str:
    job_id = queue.enqueue(
        db,
        owner_user_id=user.id,
        queue="ai-image",
        kind="image-generation",
        payload=_job_payload(row, img, reference_asset_ids, chain=False),
    )
    # 记下这一刻的文案：之后改描述但未重画时，retry 不能拿旧 payload 再跑一遍
    img.generated_prompt = img.frame_prompt
    return job_id


@router.patch("/stickers/{set_id}/image-model", response_model=schemas.StickerSetOut)
def set_image_model(
    set_id: str, payload: schemas.StickerImageModelIn, user: CurrentUser, db: DbSession
) -> schemas.StickerSetOut:
    """选定本集生图模型。空值表示环境变量供应商，后续生成与重试都沿用。"""
    row = _load_set(db, user, set_id)
    if row.status == "deleted":
        raise ApiError("invalid_transition", "已删除的贴图集不能改生图模型", status_code=409)
    _remember_image_model(row, payload.model_id)
    db.commit()
    return _set_out(db, row, _images(db, row.id), with_images=True)


@router.post("/stickers/{set_id}/generate-anchor", response_model=schemas.StickerSetOut)
def generate_anchor(set_id: str, user: CurrentUser, db: DbSession) -> schemas.StickerSetOut:
    row = _load_set(db, user, set_id)
    if row.status not in ("outlined", "generating", "review"):
        raise ApiError(
            "invalid_transition", f"当前状态 {row.status} 不能生成锚点", status_code=409
        )
    images = _images(db, row.id)
    # 校验前先对账，避免拿 stale 的 queued/pending 状态拒绝一次合法重抽
    if _sync_images_from_jobs(db, images):
        db.refresh(row)
        images = _images(db, row.id)
    if not images:
        raise ApiError("invalid_transition", "尚未设置图序", status_code=409)
    anchor = images[0]
    # succeeded 也放行 = 「重抽锚点」：旧 asset 作废（finalize 后标记由执行器回写补盖）
    if anchor.status == "queued":
        raise ApiError("invalid_transition", "锚点任务已在排队中", status_code=409)
    # 重抽锚点是无链任务；与在跑的链并发会双开生成（撞限流），
    # 且其余行参考的是即将被换掉的旧锚点，出来必然不成套。
    _reject_if_chain_active(db, user, row.id)
    job_id = _enqueue_image_job(db, user, row, anchor, [])
    anchor.status = "queued"
    anchor.job_id = job_id
    anchor.error_code = None
    if row.status != "done":
        row.status = "generating"
    db.commit()
    detail = _set_out(db, row, _images(db, row.id), with_images=True)
    detail.images = [_image_out(img) for img in _images(db, row.id)]
    return detail


@router.post("/stickers/{set_id}/generate-rest", response_model=schemas.StickerSetOut)
def generate_rest(
    set_id: str, user: CurrentUser, db: DbSession
) -> schemas.StickerSetOut:
    row = _load_set(db, user, set_id)
    images = _images(db, row.id)
    if _sync_images_from_jobs(db, images):
        db.refresh(row)
        images = _images(db, row.id)
    if not images:
        raise ApiError("invalid_transition", "尚未设置图序", status_code=409)
    # asset 复用（done 重排/补一张）：put_outline 已把可复用素材随行落库（状态仍 pending）。
    # 先算出还要真生成哪些行，再决定是否放行——守卫放在写库之前，免得复用转正
    # 已经改在内存里、却因为 409 半途返回。
    reusable_ids = {
        img.id for img in images if img.asset_id and img.status in ("pending", "failed")
    }
    pending_rest = [
        img for img in images[1:] if img.status in ("pending", "failed") and img.id not in reusable_ids
    ]
    if pending_rest:
        # 已有链在跑时再发链会出现两个 running 成员，供应商限流前就先挡住
        _reject_if_chain_active(db, user, row.id)
    reused = 0
    for img in images:
        if img.id in reusable_ids:
            img.status = "succeeded"
            img.error_code = None
            reused += 1
    anchor = images[0]
    if anchor.status != "succeeded" or not anchor.asset_id:
        raise ApiError("invalid_transition", "需先生成锚点图", status_code=409)
    batch = pending_rest
    if batch:
        # 串行链：同集合逐张执行，前一张落定后由 chain_next_if_done 接力投递下一单
        payloads = [_job_payload(row, img, [anchor.asset_id], chain=True) for img in batch]
        job_ids = queue.enqueue_chain(
            db, owner_user_id=user.id, queue="ai-image", kind="image-generation", payloads=payloads
        )
        for img, job_id in zip(batch, job_ids):
            img.status = "queued"
            img.job_id = job_id
            img.error_code = None
            img.generated_prompt = img.frame_prompt
    enqueued = len(batch)
    siblings = images
    if reused and enqueued == 0 and all(row_img.status == "succeeded" for row_img in siblings):
        # done 集合只补了 asset、零新任务：就地推进状态机，finalize 重新盖归档标记
        if row.status != "done":
            row.status = "review"
        else:
            for row_img in siblings:
                asset = db.get(models.Asset, row_img.asset_id) if row_img.asset_id else None
                if asset and asset.owner_user_id == user.id:
                    asset.source_type = "sticker"
    elif enqueued and row.status != "done":
        # 仅在确有入队时改状态，避免把终态集合无谓拉回 generating
        row.status = "generating"
    db.commit()
    return _set_out(db, row, _images(db, row.id), with_images=True)


@router.post("/stickers/{set_id}/images/{image_id}/retry", response_model=schemas.StickerSetOut)
def retry_image(
    set_id: str, image_id: str, user: CurrentUser, db: DbSession
) -> schemas.StickerSetOut:
    row = _load_set(db, user, set_id)
    images = _images(db, row.id)
    if _sync_images_from_jobs(db, images):
        db.refresh(row)
        images = _images(db, row.id)
    target = next((img for img in images if img.id == image_id), None)
    if target is None:
        raise not_found("贴图")
    if target.status not in ("failed", "pending"):
        raise ApiError(
            "invalid_transition", f"该图状态 {target.status} 不能重试", status_code=409
        )
    anchor = images[0]
    references: list[str] = []
    # 非锚点且锚点已成功 → 带锚点做一致性参考；锚点自身重试不带参考图
    if target is not anchor and anchor.status == "succeeded" and anchor.asset_id:
        references = [anchor.asset_id]
    # 单张重试也是真打供应商：与在跑的串行链并发同样会撞限流
    _reject_if_chain_active(db, user, row.id)
    job_id = _enqueue_image_job(db, user, row, target, references)
    target.status = "queued"
    target.job_id = job_id
    target.error_code = None
    if row.status != "done":
        row.status = "generating"
    db.commit()
    detail = _set_out(db, row, _images(db, row.id), with_images=True)
    detail.images = [_image_out(img) for img in _images(db, row.id)]
    return detail


@router.post("/stickers/{set_id}/retry-failed", response_model=schemas.StickerSetOut)
def retry_failed(set_id: str, user: CurrentUser, db: DbSession) -> schemas.StickerSetOut:
    """批量重试全部失败张：走串行链，避免一次全打出去撞供应商限流。"""
    row = _load_set(db, user, set_id)
    images = _images(db, row.id)
    if _sync_images_from_jobs(db, images):
        db.refresh(row)
        images = _images(db, row.id)
    anchor = images[0] if images else None
    # 按图序取失败张：锚点（sort_order 0）自然排在链头
    batch = [img for img in images if img.status == "failed"]
    if not batch:
        raise ApiError("nothing_to_retry", "没有失败的图片可重试", status_code=409)
    _reject_if_chain_active(db, user, row.id)
    # 锚点没成功就没有可用的一致性参考：绝不把空/旧 asset 塞进 referenceAssetIds。
    # （锚点自身同理——它带不了自己的参考图，只能走纯文生图路径。）
    references = (
        [anchor.asset_id]
        if anchor is not None and anchor.status == "succeeded" and anchor.asset_id
        else []
    )
    # 锚点必须留在链里、作为链头，而不是单独 enqueue 出去：单独投递会与链头同时
    # 执行，等于同一集合并发两次供应商调用——正是串行链要消灭的 429 场景。
    payloads = [
        _job_payload(row, img, [] if img is anchor else references, chain=True) for img in batch
    ]
    job_ids = queue.enqueue_chain(
        db, owner_user_id=user.id, queue="ai-image", kind="image-generation", payloads=payloads
    )
    for img, job_id in zip(batch, job_ids):
        img.status = "queued"
        img.job_id = job_id
        img.error_code = None
        img.generated_prompt = img.frame_prompt
    if row.status != "done":
        row.status = "generating"
    db.commit()
    return _set_out(db, row, _images(db, row.id), with_images=True)


@router.post("/stickers/{set_id}/finalize", response_model=schemas.StickerSetOut)
def finalize_sticker_set(set_id: str, user: CurrentUser, db: DbSession) -> schemas.StickerSetOut:
    row = _load_set(db, user, set_id)
    images = _images(db, row.id)
    if not images:
        raise ApiError("invalid_transition", "尚无图片可完成", status_code=409)
    if any(img.status != "succeeded" for img in images):
        raise ApiError("incomplete", "仍有未成功的图片，无法标记完成", status_code=409)
    for img in images:
        asset = db.get(models.Asset, img.asset_id) if img.asset_id else None
        if asset and asset.owner_user_id == user.id:
            asset.source_type = "sticker"
    row.status = "done"
    db.commit()
    detail = _set_out(db, row, images)
    detail.images = [_image_out(img) for img in images]
    return detail


@router.get("/stickers/{set_id}/archive.zip")
def archive_zip(set_id: str, user: CurrentUser, db: DbSession) -> Response:
    row = _load_set(db, user, set_id)
    if row.status not in ("done", "review"):
        raise ApiError("invalid_transition", f"当前状态 {row.status} 不能打包下载", status_code=409)
    images = _images(db, row.id)
    succeeded = [img for img in images if img.status == "succeeded" and img.asset_id]
    if not succeeded:
        raise ApiError("no_images", "没有可下载的成功图片", status_code=409)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for img in succeeded:
            asset = db.get(models.Asset, img.asset_id)
            if not asset or asset.owner_user_id != user.id:
                continue
            path = storage_path(get_settings().storage_dir, asset.storage_key)
            if not path.is_file():
                continue
            ext = ".png"
            mime = (asset.mime_type or "").lower()
            if "jpeg" in mime or "jpg" in mime:
                ext = ".jpg"
            elif "webp" in mime:
                ext = ".webp"
            archive.writestr(f"{img.sort_order + 1:02d}{ext}", path.read_bytes())
    buffer.seek(0)
    filename = f"stickers-{row.id}.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
