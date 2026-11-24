"""写作工具技能选择、权限和提示词传递。"""
import asyncio
import json

from tests.conftest import login


def test_writing_skill_is_validated_and_passed_to_both_draft_routes(client, monkeypatch):
    from app.services import studio_pipeline as SP

    calls = []
    monkeypatch.setattr(SP.PIPELINE, 'draft', lambda article_id, **kwargs: calls.append((article_id, kwargs)))
    headers = login(client, 'studio-skill-owner')
    other = login(client, 'studio-skill-other')
    skill = client.post('/api/v1/skill-center/skills', headers=headers, json={
        'name': '写作技能测试', 'skillType': 'writing',
        'config': {'styleRules': ['用短句说明事实']},
    })
    assert skill.status_code == 201, skill.text
    skill_id = skill.json()['id']
    created = client.post('/api/v1/studio/articles/new', headers=headers, json={
        'topic': '选题', 'draft': True, 'skillIds': [skill_id],
    })
    assert created.status_code == 200, created.text
    article_id = created.json()['id']
    assert '用短句说明事实' in calls[-1][1]['skill_prompt']
    redraft = client.post(f'/api/v1/studio/articles/{article_id}/draft', headers=headers,
                          json={'skillIds': [skill_id]})
    assert redraft.status_code == 200, redraft.text
    assert '用短句说明事实' in calls[-1][1]['skill_prompt']
    denied = client.post('/api/v1/studio/articles/new', headers=other, json={
        'topic': '选题', 'draft': True, 'skillIds': [skill_id],
    })
    assert denied.status_code == 404
    assert len(calls) == 2
    too_many = client.post(f'/api/v1/studio/articles/{article_id}/draft', headers=headers,
                           json={'skillIds': ['x'] * 6})
    assert too_many.status_code == 422


def test_pipeline_includes_selected_skill_and_output_rules(client, monkeypatch):
    from app.services import ai_gateway, studio_pipeline as SP

    seen = []
    async def generate(db, **kwargs):
        if kwargs.get('task_type') == 'studio_draft':
            seen.append(kwargs['system_prompt'])
            return {'text': '# 标题\n\n正文。', 'errorCode': None}
        return {'text': json.dumps({'titles': [{'text': '标题', 'hook': 'C'}]}), 'errorCode': None}
    monkeypatch.setattr(ai_gateway, 'generate_text', generate)
    headers = login(client, 'studio-skill-prompt')
    response = client.post('/api/v1/studio/articles/new', headers=headers, json={'topic': '选题'})
    assert response.status_code == 200, response.text
    asyncio.run(SP.PIPELINE._draft(response.json()['id'], {}, skill_prompt='技能规则：只用短句'))
    assert '技能规则：只用短句' in seen[0]
    assert '只输出文章本身的 Markdown' in seen[0]
