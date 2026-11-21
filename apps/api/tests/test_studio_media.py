"""成稿台媒体能力：封面生成/挑图、正文插图、SVG 动画卡。

覆盖四类行为：没有供应商时明确失败、任务状态流转、插图插块的幂等、
SVG 卡校验失败原样透传，以及越权访问一律 404。
"""

import io
import uuid

from PIL import Image
from sqlalchemy import select

from tests.conftest import login

MARKDOWN = """# 测试标题

第一段正文写得足够长，用来给封面提示词提供素材，也用来验证插图插在段落之后的位置是否正确。

第二段继续展开，说明这件事为什么值得做，以及做完之后读者能得到什么具体的好处。

第三段收尾。
"""


# ---------------- 辅助 ----------------


def _user_id(subject: str) -> str:
    from app import models
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        return db.scalar(select(models.User.id).where(models.User.auth_subject == subject))


def _client_with_article(client, subject: str):
    headers = login(client, subject=subject)
    response = client.post(
        "/api/v1/studio/articles/new",
        json={"topic": "测试选题", "material": "素材", "markdown": MARKDOWN, "titles": False},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return headers, response.json(), _user_id(subject)


def _provider(monkeypatch, available: bool) -> None:
    from app.services import image_provider

    monkeypatch.setattr(
        image_provider,
        "status",
        lambda db, owner_user_id: {"available": available, "providers": []},
    )


def _fake_enqueue(monkeypatch, *, status: str = "succeeded", asset_ids: list[str] | None = None):
    """替身入队：真建一条 BackgroundJob 行，sync 就能按 jobId 读到。"""
    from app import models
    from app.core import queue

    calls: list[dict] = []

    def fake_enqueue(db, **kwargs):
        calls.append(kwargs)
        job = models.BackgroundJob(
            owner_user_id=kwargs["owner_user_id"],
            queue=kwargs["queue"],
            kind=kwargs["kind"],
            payload=kwargs["payload"],
            status="queued",
        )
        db.add(job)
        db.flush()
        job.status = status
        job.result = {"assetIds": list(asset_ids or [])}
        db.commit()
        return job.id

    monkeypatch.setattr(queue, "enqueue", fake_enqueue)
    return calls


def _upload(client, headers, name: str = "cover.png") -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (24, 24), (30, 80, 120)).save(buffer, format="PNG")
    buffer.seek(0)
    response = client.post(
        "/api/v1/assets", files={"file": (name, buffer, "image/png")}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _article_url(article_id: str, suffix: str) -> str:
    return f"/api/v1/studio/articles/{article_id}/{suffix}"


# ---------------- 封面 ----------------


def test_cover_generate_without_provider_fails_loudly(client, monkeypatch):
    headers, article, _uid = _client_with_article(client, "media-no-provider")
    _provider(monkeypatch, False)

    response = client.post(
        _article_url(article["id"], "cover/generate"), json={}, headers=headers
    )
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["code"] == "image_provider_unavailable"
    assert "生图模型" in body["message"] and "设置" in body["message"]

    current = client.get(f"/api/v1/studio/articles/{article['id']}", headers=headers).json()
    assert current["cover"]["status"] == "failed"
    assert current["cover"]["status"] != "running"
    assert current["cover"]["jobId"] is None


def test_cover_generate_enqueues_and_marks_running(client, monkeypatch):
    headers, article, _uid = _client_with_article(client, "media-cover-run")
    _provider(monkeypatch, True)
    calls = _fake_enqueue(monkeypatch, status="running")

    response = client.post(
        _article_url(article["id"], "cover/generate"),
        json={"prompt": "抽象几何封面", "aspectRatio": "4:3"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    cover = response.json()["cover"]
    assert cover["status"] == "running"
    assert cover["jobId"]
    assert cover["prompt"] == "抽象几何封面"
    assert cover["aspectRatio"] == "4:3"

    assert len(calls) == 1
    payload = calls[0]["payload"]
    assert calls[0]["queue"] == "ai-image"
    assert calls[0]["kind"] == "image-generation"
    assert payload["purpose"] == "studio-cover"
    assert payload["count"] == 1
    assert payload["prompt"] == "抽象几何封面"


def test_cover_generate_defaults_prompt_from_title_and_body(client, monkeypatch):
    headers, article, _uid = _client_with_article(client, "media-cover-prompt")
    _provider(monkeypatch, True)
    calls = _fake_enqueue(monkeypatch)

    response = client.post(
        _article_url(article["id"], "cover/generate"), json={}, headers=headers
    )
    assert response.status_code == 200, response.text
    prompt = response.json()["cover"]["prompt"]
    assert "测试标题" in prompt
    assert "第一段正文" in prompt
    assert calls[0]["payload"]["aspectRatio"] == "16:9"


def test_cover_generate_enqueue_failure_is_persisted(client, monkeypatch):
    from app.core import queue

    headers, article, _uid = _client_with_article(client, "media-cover-enqueue-fail")
    _provider(monkeypatch, True)

    def broken_enqueue(db, **kwargs):
        raise RuntimeError("threadpool closed")

    monkeypatch.setattr(queue, "enqueue", broken_enqueue)

    response = client.post(
        _article_url(article["id"], "cover/generate"), json={}, headers=headers
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "image_enqueue_failed"

    current = client.get(f"/api/v1/studio/articles/{article['id']}", headers=headers).json()
    assert current["cover"]["status"] == "failed"
    assert "设置" in current["cover"]["error"]


def test_cover_sync_ready_then_idempotent(client, monkeypatch):
    headers, article, _uid = _client_with_article(client, "media-cover-sync")
    _provider(monkeypatch, True)
    asset_id = _upload(client, headers, "cover-sync.png")
    _fake_enqueue(monkeypatch, status="succeeded", asset_ids=[asset_id])

    generated = client.post(
        _article_url(article["id"], "cover/generate"), json={}, headers=headers
    )
    assert generated.json()["cover"]["status"] == "running"

    synced = client.post(_article_url(article["id"], "cover/sync"), headers=headers)
    assert synced.status_code == 200, synced.text
    cover = synced.json()["cover"]
    assert cover["status"] == "ready"
    assert cover["assetId"] == asset_id
    assert cover["url"].startswith(f"/api/v1/assets/{asset_id}/raw?")

    again = client.post(_article_url(article["id"], "cover/sync"), headers=headers).json()
    assert again["cover"] == cover
    assert len(again["blocks"]) == len(article["blocks"])


def test_cover_sync_keeps_running_when_job_not_done(client, monkeypatch):
    headers, article, _uid = _client_with_article(client, "media-cover-pending")
    _provider(monkeypatch, True)
    _fake_enqueue(monkeypatch, status="running")

    client.post(_article_url(article["id"], "cover/generate"), json={}, headers=headers)
    synced = client.post(_article_url(article["id"], "cover/sync"), headers=headers)
    assert synced.status_code == 200, synced.text
    assert synced.json()["cover"]["status"] == "running"

    current = client.get(f"/api/v1/studio/articles/{article['id']}", headers=headers).json()
    assert current["cover"]["status"] == "running"


def test_cover_pick_and_clear(client, monkeypatch):
    headers, article, _uid = _client_with_article(client, "media-cover-pick")
    asset_id = _upload(client, headers, "picked.png")

    picked = client.post(
        _article_url(article["id"], "cover/pick"),
        json={"assetId": asset_id},
        headers=headers,
    )
    assert picked.status_code == 200, picked.text
    cover = picked.json()["cover"]
    assert cover["status"] == "ready"
    assert cover["assetId"] == asset_id

    cleared = client.post(
        _article_url(article["id"], "cover/pick"), json={"assetId": None}, headers=headers
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["cover"] == {}


# ---------------- 正文插图 ----------------


def test_inline_sync_inserts_media_block_once(client, monkeypatch):
    headers, article, _uid = _client_with_article(client, "media-inline-sync")
    _provider(monkeypatch, True)
    asset_id = _upload(client, headers, "inline.png")
    calls = _fake_enqueue(monkeypatch, status="succeeded", asset_ids=[asset_id])
    original_ids = [block["id"] for block in article["blocks"]]
    anchor = article["blocks"][0]["id"]

    generated = client.post(
        _article_url(article["id"], "inline/generate"),
        json={"blockId": anchor, "prompt": "示意图"},
        headers=headers,
    )
    assert generated.status_code == 200, generated.text
    body = generated.json()
    diagrams = body["inlineDiagrams"]
    assert len(diagrams) == 1
    diagram = diagrams[0]
    assert diagram["blockId"] == anchor
    assert diagram["status"] == "running"
    assert diagram["jobId"]
    assert calls[0]["payload"]["purpose"] == "studio-inline"
    assert calls[0]["payload"]["aspectRatio"] == "4:3"

    synced = client.post(_article_url(article["id"], "inline/sync"), headers=headers)
    assert synced.status_code == 200, synced.text
    body = synced.json()
    assert body["inlineDiagrams"][0]["status"] == "ready"
    assert body["inlineDiagrams"][0]["assetId"] == asset_id
    assert body["blocks"][0]["id"] == anchor
    inserted = body["blocks"][1]
    assert inserted["kind"] == "media"
    assert inserted["text"] == f"![](asset://{asset_id})"
    assert inserted["insertedDiagramId"] == diagram["id"]
    # 原有块 id 一个都不能变（版本对比依赖它）
    assert [block["id"] for block in body["blocks"] if block["id"] in original_ids] == original_ids

    again = client.post(_article_url(article["id"], "inline/sync"), headers=headers).json()
    assert len(again["blocks"]) == len(body["blocks"])
    assert len([b for b in again["blocks"] if b.get("insertedDiagramId") == diagram["id"]]) == 1


def test_inline_remove_deletes_diagram_and_block(client, monkeypatch):
    headers, article, _uid = _client_with_article(client, "media-inline-remove")
    _provider(monkeypatch, True)
    asset_id = _upload(client, headers, "removed.png")
    _fake_enqueue(monkeypatch, status="succeeded", asset_ids=[asset_id])
    original_ids = [block["id"] for block in article["blocks"]]

    client.post(
        _article_url(article["id"], "inline/generate"),
        json={"blockId": original_ids[0]},
        headers=headers,
    )
    body = client.post(_article_url(article["id"], "inline/sync"), headers=headers).json()
    diagram_id = body["inlineDiagrams"][0]["id"]
    assert len(body["blocks"]) == len(original_ids) + 1

    removed = client.delete(
        _article_url(article["id"], f"inline/{diagram_id}"), headers=headers
    )
    assert removed.status_code == 200, removed.text
    body = removed.json()
    assert body["inlineDiagrams"] == []
    assert [block["id"] for block in body["blocks"]] == original_ids
    assert all(block.get("insertedDiagramId") is None for block in body["blocks"])


def test_inline_sync_marks_failed_job(client, monkeypatch):
    headers, article, _uid = _client_with_article(client, "media-inline-failed")
    _provider(monkeypatch, True)
    _fake_enqueue(monkeypatch, status="failed", asset_ids=[])
    anchor = article["blocks"][0]["id"]

    client.post(
        _article_url(article["id"], "inline/generate"),
        json={"blockId": anchor},
        headers=headers,
    )
    body = client.post(_article_url(article["id"], "inline/sync"), headers=headers).json()
    diagram = body["inlineDiagrams"][0]
    assert diagram["status"] == "failed"
    assert diagram["error"]
    assert len(body["blocks"]) == len(article["blocks"])


# ---------------- SVG 动画卡 ----------------


def test_svg_card_inserts_code_block(client, monkeypatch):
    from app.services import wechat_svg_cards

    headers, article, _uid = _client_with_article(client, "media-svg-ok")
    fence = "```svg-steps 三个步骤\n先看清问题|把现象写具体\n再拆解原因|找到可验证的假设\n最后动手|小步试错\n```"

    async def fake_draft(db, **kwargs):
        assert kwargs["kind"] == "svg-steps"
        assert kwargs["owner_user_id"]
        assert kwargs["article_title"] == "测试标题"
        return {"markdown": fence, "card": {}}

    monkeypatch.setattr(wechat_svg_cards, "draft_card", fake_draft)
    anchor = article["blocks"][0]["id"]

    response = client.post(
        _article_url(article["id"], f"blocks/{anchor}/svg-card"),
        json={"kind": "svg-steps"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["blocks"][0]["id"] == anchor
    inserted = body["blocks"][1]
    assert inserted["kind"] == "code"
    assert inserted["text"] == fence
    assert inserted["insertedDiagramId"] is None
    assert len(body["blocks"]) == len(article["blocks"]) + 1


def test_svg_card_validation_error_passes_through(client, monkeypatch):
    from app.core.deps import ApiError
    from app.services import wechat_svg_cards

    headers, article, _uid = _client_with_article(client, "media-svg-invalid")

    async def bad_draft(db, **kwargs):
        raise ApiError("svg_card_invalid", "生成的卡片未通过校验", status_code=422)

    monkeypatch.setattr(wechat_svg_cards, "draft_card", bad_draft)
    anchor = article["blocks"][0]["id"]

    response = client.post(
        _article_url(article["id"], f"blocks/{anchor}/svg-card"),
        json={"kind": "svg-bars"},
        headers=headers,
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "svg_card_invalid"

    current = client.get(f"/api/v1/studio/articles/{article['id']}", headers=headers).json()
    assert len(current["blocks"]) == len(article["blocks"])


# ---------------- 越权 ----------------


def test_foreign_article_is_not_found(client, monkeypatch):
    _owner_headers, article, _uid = _client_with_article(client, "media-owner")
    other_headers = login(client, subject="media-intruder")
    _provider(monkeypatch, True)
    _fake_enqueue(monkeypatch)

    for suffix in ("cover/generate", "cover/sync", "inline/sync"):
        response = client.post(_article_url(article["id"], suffix), json={}, headers=other_headers)
        assert response.status_code == 404, (suffix, response.text)

    anchor = article["blocks"][0]["id"]
    svg = client.post(
        _article_url(article["id"], f"blocks/{anchor}/svg-card"),
        json={"kind": "svg-steps"},
        headers=other_headers,
    )
    assert svg.status_code == 404


def test_foreign_asset_is_not_found(client):
    owner_headers = login(client, subject="media-asset-owner")
    asset_id = _upload(client, owner_headers, "owner.png")
    headers, article, _uid = _client_with_article(client, "media-asset-intruder")

    response = client.post(
        _article_url(article["id"], "cover/pick"),
        json={"assetId": asset_id},
        headers=headers,
    )
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "not_found"

    current = client.get(f"/api/v1/studio/articles/{article['id']}", headers=headers).json()
    assert current["cover"] == {}


def test_unknown_article_and_block_are_not_found(client, monkeypatch):
    headers, article, _uid = _client_with_article(client, "media-missing")
    _provider(monkeypatch, True)
    _fake_enqueue(monkeypatch)

    missing = client.post(
        _article_url(uuid.uuid4().hex, "cover/generate"), json={}, headers=headers
    )
    assert missing.status_code == 404

    unknown_block = client.post(
        _article_url(article["id"], "inline/generate"),
        json={"blockId": "b_does_not_exist"},
        headers=headers,
    )
    assert unknown_block.status_code == 404
