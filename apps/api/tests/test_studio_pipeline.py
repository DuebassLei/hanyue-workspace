"""成稿台接口与流水线：段落落库、对话改稿、质检、批量修复、撤销、导出。"""

import json

import httpx
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


def test_patch_block_deletes_when_text_is_empty(client):
    headers, article = _client_with_article(client, "studio-delete-block")
    assert len(article["blocks"]) == 4
    block = article["blocks"][0]
    removed_id = block["id"]

    response = client.patch(
        f"/api/v1/studio/articles/{article['id']}/blocks/{removed_id}",
        json={"text": "   ", "expectedText": block["text"]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    blocks = response.json()["blocks"]
    assert len(blocks) == 3
    assert removed_id not in [item["id"] for item in blocks]

    revisions = client.get(f"/api/v1/studio/articles/{article['id']}/revisions", headers=headers).json()
    assert any(row["label"] == "手动改稿" for row in revisions["revisions"])


def test_patch_block_rejects_while_job_running(client, monkeypatch):
    from app.services import studio_pipeline as SP

    headers, article = _client_with_article(client, "studio-busy-block")
    block = article["blocks"][0]
    # 模拟这篇文章已有任务在跑：后端 _busy() 必须先于乐观锁检查拒绝编辑
    monkeypatch.setattr(SP.JOBS, "busy", lambda _article_id: True)

    response = client.patch(
        f"/api/v1/studio/articles/{article['id']}/blocks/{block['id']}",
        json={"text": "任务结束后再保存", "expectedText": block["text"]},
        headers=headers,
    )
    assert response.status_code == 409, response.text
    body = response.json()
    # 后端 ApiError 直接把 detail 对象作为响应体（不再是 {"detail": {...}}），两种形态都兼容
    detail = body.get("detail") if isinstance(body.get("detail"), dict) else body
    assert "任务在跑" in (detail.get("message") or "")


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
    dimensions = data["zhuque"]["dimensions"]
    assert all(set(axis) == {"id", "label", "score"} and axis["label"] for axis in dimensions)
    assert all(0 <= axis["score"] <= 100 for axis in dimensions)
    scores = {axis["id"]: axis["score"] for axis in dimensions}
    assert list(scores) == ["wording", "structure", "sentence_rhythm", "transitions", "specificity", "redundancy"]
    assert scores["transitions"] < scores["redundancy"] == 100
    assert data["zhuque"]["overall"] is None
    assert any((block.get("check") or {}).get("hits") for block in data["blocks"])


@pytest.mark.parametrize(("text", "axis"), [
    ("这个方案赋能业务、构建生态闭环，还要沉淀最佳实践。", "wording"),
    ("我们把设备搬到二楼。我们把电源接进机柜。我们把线缆藏到桌下。", "structure"),
    ("设备重启了。" + "我沿着楼梯检查每个机柜的接线" * 7 + "！", "sentence_rhythm"),
    ("值得注意的是，灯亮了。更重要的是，楼下停电了。", "transitions"),
    ("这件事本身具有重要意义。它一直发挥着重要作用。", "specificity"),
    ("下午三点我把损坏的电表换了。下午三点我把损坏的电表换了。", "redundancy"),
])
def test_local_quality_penalizes_each_dimension(text, axis):
    from app.services.article_quality import local_check

    result = local_check({"blocks": [{"text": text}]})
    scores = {dimension["id"]: dimension["score"] for dimension in result["dimensions"]}
    assert scores[axis] < 100
    assert all(0 <= score <= 100 for score in scores.values())


def test_local_quality_passes_ordinary_lists_and_single_contrast():
    from app.services.article_quality import local_check

    result = local_check({"blocks": [
        {"text": "- 核对三张发票的金额\n- 检查二楼机房的温度\n- 把两台机器搬到库房"},
        {"text": "首先，先检查左侧的电源。问题不是设备坏了，而是插头没插牢。"},
    ]})
    assert not result["segments"]


def test_local_quality_duplicate_detection_stays_inside_scope():
    from app.services.article_quality import local_check

    sentence = "下午三点我把损坏的电表换了。"
    article = {"blocks": [{"text": sentence + "旧表已经交给库管。"}, {"text": sentence + "新表读数为零。"}]}
    full = local_check(article)
    scoped = local_check(article, {1})
    assert full["dimensions"][-1]["score"] < scoped["dimensions"][-1]["score"] == 100
    assert [row["block"] for row in full["segments"]] == [2]
    assert scoped["segments"] == []


def test_detect_falls_back_when_zhuque_times_out(client, monkeypatch):
    from app.services import article_quality as AQ, studio_pipeline as SP

    headers, article = _client_with_article(client, "studio-detect-timeout")
    SP.PIPELINE.detect_now(article["id"], key=None, endpoint=None)

    async def fake_detect(*args, **kwargs):
        raise AQ.QualityError("private provider diagnostics")

    monkeypatch.setattr(AQ, "detect", fake_detect)
    SP.PIPELINE.detect_now(article["id"], key="test-key", endpoint=None)

    response = client.get(f"/api/v1/studio/articles/{article['id']}", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["zhuque"]["source"] == "local"
    assert data["zhuque"]["round"] == 2
    assert "本地规则" in data["zhuque"]["note"]
    assert "本地规则" in data["notice"]["text"]
    assert not data["notice"].get("error")
    assert "private provider diagnostics" not in response.text
    assert data["blocks"][0]["check"]["round"] == 2


@pytest.mark.parametrize("reply", ["error", "empty", "unmatched", "malformed", "zero"])
def test_detect_uses_only_usable_zhuque_results(client, monkeypatch, reply):
    from app.services import article_quality as AQ, studio_pipeline as SP

    headers, article = _client_with_article(client, "studio-detect-result")
    SP.PIPELINE.detect_now(article["id"], key=None, endpoint=None)
    paragraph = article["blocks"][0]["text"]
    rows = {
        "empty": [],
        "unmatched": [{"text": "与文章无关的片段", "label": 0, "conf": 0}],
        "malformed": ["not a segment", {"text": paragraph, "order": "bad", "label": 7, "conf": 0},
                      {"text": paragraph, "label": 1, "conf": "NaN"}],
        "zero": [{"text": paragraph, "label": 0, "conf": 0}],
    }

    def handler(request):
        if reply == "error":
            return httpx.Response(429, json={"msg": "private provider diagnostics"})
        return httpx.Response(200, json={
            "status": "success",
            "labels_ratio": {"0": 1, "1": 0, "2": 0},
            "segment_labels": rows[reply],
        })

    real_client = httpx.AsyncClient
    monkeypatch.setattr(AQ, "pin_url", lambda url: (url, {}, {}))
    monkeypatch.setattr(AQ.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    SP.PIPELINE.detect_now(article["id"], key="test-key", endpoint=None)

    response = client.get(f"/api/v1/studio/articles/{article['id']}", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["zhuque"]["round"] == 2
    if reply == "zero":
        assert data["zhuque"]["source"] == "api"
        assert data["zhuque"]["overall"] == {"human": 100, "suspect": 0, "ai": 0}
        assert data["blocks"][0]["check"]["status"] == "pass"
        assert data["blocks"][1]["check"] is None
        assert "dimensions" not in data["zhuque"]
    else:
        assert data["zhuque"]["source"] == "local"
        assert data["blocks"][0]["check"]["status"] == "flag"
        assert "本地规则" in data["notice"]["text"]
    assert "private provider diagnostics" not in response.text


def test_detect_scope_only_rechecks_selected_blocks(client):
    from app.core.db import SessionLocal
    from app import models
    from app.services import studio_pipeline as SP, studio_repo

    headers, article = _client_with_article(client, "studio-detect-scope")
    SP.PIPELINE.detect_now(article["id"], key=None, endpoint=None)
    with SessionLocal() as db:
        data = studio_repo.row_to_dict(db.get(models.StudioArticle, article["id"]))
    assert data["zhuque"]["round"] == 1
    before = {block["id"]: block.get("check") for block in data["blocks"]}

    # 只重检第一段；其余可检段落应保留上一轮结论，文章级计数仍按整篇重算
    SP.PIPELINE.detect_now(article["id"], key=None, endpoint=None, block_ids=[article["blocks"][0]["id"]], source_pref="local")
    with SessionLocal() as db:
        data = studio_repo.row_to_dict(db.get(models.StudioArticle, article["id"]))

    assert data["zhuque"]["round"] == 2
    assert data["zhuque"]["source"] == "local"
    assert "仅检测 1 段" in (data["zhuque"].get("note") or "")
    assert data["blocks"][0]["check"]["round"] == 2
    assert data["blocks"][1]["check"] == before[article["blocks"][1]["id"]]
    assert data["blocks"][3]["check"] == before[article["blocks"][3]["id"]]
    assert data["blocks"][1]["check"]["round"] == 1
    assert data["zhuque"]["flagged"] + data["zhuque"]["passed"] == sum(
        1 for b in data["blocks"] if (b.get("check") or {}).get("status") in ("flag", "pass")
    )


def test_detect_source_local_skips_zhuque_even_with_key(client, monkeypatch):
    from app.services import article_quality as AQ, studio_pipeline as SP

    headers, article = _client_with_article(client, "studio-detect-force-local")
    called = {"n": 0}

    async def fake_detect(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("朱雀不应被调用")

    monkeypatch.setattr(AQ, "detect", fake_detect)
    SP.PIPELINE.detect_now(article["id"], key="test-key", endpoint=None, source_pref="local")

    data = client.get(f"/api/v1/studio/articles/{article['id']}", headers=headers).json()
    assert called["n"] == 0
    assert data["zhuque"]["source"] == "local"
    assert "本地规则" in data["zhuque"]["note"]


def test_detect_source_zhuque_falls_back_on_any_error(client, monkeypatch):
    from app.services import article_quality as AQ, studio_pipeline as SP

    headers, article = _client_with_article(client, "studio-detect-zhuque-fallback")

    async def fake_detect(*args, **kwargs):
        raise AQ.QualityError("朱雀 Key 无效", status=401)

    monkeypatch.setattr(AQ, "detect", fake_detect)
    # 选了朱雀：401 也兜底本地，不抛错
    SP.PIPELINE.detect_now(article["id"], key="test-key", endpoint=None, source_pref="zhuque")
    data = client.get(f"/api/v1/studio/articles/{article['id']}", headers=headers).json()
    assert data["zhuque"]["source"] == "local"
    assert data["zhuque"]["round"] == 1
    assert "本地规则" in data["zhuque"]["note"]
    assert "朱雀 Key 无效" in data["zhuque"]["note"]

    # 自动模式：非 502 错误照常抛出，不静默兜底
    with pytest.raises(AQ.QualityError):
        SP.PIPELINE.detect_now(article["id"], key="test-key", endpoint=None, source_pref="auto")


def test_detect_route_parses_scope_and_source_body(client):
    headers, article = _client_with_article(client, "studio-detect-route-body")
    # 带 body 不应 422：路由能解析 blockIds / source
    resp = client.post(
        f"/api/v1/studio/articles/{article['id']}/zhuque/detect",
        json={"blockIds": [article["blocks"][0]["id"]], "source": "local"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    # 选中的段落都不可检（小标题）→ 400
    headers2, other = _client_with_article(client, "studio-detect-route-bad-scope")
    heading_id = next(b["id"] for b in other["blocks"] if b["kind"] == "heading")
    bad = client.post(
        f"/api/v1/studio/articles/{other['id']}/zhuque/detect",
        json={"blockIds": [heading_id]},
        headers=headers2,
    )
    assert bad.status_code == 400, bad.text


def test_detect_does_not_hide_database_error(client, monkeypatch):
    from app.services import article_quality as AQ, studio_pipeline as SP, studio_repo

    headers, article = _client_with_article(client, "studio-detect-db-error")

    async def fake_detect(*args, **kwargs):
        return {"segments": [{"text": article["blocks"][0]["text"], "label": 0, "conf": 0}]}

    def broken_save(*args, **kwargs):
        raise RuntimeError("database save failed")

    monkeypatch.setattr(AQ, "detect", fake_detect)
    monkeypatch.setattr(studio_repo, "save", broken_save)
    with pytest.raises(RuntimeError, match="database save failed"):
        SP.PIPELINE.detect_now(article["id"], key="test-key", endpoint=None)

    response = client.get(f"/api/v1/studio/articles/{article['id']}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["zhuque"]["round"] == 0


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

    SP.PIPELINE.detect_now(article["id"], key=None, endpoint=None)
    SP.PIPELINE.report_now(article["id"], report)
    with SessionLocal() as db:
        row = db.get(models.StudioArticle, article["id"])
        data = studio_repo.row_to_dict(row)
    checks = [(block.get("check") or {}).get("status") for block in data["blocks"]]
    assert "flag" in checks
    assert data["zhuque"]["source"] == "pdf"
    assert data["zhuque"]["flagged"] >= 1
    assert data["zhuque"]["round"] == 2
    assert "dimensions" not in data["zhuque"]


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


# ---------------- 写作风格档 ----------------


def test_state_lists_writer_styles(client):
    headers = login(client, subject="studio-styles")
    state = client.get("/api/v1/studio/state", headers=headers).json()
    styles = state["writerStyles"]
    assert [item["value"] for item in styles][0] == "tutorial"
    assert {"tutorial", "method", "review", "opinion", "sponsor"} <= {item["value"] for item in styles}
    assert all(item["label"] and item["hint"] for item in styles)


def test_create_rejects_unknown_writer_style(client):
    headers = login(client, subject="studio-style-bad")
    response = client.post(
        "/api/v1/studio/articles/new",
        json={"topic": "选题", "markdown": MARKDOWN, "titles": False, "writerStyle": "no-such-style"},
        headers=headers,
    )
    assert response.status_code == 400, response.text


def test_create_keeps_chosen_writer_style(client):
    headers = login(client, subject="studio-style-create")
    article = client.post(
        "/api/v1/studio/articles/new",
        json={"topic": "选题", "markdown": MARKDOWN, "titles": False, "writerStyle": "opinion"},
        headers=headers,
    ).json()
    assert article["writerStyle"] == "opinion"


def test_set_writer_style_endpoint(client):
    headers, article = _client_with_article(client, "studio-style-set")
    assert article["writerStyle"] == "tutorial"

    url = f"/api/v1/studio/articles/{article['id']}/writer-style"
    ok = client.post(url, json={"style": "sponsor"}, headers=headers)
    assert ok.status_code == 200, ok.text
    assert ok.json()["writerStyle"] == "sponsor"
    # 只换档位，正文一个字都不动
    assert ok.json()["blocks"] == article["blocks"]

    assert client.post(url, json={"style": "nope"}, headers=headers).status_code == 400


def test_draft_route_accepts_empty_body_and_style(client, monkeypatch):
    """前端不带 body（旧缓存）和带 style 两种调用都要能用。"""
    from app.services import studio_pipeline as SP

    seen = []
    monkeypatch.setattr(SP.PIPELINE, "draft", lambda article_id: seen.append(article_id) or {})
    headers, article = _client_with_article(client, "studio-draft-route")
    url = f"/api/v1/studio/articles/{article['id']}/draft"

    assert client.post(url, headers=headers).status_code == 200
    assert seen == [article["id"]]

    # 带 style = 换风格重写：风格先落库，再排队
    response = client.post(url, json={"style": "review"}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["writerStyle"] == "review"
    assert len(seen) == 2


def test_draft_uses_selected_writer_style(client, monkeypatch):
    from app.services import ai_gateway, studio_pipeline as SP

    seen = {}

    async def fake_generate(db, **kwargs):
        if kwargs.get("task_type") == "studio_draft":
            seen["system"] = kwargs.get("system_prompt") or ""
            return {"text": "# 新标题\n\n重写后的正文。", "errorCode": None}
        return {
            "text": json.dumps({"titles": [{"text": "候选标题", "hook": "C"}]}, ensure_ascii=False),
            "errorCode": None,
        }

    monkeypatch.setattr(ai_gateway, "generate_text", fake_generate)
    headers = login(client, subject="studio-style-draft")
    article = client.post(
        "/api/v1/studio/articles/new",
        json={"topic": "选题", "material": "素材", "titles": False, "writerStyle": "opinion"},
        headers=headers,
    ).json()

    SP.PIPELINE.draft_now(article["id"])

    # 人设 + 选中的风格档 + 输出硬规则，三份都在，且没有混进别的档
    assert "林月半子" in seen["system"]
    assert "本次风格档：观点随笔" in seen["system"]
    assert "本次风格档：实战教程" not in seen["system"]
    assert "只输出文章本身的 Markdown" in seen["system"]


def test_redraft_replaces_body_and_keeps_undo(client, monkeypatch):
    from app import models
    from app.core.db import SessionLocal
    from app.services import ai_gateway, studio_pipeline as SP, studio_repo

    async def fake_generate(db, **kwargs):
        if kwargs.get("task_type") == "studio_draft":
            return {"text": "# 换了一版\n\n这是按新风格重写的正文。", "errorCode": None}
        return {
            "text": json.dumps({"titles": [{"text": "候选标题", "hook": "C"}]}, ensure_ascii=False),
            "errorCode": None,
        }

    monkeypatch.setattr(ai_gateway, "generate_text", fake_generate)
    headers, article = _client_with_article(client, "studio-redraft")

    SP.PIPELINE.draft_now(article["id"])

    with SessionLocal() as db:
        row = db.get(models.StudioArticle, article["id"])
        data = studio_repo.row_to_dict(row)
    assert data["title"] == "换了一版"
    assert data["blocks"][0]["text"] == "这是按新风格重写的正文。"

    # 覆盖前存了快照：撤销能退回原来的四段
    labels = [row["label"] for row in client.get(f"/api/v1/studio/articles/{article['id']}/revisions", headers=headers).json()["revisions"]]
    assert "重写正文" in labels
    # 写正文后会自动出标题，那一步也占了一条历史：先退标题，再退重写
    for _ in range(2):
        undone = client.post(f"/api/v1/studio/articles/{article['id']}/undo", headers=headers)
        assert undone.status_code == 200, undone.text
    blocks = undone.json()["blocks"]
    assert len(blocks) == 4
    assert blocks[0]["text"].startswith("首先，随着人工智能")
