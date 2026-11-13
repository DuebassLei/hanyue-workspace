"""Agent 状态快照。

设置页原本只有 Skill 列表和工具分级，看不出 Agent 到底能不能干活、数据落在哪里。
这里把两件事变成可查询的事实：

1. 运行模式：有可用远端连接 → ready；连接缺 Base URL / 密钥 → degraded（会回退本地）；
   完全没配连接 → local（本地确定性生成器，能跑但不是真实模型产出）。
2. 落地路径：数据库、素材目录、配置文件、内置 Skill 注册表，全部来自 settings
   与磁盘实际探测，附带存在性与可写性，避免界面上出现想当然的路径。

状态由配置推导，不缓存、不猜测；探测失败只降级描述，不影响接口可用。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import and_, func, or_, select

from .. import models
from ..core.config import BASE_DIR, get_settings
from ..core.deps import default_workspace
from . import ai_gateway, skill_registry

ACTIVE_RUN_STATUSES = ("queued", "running", "paused", "awaiting_approval")


def _human_bytes(size: int) -> str:
    if size <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _writable(path: Path) -> bool:
    probe = path if path.exists() else path.parent
    try:
        return probe.exists() and os.access(probe, os.W_OK)
    except OSError:
        return False


def _dir_stats(path: Path) -> dict:
    files = 0
    size = 0
    if path.exists():
        for item in path.rglob("*"):
            if not item.is_file():
                continue
            files += 1
            try:
                size += item.stat().st_size
            except OSError:
                continue
    return {"files": files, "bytes": size, "sizeLabel": _human_bytes(size)}


def _sqlite_path(database_url: str) -> Path | None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return None
    return Path(database_url[len(prefix) :])


def _mode_of(connection: models.ModelConnection | None) -> dict:
    if connection is None:
        return {
            "code": "local",
            "label": "本地演练",
            "tone": "warning",
            "detail": "未配置模型连接，当前由本地确定性生成器产出文本；流程能跑通，但不是真实模型输出。",
        }
    missing = []
    if not connection.base_url:
        missing.append("Base URL")
    if not connection.credential_ciphertext:
        missing.append("API Key")
    if missing:
        return {
            "code": "degraded",
            "label": "配置不完整",
            "tone": "warning",
            "detail": f"连接「{connection.display_name}」缺少 {'、'.join(missing)}；生成请求会明确报 provider_unavailable，不会产生占位内容。",
        }
    if connection.status != "connected":
        return {
            "code": "degraded",
            "label": "连通性未验证",
            "tone": "warning",
            "detail": f"连接「{connection.display_name}」尚未通过「测试连接」；Agent 规划会降级为确定性规则，请先验证连通性。",
        }
    return {
        "code": "ready",
        "label": "就绪",
        "tone": "success",
        "detail": f"使用连接「{connection.display_name}」调用远端模型，连通性已验证。",
    }


def _last_run(db, user: models.User) -> dict | None:
    run = db.scalar(
        select(models.AgentRun)
        .where(models.AgentRun.owner_user_id == user.id)
        .order_by(models.AgentRun.created_at.desc())
        .limit(1)
    )
    if not run:
        return None
    return {
        "id": run.id,
        "status": run.status,
        "goal": (run.goal or "")[:120],
        "roundsUsed": run.rounds_used,
        "errorCode": run.error_code,
        "createdAt": run.created_at.isoformat() if run.created_at else None,
    }


def _paths(workspace: models.Workspace, skill_total: int, builtin_total: int) -> list[dict]:
    settings = get_settings()
    items: list[dict] = []

    db_path = _sqlite_path(settings.database_url)
    if db_path is None:
        items.append(
            {
                "label": "工作区数据库",
                "value": settings.database_url.split("://", 1)[0] + "（远端托管）",
                "exists": True,
                "writable": True,
                "detail": "非本地 SQLite，连接串不回显",
                "kind": "database",
            }
        )
    else:
        exists = db_path.exists()
        size = db_path.stat().st_size if exists else 0
        items.append(
            {
                "label": "工作区数据库",
                "value": str(db_path),
                "exists": exists,
                "writable": _writable(db_path),
                "detail": f"{_human_bytes(size)} · 工作区 {workspace.id}",
                "kind": "database",
            }
        )

    storage = settings.storage_dir
    stats = _dir_stats(storage)
    items.append(
        {
            "label": "素材存储",
            "value": str(storage),
            "exists": storage.exists(),
            "writable": _writable(storage),
            "detail": f"{stats['files']} 个文件 · {stats['sizeLabel']}",
            "kind": "storage",
        }
    )

    env_path = BASE_DIR / ".env"
    items.append(
        {
            "label": "服务端配置",
            "value": str(env_path),
            "exists": env_path.exists(),
            "writable": _writable(env_path),
            "detail": "已读取" if env_path.exists() else "未创建，使用默认值（改端口/密钥时再建）",
            "kind": "config",
        }
    )

    items.append(
        {
            "label": "内置 Skill 定义",
            "value": str(skill_registry.BUILTIN_RESOURCE),
            "exists": skill_registry.BUILTIN_RESOURCE.exists(),
            "writable": False,
            "detail": f"{builtin_total} 个内置定义，随服务端资源同步；当前账号可见 {skill_total} 个",
            "kind": "skills",
        }
    )
    return items


def status_snapshot(db, user: models.User) -> dict:
    settings = get_settings()
    workspace = default_workspace(db, user)

    connection, model_code = ai_gateway.resolve_connection(db, user.id)
    mode = _mode_of(connection)

    # 只统计当前账号可见的 Skill：内置 + 本人自定义；别人创建的不计入
    skill_total = (
        db.scalar(
            select(func.count())
            .select_from(models.SkillRegistryEntry)
            .where(
                or_(
                    and_(
                        models.SkillRegistryEntry.owner_user_id.is_(None),
                        models.SkillRegistryEntry.source == "builtin",
                    ),
                    models.SkillRegistryEntry.owner_user_id == user.id,
                )
            )
        )
        or 0
    )
    builtin_total = len(skill_registry.get_builtin_specs())
    skill_enabled = (
        db.scalar(
            select(func.count())
            .select_from(models.WorkspaceSkill)
            .where(
                models.WorkspaceSkill.workspace_id == workspace.id,
                models.WorkspaceSkill.enabled.is_(True),
            )
        )
        or 0
    )

    active_runs = (
        db.scalar(
            select(func.count())
            .select_from(models.AgentRun)
            .where(
                models.AgentRun.owner_user_id == user.id,
                models.AgentRun.status.in_(ACTIVE_RUN_STATUSES),
            )
        )
        or 0
    )
    pending_approvals = (
        db.scalar(
            select(func.count())
            .select_from(models.AgentApproval)
            .join(models.AgentRun, models.AgentRun.id == models.AgentApproval.run_id)
            .where(
                models.AgentRun.owner_user_id == user.id,
                models.AgentApproval.status == "pending",
            )
        )
        or 0
    )

    return {
        "mode": mode,
        "model": (
            None
            if connection is None
            else {
                "connectionId": connection.id,
                "displayName": connection.display_name,
                "providerType": connection.provider_type,
                "baseUrl": connection.base_url,
                "status": connection.status,
                "modelCode": model_code,
            }
        ),
        "workspace": {"id": workspace.id, "name": workspace.name},
        "skills": {"enabled": skill_enabled, "total": skill_total},
        "runs": {
            "active": active_runs,
            "pendingApprovals": pending_approvals,
            "last": _last_run(db, user),
        },
        "paths": _paths(workspace, skill_total, builtin_total),
        "storageDir": str(settings.storage_dir),
        "checkedAt": datetime.now(UTC).isoformat(),
    }
