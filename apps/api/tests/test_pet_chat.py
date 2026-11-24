"""宠物公开接口的持久化、权限和模型失败回归。"""
from tests.conftest import login


def article(client, headers):
    response = client.post('/api/v1/studio/articles/new', headers=headers,
                           json={'topic': '宠物测试选题'})
    assert response.status_code in (200, 201), response.text
    return response.json()['id']


def test_pet_generation_persists_and_clears_skills(client, monkeypatch):
    from app.services import ai_gateway
    calls = []
    async def model(db, **kwargs):
        calls.append(kwargs)
        return {'text': '# 测试初稿\n\n这是根据用户需求生成的正文。', 'errorCode': None}
    monkeypatch.setattr(ai_gateway, 'generate_text', model)
    headers = login(client, 'pet-persistence')
    aid = article(client, headers)
    created = client.post('/api/v1/skill-center/skills', headers=headers, json={
        'name': '持久化测试风格', 'skillType': 'writing',
        'config': {'tone': '克制', 'styleRules': ['每段只讲一件事']}})
    assert created.status_code == 201, created.text
    chosen = created.json()
    response = client.post('/api/v1/pet/conversations', headers=headers,
        json={'studioArticleId': aid, 'message': '帮我写一篇文章', 'activeSkillIds': [chosen['id']]})
    assert response.status_code == 201, response.text
    conv = response.json()
    assert len(conv['id']) <= 32
    assert conv['lastGeneration']['content'].startswith('# 测试初稿')
    assert conv['messages'][-1]['metadata']['action'] == 'create_article'
    assert chosen['name'] in calls[0]['system_prompt']
    sent = client.post(f"/api/v1/pet/conversations/{conv['id']}/message", headers=headers,
        json={'message': '继续写一篇文章', 'activeSkillIds': []})
    assert sent.status_code == 200, sent.text
    assert sent.json()['activeSkillIds'] == []
    fetched = client.get(f"/api/v1/pet/conversations/{conv['id']}", headers=headers)
    assert len(fetched.json()['messages']) == 4
    assert fetched.json()['lastGeneration'] == conv['lastGeneration']
    listed = client.get('/api/v1/pet/conversations', headers=headers,
                         params={'studio_article_id': aid})
    assert listed.json()[0]['id'] == conv['id']


def test_pet_model_selection_explicit_auto_and_invalid(client, monkeypatch):
    from app.services import ai_gateway
    calls = []
    async def model(db, **kwargs):
        calls.append(kwargs)
        return {'text': '# 标题\n\n正文', 'errorCode': None, 'modelId': 'actual-id', 'modelCode': 'actual-code', 'fallbackUsed': True}
    def resolve(db, user_id, model_id):
        if model_id == 'forbidden':
            raise ai_gateway.InvalidModelSelection('model_not_found', '模型不可用')
        return {}
    monkeypatch.setattr(ai_gateway, 'generate_text', model)
    monkeypatch.setattr(ai_gateway, 'resolve_selection', resolve)
    headers = login(client, 'pet-selected-model')
    aid = article(client, headers)
    response = client.post('/api/v1/pet/conversations', headers=headers,
        json={'studioArticleId': aid, 'message': '写一篇文章', 'modelId': 'platform-model'})
    assert response.status_code == 201, response.text
    assert calls[-1]['model_id'] == 'platform-model'
    assert response.json()['messages'][-1]['metadata']['model'] == {'modelId': 'actual-id', 'modelCode': 'actual-code', 'fallbackUsed': True}
    cid = response.json()['id']
    response = client.post(f'/api/v1/pet/conversations/{cid}/message', headers=headers,
        json={'message': '再写一篇文章'})
    assert response.status_code == 200, response.text
    assert 'model_id' not in calls[-1]
    response = client.post(f'/api/v1/pet/conversations/{cid}/message', headers=headers,
        json={'message': '再写一篇文章', 'modelId': 'forbidden'})
    assert response.status_code == 400, response.text
    assert len(calls) == 2


def test_pet_rejects_foreign_article_and_conversation(client):
    owner = login(client, 'pet-owner')
    other = login(client, 'pet-other')
    aid = article(client, owner)
    bad = client.post('/api/v1/pet/conversations', headers=other,
        json={'studioArticleId': aid, 'message': '帮助'})
    assert bad.status_code == 404
    good = client.post('/api/v1/pet/conversations', headers=owner,
        json={'studioArticleId': aid, 'message': '帮助'})
    assert good.status_code == 201, good.text
    cid = good.json()['id']
    assert client.get(f'/api/v1/pet/conversations/{cid}', headers=other).status_code == 404


def test_pet_no_provider_never_records_success(client, monkeypatch):
    from app.services import ai_gateway
    async def model(db, **kwargs):
        return {'text': '', 'errorCode': 'provider_unavailable'}
    monkeypatch.setattr(ai_gateway, 'generate_text', model)
    headers = login(client, 'pet-no-provider')
    aid = article(client, headers)
    response = client.post('/api/v1/pet/conversations', headers=headers,
        json={'studioArticleId': aid, 'message': '帮我写一篇文章'})
    assert response.status_code == 503, response.text
    listed = client.get('/api/v1/pet/conversations', headers=headers,
                         params={'studio_article_id': aid}).json()
    assert listed == []
    assert client.post('/api/v1/pet/conversations', headers=headers,
        json={'studioArticleId': aid, 'message': '   '}).status_code == 422


def test_pet_rejects_unknown_skill_and_no_permission(client):
    headers = login(client, 'pet-invalid-skill')
    aid = article(client, headers)
    response = client.post('/api/v1/pet/conversations', headers=headers,
        json={'studioArticleId': aid, 'message': '帮助', 'activeSkillIds': ['missing']})
    assert response.status_code == 404
    limited = login(client, 'pet-limited', role='member')
    from app import models
    from app.core.db import SessionLocal
    from sqlalchemy import delete, select
    with SessionLocal() as db:
        uid = db.scalar(select(models.User.id).where(models.User.auth_subject == 'pet-limited'))
        db.execute(delete(models.UserRole).where(models.UserRole.user_id == uid))
        db.commit()
    assert client.get('/api/v1/pet/conversations', headers=limited).status_code == 403
    assert client.get('/api/v1/pet/conversations').status_code == 401


def test_intent_explicit_action_wins(client):
    from app.services.pet_intent_nlu import IntentClassifier
    assert IntentClassifier.classify('根据这篇文章生成动态卡片')[0] == 'generate_svg_card'
    assert IntentClassifier.classify('帮我润色文章')[0] == 'refine_paragraph'


def test_intent_questions_and_explicit_tasks():
    from app.services.pet_intent_nlu import IntentClassifier
    classify = lambda text: IntentClassifier.classify(text)[0]
    assert classify('这篇文章讲了什么？') == 'query_article'
    assert classify('如何润色这篇文章？') == 'query_article'
    assert classify('帮我写一篇关于如何使用工具的文章') == 'create_article'
    assert classify('请润色：如何使用工具') == 'refine_paragraph'
    assert classify('根据文章生成动态卡片') == 'generate_svg_card'


def test_pet_question_refine_and_source_metadata(client, monkeypatch):
    from app.services import ai_gateway, author_skills
    prompts = []
    async def model(db, **kwargs):
        prompts.append(kwargs['prompt'])
        return {'text': '模型结果', 'errorCode': None}
    async def fetch(url):
        return '来源正文' * 50
    monkeypatch.setattr(ai_gateway, 'generate_text', model)
    monkeypatch.setattr(author_skills, 'fetch_source_text', fetch)
    headers = login(client, 'pet-question-metadata')
    aid = article(client, headers)
    response = client.post('/api/v1/pet/conversations', headers=headers,
        json={'studioArticleId': aid, 'message': '这篇文章讲了什么？'})
    assert response.status_code == 201, response.text
    conv = response.json()
    assert conv['messages'][-1]['metadata']['action'] == 'query_article'
    assert not conv['lastGeneration']
    assert '回答' in prompts[-1]
    response = client.post(f"/api/v1/pet/conversations/{conv['id']}/message", headers=headers,
        json={'message': '请润色：https://example.com/article'})
    assert response.status_code == 200, response.text
    meta = response.json()['messages'][-1]['metadata']
    assert meta['result'] == '模型结果'
    assert meta['result_type'] == 'refine_paragraph'
    assert meta['source']['status'] == 'fetched'
    assert meta['source']['characters'] == 200
    assert '来源正文' in prompts[-1]


def test_pet_multiple_sources_and_failure(client, monkeypatch):
    from app.services import ai_gateway, author_skills
    from app.core.deps import ApiError
    calls = []
    async def model(db, **kwargs):
        calls.append(kwargs['prompt'])
        return {'text': '# 初稿\n正文', 'errorCode': None}
    async def fetch(url):
        if url.endswith('/bad'):
            raise ApiError('source_fetch_failed', '抓取失败', 422)
        return ('甲' * 20000) if url.endswith('/one') else '乙' * 200
    monkeypatch.setattr(ai_gateway, 'generate_text', model)
    monkeypatch.setattr(author_skills, 'fetch_source_text', fetch)
    headers = login(client, 'pet-multiple-sources')
    aid = article(client, headers)
    msg = '写文章 https://example.com/one，https://example.com/two。'
    response = client.post('/api/v1/pet/conversations', headers=headers,
        json={'studioArticleId': aid, 'message': msg})
    assert response.status_code == 201, response.text
    conv = response.json()
    meta = conv['messages'][-1]['metadata']
    assert len(meta['sources']) == 2
    assert meta['source'] == meta['sources'][0]
    assert meta['sources'][0]['possibly_truncated'] is True
    assert meta['sources'][1]['characters'] == 200
    assert '甲' * 200 in calls[-1] and '乙' * 200 in calls[-1]
    failure = client.post(f"/api/v1/pet/conversations/{conv['id']}/message", headers=headers,
        json={'message': '写文章 https://example.com/one https://example.com/bad'})
    assert failure.status_code == 422
    assert 'https://example.com/bad' in failure.text
    assert len(calls) == 1
    too_many = '写文章 ' + ' '.join(f'https://example.com/{i}' for i in range(6))
    assert client.post(f"/api/v1/pet/conversations/{conv['id']}/message", headers=headers,
        json={'message': too_many}).status_code == 422


def test_pet_card_uses_article_context_and_preserves_result(client, monkeypatch):
    from app.services import quick_tools
    calls = []
    async def card(db, user_id, text, **kwargs):
        calls.append(text)
        return {'svg_code': '<svg/>', 'metadata': {'kind': 'svg-steps'}}
    monkeypatch.setattr(quick_tools, 'generate_card', card)
    headers = login(client, 'pet-card-context')
    aid = article(client, headers)
    response = client.post('/api/v1/pet/conversations', headers=headers,
        json={'studioArticleId': aid, 'message': '根据这篇文章生成动态卡片'})
    assert response.status_code == 201, response.text
    meta = response.json()['messages'][-1]['metadata']
    assert meta['card']['svg_code'] == '<svg/>'
    assert meta['result_type'] == 'generate_svg_card'
    assert '宠物测试选题' in calls[0]


def test_hotspot_material_enters_only_the_matching_draft(client, monkeypatch):
    """「写这篇」把标题、平台和链接写进对应稿件的模型请求，不串到另一篇。"""
    from app import models
    from app.core.db import SessionLocal
    from app.services import ai_gateway

    calls = []

    async def model(db, **kwargs):
        calls.append(kwargs.get('prompt') or '')
        return {'text': '# 隔离测试\n\n正文。', 'errorCode': None}

    monkeypatch.setattr(ai_gateway, 'generate_text', model)
    headers = login(client, 'pet-hot-material')
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(auth_subject='pet-hot-material').one()
        workspace = db.query(models.Workspace).filter_by(owner_user_id=user.id).first()
        mine = models.ContentItem(workspace_id=workspace.id, owner_user_id=user.id, title='我的热点稿')
        other_item = models.ContentItem(workspace_id=workspace.id, owner_user_id=user.id, title='另一篇稿')
        cluster = models.HotCluster(
            fingerprint='fp-hot-material', title_norm='量子计算突破', title='量子计算突破',
            track='科技', platforms=['weibo', 'zhihu'], platform_count=2, item_count=1,
            score=88, grade='A', status='new', captured_at='2026-07-01 09:00',
        )
        db.add_all([mine, other_item, cluster])
        db.flush()
        db.add(models.HotItem(
            cluster_id=cluster.id, day='2026-07-01', platform='weibo', rank=3,
            title='量子计算突破', title_norm='量子计算突破', url='https://example.com/hot',
            source_url='https://example.com/source', first_seen_at='2026-07-01 09:00',
            last_seen_at='2026-07-01 09:00', item_uid='weibo\x1f量子计算突破',
        ))
        db.commit()
        content_id, other_id, cluster_id = mine.id, other_item.id, cluster.id

    opened = client.post(
        f'/api/v1/studio/articles/from-content/{content_id}',
        headers=headers, params={'hotClusterId': cluster_id},
    )
    assert opened.status_code == 200, opened.text
    assert '量子计算突破' in (opened.json().get('material') or '')
    assert 'weibo' in opened.json()['material']
    assert 'https://example.com/source' in opened.json()['material']

    again = client.post(
        f'/api/v1/studio/articles/from-content/{content_id}',
        headers=headers, params={'hotClusterId': cluster_id},
    )
    assert again.json()['id'] == opened.json()['id']

    other = client.post(f'/api/v1/studio/articles/from-content/{other_id}', headers=headers)
    assert other.status_code == 200, other.text
    assert '量子计算突破' not in (other.json().get('material') or '')

    sent = client.post('/api/v1/pet/conversations', headers=headers, json={
        'studioArticleId': opened.json()['id'], 'message': '帮我写一篇文章',
    })
    assert sent.status_code == 201, sent.text
    assert '量子计算突破' in calls[0]
    assert 'weibo' in calls[0]
    assert 'https://example.com/source' in calls[0]

    calls.clear()
    other_chat = client.post('/api/v1/pet/conversations', headers=headers, json={
        'studioArticleId': other.json()['id'], 'message': '帮我写一篇文章',
    })
    assert other_chat.status_code == 201, other_chat.text
    assert '量子计算突破' not in calls[0]
    assert 'https://example.com/source' not in calls[0]
