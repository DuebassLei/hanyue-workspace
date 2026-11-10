"""工作区上下文接口。

选题库已下线：创作空间的 AI 写作承担了「从想法到成稿」的全部流程，
独立的选题池不再作为产品能力维护，这里只保留 `GET /workspaces/current`。
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import models
from ..core.db import SessionLocal
from ..core.deps import CurrentUser, DbSession, default_workspace

router = APIRouter(tags=["workspaces"])


@router.get("/workspaces/current")
def current_workspace(user: CurrentUser, db: DbSession) -> dict:
    workspace = default_workspace(db, user)
    counts = {
        "content": db.query(models.ContentItem)
        .filter(models.ContentItem.workspace_id == workspace.id)
        .count(),
    }
    return {"id": workspace.id, "name": workspace.name, "counts": counts}


def _session() -> SessionLocal:  # pragma: no cover - 便于脚本调用
    return SessionLocal()
