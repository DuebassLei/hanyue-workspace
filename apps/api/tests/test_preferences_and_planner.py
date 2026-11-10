"""服务端偏好持久化 + Agent LLM 规划（含诚实降级）的回归测试。"""

from __future__ import annotations

import asyncio

import pytest

PREFIX = "/api/v1"


@pytest.fixture
def runtime():
    """延迟导入：收集阶段导入 app.services 会让 core.db 提前绑定默认开发库。"""
    from app.services import agent_runtime

    return agent_runtime


def _login(client, subject):
    res = client.post(f"{PREFIX}/auth/login", json={"subject": subject})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


# ---------------- 偏好 ----------------

def test_preferences_default_persist_validate_and_isolate(client):
    headers = _login(client, "pref-owner")
    first = client.get(f"{PREFIX}/preferences", headers=headers)
    assert first.status_code == 200
    assert first.json() == {"theme": "system", "source": "default"}

    updated = client.patch(f"{PREFIX}/preferences", json={"theme": "dark"}, headers=headers)
    assert updated.status_code == 200
    assert updated.json() == {"theme": "dark", "source": "server"}
    reread = client.get(f"{PREFIX}/preferences", headers=headers)
    assert reread.json()["theme"] == "dark"

    bad = client.patch(f"{PREFIX}/preferences", json={"theme": "neon"}, headers=headers)
    assert bad.status_code == 422

    other = _login(client, "pref-other")
    assert client.get(f"{PREFIX}/preferences", headers=other).json() == {
        "theme": "system",
        "source": "default",
    }


# ---------------- LLM 规划 ----------------

def test_llm_steps_are_whitelisted_and_bounded(runtime):
    raw = [
        {"tool": "run_shell", "args": {"cmd": "rm -rf /"}},
        {"tool": "generate_article", "args": {"prompt": "长" * 900}},
        {"tool": "create_outline", "args": {}},
        {"tool": "unknown_tool", "args": {}},
    ]
    steps = runtime.sanitize_llm_steps(
        raw, ["generate_article", "create_outline"], "写一篇关于效率的文章"
    )
    assert [s["tool"] for s in steps] == ["generate_article", "create_outline"]
    assert len(steps[0]["args"]["prompt"]) == 500, "字符串参数必须限长"
    assert steps[1]["args"]["prompt"], "工具必需参数由服务端补齐"


def test_planner_falls_back_when_connection_not_verified(monkeypatch, runtime):
    class FakeConn:
        status = "untested"

    monkeypatch.setattr(
        runtime.ai_gateway,
        "resolve_connections",
        lambda db, uid: [(FakeConn(), "some-model")],
    )
    steps, planner, note = asyncio.run(
        runtime.plan_steps_with_llm(None, "u1", "写篇文章", ["create_outline"])
    )
    assert planner == "deterministic"
    assert "连通性" in note
    assert steps[0]["tool"] == "create_outline"


def test_planner_uses_llm_and_drops_forbidden_tools(monkeypatch, runtime):
    class FakeConn:
        status = "connected"

    monkeypatch.setattr(
        runtime.ai_gateway,
        "resolve_connections",
        lambda db, uid: [(FakeConn(), "planner-model")],
    )

    async def fake_generate_text(db, **kwargs):
        return {
            "text": '```json\n[{"tool":"create_outline","args":{}},{"tool":"run_shell","args":{}}]\n```',
            "modelCode": "planner-model",
            "provider": "openai_compatible",
            "errorCode": None,
        }

    monkeypatch.setattr(runtime.ai_gateway, "generate_text", fake_generate_text)
    steps, planner, note = asyncio.run(
        runtime.plan_steps_with_llm(None, "u1", "写篇文章", ["create_outline"])
    )
    assert planner == "llm"
    assert [s["tool"] for s in steps] == ["create_outline"]
    assert "planner-model" in note


def test_planner_falls_back_on_provider_error(monkeypatch, runtime):
    class FakeConn:
        status = "connected"

    monkeypatch.setattr(
        runtime.ai_gateway,
        "resolve_connections",
        lambda db, uid: [(FakeConn(), "m")],
    )

    async def failing(db, **kwargs):
        return {"text": "", "modelCode": "m", "provider": "openai_compatible", "errorCode": "provider_unavailable"}

    monkeypatch.setattr(runtime.ai_gateway, "generate_text", failing)
    steps, planner, note = asyncio.run(
        runtime.plan_steps_with_llm(None, "u1", "写篇文章", ["create_outline"])
    )
    assert planner == "deterministic"
    assert "不可用" in note
    assert steps[0]["tool"] == "create_outline"
