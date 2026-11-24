"""成稿台发布与复盘：导出落稿、按平台建任务、排期、复盘聚合。

约定同其它用例：app 相关模块在函数内延迟导入，避免收集阶段绑定开发库。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from tests.conftest import login

MARKDOWN = """# 发布测试标题

第一段讲的是具体场景：编辑每天手工搬稿，错漏很多。

第二段讲方法，给出可复制的步骤。

## 小标题

最后一段收尾，讲成本和时间。
"""


def _article(client, subject: str = "studio-publish-user") -> tuple[dict, dict]:
    headers = login(client, subject=subject)
    response = client.post(
        "/api/v1/studio/articles/new",
        json={"topic": "发布选题", "material": "素材", "markdown": MARKDOWN, "titles": False},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return headers, response.json()


def _bind_account(
    client,
    headers: dict,
    *,
    status: str = "connected",
    auth_source: str = "app_secret",
) -> str:
    """直接落一条平台账号：发布任务需要 accountId，账号授权流程不属于本用例范围。"""
    from app import models
    from app.core.db import SessionLocal

    user_id = client.get("/api/v1/auth/me", headers=headers).json()["id"]
    with SessionLocal() as db:
        workspace = db.scalar(
            select(models.Workspace).where(models.Workspace.owner_user_id == user_id)
        )
        if workspace is None:
            workspace = models.Workspace(owner_user_id=user_id, name="测试工作区", settings={})
            db.add(workspace)
            db.flush()
        account = models.ConnectedAccount(
            owner_user_id=user_id,
            workspace_id=workspace.id,
            platform="wechat_official",
            display_name="测试公众号",
            status=status,
            auth_source=auth_source,
        )
        db.add(account)
        db.commit()
        return account.id


def _jobs(content_item_id: str) -> list:
    from app import models
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        return list(
            db.scalars(
                select(models.PublishJob).where(
                    models.PublishJob.content_item_id == content_item_id
                )
            ).all()
        )


def test_dry_run_only_exports_and_enqueues_nothing(client):
    headers, article = _article(client, "studio-publish-dry")

    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/publish",
        json={"dryRun": True},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["publishJobIds"] == []
    assert body["contentItemId"]
    assert body["scheduledFor"] is None
    assert _jobs(body["contentItemId"]) == []


def _archive(client, headers, article):
    response = client.post(f"/api/v1/studio/articles/{article['id']}/export", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_push_uses_archived_version_and_queues_one_real_draft(client, monkeypatch):
    from app import models
    from app.connectors import registry, wechat_mp
    from app.core import queue
    from app.core.db import SessionLocal
    from app.services import job_runner
    from tests.test_wechat_mp import _upload_asset

    headers, article = _article(client, "studio-publish-ok")
    account_id = _bind_account(client, headers)
    cover = _upload_asset(client, headers, "cover.png")
    inline = _upload_asset(client, headers, "inline.png")
    base = f"/api/v1/studio/articles/{article['id']}"
    assert client.post(f"{base}/cover/pick", json={"assetId": cover}, headers=headers).status_code == 200
    assert client.post(
        f"{base}/inline/pick", json={"assetId": inline, "blockId": article['blocks'][0]['id']}, headers=headers
    ).status_code == 200
    archive = _archive(client, headers, article)
    assert client.post(f"{base}/cover/pick", json={"assetId": None}, headers=headers).status_code == 200
    edited = client.get(f"{base}/markdown", headers=headers).json()["markdown"]
    assert client.patch(f"{base}/markdown", json={"expectedMarkdown": edited, "markdown": edited + "\n存档后新写的段落\n"}, headers=headers).status_code == 200
    before = client.get(base, headers=headers).json()

    monkeypatch.setenv("WECHAT_MP_APPID", "wxTESTSTUDIO0001")
    monkeypatch.setenv("WECHAT_MP_APPSECRET", "test-studio-secret")
    registry._CACHE.clear()
    dispatched = []
    monkeypatch.setattr(queue, "dispatch", lambda job_id: dispatched.append(job_id))
    payload = {"platforms": ["wechat-mp"], "accountId": account_id}
    response = client.post(f"{base}/publish", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["contentItemId"] == archive["contentId"]
    assert body["versionId"] == archive["versionId"]
    job, = _jobs(body["contentItemId"])
    assert job.id == body["publishJobIds"][0]
    assert (job.platform, job.target, job.status) == ("wechat_official", "draft", "queued")
    assert job.payload_snapshot["assetIds"] == [cover, inline]
    assert "{{image1}}" in job.payload_snapshot["body"]
    assert "存档后新写的段落" not in job.payload_snapshot["body"]
    again = client.post(f"{base}/publish", json=payload, headers=headers)
    assert again.json()["publishJobIds"] == body["publishJobIds"]
    background_id, = dispatched
    with SessionLocal() as db:
        background = db.get(models.BackgroundJob, background_id)
        assert background.kind == "wechat_draft"
        assert background.payload == {"publishJobId": job.id, "accountId": account_id, "target": "draft"}

    monkeypatch.setattr(wechat_mp.WeChatMPConnector, "upload_thumb", lambda *a, **kw: {"mediaId": "cover-media"})
    name = "https://mmbiz.qpic.cn/demo.png"
    monkeypatch.setattr(wechat_mp.WeChatMPConnector, "upload_inline_image", lambda *a, **kw: name)

    def fail_draft(_connector, _db, *, article):
        raise wechat_mp.WeChatMPError("wechat_45009", "微信请求超过限额")

    monkeypatch.setattr(wechat_mp.WeChatMPConnector, "add_draft", fail_draft)
    job_runner.run_job(background_id)
    failed = client.get(f"{base}/review", headers=headers).json()["platforms"][0]
    assert failed["status"] == "failed"
    assert failed["error"] == "微信请求超过限额"
    assert failed["canRetry"] is True

    retry = f"{base}/publish/{job.id}/retry"
    assert client.post(retry, headers=headers).status_code == 200
    assert client.post(retry, headers=headers).status_code == 409
    assert len(dispatched) == 2

    def save_draft(_connector, _db, *, article):
        assert article["thumb_media_id"] == "cover-media"
        assert article["content"].count(name) == 1
        assert "存档后新写的段落" not in article["content"]
        return "remote-draft"

    monkeypatch.setattr(wechat_mp.WeChatMPConnector, "add_draft", save_draft)
    job_runner.run_job(dispatched[-1])
    result = client.get(f"{base}/review", headers=headers).json()
    row, = result["platforms"]
    assert (row["status"], row["draftMediaId"], row["error"]) == ("drafted", "remote-draft", None)
    assert row["url"] is None
    assert result["publishedAt"] is None
    assert client.get(base, headers=headers).json() == before
    registry._CACHE.clear()


def test_push_requires_an_archived_version(client):
    headers, article = _article(client, "studio-publish-unarchived")
    _bind_account(client, headers)
    response = client.post(f"/api/v1/studio/articles/{article['id']}/publish", json={}, headers=headers)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "archive_required"


def test_publish_rejects_unknown_platform(client):
    headers, article = _article(client, "studio-publish-bad-platform")

    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/publish",
        json={"platforms": ["douyin"]},
        headers=headers,
    )
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["code"] == "unknown_platform"
    assert "wechat_official" in body["message"]


def test_publish_rejects_past_schedule(client):
    headers, article = _article(client, "studio-publish-past")
    _bind_account(client, headers)

    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/publish",
        json={"platforms": ["wechat_official"], "scheduledAt": "2000-01-01T00:00:00Z"},
        headers=headers,
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "scheduled_at_past"


def test_publish_without_account_returns_readable_conflict(client):
    headers, article = _article(client, "studio-publish-no-account")

    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/publish",
        json={"platforms": ["wechat_official"]},
        headers=headers,
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "account_not_bound"
    assert "绑定" in body["message"]


def test_review_without_records_is_empty(client):
    headers, article = _article(client, "studio-review-empty")

    response = client.get(f"/api/v1/studio/articles/{article['id']}/review", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["articleId"] == article["id"]
    assert body["contentItemId"] is None
    assert body["platforms"] == []
    assert body["publishedAt"] is None
    assert body["stats"]["blocks"] == len(article["blocks"])
    assert body["stats"]["chars"] > 0
    assert body["stats"]["flagged"] == 0
    assert body["stats"]["checked"] is False


def test_schedule_is_calendar_only(client):
    headers, article = _article(client, "studio-review-scheduled")
    base = f"/api/v1/studio/articles/{article['id']}"
    scheduled = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(timespec="seconds")
    response = client.post(f"{base}/schedule", json={"at": scheduled}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["scheduledFor"].startswith(scheduled[:19])
    assert client.get(f"{base}/review", headers=headers).json()["platforms"] == []
    assert client.post(f"{base}/schedule", json={"at": None}, headers=headers).json()["scheduledFor"] is None
    rejected = client.post(f"{base}/publish", json={"scheduledAt": scheduled}, headers=headers)
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "account_not_bound"


def test_review_reports_legacy_manual_jobs_and_real_published_links(client):
    from app import models
    from app.core.db import SessionLocal

    headers, article = _article(client, "studio-review-legacy")
    account_id = _bind_account(client, headers)
    archive = _archive(client, headers, article)
    with SessionLocal() as db:
        account = db.get(models.ConnectedAccount, account_id)
        job = models.PublishJob(
            owner_user_id=account.owner_user_id,
            workspace_id=account.workspace_id,
            content_item_id=archive["contentId"],
            connected_account_id=account_id,
            platform="wechat_official",
            status="approved",
            target="publish",
            idempotency_key="legacy-studio-publish-test",
            payload_snapshot={"studioArticleId": article["id"], "mode": "manual"},
        )
        db.add(job)
        db.commit()
        job_id = job.id

    base = f"/api/v1/studio/articles/{article['id']}"
    row, = client.get(f"{base}/review", headers=headers).json()["platforms"]
    assert row["status"] == "manual"
    assert row["canRetry"] is False
    assert client.post(f"{base}/publish/{job_id}/retry", headers=headers).status_code == 404

    with SessionLocal() as db:
        job = db.get(models.PublishJob, job_id)
        job.status = "succeeded"
        job.remote_content_id = "remote-1"
        db.add(
            models.PublishRecord(
                publish_job_id=job.id,
                platform=job.platform,
                remote_content_id="remote-1",
                published_at=datetime.now(timezone.utc),
                permalink="https://mp.weixin.qq.com/s/demo",
                result_status="success",
            )
        )
        db.commit()

    review = client.get(f"/api/v1/studio/articles/{article['id']}/review", headers=headers)
    assert review.status_code == 200, review.text
    data = review.json()
    platform = data["platforms"][0]
    assert platform["status"] == "succeeded"
    assert platform["url"] == "https://mp.weixin.qq.com/s/demo"
    assert platform["error"] is None
    assert platform["at"] is not None
    assert data["publishedAt"] is not None


def test_review_of_other_users_article_is_not_found(client):
    _owner, article = _article(client, "studio-publish-owner")
    intruder = login(client, subject="studio-publish-intruder")

    response = client.get(f"/api/v1/studio/articles/{article['id']}/review", headers=intruder)
    assert response.status_code == 404

    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/publish",
        json={"dryRun": True},
        headers=intruder,
    )
    assert response.status_code == 404
