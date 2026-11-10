"""Agent 风险分级与工具授权合并测试（对应首期验收 16）。"""

import pytest

from app.services import agent_policy as ap


def test_forbidden_tools_never_resolved():
    allowed = ap.resolve_allowed_tools(
        skill_tools=["read_content", "run_shell", "raw_sql"],
        workspace_granted=["read_content", "run_shell", "raw_sql"],
    )
    assert allowed == ["read_content"]


def test_intersection_with_workspace_grant():
    allowed = ap.resolve_allowed_tools(
        skill_tools=["read_content", "create_outline", "generate_article"],
        workspace_granted=["read_content", "generate_article"],
    )
    assert set(allowed) == {"read_content", "generate_article"}


def test_policy_risk_cap_excludes_side_effects():
    allowed = ap.resolve_allowed_tools(
        skill_tools=["generate_article", "publish_content"],
        workspace_granted=["generate_article", "publish_content"],
        policy_max_risk=ap.REVERSIBLE_WRITE,
    )
    assert allowed == ["generate_article"]


def test_external_side_effect_requires_approval():
    assert ap.requires_approval("publish_content")
    assert not ap.requires_approval("generate_article")
    assert not ap.requires_approval("read_content")


def test_unknown_tool_is_forbidden():
    assert ap.tool_risk("delete_everything") == ap.FORBIDDEN


def test_assert_tool_callable_blocks_unlisted():
    with pytest.raises(ap.ToolNotAllowed):
        ap.assert_tool_callable("publish_content", ["generate_article"])


def test_assert_tool_callable_blocks_forbidden():
    with pytest.raises(ap.ToolNotAllowed):
        ap.assert_tool_callable("run_shell", ["run_shell"])


def test_budget_exceeded():
    assert ap.budget_exceeded(8, {"maxRounds": 8})
    assert not ap.budget_exceeded(3, {"maxRounds": 8})
