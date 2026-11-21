"""成稿台：media 能力的路由（见 docs/paper-studio-contract.md §3.2）。

封面 / 正文插图 / SVG 动画卡都落在稿件已有的 `cover` 与 `inline_diagrams`
JSON 字段里，不新建表；长任务只入队，结果由前端轮询 sync 拉取。
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import schemas
from ..core.deps import CurrentUser, DbSession
from ..core.rbac import require_permission
from ..services import studio_media

router = APIRouter(tags=["studio"], dependencies=[require_permission("studio.use")])


class CoverGenerateIn(schemas.CamelModel):
    prompt: str | None = None
    aspect_ratio: str = "16:9"


class CoverPickIn(schemas.CamelModel):
    asset_id: str | None = None


class InlineGenerateIn(schemas.CamelModel):
    block_id: str
    prompt: str | None = None
    aspect_ratio: str = "4:3"
    position: str = "after"


class SvgCardIn(schemas.CamelModel):
    kind: str


def _load(db, user, article_id: str):
    return studio_media.load(db, user, article_id)


def _out(db, row, data: dict) -> dict:
    return studio_media.commit(db, row, data)


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
        db, data, user_id=user.id, block_id=block_id, kind=payload.kind
    )
    return _out(db, row, data)
