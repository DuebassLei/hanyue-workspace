import pytest

SAMPLE = """第一章 开始

甲说了话。

第二章 继续
乙走了。
"""


@pytest.fixture(autouse=True)
def no_auto_dispatch(monkeypatch):
    from app.core import queue

    monkeypatch.setattr(queue, "dispatch", lambda job_id, **kwargs: True)


def login(client, subject="novel-continue"):
    response = client.post("/api/v1/auth/login", json={"subject": subject, "displayName": subject})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _project(client, headers, source=SAMPLE, title="试作"):
    response = client.post(
        "/api/v1/novels",
        json={"title": title, "sourceText": source, "rightsConfirmed": True},
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

    monkeypatch.setattr(job_runner, "_novel_complete", lambda db, user_id, prompt: text)


def _accept_in_db(chapter_id, text, facts=None):
    from app.core.db import SessionLocal
    from app import models

    with SessionLocal() as db:
        row = db.get(models.NovelChapter, chapter_id)
        row.draft_text = text
        row.accepted_text = text
        row.status = "accepted"
        row.facts = facts or {
            "characterChanges": [],
            "itemHoldings": [],
            "events": ["说了话"],
            "openHooks": [],
        }
        db.commit()


def test_build_continue_prompt_uses_only_accepted_facts():
    from types import SimpleNamespace

    from app.services.novel_continue import build_continue_prompt

    accepted = SimpleNamespace(
        sort_order=0,
        title="开篇",
        facts={"characterChanges": [], "itemHoldings": [], "events": ["灯灭了"], "openHooks": []},
        status="accepted",
        accepted_text="灯灭了以后他出了门。",
        draft_text="未接受草稿秘密甲",
    )
    pending = SimpleNamespace(
        sort_order=1,
        title="草稿章",
        facts={"events": ["不该出现的事件乙"]},
        status="drafted",
        draft_text="未接受草稿秘密乙",
        rewrite_text="改写稿秘密丙",
        pending_candidate="候选稿秘密丁",
        accepted_text="",
    )
    prompt = build_continue_prompt(
        [accepted],
        accepted.accepted_text,
        [],
        [],
        {"person": "", "style": "冷一点", "banned": ""},
    )
    assert "灯灭了" in prompt
    assert "冷一点" in prompt
    assert "未接受草稿秘密甲" not in prompt
    assert "未接受草稿秘密乙" not in prompt
    assert "改写稿秘密丙" not in prompt
    assert "候选稿秘密丁" not in prompt
    assert "不该出现的事件乙" not in prompt
    leaked = build_continue_prompt([accepted, pending], accepted.accepted_text, [], [], {})
    assert "未接受草稿秘密乙" not in leaked
    assert "候选稿秘密丁" not in leaked


def test_scan_warnings_dead_name_leak_and_short_names():
    from types import SimpleNamespace

    from app.services.novel_continue import scan_warnings

    characters = [
        SimpleNamespace(original_name="甲", new_name="张三", state="dead"),
        SimpleNamespace(original_name="乙", new_name="李", state="absent"),
        SimpleNamespace(original_name="丙", new_name="", state="present"),
    ]
    aliases = [
        SimpleNamespace(original_name="旧城", new_name="新城", kind="place"),
        SimpleNamespace(original_name="李", new_name="王", kind="person"),
    ]
    warnings = scan_warnings("张三回到旧城，李也在。", characters, aliases)
    codes = {(item["code"], item["name"]) for item in warnings}
    assert ("character_present", "张三") in codes
    assert ("original_name_leaked", "旧城") in codes
    assert all(item["name"] != "李" for item in warnings)


def test_continue_requires_accepted_then_writes_new_chapter(client, monkeypatch):
    from app.core.db import SessionLocal
    from app.services import job_runner
    from app import models

    _fake_complete(monkeypatch, "续写出来的正文")
    headers = login(client, "novel-cont-ok")
    project = _project(client, headers)
    chapters = _chapters(client, headers, project["id"])
    empty = client.post(f"/api/v1/novels/{project['id']}/continue", headers=headers)
    assert empty.status_code == 400
    assert empty.json()["code"] == "nothing_to_continue"

    _accept_in_db(chapters[0]["id"], "已接受的第一章")
    queued = client.post(f"/api/v1/novels/{project['id']}/continue", headers=headers)
    assert queued.status_code == 202, queued.text
    body = queued.json()
    assert body["expectedVersion"] == 0
    with SessionLocal() as db:
        row = db.get(models.NovelChapter, body["chapterId"])
        assert row.source == "continuation"
        assert row.status == "pending"
        assert row.draft_version == 0
        assert row.source_text == ""
        assert row.sort_order == 2
        assert row.title == "续写 1"
    job_runner.run_job(body["jobId"])
    with SessionLocal() as db:
        row = db.get(models.NovelChapter, body["chapterId"])
        job = db.get(models.BackgroundJob, body["jobId"])
        assert row.draft_text == "续写出来的正文"
        assert row.accepted_text == ""
        assert row.status == "drafted"
        assert job.status == "succeeded"
        assert job.result["outcome"] == "applied"
    exported = client.get(f"/api/v1/novels/{project['id']}/export", params={"format": "md"}, headers=headers)
    assert exported.status_code == 200
    assert "已接受的第一章" in exported.text
    assert "续写出来的正文" not in exported.text
    detail = client.get(f"/api/v1/novels/{project['id']}", headers=headers)
    assert detail.status_code == 200
    found = [item for item in detail.json()["chapters"] if item["id"] == body["chapterId"]]
    assert found and found[0]["status"] == "drafted" and found[0]["source"] == "continuation"


def test_continue_blocked_by_unaccepted_continuation(client):
    headers = login(client, "novel-cont-pending")
    project = _project(client, headers)
    chapters = _chapters(client, headers, project["id"])
    _accept_in_db(chapters[0]["id"], "已接受")
    first = client.post(f"/api/v1/novels/{project['id']}/continue", headers=headers)
    assert first.status_code == 202, first.text
    second = client.post(f"/api/v1/novels/{project['id']}/continue", headers=headers)
    assert second.status_code == 400
    assert second.json()["code"] == "draft_pending"


def test_export_only_accepted_chapters(client):
    headers = login(client, "novel-export")
    project = _project(client, headers, title="bad/name:here")
    chapters = _chapters(client, headers, project["id"])
    _accept_in_db(chapters[0]["id"], "第一章定稿")
    from app.core.db import SessionLocal
    from app import models

    with SessionLocal() as db:
        row = db.get(models.NovelChapter, chapters[1]["id"])
        row.draft_text = "第二章草稿不该导出"
        row.status = "drafted"
        db.commit()
    exported = client.get(f"/api/v1/novels/{project['id']}/export", params={"format": "txt"}, headers=headers)
    assert exported.status_code == 200
    assert "第一章定稿" in exported.text
    assert "第二章草稿不该导出" not in exported.text
    disposition = exported.headers["content-disposition"]
    assert "attachment" in disposition
    assert "bad_name_here.txt" in disposition

    bad = client.get(f"/api/v1/novels/{project['id']}/export", params={"format": "pdf"}, headers=headers)
    assert bad.status_code == 400
    assert bad.json()["code"] == "bad_format"

    bare = _project(client, headers, title="空书")
    empty = client.get(f"/api/v1/novels/{bare['id']}/export", params={"format": "md"}, headers=headers)
    assert empty.status_code == 200
    assert empty.text == ""


def test_scan_impact_names_later_chapter_without_rewriting():
    from types import SimpleNamespace

    from app.services.novel_continue import scan_impact

    first = SimpleNamespace(
        id="c0",
        sort_order=0,
        status="accepted",
        facts={"characterChanges": ["张三死亡"], "itemHoldings": [], "events": [], "openHooks": []},
        accepted_text="甲倒下了。",
    )
    second = SimpleNamespace(
        id="c1",
        sort_order=1,
        status="accepted",
        accepted_text="张三还站在门口。",
        stale=False,
    )
    characters = [SimpleNamespace(original_name="甲", new_name="张三", state="dead")]
    impact = scan_impact(first, [second], characters)
    assert impact["chapters"][0]["chapterId"] == "c1"
    assert {"code": "stale_character", "name": "张三"} in impact["chapters"][0]["reasons"]
    assert second.accepted_text == "张三还站在门口。"


def test_accept_records_impact_and_leaves_later_text(client, monkeypatch):
    from app.core.db import SessionLocal
    from app import models

    _fake_complete(
        monkeypatch,
        '{"characterChanges":["张三死亡"],"itemHoldings":[],"events":[],"openHooks":[]}',
    )
    headers = login(client, "novel-impact")
    project = _project(client, headers)
    chapters = _chapters(client, headers, project["id"])
    with SessionLocal() as db:
        first = db.get(models.NovelChapter, chapters[0]["id"])
        first.draft_text = "甲在这一章死了"
        first.status = "drafted"
        second = db.get(models.NovelChapter, chapters[1]["id"])
        second.status = "accepted"
        second.accepted_text = "张三还活在后文里"
        second.draft_text = "后章草稿"
        db.add(
            models.NovelCharacter(
                project_id=project["id"], original_name="甲", new_name="张三", state="dead"
            )
        )
        db.commit()
        before = second.accepted_text
    response = client.post(
        f"/api/v1/novels/{project['id']}/chapters/{chapters[0]['id']}/accept",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    impact = response.json()["impact"]["chapters"]
    assert impact and impact[0]["chapterId"] == chapters[1]["id"]
    assert any(item["code"] == "stale_character" and item["name"] == "张三" for item in impact[0]["reasons"])
    with SessionLocal() as db:
        later = db.get(models.NovelChapter, chapters[1]["id"])
        assert later.accepted_text == before
        assert later.draft_text == "后章草稿"


def test_continue_version_conflict_keeps_candidate(client, monkeypatch):
    from app.core.db import SessionLocal
    from app.services import job_runner
    from app import models

    _fake_complete(monkeypatch, "冲突候选")
    headers = login(client, "novel-cont-conflict")
    project = _project(client, headers)
    chapters = _chapters(client, headers, project["id"])
    _accept_in_db(chapters[0]["id"], "已接受")
    queued = client.post(f"/api/v1/novels/{project['id']}/continue", headers=headers)
    assert queued.status_code == 202, queued.text
    chapter_id = queued.json()["chapterId"]
    with SessionLocal() as db:
        row = db.get(models.NovelChapter, chapter_id)
        row.draft_version = 5
        row.draft_text = ""
        db.commit()
    job_runner.run_job(queued.json()["jobId"])
    with SessionLocal() as db:
        row = db.get(models.NovelChapter, chapter_id)
        job = db.get(models.BackgroundJob, queued.json()["jobId"])
        assert row.draft_text == ""
        assert row.pending_candidate == "冲突候选"
        assert row.draft_version == 5
        assert job.status == "succeeded"
        assert job.result["outcome"] == "conflict"
