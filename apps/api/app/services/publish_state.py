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
    APPROVED: {QUEUED, CANCELLED, NEEDS_ATTENTION},
    QUEUED: {PUBLISHING, CANCELLED, FAILED, NEEDS_ATTENTION},
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
