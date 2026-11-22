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
