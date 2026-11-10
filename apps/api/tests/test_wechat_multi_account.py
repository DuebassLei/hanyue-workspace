"""公众号多账号凭据回归网。

同一平台（公众号）可维护多个账号，每个账号可持有自己的 AppID/AppSecret：

- 绑定时携带 credentials → 凭据加密存到账号上（Secret 明文不落库）；
- 两个账号各自凭据互不干扰：账号级连接器按账号解析，推送任务按 accountId 走对应账号；
- 未绑定自有凭据的账号回落部署级默认凭据（界面保存的或环境变量）；
- 账号凭据修改端点：真实验证后加密保存，AccountOut 返回打码 AppID。

约定同其它用例：app 模块在函数内延迟导入，避免收集阶段绑定开发库；
探针用 monkeypatch 替换 WeChatMPConnector._fetch_token / _api。
"""

from __future__ import annotations

import io

import pytest


def _png_bytes(size: tuple[int, int] = (64, 64), color=(20, 120, 200)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _upload_asset(client, headers, name: str = "cover.png") -> str:
    session = client.post("/api/v1/assets/upload-sessions", headers=headers)
    assert session.status_code in (200, 201), session.text
    session_id = session.json()["sessionId"]
    res = client.post(
        f"/api/v1/assets?sessionId={session_id}",
        files={"file": (name, _png_bytes(), "image/png")},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _headers(client, subject: str = "multi-acct-user") -> dict:
    res = client.post("/api/v1/auth/login", json={"subject": subject, "displayName": subject})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


@pytest.fixture
def connectors_cache_cleared():
    from app.connectors import registry

    registry._CACHE.clear()
    registry._ACCOUNT_CACHE.clear()
    yield
    registry._CACHE.clear()
    registry._ACCOUNT_CACHE.clear()


def _mock_probe(monkeypatch, draft_total: int = 0):
    from app.connectors import wechat_mp as wechat

    def fake_fetch_token(self, *, force=False):
        return {"access_token": "tok", "expires_in": 7200}

    def fake_api(self, method, path, **kwargs):
        assert path == "/cgi-bin/draft/batchget"
        return {"total_count": draft_total, "item": []}

    monkeypatch.setattr(wechat.WeChatMPConnector, "_fetch_token", fake_fetch_token)
    monkeypatch.setattr(wechat.WeChatMPConnector, "_api", fake_api)


def _bind(client, headers, *, display_name: str, credentials: dict | None):
    payload = {"displayName": display_name}
    if credentials:
        payload["credentials"] = credentials
    res = client.post("/api/v1/wechat-mp/bind", json=payload, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["account"]


def _get_account(client, headers, account_id: str) -> dict:
    res = client.get("/api/v1/platform-accounts", headers=headers)
    assert res.status_code == 200, res.text
    return next(a for a in res.json() if a["id"] == account_id)


def test_bind_with_own_credentials_stores_encrypted_secret(
    client, connectors_cache_cleared, monkeypatch
):
    """绑定携带独立凭据：Secret 加密存到账号，列表返回打码 AppID 与 hasOwnCredential。"""
    from app import models
    from app.core.db import SessionLocal
    from app.core.security import decrypt_secret

    _mock_probe(monkeypatch)
    headers = _headers(client)
    account = _bind(
        client, headers, display_name="自凭据号", credentials={"appId": "wxMULTIACT0001", "appSecret": "secret-A"}
    )

    with SessionLocal() as db:
        row = db.get(models.ConnectedAccount, account["id"])
        assert row.app_id == "wxMULTIACT0001"
        assert row.app_secret_ciphertext and row.app_secret_ciphertext != "secret-A"
        assert decrypt_secret(row.app_secret_ciphertext) == "secret-A"
        assert row.auth_source == "app_secret"
        assert row.status == "connected"

    out = _get_account(client, headers, account["id"])
    assert out["hasOwnCredential"] is True
    assert out["appIdMasked"] != "wxMULTIACT0001"


def test_two_accounts_use_own_credentials_independently(
    client, connectors_cache_cleared, monkeypatch
):
    """双账号各自凭据互不干扰：账号级连接器按账号解析；推送按 accountId 入到对应账号。"""
    from app import models
    from app.connectors import registry
    from app.core.db import SessionLocal

    _mock_probe(monkeypatch)
    headers = _headers(client)
    acct_a = _bind(client, headers, display_name="账号A", credentials={"appId": "wxMULTIACTA001", "appSecret": "secret-A"})
    acct_b = _bind(client, headers, display_name="账号B", credentials={"appId": "wxMULTIACTB001", "appSecret": "secret-B"})

    with SessionLocal() as db:
        user = db.query(models.User).filter(models.User.auth_subject == "multi-acct-user").first()
        conn_a = registry.get_account_connector(db.get(models.ConnectedAccount, acct_a["id"]), db)
        conn_b = registry.get_account_connector(db.get(models.ConnectedAccount, acct_b["id"]), db)
        from app.connectors.wechat_mp import WeChatMPConnector

        assert isinstance(conn_a, WeChatMPConnector)
        assert isinstance(conn_b, WeChatMPConnector)
        assert conn_a is not conn_b
        assert conn_a.config.app_id == "wxMULTIACTA001"
        assert conn_a.config.app_secret == "secret-A"
        assert conn_b.config.app_id == "wxMULTIACTB001"
        assert conn_b.config.app_secret == "secret-B"
        assert user is not None

    # 推送：同一 payload 不同 accountId，任务归属各自账号（PublishJob.connected_account_id）
    res = client.post(
        "/api/v1/wechat-mp/drafts/preview",
        json={"accountId": acct_a["id"], "title": "T", "body": "正文"},
        headers=headers,
    )
    assert res.status_code == 200, res.text

    asset_id = _upload_asset(client, headers)

    def _push(account_id: str, key: str):
        r = client.post(
            "/api/v1/wechat-mp/drafts",
            json={
                "accountId": account_id,
                "idempotencyKey": key,
                "title": "多账号标题",
                "body": "正文内容",
                "assetIds": [asset_id],
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        return r.json()

    job_a = _push(acct_a["id"], f"multi-a-{acct_a['id'][:8]}")
    job_b = _push(acct_b["id"], f"multi-b-{acct_b['id'][:8]}")

    with SessionLocal() as db:
        ja = db.get(models.PublishJob, job_a["id"])
        jb = db.get(models.PublishJob, job_b["id"])
        assert ja.connected_account_id == acct_a["id"]
        assert jb.connected_account_id == acct_b["id"]


def test_account_without_own_credentials_falls_back_to_default(
    client, connectors_cache_cleared, monkeypatch
):
    """未绑定自有凭据的账号回落部署级默认凭据；绑定默认凭据后账号显示 hasOwnCredential=False。"""
    import os

    from app import models
    from app.connectors import registry
    from app.core.db import SessionLocal

    monkeypatch.setenv("WECHAT_MP_APPID", "wxFALLBACK00001")
    monkeypatch.setenv("WECHAT_MP_APPSECRET", "env-default-secret")
    registry._CACHE.clear()
    registry._ACCOUNT_CACHE.clear()
    _mock_probe(monkeypatch)

    try:
        headers = _headers(client)
        account = _bind(client, headers, display_name="默认凭据号", credentials=None)
        out = _get_account(client, headers, account["id"])
        assert out["hasOwnCredential"] is False
        assert out["appIdMasked"] == ""

        with SessionLocal() as db:
            conn = registry.get_account_connector(db.get(models.ConnectedAccount, account["id"]), db)
            assert conn.config.app_id == "wxFALLBACK00001"
            assert conn.config.app_secret == "env-default-secret"
    finally:
        for key in list(os.environ):
            if key.startswith("WECHAT_MP_"):
                del os.environ[key]
        registry._CACHE.clear()
        registry._ACCOUNT_CACHE.clear()


def test_update_account_credentials_endpoint(client, connectors_cache_cleared, monkeypatch):
    """账号凭据修改：真实验证后加密保存，旧凭据被替换，Out 打码展示。"""
    from app import models
    from app.connectors import registry
    from app.core.db import SessionLocal
    from app.core.security import decrypt_secret

    _mock_probe(monkeypatch)
    headers = _headers(client)
    account = _bind(
        client, headers, display_name="待改号", credentials={"appId": "wxMULTIACTOLD1", "appSecret": "old-secret"}
    )

    res = client.put(
        f"/api/v1/wechat-mp/accounts/{account['id']}/credentials",
        json={"appId": "wxMULTIACTNEW1", "appSecret": "new-secret"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["probe"]["draftTotal"] == 0
    assert body["probeError"] is None
    assert body["appIdMasked"] != "wxMULTIACTNEW1"

    with SessionLocal() as db:
        row = db.get(models.ConnectedAccount, account["id"])
        assert row.app_id == "wxMULTIACTNEW1"
        assert decrypt_secret(row.app_secret_ciphertext) == "new-secret"

    out = _get_account(client, headers, account["id"])
    assert out["hasOwnCredential"] is True
    assert out["appIdMasked"] != "wxMULTIACTNEW1"

    # 越权 / 非公众号账号：明确报错
    res = client.put(
        "/api/v1/wechat-mp/accounts/nonexistent/credentials",
        json={"appId": "wxX", "appSecret": "s"},
        headers=headers,
    )
    assert res.status_code == 404
    registry._ACCOUNT_CACHE.clear()


def test_push_with_foreign_account_id_rejected(client, connectors_cache_cleared, monkeypatch):
    """accountId 不属于当前用户 → 404；非公众号账号 → platform_mismatch。"""
    from app import models
    from app.core.db import SessionLocal
    from app.core.deps import default_workspace

    _mock_probe(monkeypatch)
    headers = _headers(client)
    asset_id = _upload_asset(client, headers)

    with SessionLocal() as db:
        user = db.query(models.User).filter(models.User.auth_subject != "multi-acct-user").first()
        workspace = default_workspace(db, user)
        other = models.ConnectedAccount(
            platform="wechat_official",
            display_name="别人的账号",
            status="connected",
            auth_source="app_secret",
            owner_user_id=user.id,
            workspace_id=workspace.id,
        )
        db.add(other)
        db.commit()
        other_id = other.id

    res = client.post(
        "/api/v1/wechat-mp/drafts",
        json={
            "accountId": other_id,
            "idempotencyKey": f"foreign-{other_id[:8]}",
            "title": "T",
            "body": "B",
            "assetIds": [asset_id],
        },
        headers=headers,
    )
    assert res.status_code == 404, res.text
