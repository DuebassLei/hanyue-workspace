"""SVG 动画卡内容校验（wechat_svg_cards）的单元测试。

覆盖三层：
1. validate_svg_card —— 8 种卡型的严格格式校验 + 柱状图数值防幻觉；
2. card_warnings —— 渲染期宽松提示（只报渲染器发现不了的问题）；
3. draft_card —— 生成编排（AI 网关 mock），以及渲染期/HTTP 路由的集成路径。
"""

import asyncio

import pytest

from app.core.deps import ApiError
from app.services import ai_gateway, wechat_layout_engine as engine
from app.services import wechat_svg_cards as cards

from .conftest import login

# 与 test_wechat_layout_engine.SAMPLE_LINES 相同的合法样例：校验器必须接受
# 渲染器已经验收过的写法。
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


# ---------------------------------------------------------------------------
# 1. 合法内容必须全部通过
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", list(SAMPLE_LINES))
def test_sample_lines_pass_validation(kind):
    card = cards.validate_svg_card(kind, "示例标题", SAMPLE_LINES[kind])
    assert card["kind"] == kind
    assert card["title"] == "示例标题"
    assert card["lines"] == SAMPLE_LINES[kind]


def test_empty_title_falls_back_to_lead():
    card = cards.validate_svg_card("svg-bars", "", ["A|1"])
    assert card["title"] == engine.SVG_CARD_INFO["svg-bars"]["lead"]


def test_table_separator_rows_are_ignored():
    lines = ["指标|数值", "---|---", "转化|12.5%", "留存|38%"]
    card = cards.validate_svg_card("svg-table", "示例", lines)
    assert card["lines"] == lines


def test_bars_numbers_from_source_pass():
    source = "本季度转化率从 45% 提升到 80%，订单量 1,234 单。"
    lines = ["转化率|45%|45%", "转化率目标|80%", "订单|1,234"]
    cards.validate_svg_card("svg-bars", "示例", lines, source_text=source)


# ---------------------------------------------------------------------------
# 2. 通用内容规则
# ---------------------------------------------------------------------------


def test_title_rules():
    with pytest.raises(cards.SvgCardError, match="40"):
        cards.validate_svg_card("svg-bars", "长" * 41, ["A|1"])
    with pytest.raises(cards.SvgCardError, match="反引号"):
        cards.validate_svg_card("svg-bars", "标题|含竖线", ["A|1"])


def test_line_rules():
    with pytest.raises(cards.SvgCardError, match="10 行"):
        cards.validate_svg_card("svg-table", "示例", ["列|表"] + [f"r{i}|v" for i in range(10)])
    with pytest.raises(cards.SvgCardError, match="180"):
        cards.validate_svg_card("svg-table", "示例", ["列|表", "a|" + "长" * 180])
    with pytest.raises(cards.SvgCardError, match="围栏"):
        cards.validate_svg_card("svg-table", "示例", ["列|表", "```"])
    with pytest.raises(cards.SvgCardError, match="<"):
        cards.validate_svg_card("svg-table", "示例", ["列|表", "a|<b>"])
    with pytest.raises(cards.SvgCardError, match="金句"):
        cards.validate_svg_card("svg-table", "示例", ["列|表", "a|看这>里"])


def test_quote_rules():
    with pytest.raises(cards.SvgCardError, match="最多一句"):
        cards.validate_svg_card("svg-table", "示例", ["> 第一句", "列|表", "> 第二句"])
    with pytest.raises(cards.SvgCardError, match="最后一行"):
        cards.validate_svg_card("svg-table", "示例", ["> 金句", "列|表", "a|v"])
    with pytest.raises(cards.SvgCardError, match="至少一行"):
        cards.validate_svg_card("svg-bars", "示例", ["> 只有金句"])
    # 正常金句：最后一行、内容行在前
    cards.validate_svg_card("svg-table", "示例", ["列|表", "a|v", "> 数据不会说谎"])


def test_unknown_kind_rejected():
    with pytest.raises(cards.SvgCardError, match="未知"):
        cards.validate_svg_card("svg-nope", "示例", [])
    with pytest.raises(cards.SvgCardError, match="未知"):
        cards.validate_svg_card("svg-bars2", "示例", ["A|1"])


# ---------------------------------------------------------------------------
# 3. 各卡型专属规则
# ---------------------------------------------------------------------------


def test_table_rules():
    with pytest.raises(cards.SvgCardError, match="两列"):
        cards.validate_svg_card("svg-table", "示例", ["只有一列", "a|v"])
    with pytest.raises(cards.SvgCardError, match="1-2"):
        cards.validate_svg_card("svg-table", "示例", ["列1|列2", "a|v|extra|more"])


def test_steps_rules():
    with pytest.raises(cards.SvgCardError, match="2-6"):
        cards.validate_svg_card("svg-steps", "示例", ["唯一一步|说明"])
    with pytest.raises(cards.SvgCardError, match="20"):
        cards.validate_svg_card("svg-steps", "示例", ["超" * 21 + "|说明", "第二步|说明"])
    with pytest.raises(cards.SvgCardError, match="两列"):
        cards.validate_svg_card("svg-steps", "示例", ["第一步|说明|多列", "第二步|说明"])


def test_timeline_rules():
    with pytest.raises(cards.SvgCardError, match="1-6"):
        cards.validate_svg_card("svg-timeline", "示例", [f"202{i}|事件" for i in range(7)])
    with pytest.raises(cards.SvgCardError, match="两列"):
        cards.validate_svg_card("svg-timeline", "示例", ["2020"])


def test_breakdown_rules():
    with pytest.raises(cards.SvgCardError, match="12"):
        cards.validate_svg_card("svg-breakdown", "示例", ["长" * 13 + "|解释"])
    with pytest.raises(cards.SvgCardError, match="两列"):
        cards.validate_svg_card("svg-breakdown", "示例", ["词条|解释|多列"])


def test_compare_rules():
    with pytest.raises(cards.SvgCardError, match="两个表头"):
        cards.validate_svg_card("svg-compare", "示例", ["单表头", "慢|快"])
    with pytest.raises(cards.SvgCardError, match="两列"):
        cards.validate_svg_card("svg-compare", "示例", ["左|右", "慢|快|多列"])


def test_concept_rules():
    with pytest.raises(cards.SvgCardError, match="「!」"):
        cards.validate_svg_card("svg-concept", "示例", ["!备注一", "!备注二", "现在|新A"])
    with pytest.raises(cards.SvgCardError, match="1-2 条轨道"):
        cards.validate_svg_card("svg-concept", "示例", ["旧|A", "中|B", "现在|C"])
    with pytest.raises(cards.SvgCardError, match="1-4 个"):
        cards.validate_svg_card("svg-concept", "示例", ["旧|A|B|C|D|E"])
    with pytest.raises(cards.SvgCardError, match="8 字"):
        cards.validate_svg_card("svg-concept", "示例", ["轨道名超长" + "了呀呀呀|A", "现在|B"])


def test_phone_rules():
    with pytest.raises(cards.SvgCardError, match="金句"):
        cards.validate_svg_card("svg-phone", "示例", ["副标题", "场景|我|你好", "> 金句"])
    with pytest.raises(cards.SvgCardError, match="三列"):
        cards.validate_svg_card("svg-phone", "示例", ["副标题", "场景|我"])
    with pytest.raises(cards.SvgCardError, match="11 字"):
        cards.validate_svg_card("svg-phone", "示例", ["副标题", "场景名太长" + "超出上限非常多" + "|我|你好"])
    with pytest.raises(cards.SvgCardError, match="最多一行"):
        cards.validate_svg_card("svg-phone", "示例", ["副标题一", "副标题二", "场景|我|你好"])


# ---------------------------------------------------------------------------
# 4. 柱状图数值防幻觉
# ---------------------------------------------------------------------------


def test_bars_hallucinated_number_rejected():
    source = "本季度转化率从 45% 提升到 80%。"
    with pytest.raises(cards.SvgCardError, match="没有出现在目标段落"):
        cards.validate_svg_card("svg-bars", "示例", ["A|45%", "B|99%"], source_text=source)


def test_bars_percent_must_match_source():
    source = "转化率是 45%。"
    with pytest.raises(cards.SvgCardError, match="没有出现在目标段落"):
        # 45%（带百分号）与 45（不带）不算同一个数。
        cards.validate_svg_card("svg-bars", "示例", ["A|45"], source_text=source)
    cards.validate_svg_card("svg-bars", "示例", ["A|45%"], source_text=source)


def test_bars_thousand_separator_matches():
    source = "全年订单 1,234 单。"
    cards.validate_svg_card("svg-bars", "示例", ["订单|1,234"], source_text=source)
    cards.validate_svg_card("svg-bars", "示例", ["订单|1234"], source_text=source)


def test_bars_display_column_must_match_bar_value():
    source = "转化率 45%。"
    with pytest.raises(cards.SvgCardError, match="第三列"):
        cards.validate_svg_card("svg-bars", "示例", ["A|45%|80%"], source_text=source)
    with pytest.raises(cards.SvgCardError, match="第三列"):
        cards.validate_svg_card("svg-bars", "示例", ["A|45%|增长 30%，环比 20%"])
    # 显示文案不含数值（纯描述）时放行。
    cards.validate_svg_card("svg-bars", "示例", ["A|45%|本季度"], source_text=source)


def test_bars_without_source_skips_hallucination_check():
    cards.validate_svg_card("svg-bars", "示例", ["A|99%"])


# ---------------------------------------------------------------------------
# 5. 渲染期宽松提示 card_warnings
# ---------------------------------------------------------------------------


def test_card_warnings_bars_hallucination():
    warns = cards.card_warnings("svg-bars", "示例", ["A|45%", "B|99%"], source_text="转化率 45%")
    assert any("99%" in w for w in warns)


def test_card_warnings_clean_bars_is_silent():
    assert cards.card_warnings("svg-bars", "示例", ["A|45%"], source_text="转化率 45%") == []


def test_card_warnings_skips_broken_format():
    # 第二列不是数字：渲染器会回退代码框并自带 warning，这里不再重复报。
    assert cards.card_warnings("svg-bars", "示例", ["A|不是数字"], source_text="正文") == []


def test_card_warnings_silent_truncation():
    warns = cards.card_warnings("svg-timeline", "示例", [f"202{i}|事件" for i in range(7)])
    assert any("前 6 个" in w for w in warns)
    warns = cards.card_warnings("svg-phone", "示例", ["副标题", "场景|我|你好", "> 金句"])
    assert any("金句" in w for w in warns)
    warns = cards.card_warnings("svg-concept", "示例", ["旧|A", "中|B", "现在|C"])
    assert any("两条轨道" in w for w in warns)
    warns = cards.card_warnings("svg-table", "示例", ["列|表", "a|v"])
    assert warns == []


# ---------------------------------------------------------------------------
# 6. 序列化 / 解析 / 指纹
# ---------------------------------------------------------------------------


def test_serialize_and_parse_roundtrip():
    card = {"kind": "svg-bars", "title": "数据对比", "lines": ["A|45%", "B|80%"]}
    block = cards.serialize_svg_card(card)
    assert block.startswith("```svg-bars 数据对比\n")
    assert block.endswith("\n```")
    parsed = cards.parse_svg_card_block(block)
    assert parsed == card


def test_parse_svg_card_block_variants():
    assert cards.parse_svg_card_block("```svg-bars\nA|1\n```") == {
        "kind": "svg-bars", "title": "", "lines": ["A|1"],
    }
    assert cards.parse_svg_card_block("不是围栏") is None
    assert cards.parse_svg_card_block("```svg-nope\nx\n```")["kind"] == "svg-nope"


def test_source_hash_stable():
    assert cards.source_hash("abc") == cards.source_hash("abc")
    assert cards.source_hash("abc") != cards.source_hash("abd")


def test_extract_json_variants():
    assert cards._extract_json('{"title": "t"}') == {"title": "t"}
    assert cards._extract_json('```json\n{"title": "t"}\n```') == {"title": "t"}
    assert cards._extract_json('前置说明 {"title": "t"} 尾随文字') == {"title": "t"}
    assert cards._extract_json("完全不是 JSON") is None
    assert cards._extract_json("") is None


# ---------------------------------------------------------------------------
# 7. 生成编排 draft_card（AI 网关 mock）
# ---------------------------------------------------------------------------


def _gateway_result(text):
    return {
        "text": text, "modelCode": "test-double", "provider": "test-double",
        "errorCode": None, "fallbackUsed": False, "primaryErrorCode": None, "attempts": 1,
    }


def test_draft_card_success(monkeypatch):
    captured = {}

    async def fake_generate_text(db, **kwargs):
        captured.update(kwargs)
        return _gateway_result('{"title": "数据对比", "lines": ["转化率|45%"]}')

    monkeypatch.setattr(ai_gateway, "generate_text", fake_generate_text)
    result = asyncio.run(
        cards.draft_card(
            None,
            owner_user_id="u1",
            kind="svg-bars",
            text="本季度转化率 45%，环比提升明显。",
            article_title="季度复盘",
        )
    )
    assert result["card"] == {"kind": "svg-bars", "title": "数据对比", "lines": ["转化率|45%"]}
    assert result["markdown"].startswith("```svg-bars 数据对比")
    assert result["modelCode"] == "test-double"
    assert captured["task_type"] == "svg_card"
    assert "柱状图" in captured["system_prompt"]
    assert "防幻觉" in captured["system_prompt"]


def test_draft_card_rejects_hallucinated_numbers(monkeypatch):
    async def fake_generate_text(db, **kwargs):
        return _gateway_result('{"title": "x", "lines": ["A|99%"]}')

    monkeypatch.setattr(ai_gateway, "generate_text", fake_generate_text)
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            cards.draft_card(
                None, owner_user_id="u1", kind="svg-bars", text="本季度转化率 45%。"
            )
        )
    assert exc_info.value.detail["code"] == "svg_card_invalid"
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["retryable"] is True


def test_draft_card_rejects_unparseable_output(monkeypatch):
    async def fake_generate_text(db, **kwargs):
        return _gateway_result("我觉得应该用表格而不是 JSON。")

    monkeypatch.setattr(ai_gateway, "generate_text", fake_generate_text)
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            cards.draft_card(None, owner_user_id="u1", kind="svg-table", text="一段足够长的正文内容。")
        )
    assert exc_info.value.detail["code"] == "svg_card_unparseable"


def test_draft_card_surfaces_gateway_failure(monkeypatch):
    async def fake_generate_text(db, **kwargs):
        return _gateway_result("")
        # text 为空时 errorCode 也为空的场景由下一个用例覆盖

    async def fake_generate_text_no_code(db, **kwargs):
        return {"text": "", "modelCode": "", "errorCode": "provider_unavailable"}

    monkeypatch.setattr(ai_gateway, "generate_text", fake_generate_text)
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            cards.draft_card(None, owner_user_id="u1", kind="svg-table", text="一段足够长的正文内容。")
        )
    assert exc_info.value.status_code == 502

    monkeypatch.setattr(ai_gateway, "generate_text", fake_generate_text_no_code)
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            cards.draft_card(None, owner_user_id="u1", kind="svg-table", text="一段足够长的正文内容。")
        )
    assert exc_info.value.detail["code"] == "provider_unavailable"


def test_draft_card_input_guards(monkeypatch):
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            cards.draft_card(None, owner_user_id="u1", kind="svg-nope", text="一段足够长的正文内容。")
        )
    assert exc_info.value.detail["code"] == "svg_card_bad_kind"
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            cards.draft_card(None, owner_user_id="u1", kind="svg-table", text="太短")
        )
    assert exc_info.value.detail["code"] == "svg_card_text_too_short"


# ---------------------------------------------------------------------------
# 8. 渲染期集成：studio 渲染时对正文中的卡做防幻觉提示
# ---------------------------------------------------------------------------


def test_render_studio_flags_hallucinated_bar_values():
    body = "## 数据\n\n```svg-bars 数据\n转化率|45%\n虚构值|99%\n```\n"
    result = engine.render_studio_document(body, {"style": "std", "theme": "orange"}, [])
    assert result["stats"]["svgCards"] == 1
    assert any("99%" in w for w in result["warnings"])


def test_render_studio_clean_bars_has_no_extra_warning():
    body = "本季度转化率 45%。\n\n```svg-bars 数据\n转化率|45%\n```\n"
    result = engine.render_studio_document(body, {"style": "std", "theme": "orange"}, [])
    assert not any("未在正文中出现" in w for w in result["warnings"])
    assert not any(w.startswith("svg-bars") for w in result["warnings"])


def test_render_studio_legacy_fence_also_checked():
    body = ":::svg-bars 数据\n转化率|45%\n虚构值|99%\n:::\n"
    result = engine.render_studio_document(body, {"style": "std", "theme": "orange"}, [])
    assert any("99%" in w for w in result["warnings"])


def test_render_studio_note_zen_bento_frames_render():
    body = "```svg-bars 数据\n转化率|45%\n```\n"
    htmls = set()
    for style in ("note", "zen", "bento"):
        result = engine.render_studio_document(body, {"style": style, "theme": "orange"}, [])
        assert result["stats"]["svgCards"] == 1
        html = result["html"]
        assert "<svg" in html
        htmls.add(html)
    assert len(htmls) == 3


# ---------------------------------------------------------------------------
# 9. HTTP 路由
# ---------------------------------------------------------------------------


def test_generate_endpoint_roundtrip(client, monkeypatch):
    headers = login(client)

    async def fake_generate_text(db, **kwargs):
        return _gateway_result('{"title": "数据对比", "lines": ["转化率|45%"]}')

    monkeypatch.setattr(ai_gateway, "generate_text", fake_generate_text)
    res = client.post(
        "/api/v1/wechat-mp/layout-studio/svg-card/generate",
        headers=headers,
        json={
            "text": "本季度转化率 45%，环比提升明显。",
            "kind": "svg-bars",
            "articleTitle": "季度复盘",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["card"]["kind"] == "svg-bars"
    assert body["card"]["lines"] == ["转化率|45%"]
    assert body["markdown"].startswith("```svg-bars 数据对比")
    assert body["sourceHash"]


def test_generate_endpoint_rejects_bad_kind(client):
    headers = login(client)
    res = client.post(
        "/api/v1/wechat-mp/layout-studio/svg-card/generate",
        headers=headers,
        json={"text": "一段足够长的正文内容。", "kind": "svg-nope"},
    )
    assert res.status_code == 422  # pydantic Literal 直接拒绝


def test_generate_endpoint_requires_layout_permission(client):
    headers = login(client, role="member")
    res = client.post(
        "/api/v1/wechat-mp/layout-studio/svg-card/generate",
        headers=headers,
        json={"text": "一段足够长的正文内容。", "kind": "svg-table"},
    )
    assert res.status_code == 403
