"""Skill 管理 API：注册表、个人定制与工作区授权。

所有写入都经 services/skill_registry，路由只负责鉴权、取默认工作区和响应序列化。
这个模块是 /skills 的唯一入口，不再在 agent / workspaces 里重复注册同名路由。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Response

from .. import models, schemas
from ..core import pagination
from ..core.deps import CurrentUser, DbSession, default_workspace, not_found
from ..services import skill_registry

router = APIRouter(tags=["skills"])


@router.get("/skills", response_model=list[schemas.SkillOut])
def list_skills(
    user: CurrentUser,
    db: DbSession,
    response: Response,
    limit: int = Query(default=100, le=200),
    cursor: str | None = Query(default=None),
) -> list[schemas.SkillOut]:
    result, next_cursor = skill_registry.list_visible(db, user, limit=limit, cursor=cursor)
    pagination.set_cursor_header(response, next_cursor)
    return result


@router.post("/skills", response_model=schemas.SkillOut, status_code=201)
def create_skill(
    payload: schemas.SkillCreateIn, user: CurrentUser, db: DbSession
) -> schemas.SkillOut:
    """创建当前账号私有的自定义 Skill（写作风格模板）。"""
    return skill_registry.create_skill(db, user, payload)


@router.put("/skills/{skill_id}", response_model=schemas.SkillOut)
def update_skill(
    skill_id: str,
    payload: schemas.SkillUpdateIn,
    user: CurrentUser,
    db: DbSession,
) -> schemas.SkillOut:
    """更新自定义 Skill；内置 Skill 不可直接改，需先定制。"""
    return skill_registry.update_skill(db, user, skill_id, payload)


@router.post("/skills/{skill_id}/customize", response_model=schemas.SkillOut, status_code=201)
def customize_skill(
    skill_id: str,
    payload: schemas.SkillUpdateIn,
    user: CurrentUser,
    db: DbSession,
) -> schemas.SkillOut:
    """把内置 Skill 复制为当前账号的个人副本；删除副本即恢复默认。"""
    return skill_registry.fork_builtin(db, user, skill_id, payload)


@router.delete("/skills/{skill_id}", status_code=204)
def delete_skill(skill_id: str, user: CurrentUser, db: DbSession) -> None:
    skill_registry.delete_skill(db, user, skill_id)


@router.patch("/skills/{skill_id}", response_model=schemas.SkillOut)
def toggle_skill(
    skill_id: str,
    payload: schemas.SkillToggleIn,
    user: CurrentUser,
    db: DbSession,
) -> schemas.SkillOut:
    entry = db.get(models.SkillRegistryEntry, skill_id)
    if not entry:
        raise not_found("Skill")
    workspace = default_workspace(db, user)
    grant = skill_registry.set_enabled(
        db,
        user=user,
        workspace=workspace,
        entry=entry,
        enabled=payload.enabled,
        granted_tool_ids=payload.granted_tool_ids,
        approval_policy=payload.approval_policy,
    )
    return skill_registry.build_skill_out(entry, grant)
