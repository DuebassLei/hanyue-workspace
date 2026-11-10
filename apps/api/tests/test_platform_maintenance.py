"""平台「独立维护与验证」能力的回归网。

平台收敛说明：内容发布平台仅保留**微信公众号**（AppID/AppSecret + 草稿箱），
小红书 / 抖音 / 视频号 / B站已整体下线，不提供任何入口。本组用例保证：

- `GET /platforms/wechat_official/maintenance` 返回统一的维护视图；
- 已下线平台的维护入口一律明确报 unknown_platform，绝不假装可用；
- 连接器状态如实区分 app_secret / 未配置两种接入方式；
- 统一验证入口对演示账号 / 未配置凭据如实报错；公众号（app_secret 型）
  账号走真实 draft/batchget 探针。

约定同其它用例：app 模块在函数内延迟导入，避免收集阶段绑定开发库。
"""

from __future__ import annotations

import pytest


def _headers(client, subject: str = "maint-user") -> dict:
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


def _create_account(client, headers, platform: str) -> dict:
    res = client.post(
        "/api/v1/platform-accounts", json={"platform": platform}, headers=headers
    )
    assert res.status_code == 201, res.text
    return res.json()


# ---------- 维护视图 ----------


def test_maintenance_endpoint_covers_all_platforms(client, headers=None):
    headers = headers or _headers(client)
    from app.connectors.base import PLATFORMS

    assert PLATFORMS == ["wechat_official"]
    for platform in PLATFORMS:
        res = client.get(f"/api/v1/platforms/{platform}/maintenance", headers=headers)
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["platform"] == platform
        assert data["label"]
        assert "connector" in data and "capability" in data
        assert data["connector"]["mode"] in ("app_secret", "none")
        # verifyCapable / verifyNote 必须如实返回
        assert isinstance(data["verifyCapable"], bool)
        assert data["verifyNote"]


@pytest.mark.parametrize(
    "platform",
    ["xiaohongshu", "douyin", "wechat_video", "bilibili", "not_a_platform"],
)
def test_maintenance_unknown_platform_rejected(client, platform):
    """已下线平台与未知平台都不提供维护入口，明确报 unknown_platform。"""
    headers = _headers(client)
    res = client.get(f"/api/v1/platforms/{platform}/maintenance", headers=headers)
    assert res.status_code == 400, res.text
    assert res.json()["code"] == "unknown_platform"


def test_maintenance_lists_bound_accounts(client):
    headers = _headers(client)
    account = _create_account(client, headers, "wechat_official")
    res = client.get("/api/v1/platforms/wechat_official/maintenance", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    ids = [a["id"] for a in data["accounts"]]
    assert account["id"] in ids
    entry = next(a for a in data["accounts"] if a["id"] == account["id"])
    assert entry["status"] == "pending_authorization"
    # pending 授权占位账号不可验证
    assert entry["verifiable"] is False


# ---------- 连接器状态诚实区分 ----------


def test_connector_status_reports_modes(client, connectors_cache_cleared, monkeypatch):
    """已下线平台不再出现；未配置时 mode=none，配置后公众号如实报告 app_secret 模式。"""
    headers = _headers(client)
    res = client.get("/api/v1/platforms/connectors/status", headers=headers)
    assert res.status_code == 200, res.text
    platforms = res.json()["platforms"]
    assert [s["platform"] for s in platforms] == ["wechat_official"]
    by_platform = {s["platform"]: s for s in platforms}
    # 未配置凭据时如实显示 none
    assert by_platform["wechat_official"]["mode"] == "none"

    from app.connectors import registry

    monkeypatch.setenv("WECHAT_MP_APPID", "wxTESTAPPID0001")
    monkeypatch.setenv("WECHAT_MP_APPSECRET", "test-app-secret")
    registry._CACHE.clear()
    try:
        res = client.get("/api/v1/platforms/connectors/status", headers=headers)
        platforms = res.json()["platforms"]
        assert [s["platform"] for s in platforms] == ["wechat_official"]
        wechat = platforms[0]
        assert wechat["mode"] == "app_secret"
        assert wechat["draftCapable"] is True
        assert wechat["verifyCapable"] is True
    finally:
        import os

        for key in list(os.environ):
            if key.startswith("WECHAT_MP_"):
                del os.environ[key]
        registry._CACHE.clear()


def test_removed_platforms_leave_no_connector_residue(client):
    """遗留的其它平台环境变量不能让已下线平台复活。"""
    import os

    from app.connectors import registry

    os.environ["PLATFORM_XIAOHONGSHU_CLIENT_ID"] = "legacy-client"
    registry._CACHE.clear()
    try:
        headers = _headers(client)
        res = client.get("/api/v1/platforms/connectors/status", headers=headers)
        assert res.status_code == 200, res.text
        codes = [s["platform"] for s in res.json()["platforms"]]
        assert codes == ["wechat_official"]
    finally:
        os.environ.pop("PLATFORM_XIAOHONGSHU_CLIENT_ID", None)
        registry._CACHE.clear()


def _drop_credential_setting():
    """共享测试库清理：删除界面保存的公众号凭据，避免污染其他用例。"""
    from app import models
    from app.core.db import SessionLocal
    from app.connectors import wechat_mp as wechat

    with SessionLocal() as db:
        row = db.get(models.AppSetting, wechat.CREDENTIAL_SETTING_KEY)
        if row:
            db.delete(row)
            db.commit()


# ---------- 公众号凭据界面管理 ----------


def test_wechat_credentials_roundtrip(client, connectors_cache_cleared, monkeypatch):
    """凭据可在界面保存（Secret 加密存库）、查看打码、真实验证、清除。"""
    from app import models
    from app.connectors import registry
    from app.connectors import wechat_mp as wechat
    from app.core.db import SessionLocal
    from app.core.security import decrypt_secret

    _drop_credential_setting()
    monkeypatch.delenv("WECHAT_MP_APPID", raising=False)
    monkeypatch.delenv("WECHAT_MP_APPSECRET", raising=False)
    registry._CACHE.clear()

    # 未配置状态
    res = client.get("/api/v1/wechat-mp/credentials", headers=_headers(client))
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is False
    assert body["source"] == "none"

    # 保存 + 真实验证（探针 mock）
    def fake_fetch_token(self, *, force=False):
        return {"access_token": "tok", "expires_in": 7200}

    def fake_api(self, method, path, **kwargs):
        assert path == "/cgi-bin/draft/batchget"
        return {"total_count": 0, "item": []}

    monkeypatch.setattr(wechat.WeChatMPConnector, "_fetch_token", fake_fetch_token)
    monkeypatch.setattr(wechat.WeChatMPConnector, "_api", fake_api)

    headers = _headers(client)
    try:
        res = client.put(
            "/api/v1/wechat-mp/credentials",
            json={"appId": "wxROUNDTRIP0001", "appSecret": "plain-secret-123"},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["source"] == "db"
        assert body["configured"] is True
        assert body["probe"]["draftTotal"] == 0

        # 库里是密文，解密后等于原文
        with SessionLocal() as db:
            value = db.get(models.AppSetting, wechat.CREDENTIAL_SETTING_KEY).value
        assert value["app_secret"] != "plain-secret-123"
        assert decrypt_secret(value["app_secret"]) == "plain-secret-123"

        # 查看打码：AppID 非原文展示，Secret 永不回传
        res = client.get("/api/v1/wechat-mp/credentials", headers=headers)
        body = res.json()
        assert body["configured"] is True
        assert body["source"] == "db"
        assert body["appIdMasked"] != "wxROUNDTRIP0001"
        assert "*" in body["appIdMasked"]
        assert "appSecret" not in body and "app_secret" not in body
    finally:
        # 无论断言是否失败都清掉共享库中的凭据行，避免级联污染其他用例
        _drop_credential_setting()
        registry._CACHE.clear()

    # 清除
    res = client.delete("/api/v1/wechat-mp/credentials", headers=headers)
    assert res.status_code == 204
    res = client.get("/api/v1/wechat-mp/credentials", headers=headers)
    assert res.json()["configured"] is False


def test_credential_source_priority_db_over_env(client, connectors_cache_cleared, monkeypatch):
    """凭据来源优先级：界面保存（DB）> 环境变量；DB 值删除后回落 env。"""
    from app.connectors import registry
    from app.connectors import wechat_mp as wechat

    monkeypatch.setenv("WECHAT_MP_APPID", "wxENVBACKUP0001")
    monkeypatch.setenv("WECHAT_MP_APPSECRET", "env-secret")

    def fake_fetch_token(self, *, force=False):
        return {"access_token": "tok", "expires_in": 7200}

    def fake_api(self, method, path, **kwargs):
        return {"total_count": 0, "item": []}

    monkeypatch.setattr(wechat.WeChatMPConnector, "_fetch_token", fake_fetch_token)
    monkeypatch.setattr(wechat.WeChatMPConnector, "_api", fake_api)

    headers = _headers(client)
    registry._CACHE.clear()
    assert client.get("/api/v1/wechat-mp/credentials", headers=headers).json()["source"] == "env"

    res = client.put(
        "/api/v1/wechat-mp/credentials",
        json={"appId": "wxDBPRIORITY001", "appSecret": "db-secret"},
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["source"] == "db"

    # 删除 DB 凭据后回落 env
    assert client.delete("/api/v1/wechat-mp/credentials", headers=headers).status_code == 204
    registry._CACHE.clear()
    assert client.get("/api/v1/wechat-mp/credentials", headers=headers).json()["source"] == "env"

    monkeypatch.delenv("WECHAT_MP_APPID", raising=False)
    monkeypatch.delenv("WECHAT_MP_APPSECRET", raising=False)
    registry._CACHE.clear()


def test_startup_sweep_removes_legacy_accounts(client, connectors_cache_cleared):
    """启动清扫：已下线平台账号与演示账号被移除，正常公众号账号保留。"""
    from app import models
    from app.core.db import SessionLocal
    from app.core.deps import default_workspace
    from app.modules.publishing import sweep_legacy_platform_accounts

    with SessionLocal() as db:
        user = db.query(models.User).first()
        workspace = default_workspace(db, user)
        legacy = models.ConnectedAccount(
            platform="xiaohongshu",
            display_name="遗留小红书",
            status="connected",
            auth_source="none",
            owner_user_id=user.id,
            workspace_id=workspace.id,
        )
        demo = models.ConnectedAccount(
            platform="wechat_official",
            display_name="演示公众号",
            status="connected",
            auth_source="demo",
            owner_user_id=user.id,
            workspace_id=workspace.id,
        )
        keep = models.ConnectedAccount(
            platform="wechat_official",
            display_name="正常账号",
            status="pending_authorization",
            auth_source="none",
            owner_user_id=user.id,
            workspace_id=workspace.id,
        )
        db.add_all([legacy, demo, keep])
        db.commit()
        ids = {"legacy": legacy.id, "demo": demo.id, "keep": keep.id}

    removed = sweep_legacy_platform_accounts()
    assert removed >= 2

    with SessionLocal() as db:
        assert db.get(models.ConnectedAccount, ids["legacy"]) is None
        assert db.get(models.ConnectedAccount, ids["demo"]) is None
        kept = db.get(models.ConnectedAccount, ids["keep"])
        assert kept is not None
        db.delete(kept)
        db.commit()


# ---------- 统一验证入口 ----------


def test_demo_account_still_rejected_by_unified_test(client):
    headers = _headers(client)
    account = _create_account(client, headers, "wechat_official")
    # 历史演示账号没有平台凭据：统一验证入口必须如实拒绝
    from app import models
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        row = db.get(models.ConnectedAccount, account["id"])
        row.auth_source = "demo"
        db.commit()

    res = client.post(f"/api/v1/platform-accounts/{account['id']}/test", headers=headers)
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "demo_account"


def test_unconfigured_connector_rejected_by_unified_test(client, connectors_cache_cleared):
    """凭据未配置时统一验证入口明确报 connector_unavailable，不伪造验证通过。"""
    headers = _headers(client)
    account = _create_account(client, headers, "wechat_official")
    res = client.post(f"/api/v1/platform-accounts/{account['id']}/test", headers=headers)
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "connector_unavailable"


def test_wechat_account_verified_via_unified_test(client, connectors_cache_cleared, monkeypatch):
    """公众号（app_secret 型）账号走统一验证入口时，使用真实 draft/batchget 探针。"""
    from app.connectors import registry
    from app.connectors import wechat_mp as wechat
    from app.core.db import SessionLocal
    from app import models

    # 用独立 AppID，避免污染 test_wechat_mp.py 的令牌缓存断言（共享测试库）
    monkeypatch.setenv("WECHAT_MP_APPID", "wxMAINTTEST0001")
    monkeypatch.setenv("WECHAT_MP_APPSECRET", "test-app-secret")
    registry._CACHE.clear()

    def fake_fetch_token(self, *, force=False):
        return {"access_token": "tok", "expires_in": 7200}

    def fake_api(self, method, path, *, token=None, params=None, json_body=None, files=None, timeout=8.0):
        assert path == "/cgi-bin/draft/batchget"
        return {"total_count": 3, "item": []}

    monkeypatch.setattr(wechat.WeChatMPConnector, "_fetch_token", fake_fetch_token)
    monkeypatch.setattr(wechat.WeChatMPConnector, "_api", fake_api)
    try:
        headers = _headers(client)
        # 通过绑定接口创建 app_secret 型账号
        res = client.post("/api/v1/wechat-mp/bind", json={}, headers=headers)
        assert res.status_code == 200, res.text
        account_id = res.json()["account"]["id"]

        # 统一验证入口
        res = client.post(f"/api/v1/platform-accounts/{account_id}/test", headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["verified"] is True
        assert body["detail"]["draftTotal"] == 3
        assert body["detail"]["mode"] == "app_secret"
    finally:
        import os

        for key in list(os.environ):
            if key.startswith("WECHAT_MP_"):
                del os.environ[key]
        # 清理本用例写入共享测试库的令牌缓存行，避免污染 test_wechat_mp 的缓存断言
        from sqlalchemy import select as _select

        with SessionLocal() as db:
            for row in db.scalars(_select(models.PlatformAccessToken)).all():
                db.delete(row)
            db.commit()
        registry._CACHE.clear()
