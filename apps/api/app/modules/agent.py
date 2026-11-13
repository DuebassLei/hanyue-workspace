from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from .. import models, schemas
from ..core.deps import ApiError, CurrentUser, DbSession, default_workspace, not_found
from ..services import agent_policy, agent_runtime, agent_status

router = APIRouter(tags=["agent"])


@router.get("/agent/policy")
def get_agent_policy(user: CurrentUser) -> dict:
    """返回服务端实际生效的工具风险策略，供设置页直接渲染。"""
    return agent_policy.policy_summary()


@router.get("/agent/status")
def get_agent_status(user: CurrentUser, db: DbSession) -> dict:
    """Agent 当前运行模式与数据落地路径，供设置页展示真实状态。"""
    return agent_status.status_snapshot(db, user)


@router.get("/agent/providers")
def list_providers(user: CurrentUser, db: DbSession) -> list[dict]:
    rows = db.scalars(
        select(models.AgentProviderConnection).where(
            models.AgentProviderConnection.owner_user_id == user.id
        )
    ).all()
    providers = [
        {
            "id": "native",
            "label": "工作台原生 Agent",
            "configured": True,
            "description": "仅使用工作台内受控工具，无本机文件与命令权限",
        }
    ]
    for row in rows:
        providers.append(
            {
                "id": row.id,
                "label": row.provider,
                "configured": row.status == "configured",
                "description": "外部 Provider（可选）",
            }
        )
    return providers


@router.post("/agent/sessions", response_model=schemas.AgentRunOut | dict, status_code=201)
def create_session(
    payload: schemas.AgentSessionIn, user: CurrentUser, db: DbSession
) -> dict:
    workspace = default_workspace(db, user)
    session = models.AgentSession(
        owner_user_id=user.id, workspace_id=workspace.id, title=payload.title
    )
    db.add(session)
    db.commit()
    return {"id": session.id, "title": session.title, "status": session.status}


@router.get("/agent/sessions")
def list_sessions(user: CurrentUser, db: DbSession) -> list[dict]:
    rows = db.scalars(
        select(models.AgentSession)
        .where(models.AgentSession.owner_user_id == user.id)
        .order_by(models.AgentSession.created_at.desc())
        .limit(30)
    ).all()
    return [{"id": r.id, "title": r.title, "status": r.status} for r in rows]


@router.post("/agent/sessions/{session_id}/messages", response_model=schemas.AgentRunOut)
async def send_message(
    session_id: str, payload: schemas.AgentMessageIn, user: CurrentUser, db: DbSession
) -> schemas.AgentRunOut:
    session = db.get(models.AgentSession, session_id)
    if not session or session.owner_user_id != user.id:
        raise not_found("Agent 会话")
    if not payload.skill_ids:
        raise ApiError("skill_required", "请先选择已启用的 Skill")

    run = models.AgentRun(
        session_id=session.id,
        owner_user_id=user.id,
        workspace_id=session.workspace_id,
        goal=payload.goal,
        status="queued",
        permission_snapshot={"skillIds": payload.skill_ids},
        budget_limit={"maxRounds": payload.max_rounds},
    )
    db.add(run)
    db.flush()
    await agent_runtime.start_run(db, run)
    db.commit()
    return _run_out(run)


@router.get("/agent/runs/{run_id}", response_model=schemas.AgentRunOut)
def get_run(run_id: str, user: CurrentUser, db: DbSession) -> schemas.AgentRunOut:
    run = db.get(models.AgentRun, run_id)
    if not run or run.owner_user_id != user.id:
        raise not_found("Agent 运行")
    return _run_out(run)


@router.get("/agent/runs/{run_id}/events", response_model=list[schemas.AgentEventOut])
def get_events(
    run_id: str, user: CurrentUser, db: DbSession, after_seq: int = 0
) -> list[schemas.AgentEventOut]:
    run = db.get(models.AgentRun, run_id)
    if not run or run.owner_user_id != user.id:
        raise not_found("Agent 运行")
    rows = db.scalars(
        select(models.AgentEvent)
        .where(models.AgentEvent.run_id == run.id, models.AgentEvent.seq > after_seq)
        .order_by(models.AgentEvent.seq)
    ).all()
    return [
        schemas.AgentEventOut(
            seq=row.seq,
            event_type=row.event_type,
            payload=row.safe_payload or {},
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post("/agent/runs/{run_id}/pause", response_model=schemas.AgentRunOut)
def pause_run(run_id: str, user: CurrentUser, db: DbSession) -> schemas.AgentRunOut:
    run = db.get(models.AgentRun, run_id)
    if not run or run.owner_user_id != user.id:
        raise not_found("Agent 运行")
    if run.status in {"succeeded", "failed", "cancelled"}:
        raise ApiError("invalid_state", "该运行已结束", status_code=409)
    run.status = "paused"
    agent_runtime.emit(db, run, "run.paused", {})
    db.commit()
    return _run_out(run)


@router.post("/agent/runs/{run_id}/resume", response_model=schemas.AgentRunOut)
def resume_run(run_id: str, user: CurrentUser, db: DbSession) -> schemas.AgentRunOut:
    run = db.get(models.AgentRun, run_id)
    if not run or run.owner_user_id != user.id:
        raise not_found("Agent 运行")
    if run.status != "paused":
        raise ApiError("invalid_state", "只有已暂停的运行可以继续", status_code=409)
    run.status = "running"
    agent_runtime.emit(
        db, run, "run.resumed_manually", {"allowedTools": agent_runtime.resolve_run_permissions(db, run)}
    )
    db.commit()
    return _run_out(run)


@router.post("/agent/runs/{run_id}/cancel", response_model=schemas.AgentRunOut)
def cancel_run(run_id: str, user: CurrentUser, db: DbSession) -> schemas.AgentRunOut:
    run = db.get(models.AgentRun, run_id)
    if not run or run.owner_user_id != user.id:
        raise not_found("Agent 运行")
    run.status = "cancelled"
    agent_runtime.emit(db, run, "run.cancelled", {})
    db.commit()
    return _run_out(run)


@router.get("/agent/runs/{run_id}/approvals", response_model=list[schemas.ApprovalOut])
def list_approvals(run_id: str, user: CurrentUser, db: DbSession) -> list[schemas.ApprovalOut]:
    run = db.get(models.AgentRun, run_id)
    if not run or run.owner_user_id != user.id:
        raise not_found("Agent 运行")
    rows = db.scalars(
        select(models.AgentApproval).where(models.AgentApproval.run_id == run.id)
    ).all()
    return [
        schemas.ApprovalOut(
            id=row.id,
            run_id=row.run_id,
            requested_action=row.requested_action,
            risk_level=row.risk_level,
            status=row.status,
            params=row.params or {},
            expires_at=row.expires_at,
        )
        for row in rows
    ]


@router.post("/agent/approvals/{approval_id}/decision", response_model=schemas.AgentRunOut)
async def decide(
    approval_id: str, payload: schemas.ApprovalDecisionIn, user: CurrentUser, db: DbSession
) -> schemas.AgentRunOut:
    approval = db.get(models.AgentApproval, approval_id)
    if not approval:
        raise not_found("审批")
    run = db.get(models.AgentRun, approval.run_id)
    if not run or run.owner_user_id != user.id:
        raise not_found("审批")
    try:
        agent_runtime.decide_approval(db, approval, run, payload.decision, user.id)
    except agent_runtime.AgentRuntimeError as exc:
        raise ApiError("approval_failed", str(exc), status_code=409) from exc
    db.commit()
    return _run_out(run)


def _run_out(run: models.AgentRun) -> schemas.AgentRunOut:
    return schemas.AgentRunOut(
        id=run.id,
        session_id=run.session_id,
        status=run.status,
        goal=run.goal,
        rounds_used=run.rounds_used,
        error_code=run.error_code,
        created_at=run.created_at,
    )
