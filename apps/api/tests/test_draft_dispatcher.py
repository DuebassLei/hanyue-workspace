"""No external calls: durable draft schedules, competing consumers and cancellation."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from tests.test_studio_publish import _article, _archive, _bind_account


def _scheduled(client, monkeypatch, subject="draft-schedule"):
    from app.core import queue
    from app.connectors import registry
    from tests.test_wechat_mp import _upload_asset
    headers, article = _article(client, subject)
    account = _bind_account(client, headers)
    cover = _upload_asset(client, headers, "cover.png")
    base = f"/api/v1/studio/articles/{article['id']}"
    assert client.post(f"{base}/cover/pick", json={"assetId": cover}, headers=headers).status_code == 200
    _archive(client, headers, article)
    monkeypatch.setenv("WECHAT_MP_APPID", "wxTESTSCHEDULE0001")
    monkeypatch.setenv("WECHAT_MP_APPSECRET", "test-secret")
    registry._CACHE.clear()
    dispatched = []
    monkeypatch.setattr(queue, "dispatch", lambda job_id, **kw: dispatched.append(job_id))
    at = datetime.now(timezone.utc) + timedelta(hours=1)
    result = client.post(f"{base}/publish", json={"accountId": account, "scheduledAt": at.isoformat()}, headers=headers)
    assert result.status_code == 200, result.text
    return headers, base, result.json()["publishJobIds"][0], at, dispatched


def _backgrounds_for(job_id: str) -> int:
    """只数本任务的执行记录：全库共用测试库，别的用例也会留下 wechat_draft。"""
    from app import models
    from app.core.db import SessionLocal
    with SessionLocal() as db:
        return len([
            ident for ident in db.scalars(
                select(models.BackgroundJob.id).where(models.BackgroundJob.kind == "wechat_draft")
            ).all()
            if (db.get(models.BackgroundJob, ident).payload or {}).get("publishJobId") == job_id
        ])


def test_due_scan_is_durable_idempotent_and_not_read_triggered(client, monkeypatch):
    from app import models
    from app.core.db import SessionLocal
    from app.services import draft_dispatcher
    headers, base, job_id, at, dispatched = _scheduled(client, monkeypatch)
    assert dispatched == []
    draft_dispatcher.scan_due(now=at-timedelta(seconds=1))
    with SessionLocal() as db:
        assert db.get(models.PublishJob, job_id).status == "queued"
        assert _backgrounds_for(job_id) == 0
        # Simulate time passing while every browser remains closed.
        db.get(models.PublishJob, job_id).scheduled_at = datetime.now(timezone.utc)-timedelta(seconds=1)
        db.commit()
    draft_dispatcher.scan_due()
    draft_dispatcher.scan_due()
    with SessionLocal() as db:
        assert _backgrounds_for(job_id) == 1
        assert db.get(models.PublishJob, job_id).status == "approved"
    detail = client.get(f"/api/v1/publishing/jobs/{job_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["accountName"] == "测试公众号"
    assert detail.json()["scheduledAt"]


def test_cancelled_schedule_cannot_be_claimed(client, monkeypatch):
    from app import models
    from app.core.db import SessionLocal
    from app.services import draft_dispatcher
    headers, _, job_id, at, dispatched = _scheduled(client, monkeypatch, "draft-cancel")
    response = client.post(f"/api/v1/publishing/jobs/{job_id}/cancel", headers=headers)
    assert response.status_code == 200, response.text
    draft_dispatcher.scan_due(now=at+timedelta(seconds=1))
    with SessionLocal() as db:
        assert db.get(models.PublishJob, job_id).status == "cancelled"
        assert _backgrounds_for(job_id) == 0
        assert all((db.get(models.BackgroundJob, ident).payload or {}).get("publishJobId") != job_id for ident in dispatched)


def test_execution_cas_and_ambiguous_result_disable_retry(client, monkeypatch):
    from app import models
    from app.core.db import SessionLocal
    from app.connectors import wechat_mp
    from app.services import draft_dispatcher, job_runner
    headers, base, job_id, at, dispatched = _scheduled(client, monkeypatch, "draft-execute")
    with SessionLocal() as db:
        db.get(models.PublishJob, job_id).scheduled_at = datetime.now(timezone.utc)-timedelta(seconds=1)
        db.commit()
    draft_dispatcher.scan_due()
    calls = []
    monkeypatch.setattr(wechat_mp.WeChatMPConnector, "upload_thumb", lambda *a, **kw: {"mediaId": "thumb"})
    def unknown(*a, **kw):
        calls.append(1)
        raise wechat_mp.WeChatMPError("network_error", "lost response", retryable=True)
    monkeypatch.setattr(wechat_mp.WeChatMPConnector, "add_draft", unknown)
    with SessionLocal() as db:
        background_id = next(ident for ident in dispatched if (db.get(models.BackgroundJob, ident).payload or {}).get("publishJobId") == job_id)
    with SessionLocal() as db:
        from app.modules.wechat_mp import _create_draft_background_job
        duplicate_id = _create_draft_background_job(db, db.get(models.PublishJob, job_id))
        db.commit()
    job_runner.run_job(background_id)
    job_runner.run_job(background_id)
    job_runner.run_job(duplicate_id)
    assert len(calls) == 1
    with SessionLocal() as db:
        job = db.get(models.PublishJob, job_id)
        assert job.status == "needs_attention"
        assert job.error_code == "draft_result_unknown"
    detail = client.get(f"/api/v1/publishing/jobs/{job_id}", headers=headers).json()
    assert detail["startedAt"] and detail["completedAt"]
    assert detail["canRetry"] is False
    assert client.post(f"{base}/publish/{job_id}/retry", headers=headers).status_code == 409


def test_worker_never_executes_before_scheduled_time(client, monkeypatch):
    from app import models
    from app.core.db import SessionLocal
    from app.modules.wechat_mp import _create_draft_background_job
    from app.services import job_runner
    _, _, job_id, _, _ = _scheduled(client, monkeypatch, "draft-early")
    with SessionLocal() as db:
        background = _create_draft_background_job(db, db.get(models.PublishJob, job_id))
        db.commit()
    job_runner.run_job(background)
    with SessionLocal() as db:
        job = db.get(models.PublishJob, job_id)
        assert job.status == "queued"
        assert job.remote_content_id is None


def test_running_draft_cannot_be_cancelled(client, monkeypatch):
    from app import models
    from app.core.db import SessionLocal
    headers, _, job_id, _, _ = _scheduled(client, monkeypatch, "draft-running-cancel")
    with SessionLocal() as db:
        db.get(models.PublishJob, job_id).status = "publishing"
        db.commit()
    result = client.post(f"/api/v1/publishing/jobs/{job_id}/cancel", headers=headers)
    assert result.status_code == 409


def test_snapshot_is_immutable_and_cancel_can_create_a_fresh_schedule(client, monkeypatch):
    from app import models
    from app.core.db import SessionLocal
    headers, base, job_id, at, _ = _scheduled(client, monkeypatch, "draft-recreate")
    patch = client.patch(f"/api/v1/publishing/jobs/{job_id}", json={"body": "changed"}, headers=headers)
    assert patch.status_code == 409
    assert client.post(f"/api/v1/publishing/jobs/{job_id}/cancel", headers=headers).status_code == 200
    with SessionLocal() as db:
        account_id = db.get(models.PublishJob, job_id).connected_account_id
    result = client.post(f"{base}/publish", json={"accountId": account_id, "scheduledAt": (at+timedelta(hours=1)).isoformat()}, headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["publishJobIds"] != [job_id]
    with SessionLocal() as db:
        assert db.get(models.PublishJob, job_id).status == "cancelled"


def test_scheduled_success_reports_plan_account_and_real_push_time(client, monkeypatch):
    from app import models
    from app.core.db import SessionLocal
    from app.connectors import wechat_mp
    from app.services import draft_dispatcher, job_runner
    headers, base, job_id, at, dispatched = _scheduled(client, monkeypatch, "draft-success")
    with SessionLocal() as db:
        db.get(models.PublishJob, job_id).scheduled_at = datetime.now(timezone.utc)-timedelta(seconds=1)
        db.commit()
    draft_dispatcher.scan_due()
    monkeypatch.setattr(wechat_mp.WeChatMPConnector, "upload_thumb", lambda *a, **kw: {"mediaId": "thumb"})
    monkeypatch.setattr(wechat_mp.WeChatMPConnector, "add_draft", lambda *a, **kw: "remote-draft")
    # scan_due 会顺带重投所有遗留的 queued 后台任务（恢复语义），只挑本次任务
    with SessionLocal() as db:
        background_id = next(
            ident for ident in dispatched
            if (db.get(models.BackgroundJob, ident).payload or {}).get("publishJobId") == job_id
        )
    job_runner.run_job(background_id)

    review = client.get(f"{base}/review", headers=headers).json()
    row, = review["platforms"]
    assert row["status"] == "drafted"
    assert row["draftMediaId"] == "remote-draft"
    assert row["accountLabel"] == "测试公众号"
    assert row["scheduledAt"], "未推送前也要能显示计划推送时间"
    assert row["pushedAt"], "成功推送必须记录真实推送时间"
    assert row["error"] is None
    assert review["publishedAt"] is None, "进草稿箱不等于正式发布"

    # 详情页同样给出账号、计划时间与真实推送时间
    detail = client.get(f"/api/v1/publishing/jobs/{job_id}", headers=headers).json()
    assert detail["accountName"] == "测试公众号"
    assert detail["scheduledAt"] and detail["pushedAt"]
    assert detail["canRetry"] is False


def test_review_shows_cancelled_and_permits_a_fresh_schedule(client, monkeypatch):
    from app import models
    from app.core.db import SessionLocal
    headers, base, job_id, at, _ = _scheduled(client, monkeypatch, "draft-review-cancel")
    assert client.post(f"/api/v1/publishing/jobs/{job_id}/cancel", headers=headers).status_code == 200
    row, = client.get(f"{base}/review", headers=headers).json()["platforms"]
    assert row["status"] == "cancelled"
    assert row["scheduledAt"] and row["canRetry"] is False
    with SessionLocal() as db:
        account_id = db.get(models.PublishJob, job_id).connected_account_id
    again = client.post(f"{base}/publish", json={"accountId": account_id, "scheduledAt": (at+timedelta(hours=1)).isoformat()}, headers=headers)
    assert again.status_code == 200, again.text
    assert again.json()["publishJobIds"] != [job_id]


def test_legacy_manual_and_approve_endpoints_reject_draft_jobs(client, monkeypatch):
    headers, base, job_id, _, _ = _scheduled(client, monkeypatch, "draft-legacy")
    manual = client.post(
        f"/api/v1/publishing/jobs/{job_id}/record-manual-result",
        json={"remoteContentId": "fake", "permalink": "https://example.com"},
        headers=headers,
    )
    assert manual.status_code == 409
    assert manual.json()["code"] == "manual_record_unsupported"
    approved = client.post(f"/api/v1/publishing/jobs/{job_id}/approve", headers=headers)
    assert approved.status_code == 409
    from app import models
    from app.core.db import SessionLocal
    with SessionLocal() as db:
        job = db.get(models.PublishJob, job_id)
        assert job.status == "queued"
        assert job.remote_content_id is None


def test_crashed_draft_is_not_automatically_replayed(client, monkeypatch):
    from app import models
    from app.core import queue
    from app.core.db import SessionLocal
    from app.modules.wechat_mp import _create_draft_background_job
    headers, _, job_id, _, _ = _scheduled(client, monkeypatch, "draft-crashed")
    with SessionLocal() as db:
        job = db.get(models.PublishJob, job_id)
        job.status = "publishing"
        ident = _create_draft_background_job(db, job)
        background = db.get(models.BackgroundJob, ident)
        background.status = "running"
        background.started_at = datetime.now(timezone.utc)-timedelta(days=1)
        db.commit()
    queue.recover_stale_running(lease_seconds=1)
    with SessionLocal() as db:
        assert db.get(models.BackgroundJob, ident).status == "failed"
        job = db.get(models.PublishJob, job_id)
        assert job.status == "needs_attention"
        assert job.error_code == "draft_result_unknown"
    assert client.get(f"/api/v1/publishing/jobs/{job_id}", headers=headers).json()["canRetry"] is False
