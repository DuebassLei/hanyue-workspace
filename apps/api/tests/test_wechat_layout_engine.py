"""Unit tests for the WeChat layout enhancement engine (wechat_layout_engine).

The engine is intentionally pure: no database, no TestClient, no network.
These tests only exercise its public surface and the WeChat-safe output
contract (inline presentation attributes only, no class/id/style/script).
"""

import copy
import re

import pytest

from app.services import wechat_layout_engine as engine

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
UNSAFE = ("class=", " id=", "<style", "<script", "background-image")

# Representative, valid line payloads for each of the 8 card renderers.
# Format follows what each renderer expects from _sSplit().
SAMPLE_LINES = {
    "svg-table": ["指标|数值", "★转化|12.5%", "留存|38%"],
    "svg-bars": ["A|45%", "B|80%", "C|30%"],
    "svg-steps": ["第一步|打开设置", "第二步|点击保存"],
    "svg-timeline": ["2020|发布", "2021|增长", "2022|爆发"],
    "svg-breakdown": ["模块A|内容A", "模块B|内容B"],
    "svg-compare": ["传统|新法", "慢|快", "贵|省"],
    "svg-concept": ["核心概念", "旧|旧A|旧B", "现在|新A|新B", "!一句话备注"],
    "svg-phone": ["示意演示", "场景1|我|你好呀", "场景2|对方|收到"],
}


def safe(html):
    low = (html or "").lower()
    for token in UNSAFE:
        assert token not in low, f"unsafe token {token!r} leaked into body html"


# ---------------------------------------------------------------------------
# 1. Catalog integrity
# ---------------------------------------------------------------------------


def test_catalog_counts():
    assert len(engine.THEMES) == 15
    assert len(engine.STYLES) == 10
    assert len(engine.SVG_CARD_KINDS) == 8
    assert len(engine.SVG_CARD_INFO) == 8
    assert set(engine.SVG_CARD_KINDS) == set(engine.SVG_CARD_INFO)


@pytest.mark.parametrize("key", list(engine.THEMES))
def test_theme_shape(key):
    theme = engine.THEMES[key]
    assert theme["name"], f"{key} missing name"
    for field in ("main", "soft", "tint", "line", "mark"):
        assert HEX.match(theme[field]), f"{key}.{field} is not a #hex color: {theme[field]!r}"


@pytest.mark.parametrize("key", list(engine.STYLES))
def test_style_shape(key):
    style = engine.STYLES[key]
    assert style["name"], f"{key} missing name"
    assert isinstance(style["r"], int), f"{key}.r must be an int radius"
    assert style["desc"], f"{key} missing desc"


@pytest.mark.parametrize("key", engine.SVG_CARD_KINDS)
def test_card_info_shape(key):
    info = engine.SVG_CARD_INFO[key]
    assert info["name"] and info["lead"], f"{key} needs name and lead"


def test_resolve_style_theme_returns_a_copy():
    ctx = engine.resolve_style_theme("mag", "blue")
    assert ctx["st"] == "mag"
    assert ctx["main"] == engine.THEMES["blue"]["main"]
    ctx["main"] = "#000000"
    assert engine.THEMES["blue"]["main"] != "#000000", "resolve_style_theme must not mutate THEMES"


# ---------------------------------------------------------------------------
# 2. Every card renders as WeChat-safe SVG
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", engine.SVG_CARD_KINDS)
def test_each_card_renders_clean_svg(kind):
    out = engine.render_svg_card(kind, "示例标题", SAMPLE_LINES[kind])
    assert out["warnings"] == [], out["warnings"]
    assert "<svg" in out["html"]
    assert "viewBox" in out["html"]
    safe(out["html"])


@pytest.mark.parametrize("kind", engine.SVG_CARD_KINDS)
def test_every_card_uses_smil_animation(kind):
    out = engine.render_svg_card(kind, "示例标题", SAMPLE_LINES[kind])
    assert "<animate" in out["html"] or "<animateTransform" in out["html"]


# ---------------------------------------------------------------------------
# 3. 10 styles x several themes, and fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("style", list(engine.STYLES))
@pytest.mark.parametrize("theme", ["orange", "blue", "teal", "ink", "forest"])
def test_style_theme_cross(style, theme):
    out = engine.render_svg_card("svg-table", "示例", ["列1|列2", "★v|12"], style=style, theme=theme)
    assert out["warnings"] == []
    assert engine.THEMES[theme]["main"] in out["html"], "theme main color must appear in output"
    safe(out["html"])


def test_themes_produce_distinct_html():
    htmls = [
        engine.render_svg_card("svg-table", "示例", ["列1|列2", "★v|12"], theme=t)["html"]
        for t in engine.THEMES
    ]
    assert len(set(htmls)) == 15


def test_implemented_styles_produce_distinct_html():
    # 每个风格都有独立的卡框分支（note/zen/bento 于本次优化补齐）。
    styles = list(engine.STYLES)
    htmls = [
        engine.render_svg_card("svg-table", "示例", ["列1|列2", "★v|12"], style=s)["html"]
        for s in styles
    ]
    assert len(set(htmls)) == len(styles)


def test_invalid_style_and_theme_fall_back():
    bad = engine.render_svg_card(
        "svg-table", "示例", ["列1|列2", "★v|12"], style="__nope__", theme="__nope__"
    )
    default = engine.render_svg_card("svg-table", "示例", ["列1|列2", "★v|12"])
    assert bad["html"] == default["html"]
    assert bad["warnings"] == []
    assert engine.resolve_style_theme("__nope__", "__nope__") == engine.resolve_style_theme("std", "orange")


def test_unknown_card_kind_does_not_crash():
    out = engine.render_svg_card("svg-nope", "示例", [])
    assert out["html"], "未知卡种也要回退成可读代码框"
    assert "background:#0B0B0C" in out["html"]
    assert "TypeError" in out["warnings"][0]


# ---------------------------------------------------------------------------
# 4. Malformed content degrades to a readable code box
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,lines",
    [
        ("svg-compare", ["只有一个表头"]),
        ("svg-compare", []),
        ("svg-table", []),
        ("svg-bars", []),
        ("svg-bars", ["只有名字"]),
        ("svg-concept", ["只有一句话"]),
        ("svg-phone", ["只有副标题"]),
    ],
)
def test_bad_format_degrades_to_code_box(kind, lines):
    out = engine.render_svg_card(kind, "示例", lines)
    assert out["warnings"], "malformed input must produce a warning"
    assert "background:#0B0B0C" in out["html"]
    assert "<svg" not in out["html"]
    safe(out["html"])


# ---------------------------------------------------------------------------
# 5. Banner
# ---------------------------------------------------------------------------


def test_check_banner_catches_poster_overlength():
    spec = {
        "style": "poster",
        "poster": {"eyebrow": "眉" * 11, "title1": "标" * 9, "title2": "副" * 9, "big": "大" * 3, "right": "stat"},
    }
    warnings = engine.check_banner(spec)
    assert len(warnings) == 4
    assert any("眉标" in w for w in warnings)


def test_check_banner_catches_card_overlength():
    warnings = engine.check_banner(
        {"style": "card", "card": {"eyebrow": "眉" * 16, "title1": "标" * 10, "title2": "副" * 10, "sub": "词" * 21}}
    )
    assert len(warnings) == 4


def test_check_banner_unknown_style_and_bad_spec():
    # 非 poster/neo/card 会走参考实现的场景模板自检分支（scene 渲染未移植）
    assert any("场景模板" in w for w in engine.check_banner({"style": "mystery"}))
    assert engine.check_banner("not a dict")


@pytest.mark.parametrize("theme", ["orange", "blue", "teal", "__bad__"])
@pytest.mark.parametrize("spec", [
    {"style": "poster", "poster": {"eyebrow": "开篇", "title1": "你好", "title2": "世界", "big": "新",
                                   "right": "flow", "ask": "帮我排好", "result": "排好了"}},
    {"style": "card", "card": {"eyebrow": "导语", "title1": "标题", "title2": "副题", "sub": "副标题"}},
])
def test_render_banner_is_wechat_safe(spec, theme):
    out = engine.render_banner(spec, theme=theme)
    assert out["warnings"] == []
    assert "<svg" in out["html"]
    assert "viewBox" in out["html"]
    safe(out["html"])


def test_render_banner_bad_spec_does_not_crash():
    out = engine.render_banner("not a dict")
    assert out["html"] == ""
    assert out["warnings"]


# ---------------------------------------------------------------------------
# 6. Full HTML preview page (iframe only) must not pollute body renderers
# ---------------------------------------------------------------------------


def test_render_preview_page_is_full_html_with_palette_data():
    page = engine.render_preview_page("<p>hello</p>", title="预览", style="mag", theme="blue", report="ok")
    low = page.lower()
    assert "<!doctype" in low
    assert "<style" in low and "<script" in low
    assert "swatches" in page and "palette" in page and "themes" in page
    assert "#2952B6" in page, "selected theme main color must be present"
    assert "品牌橙" in page, "palette must carry theme display names"
    assert "<p>hello</p>" in page and "ok" in page


def test_preview_page_does_not_pollute_body_renderers():
    themes_before = copy.deepcopy(engine.THEMES)
    styles_before = copy.deepcopy(engine.STYLES)
    lines = SAMPLE_LINES["svg-table"]

    before = engine.render_svg_card("svg-table", "示例", lines, theme="orange")["html"]
    engine.render_preview_page(before, title="预览", theme="blue")
    after = engine.render_svg_card("svg-table", "示例", lines, theme="orange")["html"]

    assert after == before
    safe(after)
    assert engine.THEMES == themes_before and engine.STYLES == styles_before


# ---------------------------------------------------------------------------
# 7. Determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", engine.SVG_CARD_KINDS)
def test_card_render_is_deterministic(kind):
    a = engine.render_svg_card(kind, "确定", SAMPLE_LINES[kind], style="neo", theme="violet")
    b = engine.render_svg_card(kind, "确定", SAMPLE_LINES[kind], style="neo", theme="violet")
    assert a == b


def test_banner_and_preview_are_deterministic():
    spec = {"style": "poster", "poster": {"title1": "你好", "title2": "世界"}}
    assert engine.render_banner(spec, theme="teal") == engine.render_banner(spec, theme="teal")
    assert engine.render_preview_page("<p>x</p>", theme="rose") == engine.render_preview_page(
        "<p>x</p>", theme="rose"
    )
