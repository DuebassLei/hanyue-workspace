"""Agent 运行器（设计文档 5.7）。

运行前冻结权限快照，逐步记录事件，高风险工具生成审批请求并暂停，恢复时重新校验权限。
步骤规划优先使用真实 LLM（需供应商已通过连通性验证），失败时明确降级为确定性规则，
并把 planner / 降级原因写进 plan.created 事件；权限与审批路径两种模式完全一致。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import agent_policy, ai_gateway
from .agent_policy import ToolNotAllowed

APPROVAL_TTL_SECONDS = 15 * 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentRuntimeError(RuntimeError):
    pass


def next_seq(db: Session, run_id: str) -> int:
    last = db.scalar(
        select(models.AgentEvent.seq)
        .where(models.AgentEvent.run_id == run_id)
        .order_by(models.AgentEvent.seq.desc())
        .limit(1)
    )
    return (last or 0) + 1


def emit(db: Session, run: models.AgentRun, event_type: str, payload: dict) -> models.AgentEvent:
    event = models.AgentEvent(
        run_id=run.id, seq=next_seq(db, run.id), event_type=event_type, safe_payload=payload
    )
    db.add(event)
    db.flush()
    return event


def resolve_run_permissions(db: Session, run: models.AgentRun) -> list[str]:
    """恢复运行时重新按当前工作区授权计算工具集合，不信任旧快照。"""
    skill_ids = run.permission_snapshot.get("skillIds") or []
    workspace_id = run.workspace_id
    allowed: list[str] = []
    for skill_id in skill_ids:
        entry = db.get(models.SkillRegistryEntry, skill_id)
        if not entry or entry.trust_status != "trusted":
            continue
        grant = db.scalar(
            select(models.WorkspaceSkill).where(
                models.WorkspaceSkill.workspace_id == workspace_id,
                models.WorkspaceSkill.skill_entry_id == entry.id,
                models.WorkspaceSkill.enabled.is_(True),
            )
        )
        if not grant:
            continue
        allowed.extend(
            agent_policy.resolve_allowed_tools(
                skill_tools=entry.allowed_tool_ids or [],
                workspace_granted=grant.granted_tool_ids or [],
            )
        )
    snapshot_tools = set(run.permission_snapshot.get("allowedToolIds") or [])
    # 取交集：既要在当前授权内，也不能超出运行建立时的快照
    return sorted({t for t in allowed if not snapshot_tools or t in snapshot_tools})


def plan_steps(goal: str, allowed_tools: list[str]) -> list[dict]:
    """确定性规划：按目标关键词选择工作台内部工具，永不生成 shell/SQL。"""
    steps: list[dict] = []
    has = set(allowed_tools)
    if "create_outline" in has:
        steps.append({"tool": "create_outline", "args": {"prompt": goal}})
    if "generate_article" in has:
        steps.append({"tool": "generate_article", "args": {"prompt": goal}})
    if "create_publish_preview" in has:
        steps.append({"tool": "create_publish_preview", "args": {"goal": goal}})
    return steps or [{"tool": "read_content", "args": {"goal": goal}}]


PLANNER_INSTRUCTION = (
    "你是自媒体创作工作台的任务规划器。严格只输出一个 JSON 数组，"
    "不要 Markdown 代码块、不要解释文字。每项形如 "
    '{"tool":"工具名","args":{...}}。只能使用「可用工具」中列出的名称，'
    "禁止输出其它工具；最多 5 步，按创作流程排序。"
)

_ARG_KEYS = {
    "create_outline": "prompt",
    "generate_article": "prompt",
    "prepare_platform_variant": "prompt",
    "create_publish_preview": "goal",
    "read_content": "goal",
}


def _default_args(tool: str, goal: str) -> dict:
    """工具必需参数由服务端注入，不依赖模型是否填对。"""
    key = _ARG_KEYS.get(tool)
    if not key:
        return {}
    value = goal[:80] if key == "query" else (goal[:60] if key == "title" else goal)
    return {key: value}


def _extract_json_array(text: str) -> list:
    cleaned = text.strip()
    if "```" in cleaned:
        parts = cleaned.split("```")
        if len(parts) >= 2:
            cleaned = parts[1]
            if cleaned.lstrip().lower().startswith("json"):
                cleaned = cleaned.lstrip()[4:]
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("no json array")
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, list):
        raise ValueError("not an array")
    return parsed


def sanitize_llm_steps(raw, allowed_tools: list[str], goal: str) -> list[dict]:
    """白名单过滤：模型不能越过 Skill ∩ 工作区授权，也不能塞入超长/异常参数。"""
    allow = set(allowed_tools)
    steps: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        tool = item.get("tool")
        if tool not in allow:
            continue
        args = _default_args(tool, goal)
        extra = item.get("args")
        if isinstance(extra, dict):
            for key, value in extra.items():
                if not isinstance(key, str) or not isinstance(value, (str, int, float, bool)):
                    continue
                args[key] = value[:500] if isinstance(value, str) else value
        steps.append({"tool": tool, "args": args})
        if len(steps) >= 5:
            break
    return steps


async def plan_steps_with_llm(
    db: Session, owner_user_id: str, goal: str, allowed_tools: list[str]
) -> tuple[list[dict], str, str]:
    """返回 (steps, planner, note)。planner ∈ {llm, deterministic}。

    只有「模型连接存在且已通过真实连通性测试」时才发起 LLM 规划；
    本地确定性生成器、调用失败、无法解析或白名单过滤后为空，一律明确降级。
    """
    fallback = plan_steps(goal, allowed_tools)
    candidates = ai_gateway.resolve_connections(db, owner_user_id)
    connection = candidates[0][0] if candidates else None
    if connection is None or connection.status != "connected":
        return fallback, "deterministic", "模型未通过连通性验证，使用确定性规划"

    prompt = (
        f"{PLANNER_INSTRUCTION}\n\n用户目标：{goal[:500]}\n"
        f"可用工具：{', '.join(allowed_tools) if allowed_tools else '（无）'}\n"
        "请输出步骤 JSON 数组。"
    )
    try:
        result = await ai_gateway.generate_text(
            db,
            owner_user_id=owner_user_id,
            task_type="agent_planner",
            prompt=prompt,
        )
    except Exception:  # 单个供应商异常不能让整个 Agent 运行崩掉
        return fallback, "deterministic", "规划模型调用异常，使用确定性规划"

    if result.get("errorCode") or result.get("provider") == "local":
        return fallback, "deterministic", "规划模型不可用，使用确定性规划"
    try:
        parsed = _extract_json_array(result.get("text") or "")
        steps = sanitize_llm_steps(parsed, allowed_tools, goal)
    except (ValueError, TypeError, json.JSONDecodeError):
        steps = []
    if not steps:
        return fallback, "deterministic", "规划模型未产出可用步骤，使用确定性规划"
    return steps, "llm", f"由 {result.get('modelCode') or 'configured-model'} 规划"


async def execute_step(
    db: Session, run: models.AgentRun, step: dict, allowed_tools: list[str]
) -> dict:
    tool_id = step["tool"]
    try:
        agent_policy.assert_tool_callable(tool_id, allowed_tools)
    except ToolNotAllowed as exc:
        raise AgentRuntimeError(str(exc)) from exc

    if agent_policy.requires_approval(tool_id):
        approval = models.AgentApproval(
            run_id=run.id,
            requested_action=agent_policy.TOOL_CATALOG[tool_id]["label"],
            risk_level=agent_policy.tool_risk(tool_id),
            params=step.get("args") or {},
            parameter_digest=_digest(step.get("args") or {}),
            expires_at=_utcnow() + timedelta(seconds=APPROVAL_TTL_SECONDS),
        )
        db.add(approval)
        db.flush()
        call = models.AgentToolCall(
            run_id=run.id,
            tool_id=tool_id,
            input_summary={"args": step.get("args") or {}},
            status="awaiting_approval",
            approval_id=approval.id,
        )
        db.add(call)
        db.flush()
        approval.tool_call_id = call.id
        emit(
            db,
            run,
            "tool.approval_requested",
            {"toolId": tool_id, "approvalId": approval.id, "riskLevel": approval.risk_level},
        )
        run.status = "awaiting_approval"
        return {"status": "awaiting_approval", "approvalId": approval.id}

    result = await _execute_tool(db, run, tool_id, step.get("args") or {})
    call = models.AgentToolCall(
        run_id=run.id,
        tool_id=tool_id,
        input_summary={"args": step.get("args") or {}},
        status="succeeded",
        result_ref=result,
        started_at=_utcnow(),
        completed_at=_utcnow(),
    )
    db.add(call)
    emit(db, run, "tool.result", {"toolId": tool_id, "result": result})
    return {"status": "succeeded", "result": result}


async def _execute_tool(db: Session, run: models.AgentRun, tool_id: str, args: dict) -> dict:
    """工作台内部工具实现：写草稿类数据，AI 类工具走真实模型网关。

    模型无返回时明确失败，不写入占位正文伪装成功。
    """
    if tool_id == "read_content":
        rows = db.scalars(
            select(models.ContentItem)
            .where(models.ContentItem.workspace_id == run.workspace_id)
            .order_by(models.ContentItem.updated_at.desc())
            .limit(5)
        ).all()
        return {"count": len(rows), "items": [{"id": r.id, "title": r.title} for r in rows]}

    if tool_id in {"create_outline", "generate_article", "prepare_platform_variant"}:
        task_type = {
            "create_outline": "outline",
            "generate_article": "article",
            "prepare_platform_variant": "platform_variant",
        }[tool_id]
        prompt = str(args.get("prompt") or run.goal or "写一篇短文章")
        generated = await ai_gateway.generate_text(
            db,
            owner_user_id=run.owner_user_id,
            task_type=task_type,
            prompt=prompt,
            constraints={"targetLength": 400 if task_type == "outline" else 900},
        )
        if not generated["text"]:
            raise AgentRuntimeError(
                f"模型未返回内容（{generated.get('errorCode') or 'empty_response'}），请检查模型连接"
            )
        item = models.ContentItem(
            workspace_id=run.workspace_id,
            owner_user_id=run.owner_user_id,
            title=prompt[:80],
            type="article",
            status="draft",
        )
        db.add(item)
        db.flush()
        version = models.ContentVersion(
            content_item_id=item.id,
            version_no=1,
            source_type="agent",
            model_id=generated["modelCode"],
            title=item.title,
            body=generated["text"],
            summary=f"Agent 工具 {tool_id} 生成",
            prompt_template_version=ai_gateway.PROMPT_TEMPLATE_VERSION,
        )
        db.add(version)
        db.flush()
        item.current_version_id = version.id
        return {
            "contentItemId": item.id,
            "versionId": version.id,
            "chars": len(generated["text"]),
            "modelCode": generated["modelCode"],
        }

    if tool_id == "create_publish_preview":
        return _build_publish_preview(db, run, args)
    # 兜底不再返回伪成功：未实现的工具必须让本轮运行明确失败。
    raise AgentRuntimeError(f"工具未在服务端实现：{tool_id}")


def _build_publish_preview(db: Session, run: models.AgentRun, args: dict) -> dict:
    """基于真实稿件与账号能力计算发布预览，不返回固定成功标记。"""
    from ..connectors import base as connectors
    from . import publish_state

    item = db.scalar(
        select(models.ContentItem)
        .where(models.ContentItem.workspace_id == run.workspace_id)
        .order_by(models.ContentItem.updated_at.desc())
        .limit(1)
    )
    version = db.get(models.ContentVersion, item.current_version_id) if item and item.current_version_id else None
    if not version:
        return {
            "previewCreated": False,
            "code": "missing_content_version",
            "message": "工作区尚无稿件版本，无法生成发布预览",
        }
    accounts = db.scalars(
        select(models.ConnectedAccount).where(
            models.ConnectedAccount.workspace_id == run.workspace_id
        )
    ).all()
    platforms = []
    for account in accounts:
        cap = connectors.get_capability(account.platform)
        issues = connectors.validate_draft(
            account.platform, title=version.title, body=version.body, asset_count=0
        )
        ok, reason = publish_state.can_submit_auto_publish(
            account_status=account.status,
            capability_confirmed=cap.confirmed,
            capability_supports_publish=cap.publish,
            auto_publish_enabled=account.auto_publish_enabled,
            has_review=False,  # 预览阶段尚未人工确认
        )
        platforms.append(
            {
                "accountId": account.id,
                "platform": account.platform,
                "platformLabel": cap.label,
                "authSource": account.auth_source or "none",
                "canAutoPublish": ok,
                "blockReason": reason,
                "issues": issues,
                "requiresConfirmation": True,
            }
        )
    return {
        "previewCreated": bool(platforms),
        "contentVersionId": version.id,
        "title": version.title,
        "bodyChars": len(version.body or ""),
        "platforms": platforms,
        "code": None if platforms else "no_platform_account",
        "message": "预览基于真实稿件与账号状态生成；自动发布仍需逐篇人工审核"
        if platforms
        else "工作区尚未绑定平台账号，无法生成逐平台预览",
        "requestedGoal": str(args.get("goal") or "")[:120],
    }



def _digest(payload: dict) -> str:
    import hashlib
    import json

    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()[:32]


async def start_run(db: Session, run: models.AgentRun) -> None:
    run.status = "running"
    run.started_at = _utcnow()
    allowed_tools = resolve_run_permissions(db, run)
    run.permission_snapshot = {**run.permission_snapshot, "resolvedToolIds": allowed_tools}
    emit(db, run, "run.started", {"goal": run.goal, "allowedTools": allowed_tools})

    steps, planner, planner_note = await plan_steps_with_llm(
        db, run.owner_user_id, run.goal, allowed_tools
    )
    emit(
        db,
        run,
        "plan.created",
        {"steps": [s["tool"] for s in steps], "planner": planner, "note": planner_note},
    )

    for step in steps:
        if agent_policy.budget_exceeded(run.rounds_used, run.budget_limit or {}):
            run.status = "stopped_budget"
            emit(db, run, "run.stopped", {"reason": "budget_exceeded"})
            return
        run.rounds_used += 1
        try:
            outcome = await execute_step(db, run, step, allowed_tools)
        except AgentRuntimeError as exc:
            run.status = "failed"
            run.error_code = "tool_failed"
            emit(db, run, "run.failed", {"message": str(exc)})
            return
        if outcome["status"] == "awaiting_approval":
            return

    run.status = "succeeded"
    run.completed_at = _utcnow()
    emit(db, run, "run.completed", {"rounds": run.rounds_used})


def decide_approval(
    db: Session, approval: models.AgentApproval, run: models.AgentRun, decision: str, user_id: str
) -> dict:
    if approval.status != "pending":
        raise AgentRuntimeError("该审批已处理")
    if approval.expires_at:
        # SQLite 会把带时区的 datetime 读回 naive，比较前统一补 UTC（否则 decide 接口 500）
        expires = approval.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < _utcnow():
            approval.status = "expired"
            db.flush()
            raise AgentRuntimeError("审批已过期，请重新发起运行")

    approval.decided_by = user_id
    approval.decided_at = _utcnow()
    approval.status = "approved" if decision == "approve" else "rejected"
    emit(
        db,
        run,
        "approval.decided",
        {"approvalId": approval.id, "decision": approval.status},
    )

    if approval.status == "rejected":
        run.status = "stopped_by_user"
        run.completed_at = _utcnow()
        return {"status": run.status}

    # 批准后重新校验工具授权，避免审批期间权限被回收
    allowed_tools = resolve_run_permissions(db, run)
    if approval.risk_level == agent_policy.FORBIDDEN:
        run.status = "failed"
        run.error_code = "forbidden_tool"
        return {"status": run.status}

    call = db.get(models.AgentToolCall, approval.tool_call_id) if approval.tool_call_id else None
    if call:
        call.status = "succeeded"  # 仅表示「审批动作」成功，不代表外部动作已执行
        call.completed_at = _utcnow()
        call.result_ref = {
            "approved": True,
            "actionExecuted": False,
            "note": "外部副作用适配器未配置，仅记录审批结果，未产生平台动作",
        }
        emit(
            db,
            run,
            "approval.no_external_effect",
            {"toolCallId": call.id, "reason": "connector_not_configured"},
        )
    run.status = "succeeded"
    run.completed_at = _utcnow()
    emit(db, run, "run.resumed", {"approvedTools": allowed_tools})
    return {"status": run.status}