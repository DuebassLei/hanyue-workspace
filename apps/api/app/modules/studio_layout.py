"""成稿台：排版渲染接口（契约见 docs/paper-studio-contract.md 3.1）。

只读接口，不改稿件：选项由请求与稿件已存值合并，渲染结果直接给右栏预览。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import models
from ..core.deps import CurrentUser, DbSession, not_found
from ..core.rbac import require_permission
from ..schemas import CamelModel
from ..services import studio_render, studio_repo

router = APIRouter(tags=["studio"], dependencies=[require_permission("studio.use")])


class RenderIn(CamelModel):
    """渲染入参：options 里缺的字段沿用稿件已存选项。"""

    options: dict | None = None


def _load(db, user, article_id: str) -> tuple[models.StudioArticle, dict]:
    row = db.get(models.StudioArticle, article_id)
    if not row or row.owner_user_id != user.id:
        raise not_found("稿件")
    return row, studio_repo.row_to_dict(row)


@router.post("/studio/articles/{article_id}/render")
def render_studio_article(
    article_id: str, db: DbSession, user: CurrentUser, request: Request,
    payload: RenderIn | None = None,
) -> dict:
    _row, data = _load(db, user, article_id)
    # 把正文里的 asset:// 记号换成绝对直链：预览的 iframe 只认 http/https 的地址
    return studio_render.render_article(
        data, payload.options if payload else None, base_url=str(request.base_url)
    )


@router.get("/studio/articles/{article_id}/render/options")
def render_studio_options(article_id: str, db: DbSession, user: CurrentUser) -> dict:
    _row, data = _load(db, user, article_id)
    return {
        "options": studio_render.effective_options(data),
        "catalog": studio_render.catalog(),
    }
