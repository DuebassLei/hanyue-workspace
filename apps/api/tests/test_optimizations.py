"""新增能力的回归测试：SSRF、真实探测、任务终态、工作区隔离、证据登记。"""

import io
import time

import httpx
import pytest
from PIL import Image


def _login(client, subject):
    res = client.post("/api/v1/auth/login", json={"subject": subject, "displayName": subject})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _wait_job(client, headers, job_id, timeout=8.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        res = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
        assert res.status_code == 200, res.text
        last = res.json()
        if last["status"] in ("succeeded", "failed", "cancelled"):
            return last
        time.sleep(0.1)
    return last


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 120, 60)).save(buf, format="PNG")
    return buf.getvalue()


# ---------------- SSRF ----------------

@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.5/v1",
        "https://192.168.1.8/v1",
        "https://user:pass@api.example.com/v1",
        "https://metadata.google.internal/v1",
        "http://10.0.0.5:11434/v1",
    ],
)
def test_ssrf_rejects_internal_urls(url):
    from app.core import ssrf

    with pytest.raises(ssrf.UnsafeUrl):
        ssrf.validate_base_url(url)


def test_ssrf_allows_public_https_without_dns():
    from app.core import ssrf

    assert ssrf.validate_base_url("https://api.example.com/v1/") == "https://api.example.com/v1"


def test_ssrf_allows_loopback_http_without_dns():
    from app.core import ssrf

    assert ssrf.validate_base_url("http://127.0.0.1:3425/v1/") == "http://127.0.0.1:3425/v1"
    assert ssrf.validate_base_url("http://localhost:11434/v1") == "http://localhost:11434/v1"


# ---------------- 真实连接探测 ----------------

def test_probe_only_reports_connected_on_2xx(monkeypatch):
    from app.services import provider_probe

    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(url)
        return httpx.Response(
            200, json={"data": [{"id": "m1"}]}, request=httpx.Request(method, url)
        )

    monkeypatch.setattr(provider_probe.ssrf, "assert_safe_url", lambda u: u.rstrip("/"))
    monkeypatch.setattr(provider_probe.ssrf, "safe_request", fake_request)
    result = provider_probe.probe_connection(
        "https://api.example.com/v1", "sk-test", "openai_compatible"
    )
    assert result["status"] == "connected"
    assert result["modelCount"] == 1
    assert calls[0].endswith("/v1/models")


def test_probe_reports_unauthorized_and_never_fakes_success(monkeypatch):
    from app.services import provider_probe

    monkeypatch.setattr(provider_probe.ssrf, "assert_safe_url", lambda u: u.rstrip("/"))
    monkeypatch.setattr(
        provider_probe.ssrf,
        "safe_request",
        lambda method, url, **kwargs: httpx.Response(
            401, json={}, request=httpx.Request(method, url)
        ),
    )
    result = provider_probe.probe_connection(
        "https://api.example.com/v1", "sk-bad", "openai_compatible"
    )
    assert result["status"] == "unauthorized"


def test_probe_does_not_call_network_without_key_or_adapter(monkeypatch):
    from app.services import provider_probe

    calls = []
    monkeypatch.setattr(
        provider_probe.ssrf,
        "safe_request",
        lambda *a, **k: calls.append(a) or httpx.Response(200, json={"data": []}),
    )
    assert provider_probe.probe_connection("https://api.example.com", "", "openai_compatible")["status"] == "missing_credential"
    assert provider_probe.probe_connection("https://api.example.com", "sk-x", "vendor_native")["status"] == "adapter_unavailable"
    assert not calls


def test_connection_test_endpoint_reports_missing_credential(client):
    headers = _login(client, "probe-user")
    created = client.post(
        "/api/v1/models/connections",
        json={"displayName": "无密钥", "baseUrl": "https://api.example.com/v1"},
        headers=headers,
    ).json()
    res = client.post(f"/api/v1/models/connections/{created['id']}/test", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "missing_credential"
    assert body["verified"] is False


# ---------------- 授权与能力矩阵 ----------------

def test_created_account_has_no_fake_authorization(client):
    """没有任何演示授权端点：新建账号绝不伪造 connected / scopes。"""
    headers = _login(client, "demo-user")
    account = client.post(
        "/api/v1/platform-accounts", json={"platform": "wechat_official"}, headers=headers
    ).json()
    assert account["scopes"] == []
    assert account["authSource"] == "none"
    assert account["status"] == "pending_authorization"


def test_capabilities_expose_wechat_official_only_state(client):
    """平台收敛后只有公众号真实可用，下线的平台只以未接入占位出现（不可绑定）；能力与证据报告如实返回。"""
    headers = _login(client, "cap-user")
    body = client.get("/api/v1/platforms/capabilities", headers=headers).json()
    platforms = {item["platform"]: item for item in body["platforms"]}
    # 只有公众号带真实能力，且不可自动发布、但可以推草稿
    platform = platforms["wechat_official"]
    assert platform["available"] is True
    assert platform["confirmed"] is True
    assert platform["publish"] is False
    assert platform["draftUpload"] is True
    # 已下线平台只能作为占位，不能冒充已接入
    online = [code for code, item in platforms.items() if item["available"]]
    assert online == ["wechat_official"]
    for code in ("xiaohongshu", "douyin"):
        assert code in platforms
        assert platforms[code]["available"] is False
        assert platforms[code]["confirmed"] is False
    # 证据报告：公众号一项已核验，无警告、无核验时间戳
    assert body["verifiedCount"] == 1
    assert body["verifiedAt"] is None
    assert body["warnings"] == []


def test_removed_platform_account_creation_rejected(client):
    """已下线平台（小红书/抖音/视频号/B站）不再提供任何账号入口。"""
    headers = _login(client, "removed-user")
    res = client.post(
        "/api/v1/platform-accounts", json={"platform": "bilibili"}, headers=headers
    )
    assert res.status_code == 400, res.text
    assert res.json()["code"] == "unknown_platform"


# ---------------- 任务队列与终态 ----------------

def test_ai_image_task_fails_explicitly_without_provider(client):
    headers = _login(client, "image-user")
    res = client.post(
        "/api/v1/ai/image-generations",
        json={"prompt": "一张封面", "count": 1},
        headers=headers,
    )
    assert res.status_code == 202, res.text
    job = _wait_job(client, headers, res.json()["jobId"])
    assert job["status"] == "failed", job
    assert job["errorCode"] == "image_provider_unavailable"


def _image_catalog(db, owner_id, model_code="agnes-image-2.1-flash"):
    """登记一个带 image.generate 能力的供应商，凭据为占位密文。"""
    from app import models
    from app.core.security import encrypt_secret

    connection = models.ModelConnection(
        owner_user_id=owner_id,
        display_name="测试图像供应商",
        base_url="https://images.example.test/v1",
        credential_ciphertext=encrypt_secret("test-key"),
    )
    db.add(connection)
    db.flush()
    entry = models.ModelCatalogEntry(
        connection_id=connection.id,
        model_code=model_code,
        display_name=model_code,
        capabilities=["image.generate"],
    )
    db.add(entry)
    db.commit()
    return entry


def test_image_generation_degrades_to_text_when_edits_unsupported(client, monkeypatch):
    """参考图供应商返回 404 时降级为纯文生图，而不是让任务失败，并记住这家供应商。"""
    from app.core.db import SessionLocal
    from app.services import image_provider

    headers = _login(client, "edits-user")
    db = SessionLocal()
    try:
        from app import models

        owner_id = db.query(models.User).filter_by(auth_subject="edits-user").one().id
        entry = _image_catalog(db, owner_id)
        model_id = entry.id
    finally:
        db.close()

    calls = []

    def fake_generate(db, owner, **kwargs):
        calls.append(kwargs.get("reference_images"))
        if kwargs.get("reference_images"):
            raise image_provider.ImageProviderUnavailable(
                "edits_unsupported", "供应商不支持参考图编辑（HTTP 404）"
            )
        return [(b"png-bytes", "image/png")]

    monkeypatch.setattr(image_provider, "generate_images", fake_generate)
    monkeypatch.setattr(
        "app.services.job_runner._store_asset",
        lambda *args, **kwargs: type("Asset", (), {"id": "asset-1"})(),
    )
    monkeypatch.setattr(
        "app.services.job_runner._load_reference_images",
        lambda db, job, payload: [(b"ref", "image/png", "ref.png")],
    )

    res = client.post(
        "/api/v1/ai/image-generations",
        json={"prompt": "一张封面", "count": 1, "modelId": model_id},
        headers=headers,
    )
    assert res.status_code == 202, res.text
    job = _wait_job(client, headers, res.json()["jobId"])
    assert job["status"] == "succeeded", job
    assert job["result"]["degradedToText"] is True
    assert "不支持参考图" in job["result"]["notice"]
    # 先带参考图失败一次，再无参考图重试一次
    assert calls == [[(b"ref", "image/png", "ref.png")], None]

    db = SessionLocal()
    try:
        from app import models

        saved = db.get(models.ModelCatalogEntry, model_id)
        assert saved.options.get("supportsEdits") is False
    finally:
        db.close()


def test_known_unsupported_provider_skips_edits_request(client, monkeypatch):
    """已经确认不支持 edits 的供应商，后续任务不再发出注定失败的参考图请求。"""
    from app.core.db import SessionLocal
    from app.services import image_provider

    headers = _login(client, "edits-known-user")
    db = SessionLocal()
    try:
        from app import models

        owner_id = db.query(models.User).filter_by(auth_subject="edits-known-user").one().id
        entry = _image_catalog(db, owner_id)
        entry.options = {"supportsEdits": False}
        db.commit()
        model_id = entry.id
    finally:
        db.close()

    calls = []
    monkeypatch.setattr(
        image_provider,
        "generate_images",
        lambda db, owner, **kwargs: calls.append(kwargs.get("reference_images"))
        or [(b"png-bytes", "image/png")],
    )
    monkeypatch.setattr(
        "app.services.job_runner._store_asset",
        lambda *args, **kwargs: type("Asset", (), {"id": "asset-2"})(),
    )
    monkeypatch.setattr(
        "app.services.job_runner._load_reference_images",
        lambda db, job, payload: [(b"ref", "image/png", "ref.png")],
    )

    res = client.post(
        "/api/v1/ai/image-generations",
        json={"prompt": "一张封面", "count": 1, "modelId": model_id},
        headers=headers,
    )
    job = _wait_job(client, headers, res.json()["jobId"])
    assert job["status"] == "succeeded", job
    assert calls == [None], "已知不支持时不得再发带参考图的请求"
    assert job["result"]["degradedToText"] is True


def test_jobs_list_supports_cursor(client):
    headers = _login(client, "jobs-user")
    body = client.get("/api/v1/jobs?limit=1", headers=headers).json()
    assert "items" in body and "nextCursor" in body
    assert body["backend"] in ("threadpool", "redis")
    assert len(body["items"]) <= 1


# ---------------- 工作区隔离与 Skill 注册 ----------------

def test_workspace_current_is_not_shadowed_by_path_param(client):
    """选题路由下线后，/workspaces/current 仍然不能被 {workspace_id} 抢匹配。"""
    headers = _login(client, "ws-user")
    second = client.post("/api/v1/workspaces", json={"name": "第二工作区"}, headers=headers).json()
    current = client.get("/api/v1/workspaces/current", headers=headers)
    assert current.status_code == 200
    assert current.json()["id"] != second["id"]
    assert "topics" not in current.json()["counts"]
    assert client.get(f"/api/v1/workspaces/{second['id']}/topics", headers=headers).status_code in (404, 405)


def test_registered_skill_is_owned_trusted_and_checksummed(client):
    """用户自建 Skill 归属本人、默认可信，checksum 覆盖 manifest/tools/version。"""
    headers = _login(client, "skill-user")
    res = client.post(
        "/api/v1/skills",
        json={
            "skillKey": "custom-check-skill",
            "displayName": "自定义检查",
            "allowedToolIds": ["read_content"],
            "manifest": {"input": {"goal": "string"}},
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert len(body["checksum"]) == 64
    assert body["trustStatus"] == "trusted"
    assert body["enabled"] is False

    # 本人可启用；他人看不到
    enabled = client.patch(
        f"/api/v1/skills/{body['id']}",
        json={"enabled": True, "grantedToolIds": ["read_content"]},
        headers=headers,
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    other = _login(client, "skill-other")
    keys = [s["skillKey"] for s in client.get("/api/v1/skills", headers=other).json()]
    assert "custom-check-skill" not in keys


# ---------------- 上传会话 ----------------

def test_upload_session_is_signed_and_enforced(client):
    headers = _login(client, "upload-user")
    session = client.post("/api/v1/assets/upload-sessions", headers=headers).json()
    assert session["sessionId"]
    ok = client.post(
        f"/api/v1/assets?sessionId={session['sessionId']}",
        files={"file": ("tiny.png", _png_bytes(), "image/png")},
        headers=headers,
    )
    assert ok.status_code == 201, ok.text
    bad = client.post(
        "/api/v1/assets?sessionId=forged.token",
        files={"file": ("tiny.png", _png_bytes(), "image/png")},
        headers=headers,
    )
    assert bad.json()["code"] == "invalid_upload_session"


def test_image_job_async_returns_job_id(client):
    headers = _login(client, "imgjob-user")
    asset = client.post(
        "/api/v1/assets",
        files={"file": ("tiny.png", _png_bytes(), "image/png")},
        headers=headers,
    ).json()
    res = client.post(
        "/api/v1/image-jobs/async",
        json={"assetId": asset["id"], "operation": "thumbnail", "options": {"maxSize": 16}},
        headers=headers,
    )
    assert res.status_code == 202, res.text
    job = _wait_job(client, headers, res.json()["jobId"])
    assert job["status"] == "succeeded", job
    assert len(job["result"]["assetIds"]) == 1
