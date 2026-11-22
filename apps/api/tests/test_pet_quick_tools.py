"""快捷工具的技能加载、模型故障与 SVG 安全行为。"""
import asyncio
import base64
import json
import xml.etree.ElementTree as ET

import pytest

from app.core.deps import ApiError
from app.services import author_skills, quick_tools


def mock_generation(monkeypatch, data):
    async def generate(db, user_id, prompt, system_prompt):
        return json.dumps(data, ensure_ascii=False)
    monkeypatch.setattr(author_skills, "generate_text", generate)


@pytest.mark.parametrize("refine_type", ["polish", "rewrite", "remove_ai_taste"])
def test_refine_loads_skill_and_passes_user_context(monkeypatch, refine_type):
    calls = []
    def instructions(db, user_id, ids):
        calls.append((db, user_id, ids))
        return "规则：保留专业术语"
    async def generate(db, user_id, prompt, system_prompt):
        assert db == "db" and user_id == "user"
        assert "规则：保留专业术语" in system_prompt
        assert json.loads(prompt)["paragraph"] == "原始内容"
        return '{"refined":"调整后的内容","suggestions":["简化表达"]}'
    monkeypatch.setattr(author_skills, "build_system_prompt", instructions)
    monkeypatch.setattr(author_skills, "generate_text", generate)
    result = asyncio.run(quick_tools.refine("db", "user", "原始内容", refine_type, "skill"))
    assert calls == [("db", "user", ["skill"])]
    assert result["refined"] == "调整后的内容"
    assert result["refine_type"] == refine_type


def test_invisible_skill_is_rejected_before_generation(monkeypatch):
    def denied(*args):
        raise ApiError("not_found", "找不到技能", 404)
    monkeypatch.setattr(author_skills, "build_system_prompt", denied)
    with pytest.raises(ApiError):
        asyncio.run(quick_tools.refine(None, "user", "原始内容", skill_id="private"))


def test_generation_failure_is_not_replaced_with_input(monkeypatch):
    async def failed(*args):
        raise ApiError("provider_unavailable", "模型不可用", 503)
    monkeypatch.setattr(author_skills, "generate_text", failed)
    with pytest.raises(ApiError):
        asyncio.run(quick_tools.refine(None, "user", "原始内容"))


@pytest.mark.parametrize("data", [
    {"refined": "", "suggestions": []},
    {"refined": "正文", "suggestions": "错误类型"},
    {"refined": "正文", "suggestions": [], "extra": True},
])
def test_refine_rejects_invalid_output(monkeypatch, data):
    mock_generation(monkeypatch, data)
    with pytest.raises(ApiError):
        asyncio.run(quick_tools.refine(None, "user", "原始内容"))


@pytest.mark.parametrize("kind,lines", [
    ("svg-steps", ["准备|打开设置", "完成|点击保存"]),
    ("svg-bars", ["甲|45%", "乙|80%"]),
    ("svg-timeline", ["2020|发布", "2021|升级"]),
    ("svg-breakdown", ["输入|A & B"]),
    ("svg-compare", ["旧|新", "慢|快"]),
    ("svg-table", ["列一|列二", "内容|说明"]),
    ("svg-concept", ["变化", "以前|A|B", "现在|C|D"]),
    ("svg-phone", ["演示", "场景|我|你好"]),
])
def test_card_uses_catalog_animation_and_valid_xml(monkeypatch, kind, lines):
    mock_generation(monkeypatch, {"kind": kind, "title": "生成标题", "lines": lines})
    title = '安全 <script>alert("x")</script> & 标题'
    result = asyncio.run(quick_tools.generate_card(None, "user", "甲 45%，乙 80%，2020 和 2021", title))
    svg = result["svg_code"]
    ET.fromstring(svg)
    assert "<animate" in svg
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
    assert result["metadata"]["kind"] == kind
    assert result["metadata"]["title"] == title
    assert base64.b64decode(result["preview_url"].split(",", 1)[1]).decode() == svg


@pytest.mark.parametrize("data", [
    {"kind": "svg-steps", "title": "标题", "lines": ["一步|<script>alert(1)</script>", "二步|完成"]},
    {"kind": "svg-steps", "title": "标题", "lines": "一步|完成"},
    {"kind": "svg-steps", "title": "标题", "lines": [{"x": "错误类型"}]},
    {"kind": "svg-steps", "title": "标题", "lines": ["一步|准备\n完成", "二步|完成"]},
    {"kind": "svg-bars", "title": "标题", "lines": ["编造|99%"]},
    {"kind": "unknown", "title": "标题", "lines": []},
])
def test_card_rejects_markup_bad_schema_and_invented_numbers(monkeypatch, data):
    mock_generation(monkeypatch, data)
    with pytest.raises(ApiError):
        asyncio.run(quick_tools.generate_card(None, "user", "原文只有 45% 的数据"))


def test_card_rejects_non_json_model_response(monkeypatch):
    async def generate(*args):
        return '<svg onload="alert(1)"></svg>'
    monkeypatch.setattr(author_skills, "generate_text", generate)
    with pytest.raises(ApiError):
        asyncio.run(quick_tools.generate_card(None, "user", "待生成卡片内容"))
