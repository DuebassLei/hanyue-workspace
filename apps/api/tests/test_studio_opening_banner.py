import pytest

from app.core.deps import ApiError
from app.services import article_blocks, studio_render, wechat_layout_engine as engine
from tests.test_studio_layout import _client_with_article


LEGACY_SPEC = {
    "style": "poster",
    "poster": {"title1": "Legacy custom banner", "avatar": False},
}
BANNER_START = '<section style="margin:0 0 24px"><svg'


def _draft(**overrides):
    data = {
        "title": "Example title",
        "banner_style": "poster",
        "banners": {"poster": {"spec": LEGACY_SPEC}},
        "layout_options": {"banner": "card", "outro": False},
    }
    article_blocks.set_body(data, "A body sentence.")
    return {**data, **overrides}


def _banner(html):
    return html.partition("</section>")[0] if html.startswith(BANNER_START) else ""


def test_opening_banner_preserves_legacy_generated_and_automatic_modes():
    data = _draft()
    legacy = studio_render.render_article(data)
    assert "openingBanner" not in legacy["options"]
    assert "Legacy custom banner" in _banner(legacy["html"])
    assert legacy["html"].count(BANNER_START) == 1

    automatic = studio_render.render_article({**data, "banners": {}})
    assert _banner(automatic["html"])
    assert "Legacy custom banner" not in automatic["html"]


def test_opening_banner_request_overrides_explicit_storage_and_legacy():
    data = _draft(layout_options={"openingBanner": "neo", "banner": "card", "outro": False})
    stored = studio_render.render_article(data)
    assert stored["options"]["openingBanner"] == "neo"
    assert _banner(stored["html"])
    assert "Legacy custom banner" not in stored["html"]

    suppressed = studio_render.render_article(data, {"openingBanner": "none"})
    assert suppressed["options"]["openingBanner"] == "none"
    assert not _banner(suppressed["html"])
    assert "A body sentence." in suppressed["html"]
    assert data["layout_options"]["openingBanner"] == "neo"

    selected = studio_render.render_article(data, {"openingBanner": "poster"})
    assert selected["options"]["openingBanner"] == "poster"
    assert "Legacy custom banner" in _banner(selected["html"])
    assert selected["html"].count(BANNER_START) == 1


@pytest.mark.parametrize("mode", ["poster", "neo", "card"])
def test_opening_banner_does_not_require_a_generated_spec(mode):
    data = _draft(banners={}, layout_options={"outro": False})
    result = studio_render.render_article(data, {"openingBanner": mode})
    expected = engine.render_studio_document("A body sentence.", {"title": data["title"], "banner": mode, "outro": False})
    assert result["options"]["openingBanner"] == mode
    assert _banner(result["html"]) == _banner(expected["html"]) != ""
    assert result["html"].count(BANNER_START) == 1


@pytest.mark.parametrize("value", ["scene", None, []])
def test_opening_banner_rejects_invalid_values(value):
    with pytest.raises(ApiError) as error:
        studio_render.effective_options(_draft(), {"openingBanner": value})
    assert error.value.status_code == 422
    assert "openingBanner" in str(error.value)


def test_opening_banner_catalog_compares_all_styles_with_fixed_content():
    items = {item["id"]: item["previewHtml"] for item in studio_render.catalog()["banners"]}
    assert set(items) == {"none", "poster", "neo", "card"}
    assert not _banner(items["none"])
    assert len(set(items.values())) == 4
    for mode in ("poster", "neo", "card"):
        assert _banner(items[mode])
        assert "GIF_URL_HERE" not in items[mode]


def test_opening_banner_options_save_and_reopen_without_generating(client):
    headers, article = _client_with_article(client, "opening-banner-roundtrip")
    base = f"/api/v1/studio/articles/{article['id']}"

    original = client.get(f"{base}/render/options", headers=headers)
    assert original.status_code == 200, original.text
    assert "openingBanner" not in original.json()["options"]
    assert all(item["previewHtml"] for item in original.json()["catalog"]["banners"])

    saved = client.post(f"{base}/layout/preferences", json={"openingBanner": "card"}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["layoutOptions"] == {"openingBanner": "card"}
    assert saved.json()["banners"] == article["banners"]
    assert saved.json()["job"] is None

    reopened = client.get(f"{base}/render/options", headers=headers)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["options"]["openingBanner"] == "card"
    assert reopened.json()["catalog"]["banners"] == original.json()["catalog"]["banners"]

    for options in ({}, {"openingBanner": "none"}):
        rendered = client.post(f"{base}/render", json={"options": options}, headers=headers)
        assert rendered.status_code == 200, rendered.text
        assert bool(_banner(rendered.json()["html"])) == (not options)
