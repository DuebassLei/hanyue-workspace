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


def test_row_blocks_keep_content_and_stay_wechat_safe():
    from app.services import wechat_layout

    body = (
        ":::lead\n先说结论，再展开。\n:::\n\n"
        ":::stat\n12% | 转化率\n3 | 倍增速\n:::\n\n"
        ":::steps\n定问题 | 先写清楚要回答什么\n找证据 | 每个数字都要有出处\n:::\n\n"
        ":::compare 旧做法 / 新做法\n凭感觉 | 先核对来源\n:::\n\n"
        ":::layers\n现象 | 阅读量掉了\n原因 | 标题没有承诺\n:::\n\n"
        ":::checklist\ndone | 核对数字\nmaybe | 补一张图\n:::\n\n"
        ":::quote-card 某位编辑\n这句话值得单独留下。\n:::\n"
    )
    result = wechat_layout.render_document(body, None, [])
    html = result["html"]
    for text in (
        "先说结论",
        "12%",
        "转化率",
        "定问题",
        "找证据",
        "凭感觉",
        "先核对来源",
        "阅读量掉了",
        "核对数字",
        "补一张图",
        "这句话值得单独留下",
        "某位编辑",
    ):
        assert text in html
    assert "tabular-nums" in html
    assert "border-radius:999px" in html
    assert ">1<" in html and ">2<" in html
    assert "✓" in html
    assert "○" in html  # 不认识的状态默认空心圆，正文不掉进标记列
    for forbidden in ("<style", "<script", "<div", "<h1", "<table", "class=", " id="):
        assert forbidden not in html
    assert wechat_layout.lint_html(html) == [] or all(
        item["level"] != "error" for item in wechat_layout.lint_html(html)
    )


def test_follow_block_has_layered_call_to_action_and_escapes_content():
    from app.services import wechat_layout

    result = wechat_layout.render_document(
        ":::follow\n喜欢 <这类> 内容。\n点个关注，后续更新不迷路。\n:::",
        None,
        [],
    )
    html = result["html"]
    assert "关注我们" in html
    assert "点个关注，不错过更新" in html
    assert "喜欢 &lt;这类&gt; 内容。<br/>点个关注" in html
    assert "border-left:3px solid" in html
    assert result["warnings"] == []
    assert wechat_layout.lint_html(html) == []


def test_new_heading_styles_and_presets_normalize():
    from app.services import wechat_layout

    for theme_id in ("news-ink", "paper-cinnabar", "magazine-slate", "friendly-pill"):
        tokens = wechat_layout.PRESET_THEMES[theme_id]["tokens"]
        normalized = wechat_layout.normalize_tokens(tokens)
        html = wechat_layout.render_document("# 主标题\n\n## 小标题\n\n- 一条", normalized, [])["html"]
        assert "主标题" in html and "小标题" in html
        for forbidden in ("<div", "<h1", "<h2", "<style", "class="):
            assert forbidden not in html
    pill = wechat_layout.render_document(
        "# 主标题\n\n## 小标题",
        wechat_layout.PRESET_THEMES["friendly-pill"]["tokens"],
        [],
    )["html"]
    assert pill.count("border-radius:999px") == 2
    hairline = wechat_layout.render_document(
        "## 小标题", wechat_layout.PRESET_THEMES["news-ink"]["tokens"], []
    )["html"]
    assert "0.8px solid" in hairline


# ---------- 主题 API ----------


def test_theme_crud_and_preset_protection(client):
    headers = _headers(client)
    listing = client.get("/api/v1/wechat-mp/themes", headers=headers)
    assert listing.status_code == 200, listing.text
    data = listing.json()
    assert data["defaultThemeId"] == "ink-green"
    assert len(data["presets"]) == 10
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


# ---------- 小组件注册表与 token schema ----------


def test_layout_components_cover_all_fences():
    from app.services import wechat_layout

    ids = {item["id"] for item in wechat_layout.LAYOUT_COMPONENTS}
    assert ids == set(wechat_layout.FENCE_BLOCKS)
    for item in wechat_layout.LAYOUT_COMPONENTS:
        assert item["label"] and item["snippet"] and item["sampleBody"]
        # snippet 是正文中间插入的围栏块：前后空行包裹，以 ::: 开头的围栏合法
        assert item["snippet"].startswith("\n\n:::") and item["snippet"].endswith(":::\n")
        result = wechat_layout.render_document(item["sampleBody"], None, [])
        assert result["html"], item["id"]
        assert result["warnings"] == [], f"{item['id']} 示例应零告警：{result['warnings']}"
        assert wechat_layout.lint_html(result["html"]) == []


def test_components_endpoint(client):
    headers = _headers(client)
    res = client.get("/api/v1/wechat-mp/components", headers=headers)
    assert res.status_code == 200, res.text
    components = res.json()["components"]
    assert {item["id"] for item in components} == {
        "lead", "card", "stat", "steps", "compare", "layers", "checklist",
        "quote-card", "follow", "sign",
    }


def test_theme_schema_covers_known_keys():
    from app.services import wechat_layout

    fields = wechat_layout.token_schema()
    assert {item["key"] for item in fields} == wechat_layout.known_token_keys()
    for item in fields:
        assert item["label"] and item["group"] and item["kind"]
        if item["kind"] == "number":
            low, high = wechat_layout.NUMERIC_RANGES[item["key"]]
            assert (item["min"], item["max"]) == (low, high)
        if item["kind"] == "enum":
            assert {opt["value"] for opt in item["options"]} == wechat_layout.ENUMS[item["key"]]


def test_theme_schema_endpoint(client):
    headers = _headers(client)
    res = client.get("/api/v1/wechat-mp/themes/schema", headers=headers)
    assert res.status_code == 200, res.text
    assert len(res.json()["fields"]) == 18


# ---------- 排版偏好（默认 / 收藏 / 隐藏） ----------


def test_theme_prefs_round_trip_and_flags(client):
    headers = _headers(client, "layout-prefs")
    listing = client.get("/api/v1/wechat-mp/themes", headers=headers)
    data = listing.json()
    assert data["defaultThemeId"] == "ink-green"
    assert data["favoritePresetIds"] == [] and data["hiddenPresetIds"] == []
    assert all(not item["favorite"] and not item["hidden"] for item in data["presets"])

    saved = client.put(
        "/api/v1/wechat-mp/themes/preferences",
        json={
            "defaultThemeId": "blue-business",
            "favoritePresetIds": ["blue-business", "ink-green"],
            "hiddenPresetIds": ["mono"],
        },
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    data = saved.json()
    assert data["defaultThemeId"] == "blue-business"
    flags = {item["id"]: (item["favorite"], item["hidden"]) for item in data["presets"]}
    assert flags["blue-business"] == (True, False)
    assert flags["mono"] == (False, True)

    # 全量替换语义：重发时省略的字段回到空
    again = client.put(
        "/api/v1/wechat-mp/themes/preferences",
        json={"defaultThemeId": "ink-green"},
        headers=headers,
    ).json()
    assert again["favoritePresetIds"] == [] and again["hiddenPresetIds"] == []
    # 偏好不影响外观主题（theme 列）：/preferences 仍能正常读
    pref = client.get("/api/v1/preferences", headers=headers)
    assert pref.status_code == 200 and pref.json()["theme"] == "system"


def test_theme_prefs_validation_and_invariants(client):
    headers = _headers(client, "layout-prefs-bad")
    bad = client.put(
        "/api/v1/wechat-mp/themes/preferences",
        json={"favoritePresetIds": ["nope"]},
        headers=headers,
    )
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_prefs"

    # 别人的自定义主题不能当默认
    other = _headers(client, "layout-prefs-other")
    created = client.post(
        "/api/v1/wechat-mp/themes",
        json={"name": "他人主题", "baseThemeId": "deep-blue"},
        headers=other,
    )
    foreign_id = created.json()["id"]
    res = client.put(
        "/api/v1/wechat-mp/themes/preferences",
        json={"defaultThemeId": foreign_id},
        headers=headers,
    )
    assert res.status_code == 422 and res.json()["code"] == "invalid_prefs"

    # 默认 ∈ 隐藏 → 默认获胜，从 hidden 剔除
    res = client.put(
        "/api/v1/wechat-mp/themes/preferences",
        json={"defaultThemeId": "mono", "hiddenPresetIds": ["mono"]},
        headers=headers,
    ).json()
    assert res["defaultThemeId"] == "mono"
    assert res["hiddenPresetIds"] == []

    # 默认主题被删除 → 读时回退系统默认（不写库、不报错）
    mine = client.post(
        "/api/v1/wechat-mp/themes",
        json={"name": "临时默认", "baseThemeId": "mono"},
        headers=headers,
    ).json()
    client.put(
        "/api/v1/wechat-mp/themes/preferences",
        json={"defaultThemeId": mine["id"]},
        headers=headers,
    )
    assert client.delete(f"/api/v1/wechat-mp/themes/{mine['id']}", headers=headers).status_code == 204
    data = client.get("/api/v1/wechat-mp/themes", headers=headers).json()
    assert data["defaultThemeId"] == "ink-green"


def test_theme_import_export_round_trip(client):
    """导出的全量 tokens 不带 baseThemeId 重新 POST，应精确还原（导入零后端代码）。"""
    headers = _headers(client, "layout-import")
    created = client.post(
        "/api/v1/wechat-mp/themes",
        json={"name": "待导出", "baseThemeId": "terracotta-warm"},
        headers=headers,
    ).json()
    exported = next(item for item in client.get("/api/v1/wechat-mp/themes", headers=headers).json()["custom"] if item["id"] == created["id"])
    assert exported["tokens"] == created["tokens"]

    imported = client.post(
        "/api/v1/wechat-mp/themes",
        json={"name": "导入副本", "tokens": exported["tokens"]},
        headers=headers,
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()["tokens"] == exported["tokens"]


def test_render_accepts_raw_tokens_for_editor_preview(client):
    headers = _headers(client, "layout-raw-tokens")
    res = client.post(
        "/api/v1/wechat-mp/drafts/render",
        json={"body": "# 标题", "tokens": {"bodySize": 17, "primaryColor": "#123456"}},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert "font-size:20" in res.json()["html"]  # h1 = bodySize + 3（normalize 后是 float）
    assert "#123456" in res.json()["html"]

    bad = client.post(
        "/api/v1/wechat-mp/drafts/render",
        json={"body": "正文", "tokens": {"bodySize": 99}},
        headers=headers,
    )
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_theme"


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
