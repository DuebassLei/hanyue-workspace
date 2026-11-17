"""热点相关的两条内置任务，薄封装到现有 services。

业务逻辑仍只有一份（app.services.hot_topics），这里只做「参数拆包 + 调用」，
避免迁移过程中把已经跑通的服务逻辑重写一遍。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .. import models
from ..core.deps import default_workspace
from ..services import hot_topics
from .base import ScriptContext, ScriptFailure


def refresh(params: dict, ctx: ScriptContext) -> dict:
    platforms = params.get("platforms") or None
    try:
        return hot_topics.refresh(ctx.db, platforms=platforms)
    except hot_topics.HotTopicsError as exc:
        raise ScriptFailure(exc.code, exc.message, retryable=exc.code == "all_sources_failed") from exc


def digest(params: dict, ctx: ScriptContext) -> dict:
    db: Session = ctx.db
    user = db.get(models.User, ctx.owner_user_id)
    if user is None:
        raise ScriptFailure("owner_missing", "定时任务所属的用户已不存在")
    workspace = default_workspace(db, user)
    try:
        return hot_topics.build_digest(
            db,
            user,
            workspace,
            tracks=params.get("tracks") or None,
            min_score=int(params.get("min_score", 60)),
            limit=int(params.get("limit", 3)),
            create_drafts=bool(params.get("create_drafts", True)),
        )
    except hot_topics.HotTopicsError as exc:
        raise ScriptFailure(exc.code, exc.message) from exc
