"""发布状态机与幂等策略测试（对应首期验收 5/6）。"""

import pytest

from app.services import publish_state as ps


def test_review_required_before_queue():
    assert ps.can_transition(ps.PENDING_REVIEW, ps.APPROVED)
    assert not ps.can_transition(ps.PENDING_REVIEW, ps.QUEUED)


def test_succeeded_is_terminal():
    assert not ps.can_transition(ps.SUCCEEDED, ps.QUEUED)
    assert not ps.can_transition(ps.SUCCEEDED, ps.CANCELLED)


def test_timeout_goes_to_needs_attention_not_requeue():
    assert ps.next_after_error(ps.PUBLISHING, "timeout", None) == ps.NEEDS_ATTENTION


def test_timeout_with_remote_id_marks_succeeded():
    assert ps.next_after_error(ps.PUBLISHING, "timeout", "remote-123") == ps.SUCCEEDED


def test_auth_error_is_fatal():
    assert ps.next_after_error(ps.QUEUED, "auth_expired", None) == ps.FAILED


def test_rate_limit_requeues():
    assert ps.next_after_error(ps.QUEUED, "rate_limited", None) == ps.QUEUED


def test_retry_blocked_when_remote_id_present():
    assert not ps.retry_allowed(ps.NEEDS_ATTENTION, "timeout", "remote-1")
    assert ps.retry_allowed(ps.NEEDS_ATTENTION, "timeout", None)


def test_retry_blocked_for_fatal_error():
    assert not ps.retry_allowed(ps.FAILED, "content_rejected", None)


def test_illegal_transition_raises():
    with pytest.raises(ps.TransitionError):
        ps.assert_transition(ps.PENDING_REVIEW, ps.SUCCEEDED)


@pytest.mark.parametrize(
    "kwargs,expected_ok",
    [
        ({"capability_confirmed": False}, False),
        ({"capability_supports_publish": False}, False),
        ({"account_status": "pending_authorization"}, False),
        ({"auto_publish_enabled": False}, False),
        ({"has_review": False}, False),
        ({}, True),
    ],
)
def test_auto_publish_gates(kwargs, expected_ok):
    base = {
        "account_status": "connected",
        "capability_confirmed": True,
        "capability_supports_publish": True,
        "auto_publish_enabled": True,
        "has_review": True,
    }
    base.update(kwargs)
    ok, reason = ps.can_submit_auto_publish(**base)
    assert ok is expected_ok
    if not ok:
        assert reason


# ---------------- 状态流转历史（发布中心重构 P1）----------------


def test_promote_and_worker_transitions_are_legal():
    """排期到点提升（queued→approved）与 Worker 领取（approved→publishing）是真实发生的流转。"""
    assert ps.can_transition(ps.QUEUED, ps.APPROVED)
    assert ps.can_transition(ps.APPROVED, ps.PUBLISHING)


def test_history_entry_shape():
    entry = ps.history_entry(ps.QUEUED, actor="user", note="创建")
    assert entry["status"] == ps.QUEUED
    assert entry["actor"] == "user"
    assert entry["note"] == "创建"
    assert entry["at"]  # ISO 时间戳
    assert ps.initial_history(ps.APPROVED, actor="user")[0]["status"] == ps.APPROVED


class _FakeJob:
    """最小 duck-type：apply_transition/record_history 只依赖 status/status_history。"""

    def __init__(self, status):
        self.status = status
        self.status_history = []


def test_apply_transition_writes_status_and_history():
    job = _FakeJob(ps.PENDING_REVIEW)
    assert ps.apply_transition(job, ps.APPROVED, actor="user", note="审核通过") == ps.APPROVED
    assert job.status == ps.APPROVED
    assert len(job.status_history) == 1
    assert job.status_history[0]["status"] == ps.APPROVED
    assert job.status_history[0]["actor"] == "user"


def test_apply_transition_rejects_illegal_without_history():
    import pytest

    job = _FakeJob(ps.SUCCEEDED)
    with pytest.raises(ps.TransitionError):
        ps.apply_transition(job, ps.QUEUED, actor="user")
    assert job.status == ps.SUCCEEDED
    assert job.status_history == []


def test_record_history_appends_without_validation():
    """手动回填等策略路径允许直接写终态，但必须留痕。"""
    job = _FakeJob(ps.FAILED)
    ps.record_history(job, ps.SUCCEEDED, actor="user", note="手动回填")
    assert job.status == ps.FAILED  # 只记历史，不改状态
    assert job.status_history[0]["note"] == "手动回填"
