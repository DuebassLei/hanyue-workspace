"""公众号排版能力的回归网。

覆盖：
- 主题 tokens 校验（越界/非法颜色/非法枚举全部拒绝）；
- 渲染器输出微信兼容标记：只用 section/p/span/img，无 style/class/div/h1；
- 各区块（标题/引用/列表/分割线/代码/卡片/关注/署名/图注）都能渲染；
- 未知区块与未闭合区块不丢内容，给出可执行告警；
- 主题 CRUD（预设不可改删）与渲染预览端点；
- 推送时主题快照落库，任务按快照渲染（改主题不影响历史任务）。
"""

from __future__ import annotations

import io

import pytest


def _headers(client, subject: str = "layout-user") -> dict:
    res = client.post("/api/v1/auth/login", json={"subject": subject, "displayName": subject})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


# ---------- 主题校验 ----------


def test_normalize_tokens_rejects_out_of_range_and_bad_values():
    from app.services import wechat_layout

    with pytest.raises(wechat_layout.ThemeError):
        wechat_layout.normalize_tokens({"bodySize": 99})
    with pytest.raises(wechat_layout.ThemeError):
        wechat_layout.normalize_tokens({"bodyLineHeight": 0.2})
    with pytest.raises(wechat_layout.ThemeError):
        wechat_layout.normalize_tokens({"primaryColor": "url(javascript:alert(1))"})
    with pytest.raises(wechat_layout.ThemeError):
        wechat_layout.normalize_tokens({"h1Style": "rainbow"})
    with pytest.raises(wechat_layout.ThemeError):
        wechat_layout.normalize_tokens({"fontFamily": "url(https://evil.example/f.woff)"})


def test_normalize_tokens_keeps_valid_values_and_rejects_unknown_keys():
    from app.services import wechat_layout

    tokens = wechat_layout.normalize_tokens(
        {"bodySize": 15, "primaryColor": "#185FA5", "h1Style": "center"}
    )
    assert tokens["bodySize"] == 15
    assert tokens["primaryColor"] == "#185FA5"
    assert tokens["h1Style"] == "center"

    # 拼错/不存在的参数不能静默丢弃，否则用户会以为设置生效了
    with pytest.raises(wechat_layout.ThemeError) as excinfo:
        wechat_layout.normalize_tokens({"bodyFontSize": 18})
    assert "bodyFontSize" in str(excinfo.value)
    assert "可用参数" in str(excinfo.value)


# ---------- 渲染 ----------


def test_render_uses_inline_styles_only():
    from app.services import wechat_layout

    result = wechat_layout.render_document(
        "# 大标题\n\n正文**加粗**与==高亮==\n\n- 条目一\n- 条目二",
        None,
        [],
    )
    html = result["html"]
    assert "font-size:16px" in html
    assert "line-height:1.8" in html
    assert "letter-spacing:0.5px" in html
    for forbidden in ("<style", "<script", "<div", "<h1", "class=", " id="):
        assert forbidden not in html
    assert "<strong" in html
    assert "background:#E1F5EE" in html


def test_render_handles_all_block_types():
    from app.services import wechat_layout

    body = (
        "## 小标题\n\n"
        "> 一段引用\n\n"
        "1. 第一步\n2. 第二步\n\n"
        "---\n\n"
        "```\nprint(1)\n```\n\n"
        ":::card 小结\n卡片内容\n:::\n\n"
        ":::follow\n点上面蓝字关注\n:::\n\n"
        ":::sign 作者|公众号\n:::\n\n"
        "{{image1|图片说明}}"
    )
    result = wechat_layout.render_document(body, None, ["https://mmbiz.qpic.cn/a.jpg"])
    html = result["html"]
    assert "<blockquote" in html
    assert "<ol" in html and "第一步" in html
    assert "— — —" in html
    assert "print(1)" in html
    assert "卡片内容" in html
    assert "点上面蓝字关注" in html
    assert "图片说明" in html
    assert "https://mmbiz.qpic.cn/a.jpg" in html
    assert result["warnings"] == []


def test_render_keeps_unknown_and_unclosed_blocks_with_warning():
    from app.services import wechat_layout

    result = wechat_layout.render_document(
        ":::unknown\n原始内容\n:::\n\n:::card 未闭合\n内容还在", None, []
    )
    assert "原始内容" in result["html"]
    assert "内容还在" in result["html"]
    messages = " ".join(item["message"] for item in result["warnings"])
    assert "未知区块" in messages
    assert "未闭合" in messages


def test_render_escapes_and_degrades_links():
    from app.services import wechat_layout

    result = wechat_layout.render_document(
        '<script>alert(1)</script>\n\n[外链](https://example.com/a)', None, []
    )
    html = result["html"]
    assert "<script" not in html
    assert "&lt;script&gt;" in html
    assert "<a " not in html
    assert any("外部链接" in item["message"] for item in result["warnings"])


def test_render_out_of_range_image_warns_but_keeps_layout():
    from app.services import wechat_layout

    result = wechat_layout.render_document("{{image3}}", None, ["https://mmbiz.qpic.cn/a.jpg"])
    assert any("超出已选正文图数量" in item["message"] for item in result["warnings"])


def test_theme_changes_output_style():
    from app.services import wechat_layout

    default = wechat_layout.render_document("# 标题", None, [])["html"]
    blue = wechat_layout.render_document(
        "# 标题", wechat_layout.PRESET_THEMES["deep-blue"]["tokens"], []
    )["html"]
    assert "#0F6E56" in default
    assert "#185FA5" in blue
    assert default != blue


# ---------- 主题 API ----------


def test_theme_crud_and_preset_protection(client):
    headers = _headers(client)
    listing = client.get("/api/v1/wechat-mp/themes", headers=headers)
    assert listing.status_code == 200, listing.text
    data = listing.json()
    assert data["defaultThemeId"] == "ink-green"
    assert len(data["presets"]) == 6
    assert all(item["editable"] is False for item in data["presets"])

    created = client.post(
        "/api/v1/wechat-mp/themes",
        json={"name": "我的蓝", "baseThemeId": "deep-blue", "tokens": {"bodySize": 15}},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    theme = created.json()
    assert theme["tokens"]["bodySize"] == 15
    assert theme["tokens"]["primaryColor"] == "#185FA5"

    bad = client.post(
        "/api/v1/wechat-mp/themes",
        json={"name": "坏主题", "tokens": {"bodySize": 100}},
        headers=headers,
    )
    assert bad.status_code == 422, bad.text
    assert bad.json()["code"] == "invalid_theme"

    patched = client.patch(
        f"/api/v1/wechat-mp/themes/{theme['id']}",
        json={"tokens": {"letterSpacing": 1.2}},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["tokens"]["letterSpacing"] == 1.2

    # 预设不可改、不可删
    assert (
        client.patch(
            "/api/v1/wechat-mp/themes/ink-green", json={"tokens": {}}, headers=headers
        ).status_code
        == 404
    )
    assert client.delete("/api/v1/wechat-mp/themes/ink-green", headers=headers).status_code == 404

    assert client.delete(f"/api/v1/wechat-mp/themes/{theme['id']}", headers=headers).status_code == 204


def test_render_endpoint_returns_html_and_stats(client):
    headers = _headers(client)
    res = client.post(
        "/api/v1/wechat-mp/drafts/render",
        json={
            "title": "预览",
            "body": "# 标题\n\n正文内容",
            "themeId": "warm-orange",
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["themeId"] == "warm-orange"
    assert data["themeName"] == "暖橙生活"
    assert "text-align:center" in data["html"]
    assert data["stats"]["renderedChars"] > 0
    assert data["issues"] == []


def test_render_endpoint_rejects_unknown_theme(client):
    headers = _headers(client)
    res = client.post(
        "/api/v1/wechat-mp/drafts/render",
        json={"body": "正文", "themeId": "not-a-theme"},
        headers=headers,
    )
    assert res.status_code == 404, res.text
    assert res.json()["code"] == "theme_not_found"


# ---------- 推送与快照 ----------


@pytest.fixture
def configured(monkeypatch):
    from app.connectors import registry

    monkeypatch.setenv("WECHAT_MP_APPID", "wxLAYOUTTEST01")
    monkeypatch.setenv("WECHAT_MP_APPSECRET", "test-app-secret")
    registry._CACHE.clear()
    yield
    registry._CACHE.clear()


@pytest.fixture(autouse=True)
def no_auto_dispatch(monkeypatch):
    from app.core import queue

    monkeypatch.setattr(queue, "dispatch", lambda job_id, **kw: True)


def _png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (30, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def _upload_asset(client, headers) -> str:
    session = client.post("/api/v1/assets/upload-sessions", headers=headers)
    assert session.status_code in (200, 201), session.text
    res = client.post(
        f"/api/v1/assets?sessionId={session.json()['sessionId']}",
        files={"file": ("cover.png", _png_bytes(), "image/png")},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_push_stores_layout_snapshot_and_renders_with_theme(
    client, configured, monkeypatch
):
    from app.connectors import wechat_mp as wechat
    from app.core.db import SessionLocal
    from app import models

    monkeypatch.setattr(
        wechat.WeChatMPConnector,
        "_fetch_token",
        lambda self, force=False: {"access_token": "tok", "expires_in": 7200},
    )
    monkeypatch.setattr(
        wechat.WeChatMPConnector,
        "_api",
        lambda self, method, path, **kw: {"total_count": 0, "item": []},
    )
    monkeypatch.setattr(
        wechat.WeChatMPConnector,
        "upload_thumb",
        lambda self, db, **kw: {"mediaId": "THUMB-1", "url": None},
    )
    monkeypatch.setattr(
        wechat.WeChatMPConnector, "upload_inline_image", lambda self, db, **kw: "https://mmbiz.qpic.cn/i.jpg"
    )
    captured: dict = {}

    def fake_add_draft(self, db, *, article):
        captured["article"] = article
        return "DRAFT-1"

    monkeypatch.setattr(wechat.WeChatMPConnector, "add_draft", fake_add_draft)

    headers = _headers(client, "layout-push")
    bound = client.post("/api/v1/wechat-mp/bind", json={}, headers=headers)
    assert bound.status_code == 200, bound.text
    account_id = bound.json()["account"]["id"]
    cover = _upload_asset(client, headers)

    res = client.post(
        "/api/v1/wechat-mp/drafts",
        json={
            "accountId": account_id,
            "idempotencyKey": "layout-push-1",
            "title": "带主题的草稿",
            "body": "# 标题\n\n正文内容",
            "themeId": "deep-blue",
            "assetIds": [cover],
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    job_id = res.json()["id"]
    assert res.json()["themeId"] == "deep-blue"

    from app.services import job_runner

    with SessionLocal() as db:
        job = db.get(models.PublishJob, job_id)
        assert job.theme_id == "deep-blue"
        assert job.layout_snapshot["primaryColor"] == "#185FA5"
        job_runner._handle_platform_publish(
            db,
            type("J", (), {"payload": {"publishJobId": job_id}, "owner_user_id": job.owner_user_id})(),
        )
    assert "#185FA5" in captured["article"]["content"]
