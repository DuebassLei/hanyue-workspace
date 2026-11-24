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

    monkeypatch.setattr(
        job_runner, "_novel_complete", lambda db, user_id, prompt, **kwargs: text
    )


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


def test_legacy_facts_api_normalizes_scalar_values(client):
    from app.core.db import SessionLocal
    from app import models
    headers = login(client, 'novel-legacy-facts-api')
    project = _project(client, headers)
    chapters = _chapters(client, headers, project['id'])
    with SessionLocal() as db:
        db.get(models.NovelChapter, chapters[0]['id']).facts = {'events': '灯灭', 'openHooks': 42}
        db.commit()
    response = client.get(f"/api/v1/novels/{project['id']}", headers=headers)
    assert response.status_code == 200
    facts = response.json()['chapters'][0]['facts']
    assert facts['events'] == ['灯灭'] and facts['openHooks'] == [42]


def test_legacy_facts_are_safely_projected():
    from types import SimpleNamespace
    from app.services.novel_continue import build_continue_prompt
    chapter = SimpleNamespace(id='legacy', sort_order=0, title='旧章', status='accepted', stale=False, accepted_text='正文', facts={'events': '灯灭', 'openHooks': 42, 'itemHoldings': [{'owner': '甲', 'item': '剑'}]})
    prompt = build_continue_prompt([chapter], '正文', [], [], {})
    assert '灯灭' in prompt and '42' in prompt and '剑' in prompt


def test_continue_dispatch_sees_committed_chapter_and_job(client, monkeypatch):
    from app.core import queue
    from app.core.db import SessionLocal
    from app import models
    headers = login(client, 'novel-atomic-enqueue')
    project = _project(client, headers)
    chapters = _chapters(client, headers, project['id'])
    _accept_in_db(chapters[0]['id'], '历史')
    seen = []
    def dispatch(job_id, **kwargs):
        with SessionLocal() as db:
            job = db.get(models.BackgroundJob, job_id)
            seen.append(bool(job and db.get(models.NovelChapter, job.payload['novelChapterId'])))
        return True
    monkeypatch.setattr(queue, 'dispatch', dispatch)
    assert client.post(f"/api/v1/novels/{project['id']}/continue", headers=headers).status_code == 202
    assert seen == [True]


def test_success_commits_before_late_cancel_and_preserves_result(client, monkeypatch):
    from app.services import job_runner
    headers = login(client, 'novel-success-atomic')
    project = _project(client, headers)
    chapters = _chapters(client, headers, project['id'])
    _accept_in_db(chapters[0]['id'], '历史')
    queued = client.post(f"/api/v1/novels/{project['id']}/continue", headers=headers).json()
    _fake_complete(monkeypatch, '成功正文')
    original = job_runner.queue.chain_next_if_done
    observed = []
    def after_commit(*args, **kwargs):
        observed.append(client.post(f"/api/v1/jobs/{queued['jobId']}/cancel", headers=headers).status_code)
        return original(*args, **kwargs)
    monkeypatch.setattr(job_runner.queue, 'chain_next_if_done', after_commit)
    job_runner.run_job(queued['jobId'])
    job = client.get(f"/api/v1/jobs/{queued['jobId']}", headers=headers).json()
    assert observed == [409] and job['status'] == 'succeeded'
    assert job['result']['outcome'] == 'applied'
    assert any(c['draftText'] == '成功正文' for c in client.get(f"/api/v1/novels/{project['id']}", headers=headers).json()['chapters'])


def test_cancelled_old_worker_cannot_overwrite_retry(client, monkeypatch):
    from app.core.db import SessionLocal
    from app.services import job_runner
    from app import models
    headers = login(client, 'novel-cancel-retry')
    project = _project(client, headers)
    chapters = _chapters(client, headers, project['id'])
    _accept_in_db(chapters[0]['id'], '历史')
    url = f"/api/v1/novels/{project['id']}/continue"
    first = client.post(url, headers=headers).json()
    def complete(db, user_id, prompt, **kwargs):
        assert client.post(f"/api/v1/jobs/{first['jobId']}/cancel", headers=headers).status_code == 200
        retry = client.post(url, headers=headers)
        assert retry.status_code == 202
        assert retry.json()['chapterId'] == first['chapterId']
        return '旧工作结果'
    monkeypatch.setattr(job_runner, '_novel_complete', complete)
    job_runner.run_job(first['jobId'])
    with SessionLocal() as db:
        assert not db.get(models.NovelChapter, first['chapterId']).draft_text
        assert db.get(models.BackgroundJob, first['jobId']).status == 'cancelled'


def test_stale_chapter_never_enters_worker_prompt(client, monkeypatch):
    from app.core.db import SessionLocal
    from app.services import job_runner, novel_continue
    from app import models
    headers = login(client, 'novel-stale-worker')
    project = _project(client, headers)
    chapters = _chapters(client, headers, project['id'])
    _accept_in_db(chapters[0]['id'], '安全正文')
    _accept_in_db(chapters[1]['id'], '失效秘密')
    with SessionLocal() as db:
        db.get(models.NovelChapter, chapters[1]['id']).stale = True
        db.commit()
    with SessionLocal() as db:
        stale = db.get(models.NovelChapter, chapters[1]['id'])
        assert '失效秘密' not in novel_continue.build_continue_prompt([stale], '', [], [], {})
    # An old queued job whose snapshot includes stale history must not use its text as previous.
    with SessionLocal() as db:
        db.get(models.NovelChapter, chapters[1]['id']).stale = False
        db.commit()
    queued = client.post(f"/api/v1/novels/{project['id']}/continue", headers=headers).json()
    with SessionLocal() as db:
        db.get(models.NovelChapter, chapters[1]['id']).stale = True
        job = db.get(models.BackgroundJob, queued['jobId'])
        db.flush()
        snapshot = novel_continue.context_snapshot(db, project['id'], 2)
        job.payload = {**job.payload, 'contextHash': snapshot}
        db.commit()
    prompts = []
    monkeypatch.setattr(job_runner, '_novel_complete', lambda db, user_id, prompt, **kwargs: prompts.append(prompt) or '新文')
    job_runner.run_job(queued['jobId'])
    assert prompts and '失效秘密' not in prompts[0] and '安全正文' in prompts[0]


def test_memory_budget_fallback_and_speech_style():
    from types import SimpleNamespace
    from app.services.novel_continue import build_continue_prompt
    chapters = [SimpleNamespace(id=str(i), sort_order=i, title=f'章{i}', status='accepted', stale=False, facts={}, accepted_text='旧正文' + str(i) + '甲' * 10000) for i in range(3)]
    character = SimpleNamespace(original_name='张三', new_name='', state='present', relations='', speech_style='言简意赅')
    prompt = build_continue_prompt(chapters, chapters[-1].accepted_text, [character], [], {})
    assert '言简意赅' in prompt and '章0' in prompt and '旧正文0' in prompt
    assert len(prompt) < 16001


def test_continue_rejects_stale_history(client):
    from app.core.db import SessionLocal
    from app import models
    headers = login(client, 'novel-stale-memory')
    project = _project(client, headers)
    chapters = _chapters(client, headers, project['id'])
    _accept_in_db(chapters[0]['id'], '已接受')
    with SessionLocal() as db:
        db.get(models.NovelChapter, chapters[0]['id']).stale = True
        db.commit()
    response = client.post(f"/api/v1/novels/{project['id']}/continue", headers=headers)
    assert response.status_code == 409
    assert response.json()['code'] == 'stale_history'


def test_continue_detects_context_change_during_generation(client, monkeypatch):
    from app.core.db import SessionLocal
    from app.services import job_runner
    from app import models
    headers = login(client, 'novel-context-change')
    project = _project(client, headers)
    chapters = _chapters(client, headers, project['id'])
    _accept_in_db(chapters[0]['id'], '已接受')
    queued = client.post(f"/api/v1/novels/{project['id']}/continue", headers=headers).json()
    def complete(db, user_id, prompt, **kwargs):
        with SessionLocal() as other:
            other.get(models.NovelChapter, chapters[0]['id']).accepted_text = '并发修改'
            other.commit()
        return '不可写入'
    monkeypatch.setattr(job_runner, '_novel_complete', complete)
    job_runner.run_job(queued['jobId'])
    with SessionLocal() as db:
        assert db.get(models.BackgroundJob, queued['jobId']).error_code == 'context_changed'
        assert not db.get(models.NovelChapter, queued['chapterId']).draft_text


def test_split_protects_accepted_content_and_running_job(client):
    headers = login(client, 'novel-split-protection')
    project = _project(client, headers)
    chapters = _chapters(client, headers, project['id'])
    _accept_in_db(chapters[0]['id'], '已接受')
    url = f"/api/v1/novels/{project['id']}"
    assert client.post(f'{url}/split', headers=headers).json()['code'] == 'split_would_discard'
    assert client.post(f'{url}/continue', headers=headers).status_code == 202
    assert client.post(f'{url}/split', headers=headers).status_code == 409


def test_legacy_job_without_context_fails_safely(client):
    from app.core.db import SessionLocal
    from app.services import job_runner
    from app import models
    headers = login(client, 'novel-legacy-job')
    project = _project(client, headers)
    chapters = _chapters(client, headers, project['id'])
    _accept_in_db(chapters[0]['id'], '已接受')
    queued = client.post(f"/api/v1/novels/{project['id']}/continue", headers=headers).json()
    with SessionLocal() as db:
        job = db.get(models.BackgroundJob, queued['jobId'])
        job.payload = {k: v for k, v in job.payload.items() if k != 'contextHash'}
        db.commit()
    job_runner.run_job(queued['jobId'])
    with SessionLocal() as db:
        assert db.get(models.BackgroundJob, queued['jobId']).error_code == 'context_changed'
    assert client.post(f"/api/v1/novels/{project['id']}/continue", headers=headers).status_code == 202


def test_edit_accepted_source_marks_later_stale(client):
    headers = login(client, 'novel-source-stale')
    project = _project(client, headers)
    chapters = _chapters(client, headers, project['id'])
    _accept_in_db(chapters[0]['id'], '旧章')
    _accept_in_db(chapters[1]['id'], '后章')
    url = f"/api/v1/novels/{project['id']}"
    response = client.patch(f"{url}/chapters/{chapters[0]['id']}", headers=headers, json={'sourceText': '修改'})
    assert response.status_code == 409 and response.json()['code'] == 'accepted_source_locked'
    assert client.get(url, headers=headers).json()['chapters'][0]['acceptedText'] == '旧章'


def test_reverted_history_blocks_continuation(client):
    from app.core.db import SessionLocal
    from app import models
    headers = login(client, 'novel-reverted-history')
    project = _project(client, headers)
    chapters = _chapters(client, headers, project['id'])
    _accept_in_db(chapters[0]['id'], '旧章')
    _accept_in_db(chapters[1]['id'], '后章')
    with SessionLocal() as db:
        db.get(models.NovelChapter, chapters[0]['id']).status = 'drafted'
        db.commit()
    response = client.post(f"/api/v1/novels/{project['id']}/continue", headers=headers)
    assert response.status_code == 409 and response.json()['code'] == 'stale_history'


def test_failed_continuation_can_retry_without_overwriting_draft(client, monkeypatch):
    from app.core.db import SessionLocal
    from app.services import job_runner
    from app import models
    headers = login(client, 'novel-retry')
    project = _project(client, headers)
    chapters = _chapters(client, headers, project['id'])
    _accept_in_db(chapters[0]['id'], '已接受')
    url = f"/api/v1/novels/{project['id']}/continue"
    first = client.post(url, headers=headers).json()
    _fake_complete(monkeypatch, '')
    job_runner.run_job(first['jobId'])
    retry = client.post(url, headers=headers)
    assert retry.status_code == 202
    assert retry.json()['chapterId'] == first['chapterId']
    _fake_complete(monkeypatch, '新草稿')
    job_runner.run_job(retry.json()['jobId'])
    assert client.post(url, headers=headers).json()['code'] == 'draft_pending'
    with SessionLocal() as db:
        assert db.get(models.NovelChapter, first['chapterId']).draft_text == '新草稿'


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
