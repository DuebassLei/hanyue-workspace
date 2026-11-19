import pytest


@pytest.fixture(autouse=True)
def no_auto_dispatch(monkeypatch):
    from app.core import queue

    monkeypatch.setattr(queue, "dispatch", lambda job_id, **kwargs: True)


def login(client, subject="storyboard-user"):
    response = client.post("/api/v1/auth/login", json={"subject": subject, "displayName": subject})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def create_project(client, headers, **values):
    response = client.post("/api/v1/storyboards", json={"title": "夜行列车", **values}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_storyboard_hierarchy_crud_and_order(client):
    headers = login(client)
    project = create_project(client, headers)
    scene = client.post(
        f"/api/v1/storyboards/{project['id']}/scenes",
        json={"title": "站台", "summary": "深夜的空站台"}, headers=headers,
    ).json()
    first = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": "主角走入站台", "shotSize": "wide"}, headers=headers,
    ).json()
    second = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": "列車燈光逼近", "shotSize": "close"}, headers=headers,
    ).json()

    reordered = client.put(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots/order",
        json={"ids": [second["id"], first["id"]]}, headers=headers,
    )
    assert reordered.status_code == 200, reordered.text
    detail = client.get(f"/api/v1/storyboards/{project['id']}", headers=headers)
    assert detail.status_code == 200
    assert [shot["id"] for shot in detail.json()["scenes"][0]["shots"]] == [second["id"], first["id"]]

    patched = client.patch(
        f"/api/v1/storyboards/shots/{first['id']}",
        json={"dialogue": "你也在等車嗎？", "viewAngle": "eye_level"}, headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["dialogue"] == "你也在等車嗎？"


def test_storyboard_nested_resources_are_owner_scoped(client):
    owner = login(client, "storyboard-owner")
    other = login(client, "storyboard-other")
    project = create_project(client, owner)
    scene = client.post(
        f"/api/v1/storyboards/{project['id']}/scenes", json={"title": "站台"}, headers=owner,
    ).json()
    shot = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots", json={"frameDescription": "列車進站"}, headers=owner,
    ).json()
    assert client.get(f"/api/v1/storyboards/{project['id']}", headers=other).status_code == 404
    assert client.patch(f"/api/v1/storyboards/scenes/{scene['id']}", json={"title": "越权"}, headers=other).status_code == 404
    assert client.delete(f"/api/v1/storyboards/shots/{shot['id']}", headers=other).status_code == 404


def test_storyboard_generation_persists_job_and_workspace(client, monkeypatch):
    from app.core import queue

    headers = login(client)
    project = create_project(client, headers)
    scene = client.post(
        f"/api/v1/storyboards/{project['id']}/scenes", json={"title": "站台"}, headers=headers,
    ).json()
    shot = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": "雨夜站台，列車燈光由遠至近", "shotSize": "wide"}, headers=headers,
    ).json()
    captured = {}
    original = queue.enqueue

    def enqueue_spy(db, **kwargs):
        captured.update(kwargs)
        return original(db, **kwargs)

    monkeypatch.setattr(queue, "enqueue", enqueue_spy)
    response = client.post(
        f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch",
        json={"modelId": "img-model-1"}, headers=headers,
    )
    assert response.status_code == 202, response.text
    assert captured["payload"]["storyboardShotId"] == shot["id"]
    assert captured["payload"]["modelId"] == "img-model-1"
    assert captured["payload"]["workspaceId"] == project["workspaceId"]
    assert captured["payload"]["generationStage"] == "sketch"
    detail = client.get(f"/api/v1/storyboards/{project['id']}", headers=headers).json()
    saved = detail["scenes"][0]["shots"][0]
    assert saved["sketchJobId"] == response.json()["jobId"]
    assert saved["sketchStatus"] == "queued"


def test_storyboard_exports_keep_scene_and_shot_order(client):
    headers = login(client)
    project = create_project(client, headers)
    for title, desc in (("站台", "主角入场"), ("车厢", "列车启动")):
        scene = client.post(
            f"/api/v1/storyboards/{project['id']}/scenes", json={"title": title}, headers=headers,
        ).json()
        client.post(
            f"/api/v1/storyboards/scenes/{scene['id']}/shots",
            json={"frameDescription": desc}, headers=headers,
        )
    board = client.get(f"/api/v1/storyboards/{project['id']}/exports/board", headers=headers)
    prompts = client.get(f"/api/v1/storyboards/{project['id']}/exports/prompt-pack", headers=headers)
    assert board.status_code == prompts.status_code == 200
    assert [scene["title"] for scene in board.json()["scenes"]] == ["站台", "车厢"]
    assert "主角入场" in prompts.text or "主角入场" in str(prompts.json())


def _asset(client, headers):
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (20, 40, 60)).save(buf, format="PNG")
    response = client.post(
        "/api/v1/assets",
        files={"file": ("ref.png", buf.getvalue(), "image/png")},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_visual_assets_require_name_and_reference_and_feed_generation(client, monkeypatch):
    from app.core import queue

    headers = login(client, "storyboard-assets")
    bare = client.post("/api/v1/storyboards", json={"title": "缺资产", "assets": {
        "characters": [{"id": "hero", "name": "林夏"}],
    }}, headers=headers)
    assert bare.status_code == 400

    asset_id = _asset(client, headers)
    project = create_project(client, headers, assets={
        "characters": [{"id": "hero", "name": "林夏", "assetId": asset_id, "description": "短发，深色外套"}],
        "locations": [{"id": "platform", "name": "夜站台", "description": "雨，冷白灯"}],
        "styles": [],
    })
    scene = client.post(
        f"/api/v1/storyboards/{project['id']}/scenes", json={"title": "站台"}, headers=headers,
    ).json()
    shot = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": "林夏站在雨里", "assetRefs": ["hero", "platform"]}, headers=headers,
    ).json()
    assert shot["assetRefs"] == ["hero", "platform"]
    captured = {}
    original = queue.enqueue

    def spy(db, **kwargs):
        captured.update(kwargs)
        return original(db, **kwargs)

    monkeypatch.setattr(queue, "enqueue", spy)
    response = client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch", json={}, headers=headers)
    assert response.status_code == 202, response.text
    assert captured["payload"]["referenceAssetIds"] == [asset_id]
    assert "林夏" in captured["payload"]["prompt"]
    assert "夜站台" in captured["payload"]["prompt"]
    assert "reference image 1 is character 林夏" in captured["payload"]["prompt"]
    assert "same face" in captured["payload"]["prompt"]


def test_character_references_follow_every_shot(client, monkeypatch):
    from app.core import queue

    headers = login(client, "storyboard-consistency")
    hero = _asset(client, headers)
    rival = _asset(client, headers)
    place = _asset(client, headers)
    project = create_project(client, headers, assets={
        "characters": [
            {"id": "hero", "name": "林夏", "assetId": hero, "description": "短发，深色外套"},
            {"id": "rival", "name": "周晚", "assetId": rival, "description": "长发，浅色风衣"},
        ],
        "locations": [{"id": "platform", "name": "夜站台", "assetId": place, "description": "雨"}],
        "styles": [],
    })
    scene = client.post(
        f"/api/v1/storyboards/{project['id']}/scenes", json={"title": "站台"}, headers=headers,
    ).json()
    shot = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": "空镜，雨落在站台"}, headers=headers,
    ).json()
    captured = {}
    original = queue.enqueue

    def spy(db, **kwargs):
        captured.update(kwargs)
        return original(db, **kwargs)

    monkeypatch.setattr(queue, "enqueue", spy)
    response = client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch", json={}, headers=headers)
    assert response.status_code == 202, response.text
    assert captured["payload"]["referenceAssetIds"] == []
    assert "character 林夏" not in captured["payload"]["prompt"]
    assert "character 周晚" not in captured["payload"]["prompt"]
    assert "夜站台" not in captured["payload"]["prompt"]


def _enqueue_spy(monkeypatch):
    from app.core import queue

    captured = {}
    original = queue.enqueue

    def spy(db, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return original(db, **kwargs)

    monkeypatch.setattr(queue, "enqueue", spy)
    return captured


def _scene_for(client, headers, project):
    return client.post(
        f"/api/v1/storyboards/{project['id']}/scenes", json={"title": "站台"}, headers=headers,
    ).json()


def test_longer_character_name_wins_over_prefix(client, monkeypatch):
    headers = login(client, "storyboard-prefix")
    short = _asset(client, headers)
    full = _asset(client, headers)
    project = create_project(client, headers, assets={
        "characters": [
            {"id": "lin", "name": "林", "assetId": short, "description": "路人"},
            {"id": "hero", "name": "林夏", "assetId": full, "description": "短发，深色外套"},
        ],
    })
    scene = _scene_for(client, headers, project)
    shot = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": "林夏回头", "assetRefs": ["lin", "hero"]}, headers=headers,
    ).json()
    captured = _enqueue_spy(monkeypatch)
    response = client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch", json={}, headers=headers)
    assert response.status_code == 202, response.text
    assert captured["payload"]["referenceAssetIds"] == [full]
    prompt = captured["payload"]["prompt"]
    assert "林夏 (reference image 1)" in prompt
    assert "林 (reference image" not in prompt
    assert "林: 路人" not in prompt

    alone = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": "站台空着", "action": "林抬头"}, headers=headers,
    ).json()
    alone_response = client.post(f"/api/v1/storyboards/shots/{alone['id']}/generate-sketch", json={}, headers=headers)
    assert alone_response.status_code == 202, alone_response.text
    assert captured["payload"]["referenceAssetIds"] == [short]
    assert "林 (reference image 1)" in captured["payload"]["prompt"]


def test_unchecked_names_fall_back_to_asset_refs_and_final_requires_sketch(client, monkeypatch):
    headers = login(client, "storyboard-fallback")
    hero = _asset(client, headers)
    rival = _asset(client, headers)
    project = create_project(client, headers, assets={
        "characters": [
            {"id": "hero", "name": "林夏", "assetId": hero, "description": "短发"},
            {"id": "rival", "name": "周晚", "assetId": rival, "description": "长发"},
        ],
    })
    scene = _scene_for(client, headers, project)
    shot = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": "空镜，雨落在站台", "assetRefs": ["hero", "rival"]}, headers=headers,
    ).json()
    captured = _enqueue_spy(monkeypatch)
    sketched = client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch", json={}, headers=headers)
    assert sketched.status_code == 202, sketched.text
    assert captured["payload"]["referenceAssetIds"] == [hero, rival]
    refused = client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-final", json={}, headers=headers)
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "sketch_required"
    retried = client.post(f"/api/v1/storyboards/shots/{shot['id']}/retry", json={}, headers=headers)
    assert retried.status_code == 409, retried.text
    assert retried.json()["code"] == "sketch_required"


def test_handwritten_prompt_still_appends_consistency(client, monkeypatch):
    headers = login(client, "storyboard-prompt")
    hero = _asset(client, headers)
    project = create_project(client, headers, assets={
        "characters": [{"id": "hero", "name": "林夏", "assetId": hero, "description": "短发，深色外套"}],
    })
    scene = _scene_for(client, headers, project)
    custom = "雨夜，她停在黄线外"
    shot = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": "林夏站在雨里", "prompt": custom, "assetRefs": ["hero"]}, headers=headers,
    ).json()
    captured = _enqueue_spy(monkeypatch)
    response = client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch", json={}, headers=headers)
    assert response.status_code == 202, response.text
    prompt = captured["payload"]["prompt"]
    assert prompt.startswith(custom)
    assert "reference image 1 is character 林夏" in prompt
    assert "same face" in prompt


def test_reference_cap_drops_location_before_style(client, monkeypatch):
    """风格是组级统一画风的锚点：超额时先丢地点、再让多余角色让位，风格最后丢。"""
    headers = login(client, "storyboard-cap")
    pictures = [_asset(client, headers) for _ in range(5)]
    hero, second, third, place, look = pictures
    project = create_project(client, headers, assets={
        "characters": [
            {"id": "hero", "name": "林夏", "assetId": hero, "description": "短发"},
            {"id": "second", "name": "周晚", "assetId": second, "description": "长发"},
            {"id": "third", "name": "陈舟", "assetId": third, "description": "眼镜"},
        ],
        "locations": [{"id": "platform", "name": "夜站台", "assetId": place, "description": "雨"}],
        "styles": [{"id": "ink", "name": "水墨", "assetId": look, "description": "留白"}],
    })
    scene = _scene_for(client, headers, project)
    shot = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={
            "frameDescription": "林夏、周晚和陈舟站在雨里",
            "assetRefs": ["hero", "second", "third", "platform", "ink"],
        },
        headers=headers,
    ).json()
    captured = _enqueue_spy(monkeypatch)
    response = client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch", json={}, headers=headers)
    assert response.status_code == 202, response.text
    assert captured["payload"]["referenceAssetIds"] == [hero, second, third, look]
    prompt = captured["payload"]["prompt"]
    assert "reference image 4 is style 水墨" in prompt
    assert "style 水墨" in prompt
    assert "location 夜站台" not in prompt
    assert "陈舟" in prompt

    fourth = _asset(client, headers)
    patched = client.patch(
        f"/api/v1/storyboards/{project['id']}",
        json={"assets": {
            "characters": [
                {"id": "hero", "name": "林夏", "assetId": hero, "description": "短发"},
                {"id": "second", "name": "周晚", "assetId": second, "description": "长发"},
                {"id": "third", "name": "陈舟", "assetId": third, "description": "眼镜"},
                {"id": "fourth", "name": "苏晚", "assetId": fourth, "description": "帽子"},
            ],
            "locations": [{"id": "platform", "name": "夜站台", "assetId": place, "description": "雨"}],
            "styles": [{"id": "ink", "name": "水墨", "assetId": look, "description": "留白"}],
        }},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    crowded = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": "林夏、周晚、陈舟和苏晚站在雨里", "assetRefs": ["fourth", "platform", "ink"]},
        headers=headers,
    ).json()
    from app.core.db import SessionLocal
    from app import models

    with SessionLocal() as db:
        row = db.get(models.StoryboardShot, crowded["id"])
        row.sketch_asset_id = hero
        row.sketch_status = "succeeded"
        db.commit()
    final = client.post(f"/api/v1/storyboards/shots/{crowded['id']}/generate-final", json={}, headers=headers)
    assert final.status_code == 202, final.text
    assert captured["payload"]["referenceAssetIds"] == [hero, second, look, hero]
    final_prompt = captured["payload"]["prompt"]
    assert "苏晚: 帽子" in final_prompt
    assert "苏晚 (reference image" not in final_prompt
    assert "location 夜站台" not in final_prompt
    assert "style 水墨" in final_prompt
    assert "approved sketch" in final_prompt


def test_breakdown_parser_accepts_fenced_and_aliased_json():
    from app.services.storyboard_breakdown import _loads_breakdown, _normalize

    fenced = """说明如下：
```json
{"scenes":[{"title":"雨夜","summary":"等车","chapter":"第一章","panels":[
  {"frame_description":"林夏撑伞","action":"回头","dialogue":"","shot_size":"近景"}
]}]}
```
后面还有一句。"""
    parsed = _normalize(_loads_breakdown(fenced))
    assert parsed[0]["title"] == "雨夜"
    assert parsed[0]["shots"][0]["frame_description"] == "林夏撑伞"
    assert parsed[0]["shots"][0]["shot_size"] == "close"

    trailing = '{"scenes":[{"name":"车厢","shots":[{"description":"列车启动","size":"wide shot"}]}]} 以上。'
    parsed = _normalize(_loads_breakdown(trailing))
    assert parsed[0]["shots"][0]["shot_size"] == "wide"
    assert _loads_breakdown("这不是分镜，只是一段说明。") is None


def test_breakdown_appends_and_refuses_to_overwrite_scene(client, monkeypatch):
    import app.services.storyboard_breakdown as breakdown

    async def fake_break(db, user_id, source_text, model_id):
        return [{
            "title": "雨夜站台", "summary": "列车将至", "chapter": "第一章",
            "shots": [{"frame_description": "林夏撑伞", "action": "回头", "dialogue": "", "shot_size": "medium"}],
        }]

    monkeypatch.setattr(breakdown, "break_down", fake_break)
    headers = login(client, "storyboard-break")
    project = create_project(client, headers)
    added = client.post(
        f"/api/v1/storyboards/{project['id']}/breakdown",
        json={"sourceText": "林夏在雨夜等车。"}, headers=headers,
    )
    assert added.status_code == 200, added.text
    assert added.json()["addedScenes"] == 1
    scene_id = added.json()["project"]["scenes"][0]["id"]
    again = client.post(
        f"/api/v1/storyboards/scenes/{scene_id}/breakdown",
        json={"sourceText": "再拆一次。"}, headers=headers,
    )
    assert again.status_code == 409
    assert again.json()["code"] == "scene_has_shots"


def _tiny_png() -> bytes:
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (40, 80, 90)).save(buf, format="PNG")
    return buf.getvalue()


def _stub_image_provider(monkeypatch, *, fail=False):
    from app.services import image_provider, job_runner

    def fake_generate(db, user_id, **kwargs):
        if fail:
            raise image_provider.ImageProviderUnavailable("provider_error", "供应商拒绝")
        return [(_tiny_png(), "image/png")]

    monkeypatch.setattr(job_runner.image_provider, "generate_images", fake_generate)
    monkeypatch.setattr(job_runner.image_provider, "resolve_image_provider", lambda *a, **k: None)


def test_sketch_success_writes_asset_and_status(client, monkeypatch):
    from app.core.db import SessionLocal
    from app.services import job_runner
    from app import models

    _stub_image_provider(monkeypatch)
    headers = login(client, "storyboard-success")
    project = create_project(client, headers)
    scene = client.post(
        f"/api/v1/storyboards/{project['id']}/scenes", json={"title": "站台"}, headers=headers,
    ).json()
    shot = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": "雨夜"}, headers=headers,
    ).json()
    queued = client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch", json={}, headers=headers)
    assert queued.status_code == 202, queued.text
    job_runner.run_job(queued.json()["jobId"])
    with SessionLocal() as db:
        row = db.get(models.StoryboardShot, shot["id"])
        job = db.get(models.BackgroundJob, queued.json()["jobId"])
        assert job.status == "succeeded"
        assert row.sketch_status == "succeeded"
        assert row.sketch_asset_id
        asset = db.get(models.Asset, row.sketch_asset_id)
        assert asset.workspace_id == project["workspaceId"]
        assert asset.owner_user_id == job.owner_user_id


def test_sketch_failure_marks_shot_failed(client, monkeypatch):
    from app.core.db import SessionLocal
    from app.services import job_runner
    from app import models

    _stub_image_provider(monkeypatch, fail=True)
    headers = login(client, "storyboard-fail")
    project = create_project(client, headers)
    scene = client.post(
        f"/api/v1/storyboards/{project['id']}/scenes", json={"title": "站台"}, headers=headers,
    ).json()
    shot = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": "雨夜"}, headers=headers,
    ).json()
    queued = client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch", json={}, headers=headers)
    job_runner.run_job(queued.json()["jobId"])
    with SessionLocal() as db:
        row = db.get(models.StoryboardShot, shot["id"])
        assert row.sketch_status == "failed"
        assert row.sketch_error
        assert row.sketch_asset_id is None


def test_stale_storyboard_job_does_not_overwrite_retry(client, monkeypatch):
    from app.core.db import SessionLocal
    from app.services import job_runner
    from app import models

    _stub_image_provider(monkeypatch)
    headers = login(client, "storyboard-retry")
    project = create_project(client, headers)
    scene = client.post(
        f"/api/v1/storyboards/{project['id']}/scenes", json={"title": "站台"}, headers=headers,
    ).json()
    shot = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": "雨夜"}, headers=headers,
    ).json()
    first = client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch", json={}, headers=headers)
    assert first.status_code == 202, first.text
    with SessionLocal() as db:
        job = db.get(models.BackgroundJob, first.json()["jobId"])
        job.status = "failed"
        job.error_code = "provider_error"
        job.error_message = "供应商拒绝"
        db.commit()
        row = db.get(models.StoryboardShot, shot["id"])
        row.sketch_status = "failed"
        row.sketch_error = "供应商拒绝"
        db.commit()
    second = client.post(f"/api/v1/storyboards/shots/{shot['id']}/retry", json={}, headers=headers)
    assert second.status_code == 202, second.text
    assert first.json()["jobId"] != second.json()["jobId"]

    with SessionLocal() as db:
        stale = db.get(models.BackgroundJob, first.json()["jobId"])
        job_runner._apply_storyboard_success(db, stale, ["asset-should-not-land"], stale.payload or {})
        row = db.get(models.StoryboardShot, shot["id"])
        assert row.sketch_job_id == second.json()["jobId"]
        assert row.sketch_status == "queued"
        assert row.sketch_asset_id is None
        assert row.generation_version == (stale.payload or {}).get("generationVersion") + 1

        row.generation_version = (stale.payload or {}).get("generationVersion")
        db.commit()
        job_runner._apply_storyboard_success(db, stale, ["asset-should-not-land"], stale.payload or {})
        db.refresh(row)
        assert row.sketch_job_id == second.json()["jobId"]
        assert row.sketch_status == "queued"
        assert row.sketch_asset_id is None


def test_generation_prompt_is_exposed_for_view_and_copy(client, monkeypatch):
    """镜头详情要给出实际入队的完整提示词，供大图预览查看/复制与提示词包导出。"""
    from app.core import queue

    headers = login(client, "storyboard-prompt-view")
    project = create_project(client, headers)
    scene = client.post(
        f"/api/v1/storyboards/{project['id']}/scenes", json={"title": "站台"}, headers=headers,
    ).json()
    shot = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": "林夏站在雨里", "prompt": "水墨风，中景"}, headers=headers,
    ).json()

    before = client.get(f"/api/v1/storyboards/{project['id']}", headers=headers).json()["scenes"][0]["shots"][0]
    assert before["sketchPrompt"] is None
    assert before["finalPrompt"] is None

    captured = {}
    original = queue.enqueue

    def enqueue_spy(db, **kwargs):
        captured.update(kwargs)
        return original(db, **kwargs)

    monkeypatch.setattr(queue, "enqueue", enqueue_spy)
    queued = client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch", json={}, headers=headers)
    assert queued.status_code == 202, queued.text
    expected = captured["payload"]["prompt"]

    after = client.get(f"/api/v1/storyboards/{project['id']}", headers=headers).json()["scenes"][0]["shots"][0]
    assert after["sketchPrompt"] == expected
    assert "水墨风，中景" in after["sketchPrompt"]
    assert after["finalPrompt"] is None

    pack = client.get(f"/api/v1/storyboards/{project['id']}/exports/prompt-pack", headers=headers)
    assert pack.status_code == 200
    assert expected in pack.text


# ---------- 需求 03：组级统一风格 ----------


def test_project_style_config_persists_and_roundtrips(client):
    headers = login(client, "storyboard-style-persist")
    config = {
        "style_id": "manga", "prompt_suffix": "日系漫画风格，干净线条",
        "negative_prompt": "低清晰度", "seed": 42,
    }
    project = create_project(client, headers, styleConfig=config)
    assert project["styleConfig"] == config

    detail = client.get(f"/api/v1/storyboards/{project['id']}", headers=headers).json()
    assert detail["styleConfig"] == config
    listed = client.get("/api/v1/storyboards", headers=headers).json()
    assert listed[0]["styleConfig"] == config

    updated = {
        "style_id": "watercolor", "prompt_suffix": "水彩手绘风格",
        "reference_asset_id": None, "seed": None, "negative_prompt": "",
    }
    patched = client.patch(
        f"/api/v1/storyboards/{project['id']}", json={"styleConfig": updated}, headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["styleConfig"] == updated

    # 空对象清空配置
    cleared = client.patch(
        f"/api/v1/storyboards/{project['id']}", json={"styleConfig": {}}, headers=headers,
    )
    assert cleared.json()["styleConfig"] == {}


def test_style_suffix_prepended_and_snapshot_in_payload(client, monkeypatch):
    headers = login(client, "storyboard-style-prompt")
    ref = _asset(client, headers)
    config = {
        "style_id": "manga", "prompt_suffix": "统一画风：日系漫画，赛璐璐",
        "negative_prompt": "低清晰度, 多余肢体", "seed": 123456,
        "reference_asset_id": ref, "style_name": "日系漫画",
    }
    project = create_project(client, headers, styleConfig=config)
    scene = _scene_for(client, headers, project)
    shot = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": "林夏站在雨里"}, headers=headers,
    ).json()
    captured = _enqueue_spy(monkeypatch)
    response = client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch", json={}, headers=headers)
    assert response.status_code == 202, response.text

    payload = captured["payload"]
    prompt = payload["prompt"]
    # 风格后缀在最前，画面主体次之，角色一致性句保留
    assert prompt.startswith("统一画风：日系漫画，赛璐璐\n")
    assert "林夏站在雨里" in prompt
    assert "雨" in prompt
    assert payload["negativePrompt"] == "低清晰度, 多余肢体"
    assert payload["seed"] == 123456
    # 风格参考图常驻槽：即使没在镜头 asset_refs 里登记也进参考图
    assert ref in payload["referenceAssetIds"]

    snapshot = payload["styleSnapshot"]
    assert snapshot["styleId"] == "manga"
    assert snapshot["styleName"] == "日系漫画"
    assert snapshot["promptSuffix"] == "统一画风：日系漫画，赛璐璐"
    assert snapshot["negativePrompt"] == "低清晰度, 多余肢体"
    assert snapshot["referenceAssetId"] == ref
    assert snapshot["seed"] == 123456


def test_style_suffix_resolved_from_registry_when_snapshot_missing(client, monkeypatch):
    headers = login(client, "storyboard-style-registry")
    # 只存 style_id，prompt_suffix 留空：后端应从统一注册表展开
    project = create_project(client, headers, styleConfig={"style_id": "manga"})
    scene = _scene_for(client, headers, project)
    shot = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": "雨夜站台"}, headers=headers,
    ).json()
    captured = _enqueue_spy(monkeypatch)
    response = client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch", json={}, headers=headers)
    assert response.status_code == 202, response.text
    payload = captured["payload"]
    assert payload["prompt"].startswith("日系漫画风格，干净线条，赛璐璐上色\n")
    assert payload["styleSnapshot"]["styleName"] == "日系漫画"
    assert payload["styleSnapshot"]["promptSuffix"] == "日系漫画风格，干净线条，赛璐璐上色"


def test_style_reference_survives_cap_and_final_stage(client, monkeypatch):
    headers = login(client, "storyboard-style-ref")
    style_ref = _asset(client, headers)
    sketch_ref = _asset(client, headers)
    project = create_project(
        client, headers,
        styleConfig={"style_id": "manga", "reference_asset_id": style_ref},
    )
    scene = _scene_for(client, headers, project)
    shot = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": "站台全景"}, headers=headers,
    ).json()
    captured = _enqueue_spy(monkeypatch)
    sketched = client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch", json={}, headers=headers)
    assert sketched.status_code == 202, sketched.text
    # 风格参考不在镜头 asset_refs 里也常驻
    assert captured["payload"]["referenceAssetIds"] == [style_ref]
    assert "reference image 1 is style 统一风格" in captured["payload"]["prompt"]

    from app.core.db import SessionLocal
    from app import models

    with SessionLocal() as db:
        row = db.get(models.StoryboardShot, shot["id"])
        row.sketch_asset_id = sketch_ref
        row.sketch_status = "succeeded"
        db.commit()
    final = client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-final", json={}, headers=headers)
    assert final.status_code == 202, final.text
    # 正式图阶段：风格参考 + 锁构图草图
    assert captured["payload"]["referenceAssetIds"] == [style_ref, sketch_ref]
    assert "reference image 1 is style 统一风格" in captured["payload"]["prompt"]
    assert "approved sketch" in captured["payload"]["prompt"]


def test_style_change_applies_to_new_shots_only(client, monkeypatch):
    headers = login(client, "storyboard-style-change")
    project = create_project(client, headers, styleConfig={
        "style_id": "manga", "prompt_suffix": "旧风格锚点",
    })
    scene = _scene_for(client, headers, project)
    shot = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": "列车进站"}, headers=headers,
    ).json()
    captured = _enqueue_spy(monkeypatch)
    assert client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch", json={}, headers=headers).status_code == 202
    old_prompt = captured["payload"]["prompt"]
    assert old_prompt.startswith("旧风格锚点\n")

    # 改项目风格后，同一镜头重新入队用新风格
    patched = client.patch(
        f"/api/v1/storyboards/{project['id']}",
        json={"styleConfig": {"style_id": "watercolor", "prompt_suffix": "新风格锚点"}},
        headers=headers,
    )
    assert patched.status_code == 200
    # 重置任务态，模拟镜头空闲后重新生成
    from app.core.db import SessionLocal
    from app import models

    with SessionLocal() as db:
        row = db.get(models.StoryboardShot, shot["id"])
        row.sketch_status = "pending"
        row.sketch_job_id = None
        db.commit()
    assert client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch", json={}, headers=headers).status_code == 202
    new_prompt = captured["payload"]["prompt"]
    assert new_prompt.startswith("新风格锚点\n")
    assert "旧风格锚点" not in new_prompt

