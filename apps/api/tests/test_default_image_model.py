"""全局默认生图模型：可切换、优先于环境变量、失效降级、越权拒绝。

设计共识（与默认文本模型同口径）：
- 显式设置的默认排最前，优先级高于 IMAGE_PROVIDER_* 环境变量供应商；
- 只允许设本人、启用、登记了 image.generate 且凭据齐全的模型；
- 默认失效只忽略不清除偏好，出图按环境变量 / 第一个可用连接降级并说明原因；
- 服务商列表把默认项排第一，图片生成页默认选中项与「全局默认」保持一致。
"""

from __future__ import annotations

import pytest

PREFIX = "/api/v1"


def _headers(client, subject):
    res = client.post(f"{PREFIX}/auth/login", json={"subject": subject, "displayName": subject})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _connection(client, headers, name, api_key="sk-test-key"):
    res = client.post(
        f"{PREFIX}/models/connections",
        json={
            "displayName": name,
            "baseUrl": "https://api.example.com/v1",
            "apiKey": api_key,
            "providerType": "openai_compatible",
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _catalog(client, headers, connection_id, code, capabilities):
    res = client.post(
        f"{PREFIX}/models/catalog",
        json={"connectionId": connection_id, "modelCode": code, "capabilities": capabilities},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


def _mark_connected(connection_id: str, status: str = "connected") -> None:
    from app import models
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        db.get(models.ModelConnection, connection_id).status = status
        db.commit()


def _status(client, headers) -> dict:
    res = client.get(f"{PREFIX}/models/status", headers=headers)
    assert res.status_code == 200, res.text
    return {item["kind"]: item for item in res.json()}


def _providers(client, headers) -> dict:
    res = client.get(f"{PREFIX}/ai/image-provider/status", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


@pytest.fixture(autouse=True)
def no_env_image_provider(monkeypatch):
    for key in ("IMAGE_PROVIDER_BASE_URL", "IMAGE_PROVIDER_API_KEY", "IMAGE_PROVIDER_MODEL"):
        monkeypatch.delenv(key, raising=False)


def test_image_size_registration_preserves_other_options_and_validates(client):
    from app import models
    from app.core.db import SessionLocal

    headers = _headers(client, "image-size-registration")
    conn = _connection(client, headers, "生图尺寸")
    model = _catalog(client, headers, conn, "image-test", ["image.generate"])
    assert model["supportedImageSizes"] == {}
    with SessionLocal() as db:
        entry = db.get(models.ModelCatalogEntry, model["id"])
        entry.options = {"supportsSeed": True}
        db.commit()
    endpoint = f"{PREFIX}/models/catalog/{model['id']}"
    saved = client.patch(endpoint, json={"supportedImageSizes": {"1:1": "1024x1024"}}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["supportedImageSizes"] == {"1:1": "1024x1024"}
    with SessionLocal() as db:
        assert db.get(models.ModelCatalogEntry, model["id"]).options["supportsSeed"] is True
    for invalid in ({"16:9": "1536x1024"}, {"1:1": "0x0"}, {"unknown": "1024x1024"}):
        response = client.patch(endpoint, json={"supportedImageSizes": invalid}, headers=headers)
        assert response.status_code == 400, response.text
    capabilities = client.get(f"{PREFIX}/studio/image-capabilities", headers=headers)
    assert capabilities.json()["ratios"] == ["1:1"]
    cleared = client.patch(endpoint, json={"supportedImageSizes": {}}, headers=headers)
    assert cleared.json()["supportedImageSizes"] == {}


def test_switch_default_image_model_takes_effect(client):
    headers = _headers(client, "img-default")
    conn_a = _connection(client, headers, "生图A")
    conn_b = _connection(client, headers, "生图B")
    _catalog(client, headers, conn_a, "img-a", ["image.generate"])
    entry_b = _catalog(client, headers, conn_b, "img-b", ["image.generate"])
    _mark_connected(conn_a)
    _mark_connected(conn_b)

    # 未设默认：按连接顺序用 A
    assert _status(client, headers)["image"]["modelCode"] == "img-a"

    saved = client.patch(
        f"{PREFIX}/preferences",
        json={"defaultImageModelId": entry_b["id"]},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["defaultImageModelId"] == entry_b["id"]
    assert "img-b" in (saved.json()["defaultImageModelLabel"] or "")

    effective = _status(client, headers)["image"]
    assert (effective["modelCode"], effective["state"]) == ("img-b", "ok")

    # 供应商列表把默认项排第一：图片生成页默认选中项随之切换
    providers = _providers(client, headers)["providers"]
    assert providers[0]["modelId"] == entry_b["id"]
    assert providers[0]["isDefault"] is True
    assert _providers(client, headers)["modelCode"] == "img-b"

    cleared = client.patch(
        f"{PREFIX}/preferences", json={"defaultImageModelId": ""}, headers=headers
    )
    assert cleared.status_code == 200
    assert cleared.json()["defaultImageModelId"] is None
    assert _status(client, headers)["image"]["modelCode"] == "img-a"


def test_default_image_model_wins_over_env_provider(client, monkeypatch):
    headers = _headers(client, "img-env-default")
    conn = _connection(client, headers, "生图A")
    entry = _catalog(client, headers, conn, "img-a", ["image.generate"])
    _mark_connected(conn)

    monkeypatch.setenv("IMAGE_PROVIDER_MODEL", "env-img")
    monkeypatch.setenv("IMAGE_PROVIDER_BASE_URL", "https://img.example.com/v1")
    monkeypatch.setenv("IMAGE_PROVIDER_API_KEY", "sk-env")

    # 没设默认时环境变量优先（原有行为）
    assert _status(client, headers)["image"]["modelCode"] == "env-img"

    client.patch(
        f"{PREFIX}/preferences",
        json={"defaultImageModelId": entry["id"]},
        headers=headers,
    )
    # 设了默认就必须真的生效，不能被环境变量压住
    assert _status(client, headers)["image"]["modelCode"] == "img-a"
    assert _providers(client, headers)["modelCode"] == "img-a"


def test_default_image_model_rejects_invalid_targets(client):
    headers = _headers(client, "img-invalid")
    other = _headers(client, "img-invalid-other")
    conn = _connection(client, headers, "生图A")
    text_entry = _catalog(client, headers, conn, "text-a", ["text.generate", "text.stream"])
    image_entry = _catalog(client, headers, conn, "img-a", ["image.generate"])

    foreign_conn = _connection(client, other, "别人的生图")
    foreign_entry = _catalog(client, other, foreign_conn, "img-other", ["image.generate"])

    for model_id in (foreign_entry["id"], text_entry["id"], "not-exist"):
        res = client.patch(
            f"{PREFIX}/preferences",
            json={"defaultImageModelId": model_id},
            headers=headers,
        )
        assert res.status_code == 400, res.text
        assert res.json()["code"] == "model_not_found"

    # 清空密钥后同样不能设为默认：链路不齐的默认只会制造「切了没生效」的错觉
    client.patch(f"{PREFIX}/models/connections/{conn}", json={"apiKey": ""}, headers=headers)
    res = client.patch(
        f"{PREFIX}/preferences",
        json={"defaultImageModelId": image_entry["id"]},
        headers=headers,
    )
    assert res.status_code == 400
    assert "API Key" in res.json()["message"]


def test_invalid_default_is_ignored_not_cleared(client):
    headers = _headers(client, "img-ignored")
    conn_a = _connection(client, headers, "生图A")
    conn_b = _connection(client, headers, "生图B")
    _catalog(client, headers, conn_a, "img-a", ["image.generate"])
    entry_b = _catalog(client, headers, conn_b, "img-b", ["image.generate"])
    _mark_connected(conn_a)
    _mark_connected(conn_b)

    client.patch(
        f"{PREFIX}/preferences",
        json={"defaultImageModelId": entry_b["id"]},
        headers=headers,
    )
    client.patch(
        f"{PREFIX}/models/catalog/{entry_b['id']}", json={"enabled": False}, headers=headers
    )

    pref = client.get(f"{PREFIX}/preferences", headers=headers).json()
    assert pref["defaultImageModelId"] == entry_b["id"], "失效只忽略，不清除偏好"
    assert pref["defaultImageModelIgnored"]["reason"] == "disabled"

    fallback = _status(client, headers)["image"]
    assert fallback["modelCode"] == "img-a"
    assert "默认生图模型不可用" in fallback["message"]


def test_preference_patch_does_not_clobber_other_fields(client):
    headers = _headers(client, "img-partial")
    conn = _connection(client, headers, "生图A")
    entry = _catalog(client, headers, conn, "img-a", ["image.generate"])

    client.patch(f"{PREFIX}/preferences", json={"theme": "dark"}, headers=headers)
    client.patch(
        f"{PREFIX}/preferences",
        json={"defaultImageModelId": entry["id"]},
        headers=headers,
    )
    pref = client.get(f"{PREFIX}/preferences", headers=headers).json()
    assert pref["theme"] == "dark"
    assert pref["defaultImageModelId"] == entry["id"]
    # 生图默认不能顺手把文本默认写成同一个 id
    assert pref["defaultTextModelId"] is None
