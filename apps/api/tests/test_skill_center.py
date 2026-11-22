"""技能中心：独立技能库的增删改查、蒸馏、导入导出与宠物联动。"""
import json

import pytest

from tests.conftest import login

BASE = "/api/v1/skill-center"


def make_skill(client, headers, name="我的风格", skill_type="writing", **config):
    payload = {
        "name": name,
        "skillType": skill_type,
        "summary": "测试用技能",
        "config": config or {"tone": "冷静克制", "styleRules": ["每段不超过三句话"]},
    }
    response = client.post(f"{BASE}/skills", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_crud_and_owner_isolation(client):
    owner = login(client, "skill-center-owner")
    other = login(client, "skill-center-other")
    skill = make_skill(client, owner)
    assert skill["skillType"] == "writing"
    assert skill["version"] == 1
    assert skill["config"]["tone"] == "冷静克制"

    # 同名拒绝，说明错误可操作
    dup = client.post(f"{BASE}/skills", headers=owner, json={
        "name": "我的风格", "skillType": "writing", "config": {}})
    assert dup.status_code == 409
    assert dup.json()["code"] == "skill_name_exists"

    assert client.get(f"{BASE}/skills/{skill['id']}", headers=other).status_code == 404
    assert client.get(f"{BASE}/skills", headers=other).json() == []

    updated = client.put(f"{BASE}/skills/{skill['id']}", headers=owner, json={
        "summary": "改过的定位", "config": {"tone": "更松弛"}})
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["config"]["tone"] == "更松弛"

    # 类型过滤
    make_skill(client, owner, name="芒格思维", skill_type="persona",
               config={"mentalModels": [{"name": "逆向思考", "description": "先想怎么会失败"}]})
    assert len(client.get(f"{BASE}/skills?type=persona", headers=owner).json()) == 1

    copy = client.post(f"{BASE}/skills/{skill['id']}/duplicate", headers=owner)
    assert copy.status_code == 201
    assert copy.json()["name"].startswith("我的风格 副本")

    assert client.delete(f"{BASE}/skills/{skill['id']}", headers=owner).status_code == 204
    assert client.get(f"{BASE}/skills/{skill['id']}", headers=owner).status_code == 404


def test_config_shape_is_validated(client):
    headers = login(client, "skill-center-config")
    bad = client.post(f"{BASE}/skills", headers=headers, json={
        "name": "坏配置", "skillType": "writing", "config": {"styleRules": "不是数组"}})
    assert bad.status_code == 422
    assert bad.json()["code"] == "invalid_config"

    bad_type = client.post(f"{BASE}/skills", headers=headers, json={
        "name": "坏类型", "skillType": "video", "config": {}})
    assert bad_type.status_code == 422


def test_distill_success_updates_config_and_version(client, monkeypatch):
    from app.services import ai_gateway

    async def model(db, **kwargs):
        return {
            "text": json.dumps({
                "tone": "短句、直给",
                "styleRules": ["先给结论", "每段一个观点", "避免形容词堆叠"],
                "openings": ["用一个反常识问题开场"],
                "structures": ["结论-论据-行动"],
                "vocabulary": ["拆解", "落地"],
                "taboos": ["赋能", "闭环"],
                "materialPolicy": "只写可核实的数据",
                "expression": {"sentenceLength": "20 字内", "punctuation": "少用感叹号"},
            }, ensure_ascii=False),
            "errorCode": None,
        }

    monkeypatch.setattr(ai_gateway, "generate_text", model)
    headers = login(client, "skill-center-distill")
    skill = make_skill(client, headers)
    run = client.post(f"{BASE}/skills/{skill['id']}/distill", headers=headers, json={
        "materials": ["这是第一篇样文，语言很短，句子直给。" * 20]})
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["status"] == "completed"
    assert body["materialStats"]["charCount"] > 200

    refreshed = client.get(f"{BASE}/skills/{skill['id']}", headers=headers).json()
    assert refreshed["version"] == 2
    assert refreshed["source"] == "distilled"
    assert "先给结论" in refreshed["config"]["styleRules"]
    assert client.get(f"{BASE}/runs/{body['id']}", headers=headers).status_code == 200


def test_distill_failure_keeps_skill_and_records_run(client, monkeypatch):
    from app.services import ai_gateway

    async def no_provider(db, **kwargs):
        return {"text": "", "errorCode": "provider_unavailable"}

    monkeypatch.setattr(ai_gateway, "generate_text", no_provider)
    headers = login(client, "skill-center-distill-fail")
    skill = make_skill(client, headers)
    failed = client.post(f"{BASE}/skills/{skill['id']}/distill", headers=headers, json={
        "materials": ["足够长的材料。" * 40]})
    assert failed.status_code == 503
    unchanged = client.get(f"{BASE}/skills/{skill['id']}", headers=headers).json()
    assert unchanged["version"] == 1
    assert unchanged["config"]["tone"] == "冷静克制"


def test_distill_rejects_short_material_and_foreign_skill(client, monkeypatch):
    headers = login(client, "skill-center-guard")
    skill = make_skill(client, headers)
    short = client.post(f"{BASE}/skills/{skill['id']}/distill", headers=headers,
                        json={"materials": ["太短"]})
    assert short.status_code == 422
    assert short.json()["code"] == "insufficient_material"

    other = login(client, "skill-center-guard-other")
    assert client.post(f"{BASE}/skills/{skill['id']}/distill", headers=other,
                       json={"materials": ["材料" * 200]}).status_code == 404


def test_export_import_round_trip(client):
    headers = login(client, "skill-center-io")
    skill = make_skill(client, headers, name="可导出风格")
    exported = client.get(f"{BASE}/skills/{skill['id']}/export", headers=headers)
    assert exported.status_code == 200
    package = exported.json()
    assert package["format"] == "hanyue.author-skill/v1"
    assert "attachment" in exported.headers["content-disposition"]

    imported = client.post(f"{BASE}/import", headers=headers, json=package)
    assert imported.status_code == 201, imported.text
    rows = imported.json()
    assert len(rows) == 1
    # 同名不覆盖，自动改名
    assert rows[0]["name"] != skill["name"]
    assert rows[0]["config"]["tone"] == skill["config"]["tone"]

    bad = client.post(f"{BASE}/import", headers=headers, json={"format": "other"})
    assert bad.status_code == 422
    assert bad.json()["code"] == "invalid_import"


def test_pet_uses_author_skill_rules(client, monkeypatch):
    from app.services import ai_gateway

    calls = []

    async def model(db, **kwargs):
        calls.append(kwargs)
        return {"text": "# 标题\n\n正文。", "errorCode": None}

    monkeypatch.setattr(ai_gateway, "generate_text", model)
    headers = login(client, "skill-center-pet")
    skill = make_skill(client, headers, name="宠物专用风格")
    article = client.post("/api/v1/studio/articles/new", headers=headers,
                          json={"topic": "技能联动"}).json()

    response = client.post("/api/v1/pet/conversations", headers=headers, json={
        "studioArticleId": article["id"],
        "message": "帮我写一篇文章",
        "activeSkillIds": [skill["id"]],
    })
    assert response.status_code == 201, response.text
    assert "宠物专用风格" in calls[0]["system_prompt"]
    assert "每段不超过三句话" in calls[0]["system_prompt"]

    # 越权技能 id 直接拒绝，不静默降级成默认风格
    other = login(client, "skill-center-pet-other")
    other_article = client.post("/api/v1/studio/articles/new", headers=other,
                                json={"topic": "别人的稿"}).json()
    foreign = client.post("/api/v1/pet/conversations", headers=other, json={
        "studioArticleId": other_article["id"], "message": "帮助",
        "activeSkillIds": [skill["id"]]})
    assert foreign.status_code == 404
