"""成稿台排版渲染接口：渲染结果、选项合并、目录、owner 隔离。"""

from tests.conftest import login

MARKDOWN = """# 原标题

第一段，讲清楚问题是什么，为什么要现在处理。

## 小标题

第二段，给出可以照着做的结论。
"""


def _client_with_article(client, subject="studio-layout-user", markdown=MARKDOWN):
    headers = login(client, subject=subject)
    response = client.post(
        "/api/v1/studio/articles/new",
        json={"topic": "排版选题", "material": "素材", "markdown": markdown, "titles": False},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return headers, response.json()


def _set_stored(article_id: str, **fields) -> None:
    """直接改库里的稿件字段（选项 / 横幅），跳过接口层。"""
    from app import models
    from app.core.db import SessionLocal
    from app.services import studio_repo

    with SessionLocal() as db:
        row = db.get(models.StudioArticle, article_id)
        data = studio_repo.row_to_dict(row)
        data.update(fields)
        studio_repo.save(db, row, data)
        db.commit()


def test_render_returns_html_stats_and_warnings(client):
    headers, article = _client_with_article(client, "layout-render")
    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/render", json={}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()

    html = body["html"]
    assert html and "<section" in html
    # 引擎输出不应残留模板占位符
    assert "{{" not in html and "}}" not in html
    assert "${" not in html

    stats = body["stats"]
    assert stats["sourceChars"] > 0
    assert stats["htmlChars"] == len(html)
    assert stats["readingMinutes"] >= 1
    assert isinstance(stats["images"], int)

    assert isinstance(body["warnings"], list)
    for warning in body["warnings"]:
        assert set(warning) >= {"field", "level", "message"}

    assert body["options"]["style"] == "std"
    assert body["options"]["theme"] == "orange"


def test_render_options_override_stored_and_fill_title(client):
    headers, article = _client_with_article(client, "layout-options")
    _set_stored(article["id"], layout_options={"style": "mag", "theme": "ink", "title": "已存标题"})

    # 已存值在请求没给时保留；请求给的字段覆盖
    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/render",
        json={"options": {"style": "min", "title": "自定义标题"}},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    options = response.json()["options"]
    assert options["style"] == "min"
    assert options["theme"] == "ink"
    assert options["title"] == "自定义标题"

    # 不传 title 时用稿件标题兜底
    plain = client.post(
        f"/api/v1/studio/articles/{article['id']}/render",
        json={"options": {"theme": "blue"}},
        headers=headers,
    )
    assert plain.status_code == 200, plain.text
    fallback = plain.json()["options"]
    assert fallback["title"] == "已存标题"
    assert fallback["theme"] == "blue"
    assert fallback["style"] == "mag"


def test_empty_article_renders_without_error(client):
    headers, article = _client_with_article(client, "layout-empty", markdown="")
    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/render", json={}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["html"]
    assert body["stats"]["readingMinutes"] >= 1
    assert body["options"]["title"] == ""


def test_render_puts_stored_banner_before_body(client):
    headers, article = _client_with_article(client, "layout-banner")
    spec = {
        "style": "poster",
        "poster": {
            "eyebrow": "文章精选", "title1": "原标题", "title2": "排版效果",
            "big": "原", "right": "flow", "ask": "帮我排好这篇",
            "result": "出稿了", "result_sub": "直接贴进公众号",
        },
    }
    _set_stored(article["id"], banner_style="poster", banners={"poster": {"spec": spec}})

    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/render", json={}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    html = body["html"]
    # 横幅在最前面，且不会因为 options.banner 再画一条
    assert html.lstrip().startswith("<section")
    assert "<svg" in html.split("</section>")[0]
    assert body["options"]["banner"] == "none"


def test_render_options_returns_full_catalog(client):
    headers, article = _client_with_article(client, "layout-catalog")
    response = client.get(
        f"/api/v1/studio/articles/{article['id']}/render/options", headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["options"]["style"] == "std"

    styles = body["catalog"]["styles"]
    themes = body["catalog"]["themes"]
    cards = body["catalog"]["svgCards"]
    assert styles and themes and cards

    assert all({"id", "name", "desc"} <= set(item) for item in styles)
    assert all({"id", "name", "main", "soft", "tint"} <= set(item) for item in themes)
    assert all({"id", "name", "lead", "previewHtml"} <= set(item) for item in cards)
    assert all("<svg" in item["previewHtml"] for item in cards)
    assert "svg-steps" in {item["id"] for item in cards}

    banners = body["catalog"]["banners"]
    assert [item["id"] for item in banners] == ["none", "poster", "neo", "card"]
    defaults = body["catalog"]["defaults"]
    assert defaults["style"] == "std" and defaults["theme"] == "orange"
    assert defaults["banner"] == "none" and defaults["outro"] is True


def test_render_is_owner_scoped(client):
    _headers, article = _client_with_article(client, "layout-owner-a")
    other = login(client, subject="layout-owner-b")

    detail = client.get(
        f"/api/v1/studio/articles/{article['id']}/render/options", headers=other
    )
    assert detail.status_code == 404

    rendered = client.post(
        f"/api/v1/studio/articles/{article['id']}/render", json={}, headers=other
    )
    assert rendered.status_code == 404
