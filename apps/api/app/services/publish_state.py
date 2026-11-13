"""发布任务状态机（设计文档 3.3 / 7.4 / 11.2）。

关键约束：
- 超时/未知结果进入 needs_attention，必须先核实远端，不能盲目重发。
- 未授权或平台能力未确认时不能提交自动发布。
"""

from __future__ import annotations

PENDING_REVIEW = "pending_review"
APPROVED = "approved"
QUEUED = "queued"
PUBLISHING = "publishing"
SUCCEEDED = "succeeded"
FAILED = "failed"
NEEDS_ATTENTION = "needs_attention"
CANCELLED = "cancelled"

ALL_STATUSES = {
    PENDING_REVIEW,
    APPROVED,
    QUEUED,
    PUBLISHING,
    SUCCEEDED,
    FAILED,
    NEEDS_ATTENTION,
    CANCELLED,
}

TERMINAL = {SUCCEEDED, CANCELLED}

_TRANSITIONS: dict[str, set[str]] = {
    PENDING_REVIEW: {APPROVED, CANCELLED},
    # APPROVED → PUBLISHING：Worker 领取任务后立即标记执行中（真实发生在 job_runner）
    APPROVED: {QUEUED, PUBLISHING, CANCELLED, NEEDS_ATTENTION},
    # QUEUED → APPROVED：排期到点自动提升（_promote_due_jobs）
    QUEUED: {APPROVED, PUBLISHING, CANCELLED, FAILED, NEEDS_ATTENTION},
    PUBLISHING: {SUCCEEDED, FAILED, NEEDS_ATTENTION, CANCELLED},
    NEEDS_ATTENTION: {SUCCEEDED, FAILED, QUEUED, CANCELLED},
    FAILED: {QUEUED, CANCELLED, NEEDS_ATTENTION},
    SUCCEEDED: set(),
    CANCELLED: set(),
}

# 平台返回的错误分类 → 允许的下一状态
_RETRYABLE_ERRORS = {"timeout", "network", "rate_limited", "server_error"}
_FATAL_ERRORS = {"auth_expired", "forbidden", "validation_failed", "content_rejected"}


class TransitionError(ValueError):
    pass


def can_transition(current: str, target: str) -> bool:
    return target in _TRANSITIONS.get(current, set())


# 手动回填：用户在平台侧已自行发布完成，可从这些状态直接记为成功。
# 仅用于 record-manual-result，不放开自动流程的状态流转。
MANUAL_RECORDABLE = {APPROVED, QUEUED, NEEDS_ATTENTION, FAILED, PUBLISHING}


def can_record_manual_result(current: str) -> bool:
    return current in MANUAL_RECORDABLE


def assert_transition(current: str, target: str) -> str:
    if not can_transition(current, target):
        raise TransitionError(f"非法状态流转：{current} → {target}")
    return target


# ---------------- 状态流转历史（发布中心重构 P1）----------------
# status_history 每条：{"status", "at", "actor", "note"}
# actor: "user"（界面上的人工操作）| "system"（队列 / 调度等自动流转）


def history_entry(status: str, *, actor: str = "system", note: str | None = None) -> dict:
    from datetime import datetime, timezone

    return {
        "status": status,
        "at": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "note": note or "",
    }


def initial_history(status: str, *, actor: str = "user", note: str | None = None) -> list[dict]:
    """任务创建时的第一条历史记录。"""
    return [history_entry(status, actor=actor, note=note)]


def record_history(job, status: str, *, actor: str = "system", note: str | None = None) -> None:
    """只追加历史，不做状态机校验。

    用于两类场景：一是流转本身不经过 assert_transition 的既有策略路径
    （如手动回填 MANUAL_RECORDABLE）；二是状态不变但需要留痕的操作
    （如改期、修改内容）。必须整体赋值新列表，JSON 列的原地修改不会被 SQLAlchemy 追踪。
    """
    job.status_history = [*(job.status_history or []), history_entry(status, actor=actor, note=note)]


def apply_transition(job, target: str, *, actor: str = "system", note: str | None = None) -> str:
    """校验流转合法性、写状态并追加历史；非法流转抛 TransitionError。"""
    assert_transition(job.status, target)
    job.status = target
    record_history(job, target, actor=actor, note=note)
    return target


def next_after_error(current: str, error_code: str, remote_id: str | None) -> str:
    """根据错误类型决定下一步状态，避免重复发布。"""
    code = (error_code or "").lower()
    if remote_id:
        # 远端已产生内容，绝不再排队重发
        return SUCCEEDED
    if code in _FATAL_ERRORS:
        return FAILED
    if code == "timeout":
        return NEEDS_ATTENTION
    if code in _RETRYABLE_ERRORS:
        return QUEUED
    return NEEDS_ATTENTION


def retry_allowed(current: str, error_code: str | None, remote_id: str | None) -> bool:
    if remote_id:
        return False
    if current in {PENDING_REVIEW, PUBLISHING}:
        return False
    code = (error_code or "").lower()
    return code not in _FATAL_ERRORS


def can_submit_auto_publish(
    *,
    account_status: str,
    capability_confirmed: bool,
    capability_supports_publish: bool,
    auto_publish_enabled: bool,
    has_review: bool,
) -> tuple[bool, str]:
    if not capability_confirmed:
        return False, "平台自动发布能力尚未核实，暂不可自动发布"
    if not capability_supports_publish:
        return False, "该平台当前接口不支持自动发布"
    if account_status != "connected":
        return False, "账号授权无效，请重新授权"
    if not auto_publish_enabled:
        return False, "该账号未开启自动发布"
    if not has_review:
        return False, "发布前需要完成人工审核"
    return True, ""
