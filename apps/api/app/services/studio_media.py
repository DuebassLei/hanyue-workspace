"""成稿台媒体能力：封面生成、正文插图、SVG 动画卡。

三件事共用一条链路：入队 → 前端轮询 sync 拉结果 → 把产物写回
`studio_articles.cover` / `inline_diagrams`（不新建表，见契约 §0）。

原则与 `modules/ai_images.py` 一致：没有可用生图供应商就明确失败，
绝不返回一个永远停在 running 的假任务。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .. import models
from ..core import queue
from ..core.config import get_settings
from ..core.deps import ApiError, not_found
from ..core.security import sign_asset_url
from . import article_blocks as AB, image_provider, studio_repo, wechat_svg_cards

# 环境变量与界面文案共用一套口径：用户看到这句就知道去哪里补配置。
PROVIDER_MISSING_MESSAGE = (
    "未配置可用的生图模型，暂时无法生成图片；请先到「设置 → 模型管理」里"
    "接入支持 image.generate 能力的模型，或改用「选择已有素材」。"
)

RATIOS = ("2.35:1", "16:9", "4:3", "3:4", "1:1", "9:16")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def asset_url(asset_id: str | None) -> str | None:
    """与 storyboards._url / jobs 里一致的可匿名访问直链。"""
    if not asset_id:
        return None
    return f"{get_settings().api_prefix}/assets/{asset_id}/raw?{sign_asset_url(asset_id)}"


def load(db, user, article_id: str) -> tuple[models.StudioArticle, dict]:
    """按 owner 取稿件；等价于 studio._data，但不 import 它的私有实现。"""
    row = studio_repo.get_article(db, article_id, owner_user_id=user.id)
    if not row:
        raise not_found("稿件")
    return row, studio_repo.row_to_dict(row)


def commit(db, row, data: dict) -> dict:
    AB.touch(data)
    studio_repo.save(db, row, data)
    db.commit()
    return studio_repo.row_to_dict(row)


# ---------------- 公共校验 ----------------


def _provider_available(db, user_id: str) -> bool:
    return bool(image_provider.status(db, user_id).get("available"))


def _ratio(value: str | None, fallback: str) -> str:
    ratio = (value or "").strip()
    ratio = ratio or fallback
    if ratio not in RATIOS:
        raise ApiError("invalid_aspect_ratio", f"不支持的图片比例：{ratio}", status_code=400)
    return ratio


def image_capabilities(db, user_id: str) -> dict:
    provider = image_provider.resolve_image_provider(db, user_id)
    sizes = image_provider.exact_image_sizes(provider)
    return {
        "available": bool(provider),
        "modelCode": provider.get("modelCode") if provider else None,
        "ratios": [ratio for ratio in RATIOS if ratio in sizes],
    }


def _require_ratio(db, user_id: str, ratio: str) -> None:
    capabilities = image_capabilities(db, user_id)
    if ratio not in capabilities["ratios"]:
        raise ApiError("unsupported_aspect_ratio", f"当前模型未确认支持 {ratio} 比例；请选择已确认支持的比例，或在模型配置中登记精确尺寸。", status_code=400)


def _asset_of(db, user_id: str, asset_id: str) -> models.Asset:
    asset = db.get(models.Asset, asset_id)
    if not asset or asset.owner_user_id != user_id:
        raise not_found("素材")
    return asset


def _read_job(db, user_id: str, job_id: str | None) -> models.BackgroundJob | None:
    """读任务；缺失或不属于当前用户都按「没有这个任务」处理。

    sync 是轮询接口，这里不抛 404：任务被清理时把条目标 failed，
    前端才能结束轮询，而不是一直撞错误。
    """
    if not job_id:
        return None
    job = db.get(models.BackgroundJob, job_id)
    if not job or job.owner_user_id != user_id:
        return None
    return job


def _job_error(job: models.BackgroundJob) -> str:
    if job.error_message:
        return job.error_message
    if job.status == "cancelled":
        return "生成任务已取消，请重新生成。"
    return f"生成失败（{job.error_code or job.status or 'unknown'}）"


def _enqueue_image(
    db,
    *,
    user_id: str,
    prompt: str,
    purpose: str,
    aspect_ratio: str,
) -> str:
    """入队生图任务；payload 与 modules/ai_images.create_image_generation 对齐。"""
    return queue.enqueue(
        db,
        owner_user_id=user_id,
        queue="ai-image",
        kind="image-generation",
        payload={
            "prompt": prompt,
            "purpose": purpose,
            "aspectRatio": aspect_ratio,
            "exactAspect": True,
            "count": 1,
        },
    )


def _default_cover_prompt(data: dict) -> str:
    title = (data.get("title") or data.get("topic") or "文章").strip()
    lead = ""
    for block in data.get("blocks") or []:
        if block.get("kind") != "para":
            continue
        text = AB.plain_of(block.get("text") or "")
        if len(text) >= 20:
            lead = text
            break
    base = f"为公众号文章《{title}》生成一张封面图"
    return f"{base}；内容概要：{lead[:120]}" if lead else base


def _insert_after(data: dict, anchor_id: str, block: dict) -> bool:
    """把块插到 anchor 之后。ordinal 由仓储层按列表顺序重排，已有块 id 不动。"""
    blocks = data.setdefault("blocks", [])
    index = next((i for i, item in enumerate(blocks) if item["id"] == anchor_id), -1)
    if index == -1:
        return False
    blocks.insert(index + 1, block)
    return True


# ---------------- 封面 ----------------


def generate_cover(db, row, data: dict, *, user_id: str, prompt: str | None, aspect_ratio: str | None) -> None:
    ratio = _ratio(aspect_ratio, "2.35:1")
    text = (prompt or "").strip() or _default_cover_prompt(data)
    base = {
        "jobId": None,
        "assetId": None,
        "url": None,
        "prompt": text,
        "aspectRatio": ratio,
        "requestedAt": now_iso(),
    }
    if not _provider_available(db, user_id):
        data["cover"] = {**base, "status": "failed", "error": PROVIDER_MISSING_MESSAGE}
        commit(db, row, data)
        raise ApiError("image_provider_unavailable", PROVIDER_MISSING_MESSAGE, status_code=400)
    _require_ratio(db, user_id, ratio)
    try:
        job_id = _enqueue_image(
            db, user_id=user_id, prompt=text, purpose="studio-cover", aspect_ratio=ratio
        )
    except Exception as exc:  # 入队失败（线程池关闭 / Redis 异常）也要留下可读的失败态
        db.rollback()
        message = f"封面生成任务入队失败：{exc}。请确认已在设置里配置生图模型后重试。"
        data["cover"] = {**base, "status": "failed", "error": message}
        commit(db, row, data)
        raise ApiError("image_enqueue_failed", message, status_code=400) from exc
    data["cover"] = {**base, "status": "running", "jobId": job_id, "error": None}


def sync_cover(db, row, data: dict, *, user_id: str) -> None:
    cover = dict(data.get("cover") or {})
    if cover.get("status") != "running":
        return
    job = _read_job(db, user_id, cover.get("jobId"))
    if job is None:
        data["cover"] = {**cover, "status": "failed", "error": "生成任务不存在或已被清理，请重新生成。"}
        return
    if job.status in ("queued", "running"):
        return
    if job.status != "succeeded":
        data["cover"] = {**cover, "status": "failed", "error": _job_error(job)}
        return
    asset_ids = [asset_id for asset_id in (job.result or {}).get("assetIds") or [] if asset_id]
    if not asset_ids:
        data["cover"] = {**cover, "status": "failed", "error": "任务已成功，但没有返回任何图片。"}
        return
    asset_id = asset_ids[0]
    if db.get(models.Asset, asset_id) is None:
        # 产物被删了：如实标失败，别给前端一个打不开的链接
        data["cover"] = {**cover, "status": "failed", "error": "生成的图片已被删除，请重新生成。"}
        return
    data["cover"] = {
        **cover,
        "status": "ready",
        "assetId": asset_id,
        "url": asset_url(asset_id),
        "error": None,
    }


def pick_cover(db, row, data: dict, *, user_id: str, asset_id: str | None) -> None:
    if not asset_id:
        data["cover"] = {}
        return
    asset = _asset_of(db, user_id, asset_id)
    data["cover"] = {
        "status": "ready",
        "jobId": None,
        "assetId": asset.id,
        "url": asset_url(asset.id),
        "prompt": asset.prompt or "",
        "aspectRatio": None,
        "requestedAt": now_iso(),
        "error": None,
    }


# ---------------- 正文插图 ----------------


def _new_diagram(data: dict, *, block_id: str, prompt: str, aspect_ratio: str) -> dict:
    diagram = {
        "id": f"d_{uuid.uuid4().hex[:12]}",
        "blockId": block_id,
        "position": "after",
        "jobId": None,
        "status": "running",
        "prompt": prompt,
        "aspectRatio": aspect_ratio,
        "assetId": None,
        "url": None,
        "requestedAt": now_iso(),
        "error": None,
    }
    data.setdefault("inline_diagrams", []).append(diagram)
    return diagram


def generate_inline(
    db,
    row,
    data: dict,
    *,
    user_id: str,
    block_id: str,
    prompt: str | None,
    aspect_ratio: str | None,
    position: str = "after",
) -> None:
    block = next((item for item in data.get("blocks") or [] if item["id"] == block_id), None)
    if not block:
        raise not_found("段落")
    if (position or "after") != "after":
        raise ApiError("invalid_position", "目前只支持把插图插在段落之后", status_code=400)
    ratio = _ratio(aspect_ratio, "16:9")
    text = (prompt or "").strip() or _default_inline_prompt(data, block)
    if not _provider_available(db, user_id):
        raise ApiError("image_provider_unavailable", PROVIDER_MISSING_MESSAGE, status_code=400)
    _require_ratio(db, user_id, ratio)
    try:
        job_id = _enqueue_image(
            db, user_id=user_id, prompt=text, purpose="studio-inline", aspect_ratio=ratio
        )
    except Exception as exc:
        db.rollback()
        message = f"插图生成任务入队失败：{exc}。请确认已在设置里配置生图模型后重试。"
        raise ApiError("image_enqueue_failed", message, status_code=400) from exc
    diagram = _new_diagram(data, block_id=block_id, prompt=text, aspect_ratio=ratio)
    diagram["jobId"] = job_id


def _default_inline_prompt(data: dict, block: dict) -> str:
    title = (data.get("title") or data.get("topic") or "文章").strip()
    text = AB.plain_of(block.get("text") or "")[:160]
    return f"为公众号文章《{title}》的段落配一张说明性插图；段落内容：{text}"


def sync_inline(db, row, data: dict, *, user_id: str) -> None:
    diagrams = data.get("inline_diagrams") or []
    for diagram in diagrams:
        if diagram.get("status") != "running":
            continue
        job = _read_job(db, user_id, diagram.get("jobId"))
        if job is None:
            diagram.update(status="failed", error="生成任务不存在或已被清理，请重新生成。")
            continue
        if job.status in ("queued", "running"):
            continue
        if job.status != "succeeded":
            diagram.update(status="failed", error=_job_error(job))
            continue
        asset_ids = [asset_id for asset_id in (job.result or {}).get("assetIds") or [] if asset_id]
        if not asset_ids:
            diagram.update(status="failed", error="任务已成功，但没有返回任何图片。")
            continue
        asset_id = asset_ids[0]
        if db.get(models.Asset, asset_id) is None:
            diagram.update(status="failed", error="生成的图片已被删除，请重新生成。")
            continue
        diagram.update(status="ready", assetId=asset_id, url=asset_url(asset_id), error=None)
        _insert_diagram_block(data, diagram)


def _insert_diagram_block(data: dict, diagram: dict) -> None:
    """插块并保证幂等：同一 diagram 只插一次（重复 sync 不会叠图）。"""
    blocks = data.setdefault("blocks", [])
    if any(block.get("inserted_diagram_id") == diagram["id"] for block in blocks):
        return
    _insert_after(
        data,
        diagram["blockId"],
        {
            "id": AB.new_block_id(),
            "text": f"![](asset://{diagram['assetId']})",
            "kind": "media",
            "check": None,
            "prev_text": None,
            "inserted_diagram_id": diagram["id"],
        },
    )


def remove_inline(data: dict, *, diagram_id: str) -> None:
    diagrams = data.get("inline_diagrams") or []
    index = next((i for i, item in enumerate(diagrams) if item["id"] == diagram_id), -1)
    if index == -1:
        raise not_found("插图")
    diagrams.pop(index)
    data["blocks"] = [
        block for block in data.get("blocks") or [] if block.get("inserted_diagram_id") != diagram_id
    ]


def pick_inline(
    db, row, data: dict, *, user_id: str, asset_id: str, block_id: str
) -> dict:
    """从素材图库选一张图当正文插图：不走生图，直接落成已就绪的插图并插进正文。

    与 generate_inline 的区别只是「图从哪来」：这里是用户已经有的素材，
    所以没有任务可轮询，直接 ready。
    """
    block = next((item for item in data.get("blocks") or [] if item["id"] == block_id), None)
    if not block:
        raise not_found("段落")
    asset = _asset_of(db, user_id, asset_id)
    diagram = {
        "id": f"d_{uuid.uuid4().hex[:12]}",
        "blockId": block_id,
        "position": "after",
        "jobId": None,
        "status": "ready",
        "prompt": asset.prompt or "",
        "aspectRatio": None,
        "assetId": asset.id,
        "url": asset_url(asset.id),
        "requestedAt": now_iso(),
        "error": None,
        # 记一下这张图是挑来的还是生成的：素材图没有比例信息，排版时按原图走
        "source": "asset",
    }
    data.setdefault("inline_diagrams", []).append(diagram)
    _insert_diagram_block(data, diagram)
    return diagram


# ---------------- SVG 动画卡 ----------------


# 多段合并生成一张卡时的段数上限：再多提示词会太长，生成质量也难保证。
MAX_SVG_CARD_BLOCKS = 10


async def draft_svg_card(
    db,
    data: dict,
    *,
    user_id: str,
    block_ids: list[str],
    kind: str,
) -> None:
    blocks = data.get("blocks") or []
    # 去重并保持用户选择顺序；所有 id 都必须存在于当前稿件。
    ids = list(dict.fromkeys(str(b) for b in (block_ids or []) if b))
    if not ids:
        raise not_found("段落")
    if len(ids) > MAX_SVG_CARD_BLOCKS:
        raise ApiError(
            "svg_card_too_many_blocks",
            f"一次最多选择 {MAX_SVG_CARD_BLOCKS} 个段落",
            status_code=422,
        )
    indexes = []
    for block_id in ids:
        index = next((i for i, item in enumerate(blocks) if item["id"] == block_id), -1)
        if index == -1:
            raise not_found("段落")
        indexes.append(index)
    indexes.sort()
    first, last = indexes[0], indexes[-1]
    # 合并所选段落文字作为同一份素材源：柱状图数值防幻觉校验也基于合并后的全文。
    merged = "\n\n".join(
        text.strip()
        for text in (blocks[i].get("text") or "" for i in indexes)
        if text.strip()
    )
    result = await wechat_svg_cards.draft_card(
        db,
        owner_user_id=user_id,
        kind=kind,
        text=merged,
        article_title=(data.get("title") or data.get("topic") or ""),
        context_before=blocks[first - 1].get("text", "") if first > 0 else "",
        context_after=blocks[last + 1].get("text", "") if last + 1 < len(blocks) else "",
    )
    fence = result.get("markdown") or ""
    if not fence:
        raise ApiError("svg_card_empty", "卡片内容为空，请重试", status_code=502, retryable=True)
    # 卡片插在所选段落中最后一处之后。
    _insert_after(
        data,
        blocks[last]["id"],
        {
            "id": AB.new_block_id(),
            "text": fence,
            "kind": "code",
            "check": None,
            "prev_text": None,
            "inserted_diagram_id": None,
        },
    )
