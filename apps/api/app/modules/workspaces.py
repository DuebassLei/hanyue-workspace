"""工作区 API（设计 §2.2 / §5.7 / §10.1 / §10.4）。

`/workspaces/{workspace_id}/...` 必须真正按传入的工作区鉴权并过滤，
不再忽略路径参数、一律回落到默认工作区。

Skill 的增删改与默认工作区启停已统一到 modules/skills.py；
这里只保留“按指定工作区授权”的入口，规则实现与 /skills 共用同一份服务。
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from .. import models, schemas
from ..core.deps import CurrentUser, DbSession, default_workspace, not_found, require_workspace
from ..services import skill_registry

router = APIRouter(tags=["workspaces"])


def _workspace_out(row: models.Workspace) -> schemas.WorkspaceOut:
    return schemas.WorkspaceOut(id=row.id, name=row.name, created_at=row.created_at)


def _resolve(db, user, workspace_id: str) -> models.Workspace:
    # 兼容 /workspaces/current：即使注册顺序变化也不会把它当成 id 查表
    if workspace_id == "current":
        return default_workspace(db, user)
    return require_workspace(db, user, workspace_id)


@router.get("/workspaces", response_model=list[schemas.WorkspaceOut])
def list_workspaces(user: CurrentUser, db: DbSession) -> list[schemas.WorkspaceOut]:
    rows = db.scalars(
        select(models.Workspace)
        .where(models.Workspace.owner_user_id == user.id)
        .order_by(models.Workspace.created_at)
    ).all()
    return [_workspace_out(row) for row in rows]


@router.post("/workspaces", response_model=schemas.WorkspaceOut, status_code=201)
def create_workspace(
    payload: schemas.WorkspaceIn, user: CurrentUser, db: DbSession
) -> schemas.WorkspaceOut:
    row = models.Workspace(owner_user_id=user.id, name=payload.name, settings={})
    db.add(row)
    db.commit()
    return _workspace_out(row)


@router.get("/workspaces/{workspace_id}", response_model=schemas.WorkspaceOut)
def get_workspace(
    workspace_id: str, user: CurrentUser, db: DbSession
) -> schemas.WorkspaceOut:
    return _workspace_out(_resolve(db, user, workspace_id))


@router.patch("/workspaces/{workspace_id}", response_model=schemas.WorkspaceOut)
def update_workspace(
    workspace_id: str,
    payload: schemas.WorkspacePatch,
    user: CurrentUser,
    db: DbSession,
) -> schemas.WorkspaceOut:
    row = _resolve(db, user, workspace_id)
    if payload.name:
        row.name = payload.name
        db.commit()
    return _workspace_out(row)


@router.patch("/workspaces/{workspace_id}/skills/{skill_id}", response_model=schemas.SkillOut)
def update_workspace_skill(
    workspace_id: str,
    skill_id: str,
    payload: schemas.SkillToggleIn,
    user: CurrentUser,
    db: DbSession,
) -> schemas.SkillOut:
    workspace = _resolve(db, user, workspace_id)
    entry = db.get(models.SkillRegistryEntry, skill_id)
    if not entry:
        raise not_found("Skill")
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
