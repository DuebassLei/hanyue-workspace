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
