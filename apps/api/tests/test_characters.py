"""角色库：CRUD、抽取草稿、分镜双来源引用、按镜头选图与生成快照。"""

import pytest


@pytest.fixture(autouse=True)
def no_auto_dispatch(monkeypatch):
    from app.core import queue

    monkeypatch.setattr(queue, "dispatch", lambda job_id, **kwargs: True)


def login(client, subject="character-user"):
    response = client.post("/api/v1/auth/login", json={"subject": subject, "displayName": subject})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


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


def make_character(client, headers, front, full=None, **values):
    images = [{"assetId": front, "angle": "front", "isPrimary": True}]
    if full:
        images.append({"assetId": full, "angle": "full"})
    payload = {
        "name": "林夏",
        "aliases": ["夏子"],
        "appearance": {"hair": "短发", "outfit": "深色外套"},
        "images": images,
        "notes": "夜行列车主角",
        **values,
    }
    response = client.post("/api/v1/characters", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def make_project_with_role(client, headers, role):
    response = client.post("/api/v1/storyboards", json={
        "title": "夜行列车",
        "assets": {
            "characters": [{"id": f"role:{role['id']}", "name": role["name"], "roleId": role["id"]}],
            "locations": [{"id": "platform", "name": "夜站台", "description": "雨，冷白灯"}],
            "styles": [],
        },
    }, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def add_shot(client, headers, project, frame="林夏站在雨里", refs=("platform",), **values):
    scene = client.post(
        f"/api/v1/storyboards/{project['id']}/scenes", json={"title": "站台"}, headers=headers,
    ).json()
    response = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots",
        json={"frameDescription": frame, "assetRefs": list(refs), **values}, headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_character_crud_is_owner_scoped_and_soft_deletes(client):
    headers = login(client, "char-crud")
    other = login(client, "char-other")
    front = _asset(client, headers)
    full = _asset(client, headers)
    role = make_character(client, headers, front, full)

    assert role["name"] == "林夏"
    assert role["aliases"] == ["夏子"]
    assert [image["angle"] for image in role["images"]] == ["front", "full"]
    assert sum(bool(image["isPrimary"]) for image in role["images"]) == 1
    assert all(image["url"] for image in role["images"])

    listed = client.get("/api/v1/characters", headers=headers)
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [role["id"]]

    patched = client.patch(f"/api/v1/characters/{role['id']}", json={
        "name": "林夏改", "aliases": ["夏子", "小林"],
        "appearance": {"hair": "长发", "madeUpKey": "不该被保存"},
    }, headers=headers)
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "林夏改"
    assert patched.json()["aliases"] == ["夏子", "小林"]
    assert patched.json()["appearance"] == {"hair": "长发"}

    assert client.get(f"/api/v1/characters/{role['id']}", headers=other).status_code == 404
    assert client.patch(f"/api/v1/characters/{role['id']}", json={"name": "越权"}, headers=other).status_code == 404
    assert client.delete(f"/api/v1/characters/{role['id']}", headers=other).status_code == 404

    assert client.delete(f"/api/v1/characters/{role['id']}", headers=headers).status_code == 204
    assert client.get("/api/v1/characters", headers=headers).json() == []
    assert client.get(f"/api/v1/characters/{role['id']}", headers=headers).status_code == 404


def test_extract_returns_drafts_without_persisting(client, monkeypatch):
    from app.services import character_extract

    async def fake_extract(db, user_id, source_text, model_id):
        return {
            "drafts": [{"name": "林夏", "aliases": ["夏子"], "appearance": {"hair": "短发"}}],
            "model_code": "test-model", "fallback_used": False,
        }

    monkeypatch.setattr(character_extract, "extract", fake_extract)
    headers = login(client, "char-extract")
    response = client.post("/api/v1/characters/extract", json={"sourceText": "林夏走进站台"}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["drafts"][0]["name"] == "林夏"
    assert body["drafts"][0]["exists"] is False
    assert body["modelCode"] == "test-model"
    assert client.get("/api/v1/characters", headers=headers).json() == []  # 草稿绝不落库

    front = _asset(client, headers)
    make_character(client, headers, front)
    again = client.post("/api/v1/characters/extract", json={"sourceText": "林夏走进站台"}, headers=headers)
    assert again.json()["drafts"][0]["exists"] is True


def test_extract_fails_loudly_when_no_model(client, monkeypatch):
    from app.services import ai_gateway

    monkeypatch.setattr(ai_gateway, "resolve_selection", lambda *args, **kwargs: {"candidates": []})
    headers = login(client, "char-no-model")
    response = client.post("/api/v1/characters/extract", json={"sourceText": "林夏走进站台"}, headers=headers)
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "provider_unavailable"
    assert "手工登记" in response.json()["message"]


def test_extract_endpoint_streams_from_gateway_and_falls_back(client, monkeypatch):
    from app.services import ai_gateway

    async def fake_stream(db, **kwargs):
        if kwargs.get("model_code") == "broken-model":
            yield {"event": "generation.error", "data": {"message": "供应商不可用"}}
            return
        yield {"event": "generation.delta", "data": {"text": (
            '{"characters":[{"name":"周晚","aliases":["晚晚"],'
            '"appearance":{"hair":"长发","bogus":"忽略"}}]}'
        )}}

    monkeypatch.setattr(ai_gateway, "resolve_selection", lambda *args, **kwargs: {
        "candidates": [(None, "broken-model"), (None, "good-model")],
    })
    monkeypatch.setattr(ai_gateway, "stream_text", fake_stream)
    headers = login(client, "char-stream")
    response = client.post("/api/v1/characters/extract", json={"sourceText": "周晚走进站台"}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["drafts"] == [{
        "name": "周晚", "aliases": ["晚晚"], "appearance": {"hair": "长发"}, "exists": False,
    }]
    assert body["modelCode"] == "good-model"
    assert body["fallbackUsed"] is True
    assert client.get("/api/v1/characters", headers=headers).json() == []


def test_extract_normalize_drops_garbage_and_unknown_fields(client):
    from app.services.character_extract import ExtractionError, _normalize

    drafts = _normalize({"characters": [
        {"name": "林夏", "aliases": ["夏子", "夏子", "林夏"], "appearance": {"hair": "短发", "bogus": "x"}},
        {"name": "林夏", "aliases": [], "appearance": {}},
        {"name": "", "aliases": []},
    ]})
    assert len(drafts) == 1
    assert drafts[0] == {"name": "林夏", "aliases": ["夏子"], "appearance": {"hair": "短发"}}
    with pytest.raises(ExtractionError):
        _normalize({"characters": [{"name": ""}]})


def test_project_resolves_library_character_and_picks_image_per_shot(client, monkeypatch):
    from app.core import queue

    headers = login(client, "char-storyboard")
    front = _asset(client, headers)
    full = _asset(client, headers)
    role = make_character(client, headers, front, full)
    project = make_project_with_role(client, headers, role)

    detail = client.get(f"/api/v1/storyboards/{project['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    resolved = detail.json()["assets"]["characters"][0]
    assert resolved["roleId"] == role["id"]
    assert resolved["name"] == "林夏"
    assert resolved["aliases"] == ["夏子"]
    assert "短发" in resolved["description"]
    assert len(resolved["images"]) == 2

    wide = add_shot(client, headers, project, frame="夏子走进站台", shotSize="wide")
    medium = add_shot(client, headers, project, frame="林夏抬头", shotSize="medium")

    captured = {}
    original = queue.enqueue

    def spy(db, **kwargs):
        captured.update(kwargs)
        return original(db, **kwargs)

    monkeypatch.setattr(queue, "enqueue", spy)
    queued = client.post(f"/api/v1/storyboards/shots/{wide['id']}/generate-sketch", json={}, headers=headers)
    assert queued.status_code == 202, queued.text
    # wide 景别选 full 角度图，别名「夏子」能命中角色
    assert captured["payload"]["referenceAssetIds"] == [full]
    assert "林夏" in captured["payload"]["prompt"]
    assert "短发" in captured["payload"]["prompt"]
    snapshot = captured["payload"]["characterSnapshot"][0]
    assert snapshot["roleId"] == role["id"]
    assert snapshot["aliases"] == ["夏子"]
    assert snapshot["referenceAssetId"] == full
    assert set(snapshot["allImageAssetIds"]) == {front, full}

    client.post(f"/api/v1/storyboards/shots/{medium['id']}/generate-sketch", json={}, headers=headers)
    assert captured["payload"]["referenceAssetIds"] == [front]


def test_rename_updates_project_but_history_prompt_stays(client, monkeypatch):
    from app.core import queue

    headers = login(client, "char-history")
    front = _asset(client, headers)
    role = make_character(client, headers, front)
    project = make_project_with_role(client, headers, role)
    shot = add_shot(client, headers, project)

    captured = {}
    original = queue.enqueue

    def spy(db, **kwargs):
        captured.update(kwargs)
        return original(db, **kwargs)

    monkeypatch.setattr(queue, "enqueue", spy)
    assert client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch", json={}, headers=headers).status_code == 202
    first_prompt = captured["payload"]["prompt"]

    renamed = client.patch(f"/api/v1/characters/{role['id']}", json={"name": "林秋"}, headers=headers)
    assert renamed.status_code == 200
    detail = client.get(f"/api/v1/storyboards/{project['id']}", headers=headers).json()
    assert detail["assets"]["characters"][0]["name"] == "林秋"
    # 历史实际出图提示词来自 job payload 快照，不随角色改名漂移
    assert detail["scenes"][0]["shots"][0]["sketchPrompt"] == first_prompt
    assert "林夏" in first_prompt


def test_soft_deleted_character_keeps_existing_project_usable(client):
    headers = login(client, "char-deleted")
    front = _asset(client, headers)
    role = make_character(client, headers, front)
    project = make_project_with_role(client, headers, role)

    assert client.delete(f"/api/v1/characters/{role['id']}", headers=headers).status_code == 204
    detail = client.get(f"/api/v1/storyboards/{project['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["assets"]["characters"][0]["name"] == "林夏"
    patched = client.patch(f"/api/v1/storyboards/{project['id']}", json={"title": "改名"}, headers=headers)
    assert patched.status_code == 200, patched.text


def _capture_enqueue(monkeypatch):
    from app.core import queue

    captured = {}
    original = queue.enqueue

    def spy(db, **kwargs):
        captured.update(kwargs)
        return original(db, **kwargs)

    monkeypatch.setattr(queue, "enqueue", spy)
    return captured


def test_prompt_template_is_deterministic_and_whitelisted():
    from app.services.character_images import build_prompt

    prompt = build_prompt("林夏", {"hair": "短发", "outfit": "深色外套"}, "front", "manga")
    assert "林夏" in prompt and "短发" in prompt and "深色外套" in prompt
    assert "正面半身像" in prompt and "日系漫画" in prompt
    # 未知风格/角度回落到默认词，绝不把任意输入原样拼进提示词
    weird = build_prompt("甲", {}, "evil'; DROP", "not-a-style")
    assert "evil" not in weird and "not-a-style" not in weird
    assert "设定图" in weird


def test_generate_queues_anchored_job_and_tracks_pending(client, monkeypatch):
    headers = login(client, "char-gen")
    front = _asset(client, headers)
    role = make_character(client, headers, front)
    captured = _capture_enqueue(monkeypatch)

    response = client.post(
        f"/api/v1/characters/{role['id']}/images/generate",
        json={"angle": "side", "style": "manga"}, headers=headers,
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["anchored"] is True
    assert captured["queue"] == "ai-image" and captured["kind"] == "character.image"
    assert captured["payload"]["referenceAssetIds"] == [front]
    assert captured["payload"]["characterId"] == role["id"]
    assert "侧面全身像" in captured["payload"]["prompt"]

    card = client.get(f"/api/v1/characters/{role['id']}", headers=headers).json()
    assert len(card["pendingImages"]) == 1
    pending = card["pendingImages"][0]
    assert pending["angle"] == "side" and pending["status"] == "queued"
    assert pending["jobId"] == body["jobId"]


def test_generate_without_anchor_is_text_to_image(client, monkeypatch):
    headers = login(client, "char-gen-anon")
    role = make_character(client, headers, _asset(client, headers))
    client.patch(f"/api/v1/characters/{role['id']}", json={"images": []}, headers=headers)
    captured = _capture_enqueue(monkeypatch)
    response = client.post(
        f"/api/v1/characters/{role['id']}/images/generate",
        json={"angle": "front", "style": "watercolor", "promptOverride": "自定义提示词"},
        headers=headers,
    )
    assert response.status_code == 202, response.text
    assert response.json()["anchored"] is False
    assert "referenceAssetIds" not in captured["payload"]
    assert captured["payload"]["prompt"] == "自定义提示词"


def test_accept_promotes_first_image_to_primary(client, monkeypatch):
    from app import models
    from app.core.db import SessionLocal

    headers = login(client, "char-accept")
    role = make_character(client, headers, _asset(client, headers))
    candidate = _asset(client, headers)
    session = SessionLocal()
    try:
        row = session.get(models.Character, role["id"])
        row.pending_images = [{
            "assetId": candidate, "angle": "full", "jobId": "job-a",
            "status": "succeeded", "prompt": "p", "degraded": False,
        }]
        session.commit()
    finally:
        session.close()

    accepted = client.post(f"/api/v1/characters/{role['id']}/images/job-a/accept", headers=headers)
    assert accepted.status_code == 200, accepted.text
    images = accepted.json()["images"]
    assert [image["assetId"] for image in images] == [role["images"][0]["assetId"], candidate]
    assert sum(bool(image["isPrimary"]) for image in images) == 1
    assert accepted.json()["pendingImages"] == []

    # 重复采用同一个候选 → 404
    assert client.post(f"/api/v1/characters/{role['id']}/images/job-a/accept", headers=headers).status_code == 404


def test_regenerating_same_angle_evicts_old_candidate_asset(client):
    from app import models
    from app.core.db import SessionLocal

    headers = login(client, "char-evict")
    role = make_character(client, headers, _asset(client, headers))
    old = _asset(client, headers)
    session = SessionLocal()
    try:
        row = session.get(models.Character, role["id"])
        row.pending_images = [{
            "assetId": old, "angle": "side", "jobId": "job-old",
            "status": "succeeded", "prompt": "p", "degraded": False,
        }]
        session.commit()
    finally:
        session.close()

    response = client.post(
        f"/api/v1/characters/{role['id']}/images/generate", json={"angle": "side"}, headers=headers,
    )
    assert response.status_code == 202, response.text
    card = client.get(f"/api/v1/characters/{role['id']}", headers=headers).json()
    assert len(card["pendingImages"]) == 1
    assert card["pendingImages"][0]["jobId"] != "job-old"
    session = SessionLocal()
    try:
        assert session.get(models.Asset, old) is None
    finally:
        session.close()


def test_reject_deletes_candidate_asset(client):
    from app import models
    from app.core.db import SessionLocal

    headers = login(client, "char-reject")
    role = make_character(client, headers, _asset(client, headers))
    candidate = _asset(client, headers)
    session = SessionLocal()
    try:
        row = session.get(models.Character, role["id"])
        row.pending_images = [{
            "assetId": candidate, "angle": "back", "jobId": "job-b",
            "status": "succeeded", "prompt": "p", "degraded": False,
        }]
        session.commit()
    finally:
        session.close()

    assert client.delete(f"/api/v1/characters/{role['id']}/images/job-b", headers=headers).status_code == 204
    assert client.get(f"/api/v1/characters/{role['id']}", headers=headers).json()["pendingImages"] == []
    session = SessionLocal()
    try:
        assert session.get(models.Asset, candidate) is None
    finally:
        session.close()


def test_pending_with_deleted_asset_reports_failed_not_crash(client):
    from app import models
    from app.core.db import SessionLocal

    headers = login(client, "char-dangling")
    role = make_character(client, headers, _asset(client, headers))
    gone = _asset(client, headers)
    assert client.delete(f"/api/v1/assets/{gone}", headers=headers).status_code == 204
    session = SessionLocal()
    try:
        row = session.get(models.Character, role["id"])
        row.pending_images = [{
            "assetId": gone, "angle": "front", "jobId": "job-gone",
            "status": "succeeded", "prompt": "p", "degraded": False,
        }]
        session.commit()
    finally:
        session.close()

    card = client.get(f"/api/v1/characters/{role['id']}", headers=headers)
    assert card.status_code == 200, card.text
    pending = card.json()["pendingImages"][0]
    assert pending["status"] == "failed" and pending["url"] is None

    # images 里的悬空引用同样跳过而不是 500
    session = SessionLocal()
    try:
        row = session.get(models.Character, role["id"])
        row.images = [{"assetId": gone, "angle": "front", "isPrimary": True}]
        session.commit()
    finally:
        session.close()
    again = client.get(f"/api/v1/characters/{role['id']}", headers=headers)
    assert again.status_code == 200, again.text
    assert again.json()["images"] == []


def test_appearance_complete_returns_suggestions_without_writing(client, monkeypatch):
    from app.services import ai_gateway

    async def fake_stream(db, **kwargs):
        yield {"event": "generation.delta", "data": {"text": (
            '{"appearance":{"hair":"银色长发","bogus":"忽略","eyes":"琥珀色"}}'
        )}}

    monkeypatch.setattr(ai_gateway, "resolve_selection", lambda *args, **kwargs: {
        "candidates": [(None, "good-model")],
    })
    monkeypatch.setattr(ai_gateway, "stream_text", fake_stream)
    headers = login(client, "char-complete")
    role = make_character(client, headers, _asset(client, headers))
    response = client.post(
        f"/api/v1/characters/{role['id']}/appearance/complete",
        json={"hintText": "冷峻的剑客", "sourceText": "她解开斗篷，银色长发垂到腰际。"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["appearance"] == {"hair": "银色长发", "eyes": "琥珀色"}
    assert body["modelCode"] == "good-model"
    # 建议绝不自动落库
    assert client.get(f"/api/v1/characters/{role['id']}", headers=headers).json()["appearance"] == {
        "hair": "短发", "outfit": "深色外套",
    }


def test_appearance_complete_fails_loudly_without_model(client, monkeypatch):
    from app.services import ai_gateway

    monkeypatch.setattr(ai_gateway, "resolve_selection", lambda *args, **kwargs: {"candidates": []})
    headers = login(client, "char-complete-nomodel")
    role = make_character(client, headers, _asset(client, headers))
    response = client.post(
        f"/api/v1/characters/{role['id']}/appearance/complete",
        json={"hintText": "随便"}, headers=headers,
    )
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "provider_unavailable"


def test_character_image_job_success_writes_pending(client, monkeypatch):
    from app import models
    from app.core import queue
    from app.core.db import SessionLocal
    from app.services import image_provider, job_runner

    headers = login(client, "char-job-ok")
    role = make_character(client, headers, _asset(client, headers))
    _capture_enqueue(monkeypatch)
    queued = client.post(
        f"/api/v1/characters/{role['id']}/images/generate", json={"angle": "close"}, headers=headers,
    ).json()

    monkeypatch.setattr(
        image_provider, "generate_images",
        lambda db, uid, **kwargs: [(_tiny_png(), "image/png")],
    )
    job_runner.run_job(queued["jobId"])

    session = SessionLocal()
    try:
        job = session.get(models.BackgroundJob, queued["jobId"])
        assert job.status == "succeeded", job.error_message
        asset_id = job.result["assetIds"][0]
        row = session.get(models.Character, role["id"])
        pending = row.pending_images[0]
        assert pending["status"] == "succeeded" and pending["assetId"] == asset_id
        assert pending["degraded"] is False
    finally:
        session.close()
    card = client.get(f"/api/v1/characters/{role['id']}", headers=headers).json()
    assert card["pendingImages"][0]["url"]


def test_character_image_job_failure_marks_pending(client, monkeypatch):
    from app import models
    from app.core.db import SessionLocal
    from app.services import image_provider, job_runner

    headers = login(client, "char-job-fail")
    role = make_character(client, headers, _asset(client, headers))
    _capture_enqueue(monkeypatch)
    queued = client.post(
        f"/api/v1/characters/{role['id']}/images/generate", json={"angle": "back"}, headers=headers,
    ).json()

    def boom(db, uid, **kwargs):
        raise image_provider.ImageProviderUnavailable("image_provider_unavailable", "未接入图像供应商")

    monkeypatch.setattr(image_provider, "generate_images", boom)
    job_runner.run_job(queued["jobId"])

    session = SessionLocal()
    try:
        row = session.get(models.Character, role["id"])
        assert row.pending_images[0]["status"] == "failed"
        assert "图像供应商" in row.pending_images[0]["error"]
    finally:
        session.close()


def _tiny_png() -> bytes:
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (30, 60, 90)).save(buf, format="PNG")
    return buf.getvalue()


def test_legacy_local_character_still_generates(client, monkeypatch):
    from app.core import queue

    headers = login(client, "char-legacy")
    asset_id = _asset(client, headers)
    project = client.post("/api/v1/storyboards", json={
        "title": "老项目",
        "assets": {"characters": [{"id": "hero", "name": "林夏", "assetId": asset_id, "description": "短发"}]},
    }, headers=headers)
    assert project.status_code == 201, project.text
    shot = add_shot(client, headers, project.json(), frame="林夏站在雨里", refs=())

    captured = {}
    original = queue.enqueue

    def spy(db, **kwargs):
        captured.update(kwargs)
        return original(db, **kwargs)

    monkeypatch.setattr(queue, "enqueue", spy)
    assert client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch", json={}, headers=headers).status_code == 202
    assert captured["payload"]["referenceAssetIds"] == [asset_id]
    assert captured["payload"]["characterSnapshot"][0]["name"] == "林夏"
