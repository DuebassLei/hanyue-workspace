"""AI 网关连接解析与降级测试。

覆盖两个真实踩过的坑：
1. 代理环境下把域名固定成 IP，导致 TLS 按 IP 校验证书 → 所有连接误报「不可达」。
2. 前端不传 modelId 时后端只认显式模型，配好的连接也用不上。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _headers(client, subject: str) -> dict:
    res = client.post(
        "/api/v1/auth/login", json={"subject": subject, "displayName": subject}
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _create_connection(client, headers, *, base_url: str) -> str:
    res = client.post(
        "/api/v1/models/connections",
        json={
            "displayName": base_url,
            "baseUrl": base_url,
            "apiKey": "sk-test-key",
            "providerType": "openai_compatible",
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _sse_events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if name and data:
            events.append((name, json.loads(data)))
    return events


def test_generation_without_model_id_auto_resolves_connection(client):
    """不传 modelId 时按候选链自动选模型，不能直接报「未配置可用模型」。"""
    from app.services import ai_gateway

    headers = _headers(client, "auto-model")
    _create_connection(client, headers, base_url="https://api.example.com/v1")

    with client.stream(
        "POST",
        "/api/v1/ai/text-generations",
        json={"taskType": "summarize", "prompt": "说点什么"},
        headers=headers,
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    events = dict(_sse_events(body))
    started = events["generation.started"]
    assert started["provider"] == "openai_compatible", "应解析出已配置的连接"
    assert started["modelCode"] == "unconfigured", "连接存在但模型未登记时的占位值"

    # 网络不可达属于预期：关键是没有在解析阶段就判定「未配置」
    error = events.get("generation.error")
    assert error is not None
    assert error["code"] != "provider_unavailable" or "无法连接" not in error["message"]


def test_generation_falls_back_to_second_candidate(client):
    """首个候选一个字都没吐就失败时，应静默切到下一个候选，不把错误抛给前端。"""
    from app.services import ai_gateway
    from app import models

    headers = _headers(client, "fallback-user")
    first = _create_connection(client, headers, base_url="https://api.broken.example/v1")
    second = _create_connection(client, headers, base_url="https://api.good.example/v1")

    real_stream = ai_gateway.stream_text

    async def fake_stream(db, *, connection, model_code, **kwargs):
        if connection is not None and connection.base_url.startswith("https://api.broken"):
            yield {
                "event": "generation.error",
                "data": {
                    "code": "provider_unavailable",
                    "message": "订阅已过期",
                    "retryable": False,
                    "provider": "openai_compatible",
                },
            }
            return
        yield {
            "event": "generation.delta",
            "data": {"text": "回退成功", "provider": "openai_compatible"},
        }
        yield {"event": "generation.done", "data": {"provider": "openai_compatible"}}

    original = ai_gateway.stream_text
    ai_gateway.stream_text = fake_stream
    try:
        with client.stream(
            "POST",
            "/api/v1/ai/text-generations",
            json={"taskType": "summarize", "prompt": "说点什么"},
            headers=headers,
        ) as response:
            body = "".join(response.iter_text())
    finally:
        ai_gateway.stream_text = original

    names = [name for name, _ in _sse_events(body)]
    assert "generation.delta" in names, "应回退到第二个候选并产出文本"
    assert "generation.error" not in names, "首个候选失败但未吐字，不应把错误暴露给用户"
    del first, second, real_stream


def test_article_style_cannot_override_output_contract():
    from types import SimpleNamespace

    from app.services import ai_article

    skill = SimpleNamespace(
        manifest={
            "promptTemplate": "只输出普通文本，不要 JSON，也不要生成图片提示词。",
            "tone": "轻松口语化",
            "styleRules": ["多用短句"],
        }
    )

    system_prompt = ai_article.build_system_prompt(skill)

    assert "严格只输出一个 JSON 对象" in system_prompt
    for field in ("title", "markdown", "summary", "coverPrompt", "inlinePrompts"):
        assert field in system_prompt
    assert "{{imageN}}" in system_prompt
    assert "轻松口语化" in system_prompt
    assert "多用短句" in system_prompt
