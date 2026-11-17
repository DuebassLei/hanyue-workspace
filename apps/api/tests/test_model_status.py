"""顶栏模型状态条：文本 / 生图两条链路的状态口径必须与真实解析一致。

状态只做配置链路检查，不发真实请求：
- ok 绿：模型启用、连接有凭据、最近一次连接测试通过；
- blocked 红：没可用模型 / 连接测试失败 / 生图连接缺地址或密钥；
- unchecked / none 灰：链路齐但没测过、默认模型失效自动降级、或干脆没配。
"""

from __future__ import annotations

import pytest

PREFIX = "/api/v1"


def _headers(client, subject):
    res = client.post(f"{PREFIX}/auth/login", json={"subject": subject, "displayName": subject})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _connection(client, headers, name):
    res = client.post(
        f"{PREFIX}/models/connections",
        json={
            "displayName": name,
            "baseUrl": "https://api.example.com/v1",
            "apiKey": "sk-test-key",
            "providerType": "openai_compatible",
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _catalog(client, headers, connection_id, code, capabilities=None):
    res = client.post(
        f"{PREFIX}/models/catalog",
        json={
            "connectionId": connection_id,
            "modelCode": code,
            "capabilities": capabilities or ["text.generate", "text.stream"],
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


def _status(client, headers) -> dict:
    res = client.get(f"{PREFIX}/models/status", headers=headers)
    assert res.status_code == 200, res.text
    return {item["kind"]: item for item in res.json()}


def _mark_connected(connection_id: str, status: str = "connected") -> None:
    """直接落库模拟「已经测过」；真实测试接口要打外网，单测里不碰。"""
    from app import models
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        db.get(models.ModelConnection, connection_id).status = status
        db.commit()


@pytest.fixture(autouse=True)
def no_env_image_provider(monkeypatch):
    # 开发者本机的环境变量供应商不能影响断言
    for key in ("IMAGE_PROVIDER_BASE_URL", "IMAGE_PROVIDER_API_KEY", "IMAGE_PROVIDER_MODEL"):
        monkeypatch.delenv(key, raising=False)


def test_empty_account_reports_text_missing_and_image_absent(client):
    headers = _headers(client, "status-empty")
    status = _status(client, headers)
    assert status["text"]["state"] == "blocked"
    assert status["text"]["modelCode"] is None
    assert status["image"]["state"] == "none"
    assert status["image"]["message"]


def test_text_model_states_follow_connection_probe(client):
    headers = _headers(client, "status-text")
    conn = _connection(client, headers, "主连接")
    entry = _catalog(client, headers, conn, "model-a")
    client.patch(
        f"{PREFIX}/preferences", json={"defaultTextModelId": entry["id"]}, headers=headers
    )

    # 链路齐但没测过：灰色，不冒充可用
    assert _status(client, headers)["text"]["state"] == "unchecked"

    _mark_connected(conn)
    ok = _status(client, headers)["text"]
    assert ok["state"] == "ok", ok
    assert ok["modelCode"] == "model-a"
    assert ok["connectionName"] == "主连接"
    assert ok["connectionStatus"] == "connected"

    _mark_connected(conn, "unauthorized")
    blocked = _status(client, headers)["text"]
    assert blocked["state"] == "blocked"
    assert "凭据" in blocked["message"]


def test_default_model_goes_first_and_invalid_default_falls_back(client):
    headers = _headers(client, "status-default")
    conn_a = _connection(client, headers, "连接A")
    conn_b = _connection(client, headers, "连接B")
    _catalog(client, headers, conn_a, "model-a")
    entry_b = _catalog(client, headers, conn_b, "model-b")
    _mark_connected(conn_a)
    _mark_connected(conn_b)

    client.patch(
        f"{PREFIX}/preferences",
        json={"defaultTextModelId": entry_b["id"]},
        headers=headers,
    )
    effective = _status(client, headers)["text"]
    assert (effective["modelCode"], effective["state"]) == ("model-b", "ok")

    # 默认被停用：生成会降级到 model-a，状态条必须显示实际会用的那个，且是灰色
    client.patch(f"{PREFIX}/models/catalog/{entry_b['id']}", json={"enabled": False}, headers=headers)
    fallback = _status(client, headers)["text"]
    assert fallback["modelCode"] == "model-a"
    assert fallback["state"] == "unchecked"
    assert "默认模型不可用" in fallback["message"]


def test_without_default_is_gray_even_when_connection_verified(client):
    """没设默认时生成仍会用它，但那不是用户选的模型，不能说「可用」的绿。"""
    headers = _headers(client, "status-nodefault")
    conn = _connection(client, headers, "连接")
    _catalog(client, headers, conn, "model-a")
    _mark_connected(conn)

    row = _status(client, headers)["text"]
    assert row["state"] == "unchecked"
    assert row["modelCode"] == "model-a"
    assert "尚未设置默认模型" in row["message"]

    # 连接测过失败：即便不是默认模型也不能说可用
    _mark_connected(conn, "unauthorized")
    assert _status(client, headers)["text"]["state"] == "blocked"


def test_image_model_states(client):
    headers = _headers(client, "status-image")
    conn = _connection(client, headers, "生图连接")
    _catalog(client, headers, conn, "img-model", capabilities=["image.generate"])

    # 连接齐全但没测过：灰色
    assert _status(client, headers)["image"]["state"] == "unchecked"

    _mark_connected(conn)
    ok = _status(client, headers)["image"]
    assert ok["state"] == "ok"
    assert ok["modelCode"] == "img-model"

    # 清空密钥：生图解析会直接跳过它，条目还在但链路断 → 红
    client.patch(f"{PREFIX}/models/connections/{conn}", json={"apiKey": ""}, headers=headers)
    blocked = _status(client, headers)["image"]
    assert blocked["state"] == "blocked"
    assert blocked["modelCode"] == "img-model"


def test_image_env_provider_is_reported(client, monkeypatch):
    headers = _headers(client, "status-env")
    monkeypatch.setenv("IMAGE_PROVIDER_MODEL", "env-img")
    monkeypatch.setenv("IMAGE_PROVIDER_BASE_URL", "https://img.example.com/v1")

    incomplete = _status(client, headers)["image"]
    assert incomplete["state"] == "blocked"
    assert incomplete["modelCode"] == "env-img"

    monkeypatch.setenv("IMAGE_PROVIDER_API_KEY", "sk-env")
    ready = _status(client, headers)["image"]
    assert ready["state"] == "ok"
    assert ready["connectionName"] == "环境变量"


def test_status_is_scoped_to_current_user(client):
    owner = _headers(client, "status-owner")
    other = _headers(client, "status-other")
    conn = _connection(client, other, "别人的连接")
    _catalog(client, other, conn, "foreign-model")
    _mark_connected(conn)

    assert _status(client, owner)["text"]["modelCode"] is None
    assert _status(client, other)["text"]["modelCode"] == "foreign-model"
