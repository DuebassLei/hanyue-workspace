"""工作区与 Skill 注册（设计 §2.2 / §5.7 / §10.1 / §10.4）。

重点修复：`/workspaces/{workspace_id}/...` 必须真正按传入的工作区鉴权并过滤，
不再忽略路径参数、一律回落到默认工作区。
"""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter
from sqlalchemy import select

from .. import models, schemas
from ..core.deps import (
    ApiError,
    CurrentUser,
    DbSession,
    default_workspace,
    not_found,
    require_workspace,
)
from ..services import agent_policy

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
    if payload.enabled and entry.trust_status != "trusted":
        raise ApiError("untrusted_skill", "来源未校验的 Skill 不允许启用", status_code=409)
    grant = db.scalar(
        select(models.WorkspaceSkill).where(
            models.WorkspaceSkill.workspace_id == workspace.id,
            models.WorkspaceSkill.skill_entry_id == entry.id,
        )
    )
    if not grant:
        grant = models.WorkspaceSkill(workspace_id=workspace.id, skill_entry_id=entry.id)
        db.add(grant)
    granted = payload.granted_tool_ids
    if granted is None:
        granted = entry.allowed_tool_ids or []
    # 授权集合永远取 Skill 声明 ∩ 工作区授权，且禁止级工具在策略层剔除
    grant.granted_tool_ids = agent_policy.resolve_allowed_tools(
        skill_tools=entry.allowed_tool_ids or [], workspace_granted=granted
    )
    grant.enabled = payload.enabled
    grant.approval_policy = payload.approval_policy
    db.commit()
    return schemas.SkillOut(
        id=entry.id,
        skill_key=entry.skill_key,
        version=entry.version,
        display_name=entry.display_name,
        description=entry.description,
        risk_level=entry.risk_level,
        allowed_tool_ids=entry.allowed_tool_ids or [],
        trust_status=entry.trust_status,
        enabled=grant.enabled,
        granted_tool_ids=grant.granted_tool_ids or [],
        checksum=entry.checksum,
    )


@router.post("/skills", response_model=schemas.SkillOut, status_code=201)
def register_skill(
    payload: schemas.SkillCreateIn, user: CurrentUser, db: DbSession
) -> schemas.SkillOut:
    """注册自定义 Skill：checksum 真实计算，默认 untrusted，需审核后才能启用。"""
    exists = db.scalar(
        select(models.SkillRegistryEntry).where(
            models.SkillRegistryEntry.skill_key == payload.skill_key
        )
    )
    if exists:
        raise ApiError("skill_exists", "同名 Skill 已存在", status_code=409)
    unknown = [t for t in payload.allowed_tool_ids if t not in agent_policy.TOOL_CATALOG]
    if unknown:
        raise ApiError("unknown_tool", f"未登记的工具：{', '.join(unknown)}")
    raw = json.dumps(
        {"manifest": payload.manifest, "tools": payload.allowed_tool_ids, "version": payload.version},
        sort_keys=True,
        ensure_ascii=False,
    ).encode()
    entry = models.SkillRegistryEntry(
        source="custom",
        skill_key=payload.skill_key,
        version=payload.version,
        display_name=payload.display_name,
        description=payload.description,
        manifest=payload.manifest,
        checksum=hashlib.sha256(raw).hexdigest(),
        trust_status="untrusted",
        allowed_tool_ids=payload.allowed_tool_ids,
        risk_level=agent_policy.READ_ONLY,
    )
    db.add(entry)
    db.commit()
    return schemas.SkillOut(
        id=entry.id,
        skill_key=entry.skill_key,
        version=entry.version,
        display_name=entry.display_name,
        description=entry.description,
        risk_level=entry.risk_level,
        allowed_tool_ids=entry.allowed_tool_ids or [],
        trust_status=entry.trust_status,
        enabled=False,
        granted_tool_ids=[],
        checksum=entry.checksum,
    )
