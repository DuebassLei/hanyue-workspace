"""创作空间每次生成的排版小组件选择：默认关闭，仅显式 true 开启。"""

import pytest


@pytest.mark.parametrize(
    ("constraints", "enabled"),
    [({}, False), ({"useLayoutComponents": False}, False),
     ({"useLayoutComponents": True}, True), ({"useLayoutComponents": "false"}, False)],
)
def test_generation_layout_toggle_reaches_system_prompt(client, monkeypatch, constraints, enabled):
    from app.services import ai_gateway

    captured = []

    async def fake_stream(db, **kwargs):
        captured.append(kwargs)
        yield {"event": "generation.delta", "data": {"text": "正文"}}
        yield {"event": "generation.done", "data": {}}

    monkeypatch.setattr(ai_gateway, "stream_text", fake_stream)
    login = client.post("/api/v1/auth/login", json={"subject": "layout-toggle", "displayName": "test"})
    assert login.status_code == 200, login.text
    response = client.post(
        "/api/v1/ai/text-generations",
        headers={"Authorization": f"Bearer {login.json()['token']}"},
        json={"taskType": "article", "prompt": "写一篇文章", "constraints": constraints},
    )
    assert response.status_code == 200, response.text
    assert captured
    prompt = captured[0]["system_prompt"]
    policy = prompt.split("本次生成的排版小组件开关：")[-1]
    assert policy.startswith("开启" if enabled else "关闭")
    if enabled:
        from app.services.wechat_layout_engine import SVG_CARD_KINDS

        assert all(kind in policy for kind in SVG_CARD_KINDS)
        assert "三反引号代码围栏" in policy
    else:
        assert "正文禁止使用 SVG 语义视觉卡代码围栏" in policy
        assert "{{imageN}}" in policy
    assert captured[0]["constraints"] == constraints


def test_layout_toggle_overrides_skill_suggestions_without_changing_other_callers():
    from app.services import ai_article

    original = ai_article.build_system_prompt(None)
    assert "本次生成的排版小组件开关" not in original
    assert "标准 Markdown" in original and "> [!NOTE]" in original
    assert "svg-table" in original and ":::" not in original
    markdown = '# 标题\n\n> [!NOTE]\n> 提示\n\n```svg-bars 数据\n甲 | 10\n乙 | 20\n```'
    import json
    draft = ai_article.parse_llm_output(json.dumps({"title": "标题", "markdown": markdown}))
    assert draft.ok and draft.markdown == markdown
    disabled = ai_article.build_system_prompt(None, use_layout_components=False)
    assert disabled.startswith(original)
    assert "此设置优先于前文输出契约和所有 Skill 风格中的组件建议" in disabled
