"""成稿台：media 能力的路由（见 docs/paper-studio-contract.md §3.2）。

封面 / 正文插图 / SVG 动画卡都落在稿件已有的 `cover` 与 `inline_diagrams`
JSON 字段里，不新建表；长任务只入队，结果由前端轮询 sync 拉取。
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import schemas
from ..core.deps import ApiError, CurrentUser, DbSession
from ..core.rbac import require_permission
from ..services import studio_media, studio_media_prompts

router = APIRouter(tags=["studio"], dependencies=[require_permission("studio.use")])


class CoverGenerateIn(schemas.CamelModel):
    prompt: str | None = None
    aspect_ratio: str = "2.35:1"


class CoverPickIn(schemas.CamelModel):
    asset_id: str | None = None


class InlineGenerateIn(schemas.CamelModel):
    block_id: str
    prompt: str | None = None
    aspect_ratio: str = "16:9"
    position: str = "after"


class InlinePickIn(schemas.CamelModel):
    """从素材图库挑一张图当插图：指定段落 + 素材 id。"""

    asset_id: str
    block_id: str


class PromptSuggestIn(schemas.CamelModel):
    """按当前稿件现拟提示词：scope=cover 出封面候选，scope=inline 出插图候选。"""

    scope: str = "cover"
    # 插图指定段落时只拟这一段；留空则通读全文挑出该配图的段落
    block_id: str | None = None
    count: int = 3


class SvgCardIn(schemas.CamelModel):
    kind: str
    # 多段合并生成：body 里带 blockIds 时以此为准，path 里的 block_id 仅作旧客户端兜底。
    block_ids: list[str] = []


def _load(db, user, article_id: str):
    return studio_media.load(db, user, article_id)


def _out(db, row, data: dict) -> dict:
    return studio_media.commit(db, row, data)


@router.get("/studio/image-capabilities")
def image_capabilities(db: DbSession, user: CurrentUser):
    return studio_media.image_capabilities(db, user.id)


@router.post(
    "/studio/articles/{article_id}/cover/generate",
    response_model=schemas.StudioArticleOut,
)
def generate_cover(
    article_id: str,
    db: DbSession,
    user: CurrentUser,
    payload: CoverGenerateIn | None = None,
):
    row, data = _load(db, user, article_id)
    studio_media.generate_cover(
        db,
        row,
        data,
        user_id=user.id,
        prompt=payload.prompt if payload else None,
        aspect_ratio=payload.aspect_ratio if payload else None,
    )
    return _out(db, row, data)


@router.post(
    "/studio/articles/{article_id}/cover/sync",
    response_model=schemas.StudioArticleOut,
)
def sync_cover(article_id: str, db: DbSession, user: CurrentUser):
    row, data = _load(db, user, article_id)
    studio_media.sync_cover(db, row, data, user_id=user.id)
    return _out(db, row, data)


@router.post(
    "/studio/articles/{article_id}/cover/pick",
    response_model=schemas.StudioArticleOut,
)
def pick_cover(article_id: str, payload: CoverPickIn, db: DbSession, user: CurrentUser):
    row, data = _load(db, user, article_id)
    studio_media.pick_cover(db, row, data, user_id=user.id, asset_id=payload.asset_id)
    return _out(db, row, data)


@router.post(
    "/studio/articles/{article_id}/inline/pick",
    response_model=schemas.StudioArticleOut,
)
def pick_inline(article_id: str, payload: InlinePickIn, db: DbSession, user: CurrentUser):
    """从素材图库挑一张图插到指定段落之后（不触发生图）。"""
    row, data = _load(db, user, article_id)
    studio_media.pick_inline(
        db, row, data, user_id=user.id, asset_id=payload.asset_id, block_id=payload.block_id
    )
    return _out(db, row, data)


@router.post("/studio/articles/{article_id}/media/prompt-suggestions")
def suggest_media_prompts(
    article_id: str, payload: PromptSuggestIn, db: DbSession, user: CurrentUser
):
    """按当前稿件现拟封面 / 插图提示词候选。

    建议只回前端、不落库：用户挑中后还要在面板里改，改完才随生图任务写进稿件。
    """
    row, data = _load(db, user, article_id)
    scope = (payload.scope or "cover").strip()
    if scope not in ("cover", "inline"):
        raise ApiError("invalid_scope", "scope 只能是 cover 或 inline", status_code=400)
    try:
        items = studio_media_prompts.run_sync(
            db,
            data,
            user_id=user.id,
            scope=scope,
            block_id=payload.block_id,
            count=payload.count,
        )
    except RuntimeError as exc:
        raise ApiError("prompt_suggest_failed", str(exc), status_code=502) from exc
    return {"items": items}


@router.post(
    "/studio/articles/{article_id}/inline/generate",
    response_model=schemas.StudioArticleOut,
)
def generate_inline(article_id: str, payload: InlineGenerateIn, db: DbSession, user: CurrentUser):
    row, data = _load(db, user, article_id)
    studio_media.generate_inline(
        db,
        row,
        data,
        user_id=user.id,
        block_id=payload.block_id,
        prompt=payload.prompt,
        aspect_ratio=payload.aspect_ratio,
        position=payload.position,
    )
    return _out(db, row, data)


@router.post(
    "/studio/articles/{article_id}/inline/sync",
    response_model=schemas.StudioArticleOut,
)
def sync_inline(article_id: str, db: DbSession, user: CurrentUser):
    row, data = _load(db, user, article_id)
    studio_media.sync_inline(db, row, data, user_id=user.id)
    return _out(db, row, data)


@router.delete(
    "/studio/articles/{article_id}/inline/{diagram_id}",
    response_model=schemas.StudioArticleOut,
)
def remove_inline(article_id: str, diagram_id: str, db: DbSession, user: CurrentUser):
    row, data = _load(db, user, article_id)
    studio_media.remove_inline(data, diagram_id=diagram_id)
    return _out(db, row, data)


@router.post(
    "/studio/articles/{article_id}/blocks/{block_id}/svg-card",
    response_model=schemas.StudioArticleOut,
)
async def draft_svg_card(
    article_id: str,
    block_id: str,
    payload: SvgCardIn,
    db: DbSession,
    user: CurrentUser,
):
    row, data = _load(db, user, article_id)
    await studio_media.draft_svg_card(
        db, data, user_id=user.id, block_ids=payload.block_ids or [block_id], kind=payload.kind
    )
    return _out(db, row, data)
