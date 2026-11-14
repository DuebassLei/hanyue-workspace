import json

import pytest

SAMPLE = """第一章 开始

甲说了话。

第二章 继续
乙走了。
"""

FACTS = '{"characterChanges":["甲离开"],"itemHoldings":[],"events":["说了话"],"openHooks":[]}'
EXTRACT = (
    '{"characters":[{"originalName":"甲","relations":"","speechStyle":"","state":"present"}],'
    '"aliases":[{"kind":"person","originalName":"甲"}]}'
)


@pytest.fixture(autouse=True)
def no_auto_dispatch(monkeypatch):
    from app.core import queue

    monkeypatch.setattr(queue, "dispatch", lambda job_id, **kwargs: True)


def login(client, subject="novel-jobs"):
    response = client.post("/api/v1/auth/login", json={"subject": subject, "displayName": subject})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _project(client, headers, source=SAMPLE):
    response = client.post(
        "/api/v1/novels",
        json={"title": "试作", "sourceText": source, "rightsConfirmed": True},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _chapters(client, headers, project_id):
    response = client.post(f"/api/v1/novels/{project_id}/split", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _fake_complete(monkeypatch, text):
    from app.services import job_runner

    def fake(db, user_id, prompt):
        if isinstance(text, Exception):
            raise text
        return text

    monkeypatch.setattr(job_runner, "_novel_complete", fake)


def test_extract_enqueues_and_handler_writes_counts(client, monkeypatch):
    from app.core.db import SessionLocal
    from app.services import job_runner
    from app import models

    _fake_complete(monkeypatch, EXTRACT)
    headers = login(client, "novel-extract")
    project = _project(client, headers)
    queued = client.post(f"/api/v1/novels/{project['id']}/extract", headers=headers)
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["jobId"]
    assert job_id

    busy = client.post(f"/api/v1/novels/{project['id']}/extract", headers=headers)
    assert busy.status_code == 409
    assert busy.json()["code"] == "job_in_progress"

    job_runner.run_job(job_id)
    with SessionLocal() as db:
        job = db.get(models.BackgroundJob, job_id)
        assert job.status == "succeeded"
        assert job.result == {"characters": 1, "aliases": 1}
        names = [
            row.original_name
            for row in db.query(models.NovelCharacter).filter_by(project_id=project["id"]).all()
        ]
    assert names == ["甲"]

    seen = client.get(f"/api/v1/novels/{project['id']}/jobs/{job_id}", headers=headers)
    assert seen.status_code == 200
    body = seen.json()
    assert body["id"] == job_id
    assert body["status"] == "succeeded"
    assert body["kind"] == "novel.extract"
    assert body["result"]["characters"] == 1


def test_rewrite_applies_draft(client, monkeypatch):
    from app.core.db import SessionLocal
    from app.services import job_runner
    from app import models

    _fake_complete(monkeypatch, "改写正文")
    headers = login(client, "novel-rewrite")
    project = _project(client, headers)
    chapter = _chapters(client, headers, project["id"])[0]
    before = chapter["draftVersion"]
    queued = client.post(
        f"/api/v1/novels/{project['id']}/chapters/{chapter['id']}/rewrite",
        headers=headers,
    )
    assert queued.status_code == 202, queued.text
    assert queued.json()["expectedVersion"] == before
    job_runner.run_job(queued.json()["jobId"])
    with SessionLocal() as db:
        row = db.get(models.NovelChapter, chapter["id"])
        job = db.get(models.BackgroundJob, queued.json()["jobId"])
        assert row.draft_text == "改写正文"
        assert row.status == "drafted"
        assert row.draft_version == before + 1
        assert job.status == "succeeded"
        assert job.result["outcome"] == "applied"


def test_rewrite_conflict_keeps_old_draft(client, monkeypatch):
    from app.core.db import SessionLocal
    from app.services import job_runner
    from app import models

    _fake_complete(monkeypatch, "新正文")
    headers = login(client, "novel-conflict")
    project = _project(client, headers)
    chapter = _chapters(client, headers, project["id"])[0]
    queued = client.post(
        f"/api/v1/novels/{project['id']}/chapters/{chapter['id']}/rewrite",
        headers=headers,
    )
    assert queued.status_code == 202, queued.text
    with SessionLocal() as db:
        row = db.get(models.NovelChapter, chapter["id"])
        row.draft_text = "旧草稿"
        row.draft_version = 99
        db.commit()
    job_runner.run_job(queued.json()["jobId"])
    with SessionLocal() as db:
        row = db.get(models.NovelChapter, chapter["id"])
        job = db.get(models.BackgroundJob, queued.json()["jobId"])
        assert row.draft_text == "旧草稿"
        assert row.pending_candidate == "新正文"
        assert row.draft_version == 99
        assert job.status == "succeeded"
        assert job.result["outcome"] == "conflict"


def test_rewrite_without_model_does_not_write_placeholder(client, monkeypatch):
    from app.core.db import SessionLocal
    from app.services import job_runner
    from app import models

    _fake_complete(
        monkeypatch,
        job_runner.JobFailure("provider_unavailable", "尚未配置可用的模型连接", retryable=False),
    )
    headers = login(client, "novel-nomodel")
    project = _project(client, headers)
    chapter = _chapters(client, headers, project["id"])[0]
    queued = client.post(
        f"/api/v1/novels/{project['id']}/chapters/{chapter['id']}/rewrite",
        headers=headers,
    )
    job_runner.run_job(queued.json()["jobId"])
    with SessionLocal() as db:
        row = db.get(models.NovelChapter, chapter["id"])
        job = db.get(models.BackgroundJob, queued.json()["jobId"])
        assert row.draft_text == ""
        assert row.pending_candidate is None
        assert job.status == "failed"
        assert job.error_code == "provider_unavailable"


def test_accept_empty_draft(client):
    headers = login(client, "novel-empty")
    project = _project(client, headers)
    chapter = _chapters(client, headers, project["id"])[0]
    response = client.post(
        f"/api/v1/novels/{project['id']}/chapters/{chapter['id']}/accept",
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "empty_draft"


def test_accept_sync_writes_facts_and_clears_own_stale(client, monkeypatch):
    from app.core.db import SessionLocal
    from app import models

    _fake_complete(monkeypatch, FACTS)
    headers = login(client, "novel-accept")
    project = _project(client, headers)
    chapters = _chapters(client, headers, project["id"])
    with SessionLocal() as db:
        row = db.get(models.NovelChapter, chapters[0]["id"])
        row.draft_text = "可接受的草稿"
        row.stale = True
        db.commit()
    response = client.post(
        f"/api/v1/novels/{project['id']}/chapters/{chapters[0]['id']}/accept",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "accepted"
    assert body["acceptedText"] == "可接受的草稿"
    assert body["stale"] is False
    assert body["facts"]["events"] == ["说了话"]
    with SessionLocal() as db:
        later = db.get(models.NovelChapter, chapters[1]["id"])
        assert later.stale is False
        assert later.draft_text == ""


def test_revert_marks_later_chapters_stale(client):
    from app.core.db import SessionLocal
    from app import models

    headers = login(client, "novel-revert")
    project = _project(client, headers)
    chapters = _chapters(client, headers, project["id"])
    with SessionLocal() as db:
        row = db.get(models.NovelChapter, chapters[0]["id"])
        row.status = "accepted"
        row.accepted_text = "已接受正文"
        row.draft_text = "已接受正文"
        db.commit()
    response = client.post(
        f"/api/v1/novels/{project['id']}/chapters/{chapters[0]['id']}/revert",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["chapter"]["status"] == "drafted"
    assert body["chapter"]["acceptedText"] == "已接受正文"
    assert body["staleChapterIds"] == [chapters[1]["id"]]
    with SessionLocal() as db:
        later = db.get(models.NovelChapter, chapters[1]["id"])
        assert later.stale is True


def test_other_user_cannot_extract(client):
    owner = login(client, "novel-owner-a")
    other = login(client, "novel-owner-b")
    project = _project(client, owner)
    response = client.post(f"/api/v1/novels/{project['id']}/extract", headers=other)
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_character_and_alias_rename_stay_in_sync(client):
    from app.core.db import SessionLocal
    from app import models

    headers = login(client, "novel-rename")
    project = _project(client, headers)
    with SessionLocal() as db:
        character = models.NovelCharacter(
            project_id=project["id"], original_name="甲", new_name="", state="present"
        )
        alias = models.NovelAlias(
            project_id=project["id"], kind="person", original_name="甲", new_name=""
        )
        place = models.NovelAlias(
            project_id=project["id"], kind="place", original_name="甲", new_name=""
        )
        db.add_all([character, alias, place])
        db.commit()
        character_id, alias_id, place_id = character.id, alias.id, place.id

    bad = client.patch(
        f"/api/v1/novels/{project['id']}/characters/{character_id}",
        json={"state": "ghost"},
        headers=headers,
    )
    assert bad.status_code == 400
    assert bad.json()["code"] == "bad_state"

    renamed = client.patch(
        f"/api/v1/novels/{project['id']}/characters/{character_id}",
        json={"newName": "乙", "state": "dead"},
        headers=headers,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["newName"] == "乙"
    assert renamed.json()["state"] == "dead"
    with SessionLocal() as db:
        assert db.get(models.NovelAlias, alias_id).new_name == "乙"
        assert db.get(models.NovelAlias, place_id).new_name == ""

    via_alias = client.patch(
        f"/api/v1/novels/{project['id']}/aliases/{alias_id}",
        json={"newName": "丙"},
        headers=headers,
    )
    assert via_alias.status_code == 200, via_alias.text
    with SessionLocal() as db:
        assert db.get(models.NovelCharacter, character_id).new_name == "丙"


def test_rewrite_rejects_empty_source(client):
    from app.core.db import SessionLocal
    from app import models

    headers = login(client, "novel-nothing")
    project = _project(client, headers)
    chapter = _chapters(client, headers, project["id"])[0]
    with SessionLocal() as db:
        row = db.get(models.NovelChapter, chapter["id"])
        row.source_text = "  "
        db.commit()
    response = client.post(
        f"/api/v1/novels/{project['id']}/chapters/{chapter['id']}/rewrite",
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "nothing_to_rewrite"
    assert json.loads(response.text)["code"] == "nothing_to_rewrite"
