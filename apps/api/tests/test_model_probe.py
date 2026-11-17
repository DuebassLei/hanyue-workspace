"""模型管理配置弹窗的两个探测接口：拉供应商模型列表、按表单测试模型。

这两个接口都只读：不落库、不改连接状态，失败也必须如实返回原因。
"""

import httpx


def _login(client, subject):
    res = client.post("/api/v1/auth/login", json={"subject": subject, "displayName": subject})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _patch_ssrf(monkeypatch, handler):
    """探测走 ssrf.safe_request：测试里替换掉真实出网，但保留调用形状。"""
    from app.services import provider_probe

    monkeypatch.setattr(provider_probe.ssrf, "assert_safe_url", lambda url: url.rstrip("/"))
    monkeypatch.setattr(provider_probe.ssrf, "safe_request", handler)
    return provider_probe


def _response(method, url, status=200, payload=None):
    return httpx.Response(status, json=payload or {}, request=httpx.Request(method, url))


def test_probe_models_returns_sorted_list(client, monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url))
        return _response(
            method,
            url,
            payload={"data": [{"id": "b-model", "owned_by": "x"}, {"id": "a-model"}]},
        )

    _patch_ssrf(monkeypatch, fake_request)
    headers = _login(client, "probe-list-user")
    res = client.post(
        "/api/v1/models/probe/models",
        json={"baseUrl": "https://api.example.com/v1", "apiKey": "sk-test"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert [row["id"] for row in body["models"]] == ["a-model", "b-model"]
    assert body["modelCount"] == 2
    assert body["status"] == "connected"
    assert calls == [("GET", "https://api.example.com/v1/models")]


def test_probe_models_falls_back_to_stored_key(client, monkeypatch):
    seen = {}

    def fake_request(method, url, **kwargs):
        seen.update(kwargs.get("headers") or {})
        return _response(method, url, payload={"data": [{"id": "m1"}]})

    _patch_ssrf(monkeypatch, fake_request)
    headers = _login(client, "probe-stored-key")
    conn = client.post(
        "/api/v1/models/connections",
        json={"displayName": "已存密钥", "baseUrl": "https://api.example.com/v1", "apiKey": "sk-saved"},
        headers=headers,
    ).json()
    # 编辑弹窗里密钥留空：不能因为没重新粘贴就判定缺少凭据
    res = client.post(
        "/api/v1/models/probe/models",
        json={"connectionId": conn["id"], "apiKey": ""},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["modelCount"] == 1
    assert seen.get("Authorization") == "Bearer sk-saved"


def test_probe_models_rejects_empty_base_url(client):
    headers = _login(client, "probe-empty-url")
    res = client.post(
        "/api/v1/models/probe/models", json={"apiKey": "sk-x"}, headers=headers
    )
    assert res.status_code == 400
    assert res.json()["code"] == "missing_base_url"


def test_probe_text_model_really_calls_chat_completions(client, monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs.get("json_body")))
        if url.endswith("/models"):
            return _response(method, url, payload={"data": [{"id": "gpt-4o-mini"}]})
        return _response(method, url, payload={"choices": [{"message": {"content": "p"}}]})

    _patch_ssrf(monkeypatch, fake_request)
    headers = _login(client, "probe-text-user")
    res = client.post(
        "/api/v1/models/probe/test",
        json={
            "baseUrl": "https://api.example.com/v1",
            "apiKey": "sk-test",
            "modelCode": "gpt-4o-mini",
            "kind": "text",
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["verified"] is True
    assert body["modelVerified"] is True
    assert body["status"] == "connected"
    # 真的发了一次对话请求，且只要 1 token，不偷偷烧额度
    chat = [call for call in calls if call[0] == "POST"]
    assert chat and chat[0][1].endswith("/chat/completions")
    assert chat[0][2]["model"] == "gpt-4o-mini"
    assert chat[0][2]["max_tokens"] == 1


def test_probe_text_model_reports_unknown_model_code(client, monkeypatch):
    def fake_request(method, url, **kwargs):
        if url.endswith("/models"):
            return _response(method, url, payload={"data": [{"id": "gpt-4o-mini"}]})
        return _response(
            method,
            url,
            404,
            payload={"error": {"message": "The model `nope` does not exist"}},
        )

    _patch_ssrf(monkeypatch, fake_request)
    headers = _login(client, "probe-bad-model")
    body = client.post(
        "/api/v1/models/probe/test",
        json={
            "baseUrl": "https://api.example.com/v1",
            "apiKey": "sk-test",
            "modelCode": "nope",
            "kind": "text",
        },
        headers=headers,
    ).json()
    assert body["verified"] is False
    assert body["status"] == "model_not_found"
    # 详情里的状态码要来自这次对话请求，不能沿用 /models 那次 200
    assert body["httpStatus"] == 404
    assert "nope" in body["message"]


def test_probe_image_model_never_spends_a_generation(client, monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(method)
        return _response(method, url, payload={"data": [{"id": "gpt-image-1"}]})

    _patch_ssrf(monkeypatch, fake_request)
    headers = _login(client, "probe-image-user")
    body = client.post(
        "/api/v1/models/probe/test",
        json={
            "baseUrl": "https://api.example.com/v1",
            "apiKey": "sk-test",
            "modelCode": "gpt-image-1",
            "kind": "image",
        },
        headers=headers,
    ).json()
    assert body["verified"] is True
    assert body["modelVerified"] is True
    # 生图模型只核对列表，绝不发起付费出图
    assert calls == ["GET"]


def test_probe_test_reports_missing_credential_without_network(client, monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(method)
        return _response(method, url, payload={})

    _patch_ssrf(monkeypatch, fake_request)
    headers = _login(client, "probe-nokey")
    body = client.post(
        "/api/v1/models/probe/test",
        json={"baseUrl": "https://api.example.com/v1", "modelCode": "gpt-4o-mini"},
        headers=headers,
    ).json()
    assert body["status"] == "missing_credential"
    assert body["verified"] is False
    assert calls == []
