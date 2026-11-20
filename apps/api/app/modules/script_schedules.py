"""受信任 Python 定时脚本 CRUD、发布与手动运行。"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter
from sqlalchemy import select

from .. import models, schemas
from ..core.config import get_settings
from ..core.deps import ApiError, CurrentUser, DbSession, not_found
from ..core.queue import enqueue
from ..core.rbac import require_permission
from ..services import scheduled_tasks, script_runtime
from ..services import scheduler as scheduler_module

router = APIRouter(
    prefix="/schedules/scripts",
    tags=["schedules"],
    dependencies=[require_permission("scheduled-script.admin")],
)
settings = get_settings()


def _iso(value) -> str | None:
    stamp = scheduled_tasks.as_utc(value)
    return stamp.isoformat() if stamp else None


def _require(script_id: str, user: models.User, db) -> models.ScheduledScript:
    script = db.get(models.ScheduledScript, script_id)
    if script is None or script.owner_user_id != user.id:
        raise not_found("脚本")
    return script


def _out(script: models.ScheduledScript) -> dict:
    return {
        "id": script.id,
        "ownerUserId": script.owner_user_id,
        "name": script.name,
        "description": script.description or "",
        "draftCode": script.draft_code or "",
        "publishedCode": script.published_code or "",
        "publishedVersion": script.published_version or 0,
        "publishedAt": _iso(script.published_at),
        "publishedHash": script.published_hash or "",
        "timeoutSeconds": script.timeout_seconds,
        "enabled": script.enabled,
        "createdAt": _iso(script.created_at),
        "updatedAt": _iso(script.updated_at),
    }


def _disable_related_tasks(db, script: models.ScheduledScript) -> None:
    rows = db.scalars(
        select(models.ScheduledTask)
        .where(
            models.ScheduledTask.task_type == f"user-script.{script.id}",
            models.ScheduledTask.owner_user_id == script.owner_user_id,
            models.ScheduledTask.enabled.is_(True),
        )
    ).all()
    for task in rows:
        task.enabled = False
        task.next_run_at = None
        scheduler_module.unsync(task.id)


def _ensure_related_tasks_enabled(db, script: models.ScheduledScript) -> None:
    if not script.enabled:
        return
    rows = db.scalars(
        select(models.ScheduledTask)
        .where(
            models.ScheduledTask.task_type == f"user-script.{script.id}",
            models.ScheduledTask.owner_user_id == script.owner_user_id,
            models.ScheduledTask.enabled.is_(False),
        )
    ).all()
    for task in rows:
        task.enabled = True
        task.consecutive_failures = 0
        task.paused_reason = ""
        scheduled_tasks.compute_next_run(task)
        scheduler_module.sync(task)


@router.get("")
def list_scripts(user: CurrentUser, db: DbSession) -> dict:
    rows = db.scalars(
        select(models.ScheduledScript)
        .where(models.ScheduledScript.owner_user_id == user.id)
        .order_by(
            models.ScheduledScript.updated_at.desc(),
            models.ScheduledScript.created_at.desc(),
        )
    ).all()
    return {"items": [_out(script) for script in rows]}


@router.get("/{script_id}")
def get_script(script_id: str, user: CurrentUser, db: DbSession) -> dict:
    return _out(_require(script_id, user, db))


@router.post("", status_code=201)
def create_script(
    payload: schemas.ScriptScheduleCreateIn, user: CurrentUser, db: DbSession
) -> dict:
    script = models.ScheduledScript(
        owner_user_id=user.id,
        name=payload.name.strip()[:120] or "未命名脚本",
        description=payload.description.strip()[:500],
        draft_code=payload.draft_code,
        timeout_seconds=payload.timeout_seconds,
        enabled=payload.enabled,
    )
    db.add(script)
    db.commit()
    db.refresh(script)
    return _out(script)


@router.patch("/{script_id}")
def update_script(
    script_id: str,
    payload: schemas.ScriptScheduleUpdateIn,
    user: CurrentUser,
    db: DbSession,
) -> dict:
    script = _require(script_id, user, db)
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        script.name = data["name"].strip()[:120] or "未命名脚本"
    if "description" in data and data["description"] is not None:
        script.description = data["description"].strip()[:500]
    if "draft_code" in data and data["draft_code"] is not None:
        script.draft_code = data["draft_code"]
    if "timeout_seconds" in data and data["timeout_seconds"] is not None:
        script.timeout_seconds = data["timeout_seconds"]
    if "enabled" in data and data["enabled"] is not None:
        script.enabled = bool(data["enabled"])
        if not script.enabled:
            _disable_related_tasks(db, script)
        else:
            _ensure_related_tasks_enabled(db, script)
    db.commit()
    db.refresh(script)
    return _out(script)


@router.delete("/{script_id}")
def delete_script(script_id: str, user: CurrentUser, db: DbSession) -> dict:
    script = _require(script_id, user, db)
    tasks = db.scalars(
        select(models.ScheduledTask)
        .where(
            models.ScheduledTask.task_type == f"user-script.{script.id}",
            models.ScheduledTask.owner_user_id == script.owner_user_id,
        )
    ).all()
    for task in tasks:
        for run in db.scalars(
            select(models.ScheduledTaskRun).where(models.ScheduledTaskRun.task_id == task.id)
        ).all():
            db.delete(run)
        db.delete(task)
    db.delete(script)
    db.commit()
    for task in tasks:
        scheduler_module.unsync(task.id)
    return {"id": script_id, "deleted": True}


@router.post("/{script_id}/publish")
def publish_script(
    script_id: str,
    payload: schemas.ScriptSchedulePublishIn | None,
    user: CurrentUser,
    db: DbSession,
) -> dict:
    script = _require(script_id, user, db)
    code = (
        payload.draft_code
        if payload is not None and payload.draft_code is not None
        else script.draft_code
    )
    if not code.strip():
        raise ApiError("script_empty", "脚本代码不能为空")
    try:
        script_runtime.validate_published(code)
    except script_runtime.ScriptValidationError as exc:
        raise ApiError(exc.code, exc.message) from exc

    script.draft_code = code
    script.published_code = code
    script.published_version = int(script.published_version or 0) + 1
    script.published_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    script.published_at = script_runtime.utcnow()
    db.add(
        models.ScheduledScriptVersion(
            script_id=script.id, version=script.published_version, code=code
        )
    )
    if script.enabled:
        _ensure_related_tasks_enabled(db, script)
    db.commit()
    db.refresh(script)
    return _out(script)


@router.post("/{script_id}/run")
def run_script_now(
    script_id: str,
    payload: schemas.ScriptScheduleRunIn | None,
    user: CurrentUser,
    db: DbSession,
) -> dict:
    script = _require(script_id, user, db)
    if not script.enabled:
        raise ApiError("script_disabled", "脚本已停用，请先启用再手动执行", status_code=409)
    if not script.published_code:
        raise ApiError("script_not_published", "脚本尚未发布", status_code=400)
    params = (payload.params if payload is not None else None) or {}
    if not isinstance(params, dict):
        raise ApiError("invalid_params", "params 必须是 JSON 对象")
    # 锁定当前发布版本的快照：入队后再发布新版本不影响这次执行。
    job_id = enqueue(
        db,
        owner_user_id=user.id,
        queue="user-script",
        kind=f"user-script.{script.id}",
        payload={
            "scriptId": script.id,
            "scriptVersion": script.published_version,
            "scriptHash": script.published_hash,
            "scriptCode": script.published_code,
            "scriptParams": params,
            "scriptTimeoutSeconds": script.timeout_seconds,
        },
    )
    return {"jobId": job_id, "scriptId": script.id}


def script_task_type(script: models.ScheduledScript) -> dict:
    key = f"user-script.{script.id}"
    task_spec = scheduled_tasks.dynamic_task_type(key)
    return {
        "key": key,
        "title": f"自定义脚本 · {script.name}",
        "scope": "user",
        "queue": "user-script",
        "kind": key,
        "description": script.description or "",
        "defaultCron": "0 8 * * *",
        "params": {
            "fields": [{"name": "params", "type": "object", "title": "params"}],
            "schema": task_spec.params_model.model_json_schema(),
        },
    }
