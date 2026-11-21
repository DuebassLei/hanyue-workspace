"""成稿台：发布与复盘路由（见 docs/paper-studio-contract.md §3.3）。

只做三件事：读稿件、调 `modules.studio.export_to_content` 落成正式稿件、
把后续编排交给 `services.studio_publish`。真正的发布任务组装在 service 里，
与 `modules/publishing.py` 的口径保持一致。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from .. import models
from ..core.deps import CurrentUser, DbSession, not_found
from ..core.rbac import require_permission
from ..schemas import CamelModel
from ..services import studio_publish as SPUB
from ..services import studio_repo
from . import studio

router = APIRouter(tags=["studio"], dependencies=[require_permission("studio.use")])


class StudioPublishIn(CamelModel):
    """发布入参：平台可省略（默认已接入平台），dryRun 只导出不入队。"""

    platforms: list[str] | None = None
    scheduled_at: datetime | None = None
    dry_run: bool = False


def _load(db, user, article_id: str) -> tuple[models.StudioArticle, dict]:
    """照 `modules/studio._data` 写一份，不 import 私有名。"""
    row = studio_repo.get_article(db, article_id, owner_user_id=user.id)
    if not row:
        raise not_found("稿件")
    return row, studio_repo.row_to_dict(row)


@router.post("/studio/articles/{article_id}/publish", response_model=dict)
def publish_article(
    article_id: str, payload: StudioPublishIn, db: DbSession, user: CurrentUser
) -> dict:
    """把成稿台的稿件导出成正式稿件，并按平台建发布任务。"""
    article, data = _load(db, user, article_id)
    # 平台与时间先校验：非法入参不该先产生一条 ContentItem
    platforms = SPUB.normalize_platforms(payload.platforms)
    scheduled_at = SPUB.parse_scheduled_at(payload.scheduled_at)
    return SPUB.publish(
        db,
        user,
        article,
        data,
        export=lambda: studio.export_to_content(article_id, db, user),
        platforms=platforms,
        scheduled_at=scheduled_at,
        dry_run=payload.dry_run,
    )


@router.get("/studio/articles/{article_id}/review", response_model=dict)
def review_article(article_id: str, db: DbSession, user: CurrentUser) -> dict:
    """各平台发布状态与文章体量统计；没有发布记录时返回空数组。"""
    article, data = _load(db, user, article_id)
    return SPUB.review(db, user, article, data)
