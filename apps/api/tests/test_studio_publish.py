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


def test_publish_creates_job_and_links_content_item(client):
    headers, article = _article(client, "studio-publish-ok")
    _bind_account(client, headers)

    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/publish",
        json={"platforms": ["wechat_official"]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["publishJobIds"]) == 1

    jobs = _jobs(body["contentItemId"])
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == body["publishJobIds"][0]
    assert job.platform == "wechat_official"
    assert job.status == "approved"
    assert (job.payload_snapshot or {}).get("mode") == "manual"

    detail = client.get(f"/api/v1/studio/articles/{article['id']}", headers=headers).json()
    assert detail["publishMarkedAt"] is not None

    # 同一份版本在防抖窗口内重复点「立即发布」不重复建任务
    again = client.post(
        f"/api/v1/studio/articles/{article['id']}/publish",
        json={"platforms": ["wechat_official"]},
        headers=headers,
    )
    assert again.status_code == 200, again.text
    assert again.json()["publishJobIds"] == body["publishJobIds"]


def test_publish_accepts_contract_platform_alias(client):
    headers, article = _article(client, "studio-publish-alias")
    _bind_account(client, headers)

    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/publish",
        json={"platforms": ["wechat-mp"]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["publishJobIds"]) == 1


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


def test_review_reflects_queued_scheduled_job(client):
    headers, article = _article(client, "studio-review-scheduled")
    _bind_account(client, headers)
    at = datetime.now(timezone.utc) + timedelta(days=2)
    scheduled = at.isoformat(timespec="seconds")

    published = client.post(
        f"/api/v1/studio/articles/{article['id']}/publish",
        json={"platforms": ["wechat_official"], "scheduledAt": scheduled},
        headers=headers,
    )
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["scheduledFor"] is not None
    jobs = _jobs(body["contentItemId"])
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    assert jobs[0].scheduled_at is not None

    review = client.get(f"/api/v1/studio/articles/{article['id']}/review", headers=headers)
    assert review.status_code == 200, review.text
    data = review.json()
    assert data["contentItemId"] == body["contentItemId"]
    assert len(data["platforms"]) == 1
    platform = data["platforms"][0]
    assert platform["platform"] == "wechat_official"
    assert platform["status"] == "queued"
    assert platform["error"] is None
    assert platform["at"].startswith(scheduled[:10])


def test_review_reports_succeeded_job_with_permalink(client):
    headers, article = _article(client, "studio-review-succeeded")
    _bind_account(client, headers)
    published = client.post(
        f"/api/v1/studio/articles/{article['id']}/publish",
        json={"platforms": ["wechat_official"]},
        headers=headers,
    )
    assert published.status_code == 200, published.text
    job_id = published.json()["publishJobIds"][0]

    from app import models
    from app.core.db import SessionLocal

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
