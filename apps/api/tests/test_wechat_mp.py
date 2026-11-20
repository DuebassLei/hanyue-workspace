"""微信公众号草稿箱接入的回归网。

覆盖点：
- 未配置凭据时不伪造成功（明确 409 connector_unavailable）；
- 绑定验证走真实探测函数（ping），失败时带微信真实 errcode 映射；
- 草稿预检拒绝超限内容与缺少封面；
- 推送任务经队列执行时，真实走「封面素材 → 正文图 → draft/add」三步，
  并把 media_id 落到 PublishJob / PublishRecord；
- 微信返回业务错误时任务失败且不写假 media_id；
- 正文 HTML 渲染会转义用户内容，不把 Markdown 当 HTML 注入。

约定同其它用例：app 模块在函数内延迟导入，避免收集阶段绑定开发库。
"""

from __future__ import annotations

import io
import os

import pytest


def _headers(client, subject: str = "wechat-user") -> dict:
    res = client.post("/api/v1/auth/login", json={"subject": subject, "displayName": subject})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


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


@pytest.fixture
def configured(monkeypatch):
    """注入一组测试凭据并清空连接器缓存，让 registry 真的构造 WeChatMPConnector。"""
    from app.connectors import registry

    monkeypatch.setenv("WECHAT_MP_APPID", "wxTESTAPPID0001")
    monkeypatch.setenv("WECHAT_MP_APPSECRET", "test-app-secret")
    monkeypatch.setenv("WECHAT_MP_AUTHOR", "测试作者")
    registry._CACHE.clear()
    yield
    registry._CACHE.clear()


@pytest.fixture(autouse=True)
def no_auto_dispatch(monkeypatch):
    """关掉自动投递：本文件需要确定性地手动执行任务，避免线程池与断言并发跑同一任务。"""
    from app.core import queue

    monkeypatch.setattr(queue, "dispatch", lambda job_id, **kw: True)


def _run_publish_job(job_id: str) -> dict:
    """同步执行一次发布任务，返回结果或抛出 JobFailure。"""
    from app import models
    from app.core.db import SessionLocal
    from app.services import job_runner

    with SessionLocal() as db:
        job = db.get(models.PublishJob, job_id)
        assert job is not None
        stub = type("J", (), {"payload": {"publishJobId": job_id}, "owner_user_id": job.owner_user_id})()
        return job_runner._handle_platform_publish(db, stub)


def test_status_without_credentials_says_unconfigured(client):
    from app.connectors import wechat_mp, registry

    registry._CACHE.clear()
    assert wechat_mp.config_from_env() is None
    headers = _headers(client, "wechat-unconfigured")
    res = client.get("/api/v1/wechat-mp/status", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["configured"] is False
    # 没配置就必须如实说不能推，绝不能报 draftCapable=True 让人误以为可用
    assert body["publishSupported"] is False
    assert "AppID" in body["reason"]
    assert body["account"] is None


def test_bind_requires_configured_credentials(client):
    from app.connectors import registry

    registry._CACHE.clear()
    headers = _headers(client, "wechat-bind-unconfigured")
    res = client.post("/api/v1/wechat-mp/bind", json={}, headers=headers)
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "connector_unavailable"


def test_bind_surfaces_real_wechat_errcode(client, configured, monkeypatch):
    """微信报 IP 未加白名单时，必须把该错误码原样告诉用户，而不是假装成功。"""
    from app.connectors import wechat_mp

    def fake_fetch_token(self, *, force: bool):
        raise wechat_mp.WeChatMPError(
            "ip_not_whitelisted",
            "调用来源 IP 不在公众号 IP 白名单内（微信 errcode=40164）",
            errcode=40164,
        )

    monkeypatch.setattr(wechat_mp.WeChatMPConnector, "_fetch_token", fake_fetch_token)
    headers = _headers(client, "wechat-bind-40164")
    res = client.post("/api/v1/wechat-mp/bind", json={}, headers=headers)
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "ip_not_whitelisted"
    assert "白名单" in res.json()["message"]


def test_token_is_cached_and_reused_across_calls(client, configured, monkeypatch):
    from app.connectors import registry, wechat_mp

    calls: list[str] = []

    def fake_api(self, method, path, *, token=None, params=None, json_body=None, files=None, timeout=20.0):
        calls.append(path)
        if path == "/cgi-bin/stable_token":
            return {"access_token": "TOKEN-1", "expires_in": 7200}
        if path == "/cgi-bin/draft/batchget":
            return {"total_count": 3, "item": [], "item_count": 0}
        raise AssertionError(f"未预期的调用：{path}")

    monkeypatch.setattr(wechat_mp.WeChatMPConnector, "_api", fake_api)
    headers = _headers(client, "wechat-token-cache")
    first = client.post("/api/v1/wechat-mp/bind", json={}, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["probe"]["draftTotal"] == 3
    second = client.get("/api/v1/wechat-mp/drafts", headers=headers)
    assert second.status_code == 200, second.text
    # 令牌落库缓存：第二次调用不应再去换 token
    assert calls.count("/cgi-bin/stable_token") == 1
    connector = registry.get_connector("wechat_official")
    assert isinstance(connector, wechat_mp.WeChatMPConnector)


def test_preview_rejects_missing_cover_and_overlong_title(client, configured):
    headers = _headers(client, "wechat-preview")
    res = client.post(
        "/api/v1/wechat-mp/drafts/preview",
        json={"title": "标" * 80, "body": "正文内容", "assetIds": []},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["canPush"] is False
    fields = {item["field"] for item in body["issues"] if item["level"] == "error"}
    assert {"title", "assets"} <= fields
    # 未绑定账号也要在预检里明说
    assert "account" in fields


def test_push_draft_rejects_content_before_queueing(client, configured, monkeypatch):
    from app.connectors import wechat_mp

    monkeypatch.setattr(
        wechat_mp.WeChatMPConnector,
        "ping",
        lambda self, db: {"draftTotal": 0, "appId": "wxTE***01", "apiBase": "x", "token": {}},
    )
    headers = _headers(client, "wechat-push-invalid")
    bound = client.post("/api/v1/wechat-mp/bind", json={}, headers=headers)
    assert bound.status_code == 200, bound.text
    res = client.post(
        "/api/v1/wechat-mp/drafts",
        json={
            "accountId": bound.json()["account"]["id"],
            "idempotencyKey": "wechat-invalid-0001",
            "title": "",
            "body": "正文",
            "assetIds": [],
        },
        headers=headers,
    )
    assert res.status_code == 422, res.text
    # 缺封面是比「内容不合法」更具体的错误，优先报 thumb_required 便于用户直接定位
    assert res.json()["code"] == "thumb_required"


def test_push_draft_fails_with_real_errcode_and_writes_no_media_id(
    client, configured, monkeypatch
):
    """微信 draft/add 返回业务错误时，任务必须失败，且不能写入假 media_id。"""
    from app.connectors import wechat_mp
    from app.services import job_runner

    monkeypatch.setattr(
        wechat_mp.WeChatMPConnector,
        "ping",
        lambda self, db: {"draftTotal": 0, "appId": "wxTE***01", "apiBase": "x", "token": {}},
    )
    monkeypatch.setattr(
        wechat_mp.WeChatMPConnector,
        "upload_thumb",
        lambda self, db, **kw: {"mediaId": "THUMB-1", "url": None},
    )
    monkeypatch.setattr(
        wechat_mp.WeChatMPConnector,
        "upload_inline_image",
        lambda self, db, **kw: "https://mmbiz.qpic.cn/x.png",
    )

    def boom(self, db, *, article):
        raise wechat_mp.WeChatMPError(
            "api_unauthorized", "该接口未获授权（微信 errcode=48001）", errcode=48001
        )

    monkeypatch.setattr(wechat_mp.WeChatMPConnector, "add_draft", boom)

    headers = _headers(client, "wechat-push-48001")
    assert client.post("/api/v1/wechat-mp/bind", json={}, headers=headers).status_code == 200
    asset_id = _upload_asset(client, headers)

    res = client.post(
        "/api/v1/wechat-mp/drafts",
        json={
            "accountId": "",
            "idempotencyKey": "wechat-48001-0001",
            "title": "测试草稿",
            "body": "第一段\n\n第二段",
            "digest": "摘要",
            "assetIds": [asset_id],
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    job_id = res.json()["id"]

    from app.services import job_runner

    with pytest.raises(job_runner.JobFailure) as excinfo:
        _run_publish_job(job_id)
    assert excinfo.value.code == "api_unauthorized"
    assert "48001" in excinfo.value.message

    from app import models
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        stored = db.get(models.PublishJob, job_id)
        assert stored.remote_content_id is None
        assert stored.status != "succeeded"


def test_push_draft_success_writes_media_id_and_record(client, configured, monkeypatch):
    from app.connectors import wechat_mp
    from app.services import job_runner

    seen: dict = {}

    monkeypatch.setattr(
        wechat_mp.WeChatMPConnector,
        "ping",
        lambda self, db: {"draftTotal": 0, "appId": "wxTE***01", "apiBase": "x", "token": {}},
    )

    def fake_thumb(self, db, *, data, filename, mime):
        seen["thumb"] = (len(data), mime, filename)
        return {"mediaId": "THUMB-OK", "url": None}

    def fake_inline(self, db, *, data, filename, mime):
        seen.setdefault("inline", []).append((len(data), mime))
        return f"https://mmbiz.qpic.cn/inline{len(seen['inline'])}.jpg"

    def fake_add(self, db, *, article):
        seen["article"] = article
        return "DRAFT-MEDIA-ID-1"

    monkeypatch.setattr(wechat_mp.WeChatMPConnector, "upload_thumb", fake_thumb)
    monkeypatch.setattr(wechat_mp.WeChatMPConnector, "upload_inline_image", fake_inline)
    monkeypatch.setattr(wechat_mp.WeChatMPConnector, "add_draft", fake_add)

    headers = _headers(client, "wechat-push-ok")
    assert client.post("/api/v1/wechat-mp/bind", json={}, headers=headers).status_code == 200
    cover = _upload_asset(client, headers, "cover.png")
    inline = _upload_asset(client, headers, "body.png")

    res = client.post(
        "/api/v1/wechat-mp/drafts",
        json={
            "accountId": "",
            "idempotencyKey": "wechat-ok-0001",
            "title": "一条真实草稿",
            "body": "开篇段落\n\n{{image1}}\n\n- 要点一\n- 要点二",
            "digest": "这是摘要",
            "author": "",
            "assetIds": [cover, inline],
            "needOpenComment": True,
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    job_id = res.json()["id"]

    from app.core.db import SessionLocal

    result = _run_publish_job(job_id)
    assert result["draftMediaId"] == "DRAFT-MEDIA-ID-1"
    assert result["inlineImages"] == 1

    article = seen["article"]
    assert article["title"] == "一条真实草稿"
    assert article["thumb_media_id"] == "THUMB-OK"
    assert article["digest"] == "这是摘要"
    assert article["need_open_comment"] == 1
    # 正文占位符必须被真实图片 URL 替换（只声明了 1 张正文图，编号从 1 开始）
    assert "https://mmbiz.qpic.cn/inline1.jpg" in article["content"]
    assert "{{image1}}" not in article["content"]
    # 独立工作台按参考设计用 section/p 自绘列表，不再限定旧引擎 li 标签。
    assert "要点一" in article["content"] and "要点二" in article["content"]
    assert article["content"].count('display:flex;margin:0 0 10px 0px;') == 2
    assert "- 要点" not in article["content"]

    from app import models

    with SessionLocal() as db:
        stored = db.get(models.PublishJob, job_id)
        assert stored.remote_content_id == "DRAFT-MEDIA-ID-1"
        assert stored.status == "succeeded"
        assert stored.payload_snapshot["draftMediaId"] == "DRAFT-MEDIA-ID-1"
        record = db.query(models.PublishRecord).filter_by(
            publish_job_id=job_id, remote_content_id="DRAFT-MEDIA-ID-1"
        ).one()
        assert record.result_status == "draft_created"
        # 草稿不是发布，published_at 必须留空
        assert record.published_at is None


def test_push_draft_is_idempotent(client, configured, monkeypatch):
    from app.connectors import wechat_mp

    monkeypatch.setattr(
        wechat_mp.WeChatMPConnector,
        "ping",
        lambda self, db: {"draftTotal": 0, "appId": "wxTE***01", "apiBase": "x", "token": {}},
    )
    monkeypatch.setattr(
        wechat_mp.WeChatMPConnector,
        "upload_thumb",
        lambda self, db, **kw: {"mediaId": "THUMB-1", "url": None},
    )
    headers = _headers(client, "wechat-idempotent")
    assert client.post("/api/v1/wechat-mp/bind", json={}, headers=headers).status_code == 200
    asset_id = _upload_asset(client, headers)
    payload = {
        "accountId": "",
        "idempotencyKey": "wechat-same-key-01",
        "title": "幂等草稿",
        "body": "正文",
        "assetIds": [asset_id],
    }
    first = client.post("/api/v1/wechat-mp/drafts", json=payload, headers=headers)
    second = client.post("/api/v1/wechat-mp/drafts", json=payload, headers=headers)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_render_html_escapes_user_content():
    from app.services import wechat_article

    html = wechat_article.render_html('<script>alert(1)</script> & "引号"')
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp;" in html


def test_default_render_uses_studio_engine():
    from app.services import wechat_article, wechat_layout_engine

    body = "## 标题\n\n正文"
    assert wechat_article.render_html(body) == wechat_layout_engine.render_studio_document(body, {}, None)["html"]


def test_digest_and_cover_limits_are_enforced():
    from app.services import wechat_article

    issues = wechat_article.validate_article(
        title="正常标题",
        body="正文",
        digest="摘" * 200,
        author="作者",
        asset_count=1,
    )
    assert any(item["field"] == "digest" and item["level"] == "error" for item in issues)


def test_publish_is_not_silently_supported():
    """公众号只做草稿；publish 必须明确拒绝，不能返回假成功。"""
    from app.connectors import registry

    connector = registry.get_connector("wechat_official")
    if not hasattr(connector, "publish"):
        pytest.skip("未配置公众号凭据时会得到 UnconfiguredConnector")
    with pytest.raises(registry.ConnectorUnavailable):
        connector.publish(access_token="x", payload={})
