"""需求 03 §3.1：漫画分镜·场景级风格覆盖。

场级 style_config 非空时优先于项目级统一风格，为空时回落项目级；
清除覆盖（PATCH null/{}）后重新回落；校验口径与项目级一致。
"""

import pytest


@pytest.fixture(autouse=True)
def no_auto_dispatch(monkeypatch):
    from app.core import queue

    monkeypatch.setattr(queue, "dispatch", lambda job_id, **kwargs: True)


def login(client, subject="scene-style-user"):
    response = client.post("/api/v1/auth/login", json={"subject": subject, "displayName": subject})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def create_project(client, headers, **values):
    response = client.post("/api/v1/storyboards", json={"title": "夜行列车", **values}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def create_scene(client, headers, project, **values):
    response = client.post(
        f"/api/v1/storyboards/{project['id']}/scenes", json={"title": "站台", **values}, headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_shot(client, headers, scene, description="林夏站在雨里"):
    response = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": description}, headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def sketch_once(client, headers, shot, monkeypatch):
    from app.core import queue

    captured = {}
    original = queue.enqueue

    def spy(db, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return original(db, **kwargs)

    monkeypatch.setattr(queue, "enqueue", spy)
    response = client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch", json={}, headers=headers)
    assert response.status_code == 202, response.text
    return captured["payload"]


def test_scene_style_overrides_project(client, monkeypatch):
    headers = login(client, "scene-style-override")
    project = create_project(client, headers, styleConfig={"style_number": "FA-001"})
    scene = create_scene(client, headers, project, styleConfig={"style_number": "FB-001"})
    assert scene["styleConfig"]["style_number"] == "FB-001"
    assert scene["styleConfig"]["group_key"] == "FB"
    shot = create_shot(client, headers, scene)

    payload = sketch_once(client, headers, shot, monkeypatch)
    prompt = payload["prompt"]
    snapshot = payload["styleSnapshot"]

    # 场级风格生效：prompt 头部风格段来自 FB-001，主体次之
    assert prompt.startswith("风格名称：")
    assert prompt.index("风格名称：") < prompt.index("林夏站在雨里")
    assert snapshot["styleNumber"] == "FB-001"
    assert snapshot["groupKey"] == "FB"
    assert snapshot["promptHead"] == prompt.split("\n", 1)[0]
    assert snapshot["source"] == "scene"
    # 未校准模型走参考图兜底：内置风格参考图用场级编号
    assert payload["builtinReferenceStyleNumber"] == "FB-001"

    # 场级覆盖配置在项目详情的场景列表里原样下发
    detail = client.get(f"/api/v1/storyboards/{project['id']}", headers=headers).json()
    scene_out = next(row for row in detail["scenes"] if row["id"] == scene["id"])
    assert scene_out["styleConfig"]["style_number"] == "FB-001"


def test_scene_style_falls_back_to_project(client, monkeypatch):
    headers = login(client, "scene-style-fallback")
    project = create_project(client, headers, styleConfig={"style_number": "FA-001"})
    scene = create_scene(client, headers, project)
    assert scene["styleConfig"] == {}
    shot = create_shot(client, headers, scene)

    payload = sketch_once(client, headers, shot, monkeypatch)
    assert payload["styleSnapshot"]["styleNumber"] == "FA-001"
    assert payload["styleSnapshot"]["source"] == "project"
    assert payload["builtinReferenceStyleNumber"] == "FA-001"


def test_scene_style_patch_clears_override(client, monkeypatch):
    from app.core.db import SessionLocal
    from app import models

    headers = login(client, "scene-style-clear")
    project = create_project(client, headers, styleConfig={"style_number": "FA-001"})
    scene = create_scene(client, headers, project, styleConfig={"style_number": "FB-001"})
    shot = create_shot(client, headers, scene)

    # 覆盖期：场级生效
    payload = sketch_once(client, headers, shot, monkeypatch)
    assert payload["styleSnapshot"]["source"] == "scene"
    with SessionLocal() as db:
        row = db.get(models.StoryboardShot, shot["id"])
        row.sketch_status = "pending"
        row.sketch_job_id = None
        db.commit()

    # null = 清除覆盖，回落项目级
    cleared = client.patch(
        f"/api/v1/storyboards/scenes/{scene['id']}", json={"styleConfig": None}, headers=headers,
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["styleConfig"] == {}

    payload = sketch_once(client, headers, shot, monkeypatch)
    assert payload["styleSnapshot"]["styleNumber"] == "FA-001"
    assert payload["styleSnapshot"]["source"] == "project"
    assert payload["builtinReferenceStyleNumber"] == "FA-001"

    # 空对象同样表示清除
    cleared = client.patch(
        f"/api/v1/storyboards/scenes/{scene['id']}", json={"styleConfig": {}}, headers=headers,
    )
    assert cleared.status_code == 200
    assert cleared.json()["styleConfig"] == {}

    # 未带 styleConfig 的部分更新不动覆盖语义（title/summary/chapter 照旧）
    patched = client.patch(
        f"/api/v1/storyboards/scenes/{scene['id']}", json={"title": "车厢"}, headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "车厢"
    assert patched.json()["styleConfig"] == {}


def test_scene_style_validation_matches_project_rules(client):
    from app.core.db import SessionLocal
    from app import models

    # 造一个不可用风格：无参考图也无可用特征词
    with SessionLocal() as db:
        db.add(models.HanddrawStyle(
            style_number="ZZ-000", group_key="ZZ", generation_name="不可用风格",
            traits_raw="", reference_available=False,
        ))
        db.commit()

    headers = login(client, "scene-style-invalid")
    project = create_project(client, headers)

    # 创建：不存在的编号 → 404 style_not_found
    missing = client.post(
        f"/api/v1/storyboards/{project['id']}/scenes",
        json={"title": "站台", "styleConfig": {"style_number": "ZZ-999"}}, headers=headers,
    )
    assert missing.status_code == 404, missing.text
    assert missing.json()["code"] == "style_not_found"

    # 创建：不可用风格 → 400 style_unusable
    unusable = client.post(
        f"/api/v1/storyboards/{project['id']}/scenes",
        json={"title": "站台", "styleConfig": {"style_number": "ZZ-000"}}, headers=headers,
    )
    assert unusable.status_code == 400, unusable.text
    assert unusable.json()["code"] == "style_unusable"

    scene = create_scene(client, headers, project, styleConfig={"style_number": "FA-001"})

    # PATCH：不存在的编号同样明确拒绝，不静默存下
    missing_patch = client.patch(
        f"/api/v1/storyboards/scenes/{scene['id']}",
        json={"styleConfig": {"style_number": "ZZ-999"}}, headers=headers,
    )
    assert missing_patch.status_code == 404
    assert missing_patch.json()["code"] == "style_not_found"

    unusable_patch = client.patch(
        f"/api/v1/storyboards/scenes/{scene['id']}",
        json={"styleConfig": {"style_number": "ZZ-000"}}, headers=headers,
    )
    assert unusable_patch.status_code == 400
    assert unusable_patch.json()["code"] == "style_unusable"

    # 合法覆盖可以 PATCH 进去
    patched = client.patch(
        f"/api/v1/storyboards/scenes/{scene['id']}",
        json={"styleConfig": {"style_number": "FB-001"}}, headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["styleConfig"]["style_number"] == "FB-001"


def test_no_scene_style_keeps_legacy_prompt(client, monkeypatch):
    headers = login(client, "scene-style-legacy")
    project = create_project(client, headers)
    plain_scene = create_scene(client, headers, project)
    empty_scene = create_scene(client, headers, project, styleConfig={})
    plain_shot = create_shot(client, headers, plain_scene, "水墨风，中景")
    empty_shot = create_shot(client, headers, empty_scene, "水墨风，中景")

    plain_payload = sketch_once(client, headers, plain_shot, monkeypatch)
    empty_payload = sketch_once(client, headers, empty_shot, monkeypatch)

    # 场级未选任何风格：prompt 以手写画面提示词开头，不带风格段，不占参考图槽
    for payload in (plain_payload, empty_payload):
        assert payload["prompt"].startswith("水墨风，中景")
        assert payload["styleSnapshot"] is None
        assert payload["builtinReferenceStyleNumber"] is None
    # 显式空覆盖与完全缺省逐字节一致（回归保障）
    assert plain_payload["prompt"] == empty_payload["prompt"]
