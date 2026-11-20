"""独立排版配置的版本隔离、输入校验与发布快照回归。"""
from __future__ import annotations

import pytest

from tests.test_wechat_mp import (
    _headers, _run_publish_job, _upload_asset, configured, no_auto_dispatch,
)


def _article(client, headers):
    response = client.post("/api/v1/content", json={"title": "排版文章", "type": "article"}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_studio_column_auto_migration_keeps_old_versions(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text
    from app.core import db as db_module
    from app import models

    engine = create_engine(f"sqlite:///{(tmp_path / 'studio-legacy.db').as_posix()}")
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE content_versions (id VARCHAR(32) PRIMARY KEY, title VARCHAR(200), body TEXT)"))
            conn.execute(text("INSERT INTO content_versions (id, title, body) VALUES ('old', '旧文章', '旧正文')"))
        monkeypatch.setattr(db_module, "engine", engine)
        db_module._apply_additive_migrations()
        db_module._apply_additive_migrations()
        db_module._sync_schema_from_models()
        db_module._sync_schema_from_models()
        with engine.connect() as conn:
            row = conn.execute(text("SELECT title, body, layout_options FROM content_versions WHERE id = 'old'")).one()
            assert row == ("旧文章", "旧正文", "{}")
    finally:
        engine.dispose()


def test_studio_options_persist_per_version_and_owner(client):
    from app import models
    from app.core.db import SessionLocal

    headers = _headers(client, "studio-owner")
    other = _headers(client, "studio-other")
    article = _article(client, headers)
    url = f"/api/v1/wechat-mp/layout-studio/articles/{article['id']}"
    initial = client.get(url, headers=headers)
    assert initial.status_code == 200, initial.text
    assert initial.json()["saved"] is False
    options = {"style": "zen", "theme": "forest", "banner": "card", "outro": False, "brandAuthor": "作者", "brandSlogan": "记录知识", "avatarUrl": "", "title": ""}
    saved = client.put(url, json={"options": options}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["options"] == options
    assert client.get(url, headers=headers).json()["options"] == options
    with SessionLocal() as db:
        version = db.get(models.ContentVersion, article["currentVersionId"])
        assert version.layout_options == options
        second = models.ContentVersion(content_item_id=article["id"], version_no=2, title="第二版", body="内容")
        db.add(second)
        db.commit()
        second_id = second.id
    assert client.get(url, params={"versionId": second_id}, headers=headers).json()["saved"] is False
    assert client.get(url, headers=other).status_code == 404
    assert client.put(url, json={"options": options}, headers=other).status_code == 404
    other_article = _article(client, other)
    assert client.get(url, params={"versionId": other_article["currentVersionId"]}, headers=headers).status_code == 404
    with SessionLocal() as db:
        item = db.get(models.ContentItem, article["id"])
        item.workspace_id = db.get(models.ContentItem, other_article["id"]).workspace_id
        db.commit()
    assert client.get(url, headers=headers).status_code == 404


@pytest.mark.parametrize("options", [
    {"style": "unknown"}, {"theme": "unknown"}, {"banner": "script"},
    {"outro": "yes"}, {"brandAuthor": "作" * 81}, {"brandSlogan": "字" * 201},
    {"avatarUrl": "javascript:alert(1)"}, {"avatarUrl": "http://example.com/a.png"},
    {"avatarUrl": "https://user:secret@example.com/a.png"}, {"title": "字" * 201},
    {"untrustedHtml": "<script>1</script>"},
])
def test_studio_options_reject_invalid_input(client, options):
    headers = _headers(client, "studio-invalid")
    response = client.post("/api/v1/wechat-mp/layout-studio/render", json={"body": "内容", "options": options}, headers=headers)
    assert response.status_code == 422, response.text


def test_studio_render_and_asset_access(client):
    headers = _headers(client, "studio-render")
    other = _headers(client, "studio-asset-other")
    asset = _upload_asset(client, other)
    denied = client.post("/api/v1/wechat-mp/layout-studio/render", json={"body": "内容", "assetIds": [asset]}, headers=headers)
    assert denied.status_code == 404
    response = client.post("/api/v1/wechat-mp/layout-studio/render", json={"body": "# 文章\n\n## 标题\n\n**正文**", "options": {"style": "dark", "theme": "blue", "outro": False}}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["stats"]["htmlChars"] == len(response.json()["html"])
    assert response.json()["options"]["style"] == "dark"


def test_studio_publish_snapshot_matches_preview_after_settings_change(client, configured, monkeypatch):
    from app import models
    from app.connectors import wechat_mp
    from app.core.db import SessionLocal

    captured = {}
    monkeypatch.setattr(wechat_mp.WeChatMPConnector, "ping", lambda self, db: {"draftTotal": 0, "appId": "wxTEST", "token": {}})
    monkeypatch.setattr(wechat_mp.WeChatMPConnector, "upload_thumb", lambda self, db, **kw: {"mediaId": "THUMB", "url": None})
    def add(self, db, *, article):
        captured.update(article)
        return "STUDIO-DRAFT"
    monkeypatch.setattr(wechat_mp.WeChatMPConnector, "add_draft", add)
    headers = _headers(client, "studio-snapshot")
    assert client.post("/api/v1/wechat-mp/bind", json={}, headers=headers).status_code == 200
    article = _article(client, headers)
    url = f"/api/v1/wechat-mp/layout-studio/articles/{article['id']}"
    options = {"style": "mag", "theme": "rose", "banner": "poster", "outro": False, "brandAuthor": "作者", "brandSlogan": "记录", "avatarUrl": "https://example.com/avatar.png", "title": "排版文章"}
    assert client.put(url, json={"options": options}, headers=headers).status_code == 200
    cover = _upload_asset(client, headers)
    payload = {"accountId": "", "contentItemId": article["id"], "versionId": article["currentVersionId"], "title": "排版文章", "body": "# 排版文章\n\n## 章节\n\n正文内容", "assetIds": [cover]}
    preview = client.post("/api/v1/wechat-mp/drafts/preview", json=payload, headers=headers)
    assert preview.status_code == 200, preview.text
    assert preview.json()["studioOptions"] == options
    queued = client.post("/api/v1/wechat-mp/drafts", json={**payload, "idempotencyKey": "studio-snapshot-001"}, headers=headers)
    assert queued.status_code == 201, queued.text
    job_id = queued.json()["id"]
    with SessionLocal() as db:
        job = db.get(models.PublishJob, job_id)
        assert job.layout_snapshot == options
        assert job.payload_snapshot["studioOptions"] == options
        assert job.payload_snapshot["versionId"] == article["currentVersionId"]
        assert "themeId" not in job.payload_snapshot
        assert "layoutTokens" not in job.payload_snapshot
    assert "themeId" not in queued.json()
    assert "themeId" not in preview.json()
    assert "themeName" not in preview.json()
    assert client.put(url, json={"options": {"style": "dark", "theme": "ink"}}, headers=headers).status_code == 200
    _run_publish_job(job_id)
    assert captured["content"] == preview.json()["html"]


def test_studio_warnings_are_structured_in_render_and_publish_preview(client):
    headers = _headers(client, "studio-warnings")
    body = "![危险图片](javascript:alert)\n\n{{image9}}"
    rendered = client.post("/api/v1/wechat-mp/layout-studio/render", json={"title": "真实文章标题", "body": body, "options": {"banner": "neo", "outro": False}}, headers=headers)
    assert rendered.status_code == 200, rendered.text
    output = rendered.json()
    assert output["stats"]["title"] == "真实文章标题"
    assert output["warnings"]
    assert all(item["level"] == "warning" and isinstance(item["message"], str) for item in output["warnings"])
    assert {"sourceChars", "renderedChars", "htmlChars", "images", "readingMinutes"} <= output["stats"].keys()
    preview = client.post("/api/v1/wechat-mp/drafts/preview", json={"title": "真实文章标题", "body": body}, headers=headers)
    assert preview.status_code == 200, preview.text
    assert any(item["level"] == "warning" for item in preview.json()["issues"])


def test_studio_endpoints_require_layout_permission(client):
    from tests.conftest import login

    headers = login(client, subject="studio-no-permission", role="member")
    article = _article(client, headers)
    url = f"/api/v1/wechat-mp/layout-studio/articles/{article['id']}"
    assert client.get(url, headers=headers).status_code == 403
    assert client.put(url, json={"options": {}}, headers=headers).status_code == 403
    assert client.post("/api/v1/wechat-mp/layout-studio/render", json={"body": "内容"}, headers=headers).status_code == 403
    assert client.get(url).status_code == 401


def test_studio_publish_preview_rejects_foreign_article(client):
    headers = _headers(client, "studio-preview-owner")
    other = _headers(client, "studio-preview-other")
    article = _article(client, other)
    response = client.post("/api/v1/wechat-mp/drafts/preview", json={"contentItemId": article["id"], "body": "内容"}, headers=headers)
    assert response.status_code == 404


@pytest.mark.parametrize("path", [
    "/wechat-mp/themes", "/wechat-mp/themes/schema",
    "/wechat-mp/themes/preferences", "/wechat-mp/components",
])
def test_legacy_layout_endpoints_are_not_found(client, path):
    headers = _headers(client, "studio-retired-api")
    assert client.get(f"/api/v1{path}", headers=headers).status_code == 404


@pytest.mark.parametrize("path", ["/wechat-mp/drafts", "/wechat-mp/drafts/preview"])
@pytest.mark.parametrize("legacy", [
    {"themeId": "ink-green"}, {"theme_id": "ink-green"},
    {"tokens": {}}, {"layoutTokens": {}},
])
def test_draft_endpoints_reject_retired_layout_fields(client, path, legacy):
    headers = _headers(client, "studio-retired-fields")
    response = client.post(
        f"/api/v1{path}",
        json={"accountId": "", "idempotencyKey": "studio-retired-001", "body": "正文", **legacy}
        if path.endswith("/drafts") else {"body": "正文", **legacy},
        headers=headers,
    )
    assert response.status_code == 422, response.text


def test_legacy_render_endpoint_is_removed(client):
    headers = _headers(client, "studio-retired-render")
    response = client.post("/api/v1/wechat-mp/drafts/render", json={"body": "正文"}, headers=headers)
    assert response.status_code == 405


def test_legacy_layout_api_and_models_are_removed(client):
    from app import models, schemas

    paths = client.get("/openapi.json").json()["paths"]
    for path in (
        "/api/v1/wechat-mp/themes",
        "/api/v1/wechat-mp/themes/schema",
        "/api/v1/wechat-mp/themes/preferences",
        "/api/v1/wechat-mp/themes/{theme_id}",
        "/api/v1/wechat-mp/components",
        "/api/v1/wechat-mp/drafts/render",
    ):
        assert path not in paths
    for name in ("WeChatRenderIn", "WeChatThemeIn", "WeChatThemeOut", "WeChatThemePrefsIn"):
        assert not hasattr(schemas, name)
    assert not hasattr(models, "WeChatTheme")
    assert "theme_id" not in models.PublishJob.__table__.columns
    assert "wechat_layout" not in models.UserPreference.__table__.columns
    assert "layout_snapshot" in models.PublishJob.__table__.columns
    assert "theme_id" not in schemas.WeChatDraftIn.model_fields
    assert "theme_id" not in schemas.WeChatDraftPreviewIn.model_fields
    assert "theme_id" not in schemas.PublishJobOut.model_fields
