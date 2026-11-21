"""成稿台接口与流水线：段落落库、对话改稿、质检、批量修复、撤销、导出。"""

import json

import pytest
from sqlalchemy import select

from tests.conftest import login

MARKDOWN = """# 原标题

首先，随着人工智能时代的到来，我们要理解这个问题的本质，它不仅关乎效率，而且关乎体验。其次，方法要可复制。最后，结论要能落地。总之，这就是完整的思路。

这一段是普通叙述，讲的是我上周在客户现场遇到的具体情况：他们的编辑每天手工复制粘贴三十多篇稿子，错漏很多。

## 小标题

正文最后一段，写点具体的东西，比如成本和时间。
"""


def _client_with_article(client, subject="studio-user"):
    headers = login(client, subject=subject)
    response = client.post(
        "/api/v1/studio/articles/new",
        json={"topic": "测试选题", "material": "素材", "markdown": MARKDOWN, "titles": False},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return headers, response.json()


def test_create_article_splits_blocks_and_lists_state(client):
    headers, article = _client_with_article(client, "studio-create")
    assert article["title"] == "原标题"
    assert [block["kind"] for block in article["blocks"]] == ["para", "para", "heading", "para"]

    state = client.get("/api/v1/studio/state", headers=headers).json()
    assert state["articles"][0]["blocks"] == 4
    assert state["articles"][0]["chars"] > 0
    assert state["settings"]["zhuqueConfigured"] is False


def test_patch_block_splits_paragraphs_and_rejects_stale_edit(client):
    headers, article = _client_with_article(client, "studio-patch")
    block = article["blocks"][0]
    url = f"/api/v1/studio/articles/{article['id']}/blocks/{block['id']}"

    conflict = client.patch(url, json={"text": "改", "expectedText": "不是原文"}, headers=headers)
    assert conflict.status_code == 409

    updated = client.patch(url, json={"text": "第一段。\n\n第二段。", "expectedText": block["text"]}, headers=headers)
    assert updated.status_code == 200, updated.text
    blocks = updated.json()["blocks"]
    assert blocks[0]["text"] == "第一段。"
    assert blocks[1]["text"] == "第二段。"
    assert len(blocks) == 5

    revisions = client.get(f"/api/v1/studio/articles/{article['id']}/revisions", headers=headers).json()
    assert any(row["label"] == "手动改稿" for row in revisions["revisions"])


def test_chat_route_appends_user_message(client, monkeypatch):
    from app.services import studio_pipeline as SP

    headers, article = _client_with_article(client, "studio-chat-route")
    monkeypatch.setattr(SP.PIPELINE, "chat", lambda *args, **kwargs: {})

    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/chat",
        json={"message": "把第一段写得更具体"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["chat"][-1] == {"role": "user", "text": "把第一段写得更具体"}


def test_chat_pipeline_applies_ops_and_undo_restores(client, monkeypatch):
    from app.core.db import SessionLocal
    from app import models
    from app.services import ai_gateway, article_blocks as AB, studio_pipeline as SP, studio_repo

    async def fake_generate(db, **kwargs):
        return {"text": json.dumps({
            "reply": "把第一段写具体了",
            "ops": [{"op": "rewrite", "block": 1, "text": "改写后的第一段，具体到客户现场的操作步骤和耗时。"}],
        }, ensure_ascii=False), "errorCode": None}

    monkeypatch.setattr(ai_gateway, "generate_text", fake_generate)
    _headers, article = _client_with_article(client, "chat-pipeline")

    SP.PIPELINE.chat_now(article["id"], "把第一段写具体")

    with SessionLocal() as db:
        row = db.get(models.StudioArticle, article["id"])
        data = studio_repo.row_to_dict(row)
        assert data["blocks"][0]["text"].startswith("改写后的第一段")
        assert data["chat"][-1]["role"] == "assistant"
        assert data["chat"][-1]["receipts"] == ["改写第 1 段"]

        # 撤销一步回到对话前：最后一个快照就是对话改稿前的状态
        popped = studio_repo.pop_last(db, article["id"])
        assert popped is not None
        AB.restore_payload(data, popped["payload"])
        studio_repo.save(db, row, data)
        db.commit()

    with SessionLocal() as db:
        row = db.get(models.StudioArticle, article["id"])
        assert studio_repo.row_to_dict(row)["blocks"][0]["text"].startswith("首先，随着人工智能")


def test_local_detect_marks_blocks_without_key(client):
    from app.core.db import SessionLocal
    from app.services import studio_pipeline as SP, studio_repo

    headers, article = _client_with_article(client, "studio-detect")
    SP.PIPELINE.detect_now(article["id"], key=None, endpoint=None)
    with SessionLocal() as db:
        from app import models

        row = db.get(models.StudioArticle, article["id"])
        data = studio_repo.row_to_dict(row)
    assert data["zhuque"]["source"] == "local"
    assert data["zhuque"]["round"] == 1
    assert any((block.get("check") or {}).get("status") == "flag" for block in data["blocks"])
    assert "本地规则" in (data["zhuque"].get("note") or "")


def test_fix_all_rewrites_flagged_blocks(client, monkeypatch):
    from app.core.db import SessionLocal
    from app import models
    from app.services import ai_gateway, studio_pipeline as SP, studio_repo

    async def fake_generate(db, **kwargs):
        return {"text": json.dumps({"paragraphs": [{"block": 1, "text": "批量改写后的第一段。"}]},
                                   ensure_ascii=False), "errorCode": None}

    monkeypatch.setattr(ai_gateway, "generate_text", fake_generate)

    headers, article = _client_with_article(client, "studio-fixall")
    with SessionLocal() as db:
        row = db.get(models.StudioArticle, article["id"])
        data = studio_repo.row_to_dict(row)
        data["blocks"][0]["check"] = {"status": "flag", "score": 0.92, "level": "ai", "source": "api", "round": 1}
        data["zhuque"] = {"round": 1, "lastAt": "t"}
        studio_repo.save(db, row, data)
        db.commit()

    SP.PIPELINE.fix_all_now(article["id"])
    with SessionLocal() as db:
        row = db.get(models.StudioArticle, article["id"])
        data = studio_repo.row_to_dict(row)
    block = data["blocks"][0]
    assert block["text"] == "批量改写后的第一段。"
    assert block["prev_text"].startswith("首先，随着人工智能")
    assert block["check"]["status"] == "fixed"
    assert "一键处理完" in data["notice"]["text"]


def test_report_text_marks_blocks_directly(client):
    from app.core.db import SessionLocal
    from app import models
    from app.services import studio_pipeline as SP, studio_repo

    _headers, article = _client_with_article(client, "studio-report")
    paragraph = "这一段是普通叙述，讲的是我上周在客户现场遇到的具体情况：他们的编辑每天手工复制粘贴三十多篇稿子，错漏很多。"
    report = f"NO.1 片段 1 AIGC值 0.93\n{paragraph}\n朱雀AI生成检测报告单\n"

    SP.PIPELINE.report_now(article["id"], report)
    with SessionLocal() as db:
        row = db.get(models.StudioArticle, article["id"])
        data = studio_repo.row_to_dict(row)
    checks = [(block.get("check") or {}).get("status") for block in data["blocks"]]
    assert "flag" in checks
    assert data["zhuque"]["source"] == "pdf"
    assert data["zhuque"]["flagged"] >= 1


def test_export_creates_content_version(client):
    headers, article = _client_with_article(client, "studio-export")
    response = client.post(f"/api/v1/studio/articles/{article['id']}/export", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["versionId"]

    detail = client.get(f"/api/v1/studio/articles/{article['id']}", headers=headers).json()
    assert detail["job"] is None
    state = client.get("/api/v1/studio/state", headers=headers).json()
    assert state["articles"][0]["title"] == "原标题"


def test_schedule_rejects_past_and_accepts_future(client):
    from datetime import datetime, timedelta, timezone

    headers, article = _client_with_article(client, "studio-schedule")
    url = f"/api/v1/studio/articles/{article['id']}/schedule"
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert client.post(url, json={"at": past}, headers=headers).status_code == 400
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    ok = client.post(url, json={"at": future}, headers=headers)
    assert ok.status_code == 200
    assert ok.json()["scheduledFor"]


def select_all_articles(subject, db):
    """按测试账号取最近一篇稿件。"""
    from app import models

    return (
        select(models.StudioArticle)
        .join(models.User, models.User.id == models.StudioArticle.owner_user_id)
        .where(models.User.subject == subject)
        .order_by(models.StudioArticle.created_at.desc())
        .limit(1)
    )
