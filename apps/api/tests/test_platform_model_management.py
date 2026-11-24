from .conftest import login


def test_platform_defaults_and_cross_user_management(client):
    from app import models
    from app.core.db import SessionLocal
    from sqlalchemy import select

    admin = login(client, 'model-platform-admin')
    other = login(client, 'model-platform-other')
    member = login(client, 'model-platform-member', role='member')
    root = '/api/v1/models'
    conn = client.post(root + '/connections', headers=admin, json={'displayName': 'private', 'baseUrl': '', 'apiKey': ''}).json()
    model = client.post(root + '/catalog', headers=admin, json={'connectionId': conn['id'], 'modelCode': 'test-model', 'capabilities': ['text.generate']}).json()
    assert client.patch(root + '/platform-defaults', headers=other, json={'defaultTextModelId': model['id']}).status_code == 400
    assert client.patch(root + '/platform-defaults', headers=member, json={'defaultTextModelId': model['id']}).status_code == 403
    assert client.patch(root + '/platform-defaults', headers=admin, json={'defaultTextModelId': model['id']}).status_code == 200
    rows = client.get(root + '/catalog', headers=member).json()
    item = next(x for x in rows if x['id'] == model['id'])
    assert item['source'] == 'platform'
    assert item['connectionId'] is None and item['connectionName'] is None and item['connectionStatus'] is None
    assert client.patch(root + '/connections/' + conn['id'], headers=other, json={'displayName': 'changed'}).status_code == 409
    assert client.patch(root + '/connections/' + conn['id'] + '?confirmCrossUser=true', headers=other, json={'displayName': 'changed'}).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(models.RbacAuditLog).where(models.RbacAuditLog.action == 'model.connection.update', models.RbacAuditLog.target_id == conn['id']))
    assert client.patch(root + '/platform-defaults', headers=admin, json={'defaultImageModelId': model['id']}).status_code == 400
    assert client.patch(root + '/platform-defaults', headers=admin, json={'defaultTextModelId': None}).status_code == 200
    assert all(x['id'] != model['id'] for x in client.get(root + '/catalog', headers=member).json())
    assert client.get(root + '/connections', headers=member).status_code == 403
    operator = login(client, 'model-platform-operator', role='operator')
    assert all(x['id'] != conn['id'] for x in client.get(root + '/connections', headers=operator).json())
    assert client.patch(root + '/connections/' + conn['id'] + '?confirmCrossUser=true', headers=operator, json={'displayName': 'bad'}).status_code == 403
