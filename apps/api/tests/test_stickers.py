"""贴图创作后端：CRUD、越权隔离、图序边界、状态机、软删、入队与 finalize。

约定沿用 conftest：session 级 client、login()。这里额外关掉自动投递，让
generate-anchor / generate-rest 只入队不真跑（无供应商环境下也不会误执行），
并 monkeypatch queue.enqueue 记录 payload 以断言 stickerStyleId/referenceAssetIds。
"""

import pytest


@pytest.fixture(autouse=True)
def no_auto_dispatch(monkeypatch):
    """关掉自动投递：本文件只验证入队与状态流转，不需要真实生成。"""
    from app.core import queue

    monkeypatch.setattr(queue, "dispatch", lambda job_id, **kw: True)


@pytest.fixture
def captured_jobs(monkeypatch):
    """记录每次 enqueue 的 payload；仍走真实 enqueue 落库拿 jobId。"""
    from app.core import queue

    calls: list[dict] = []
    original = queue.enqueue

    def spy(db, **kwargs):
        calls.append(kwargs)
        return original(db, **kwargs)

    monkeypatch.setattr(queue, "enqueue", spy)
    return calls


def login(client, subject="sticker-user"):
    res = client.post("/api/v1/auth/login", json={"subject": subject, "displayName": subject})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def make_set(client, headers, **overrides):
    body = {"theme": "城市通勤的一天", "styleId": "humanist_photo", "narrative": "scene"}
    body.update(overrides)
    res = client.post("/api/v1/stickers", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def outline(n=3):
    return {
        "items": [
            {"caption": f"第{i}幕", "framePrompt": f"画面描述 {i}", "slotType": "info"}
            for i in range(n)
        ]
    }


# ---------- styles ----------
def test_sticker_styles_listing(client):
    headers = login(client)
    res = client.get("/api/v1/sticker-styles", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["styles"]) == 6
    # prompt_suffix 是内部风格句，不下发给前端
    assert all("promptSuffix" not in s and "prompt_suffix" not in s for s in body["styles"])
    assert set(body["trackDefaults"].keys()) == {
        "社会民生", "教育与职场", "娱乐与情感", "国际与时事",
        "财经与商业", "AI与科技", "体育与健康", "文化与生活",
    }
    assert len(body["narratives"]) == 5


# ---------- create + validation ----------
def test_create_validates_style_and_narrative(client):
    headers = login(client)
    bad_style = client.post(
        "/api/v1/stickers",
        json={"theme": "t", "styleId": "nope", "narrative": "scene"},
        headers=headers,
    )
    assert bad_style.status_code == 400
    assert bad_style.json()["code"] == "invalid_style"

    bad_narr = client.post(
        "/api/v1/stickers",
        json={"theme": "t", "styleId": "flat_illustration", "narrative": "bogus"},
        headers=headers,
    )
    assert bad_narr.status_code == 400
    assert bad_narr.json()["code"] == "invalid_narrative"


def test_create_title_falls_back_to_theme(client):
    headers = login(client)
    long_theme = "一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十"
    body = make_set(client, headers, theme=long_theme, title="")
    assert body["status"] == "draft"
    assert body["title"] == long_theme[:20]


# ---------- ownership ----------
def test_cross_user_access_is_not_found(client):
    a = login(client, "stick-a")
    b = login(client, "stick-b")
    created = make_set(client, a)
    sid = created["id"]
    assert client.get(f"/api/v1/stickers/{sid}", headers=b).status_code == 404
    assert client.patch(f"/api/v1/stickers/{sid}", json={"title": "x"}, headers=b).status_code == 404
    assert client.delete(f"/api/v1/stickers/{sid}", headers=b).status_code == 404
    assert client.put(f"/api/v1/stickers/{sid}/outline", json=outline(), headers=b).status_code == 404
    assert client.post(f"/api/v1/stickers/{sid}/finalize", headers=b).status_code == 404


# ---------- outline boundaries ----------
def test_outline_count_boundaries(client):
    headers = login(client)
    sid = make_set(client, headers)["id"]
    one = client.put(f"/api/v1/stickers/{sid}/outline", json=outline(1), headers=headers)
    assert one.status_code == 400
    assert one.json()["code"] == "invalid_outline_count"

    ten = client.put(f"/api/v1/stickers/{sid}/outline", json=outline(10), headers=headers)
    assert ten.status_code == 400
    assert ten.json()["code"] == "invalid_outline_count"

    ok = client.put(f"/api/v1/stickers/{sid}/outline", json=outline(3), headers=headers)
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "outlined"
    assert ok.json()["total"] == 3


# ---------- state machine ----------
def test_finalize_requires_all_succeeded(client):
    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(2), headers=headers)
    # draft→outlined 后仍有 pending 图，直接 finalize 应被拒
    res = client.post(f"/api/v1/stickers/{sid}/finalize", headers=headers)
    assert res.status_code == 409
    assert res.json()["code"] == "incomplete"


def test_draft_finalize_rejected(client):
    headers = login(client)
    sid = make_set(client, headers)["id"]
    # 未设图序直接 finalize → 409
    res = client.post(f"/api/v1/stickers/{sid}/finalize", headers=headers)
    assert res.status_code == 409


def test_outline_locked_while_generating(client):
    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(2), headers=headers)
    anchor = client.post(f"/api/v1/stickers/{sid}/generate-anchor", headers=headers)
    assert anchor.status_code == 200, anchor.text
    assert anchor.json()["status"] == "generating"
    blocked = client.put(f"/api/v1/stickers/{sid}/outline", json=outline(3), headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "invalid_transition"


def test_generate_anchor_requires_outlined(client):
    headers = login(client)
    sid = make_set(client, headers)["id"]
    # draft 状态直接生成锚点 → 409
    res = client.post(f"/api/v1/stickers/{sid}/generate-anchor", headers=headers)
    assert res.status_code == 409


# ---------- enqueue payloads ----------
def test_generate_anchor_enqueue_payload(client, captured_jobs):
    headers = login(client)
    sid = make_set(client, headers, styleId="minimal_tech")["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(3), headers=headers)
    captured_jobs.clear()
    res = client.post(f"/api/v1/stickers/{sid}/generate-anchor", headers=headers)
    assert res.status_code == 200, res.text
    assert len(captured_jobs) == 1
    payload = captured_jobs[0]["payload"]
    assert payload["stickerStyleId"] == "minimal_tech"
    assert payload["aspectRatio"] == "3:4"
    assert payload["count"] == 1
    assert "referenceAssetIds" not in payload  # 锚点不带参考图
    assert payload["stickerSetId"] == sid
    detail = client.get(f"/api/v1/stickers/{sid}", headers=headers).json()
    assert detail["images"][0]["status"] == "queued"
    assert detail["images"][0]["jobId"]


def test_generate_rest_uses_anchor_reference(client, captured_jobs):
    """手工把锚点置成功，再验证其余逐行入队且带锚点 asset 作参考图。"""
    from app import models
    from app.core.db import SessionLocal

    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(3), headers=headers)
    db = SessionLocal()
    try:
        rows = db.query(models.StickerImage).filter_by(set_id=sid).order_by(models.StickerImage.sort_order).all()
        rows[0].status = "succeeded"
        rows[0].asset_id = "anchor-asset"
        db.commit()
    finally:
        db.close()
    captured_jobs.clear()
    res = client.post(f"/api/v1/stickers/{sid}/generate-rest", headers=headers)
    assert res.status_code == 200, res.text
    # generate-rest 统一返回 StickerSetOut（Lead 契约修正），入队数由 job spy 断言
    body = res.json()
    assert body["id"] == sid and body["status"] == "generating"
    assert len(captured_jobs) == 2
    for call in captured_jobs:
        assert call["payload"]["referenceAssetIds"] == ["anchor-asset"]


def test_retry_non_anchor_carries_reference(client, captured_jobs):
    from app import models
    from app.core.db import SessionLocal

    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(2), headers=headers)
    db = SessionLocal()
    try:
        rows = db.query(models.StickerImage).filter_by(set_id=sid).order_by(models.StickerImage.sort_order).all()
        rows[0].status = "succeeded"
        rows[0].asset_id = "anchor-asset"
        rows[1].status = "failed"
        db.commit()
        second_id = rows[1].id
    finally:
        db.close()
    captured_jobs.clear()
    res = client.post(f"/api/v1/stickers/{sid}/images/{second_id}/retry", headers=headers)
    assert res.status_code == 200, res.text
    assert len(captured_jobs) == 1
    assert captured_jobs[0]["payload"]["referenceAssetIds"] == ["anchor-asset"]


def test_retry_anchor_has_no_reference(client, captured_jobs):
    from app import models
    from app.core.db import SessionLocal

    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(2), headers=headers)
    db = SessionLocal()
    try:
        anchor = db.query(models.StickerImage).filter_by(set_id=sid, sort_order=0).one()
        anchor.status = "failed"
        db.commit()
        anchor_id = anchor.id
    finally:
        db.close()
    captured_jobs.clear()
    res = client.post(f"/api/v1/stickers/{sid}/images/{anchor_id}/retry", headers=headers)
    assert res.status_code == 200, res.text
    assert "referenceAssetIds" not in captured_jobs[0]["payload"]


# ---------- soft delete ----------
def test_soft_delete_hidden_from_list(client):
    headers = login(client)
    sid = make_set(client, headers)["id"]
    assert any(s["id"] == sid for s in client.get("/api/v1/stickers", headers=headers).json())
    assert client.delete(f"/api/v1/stickers/{sid}", headers=headers).status_code == 204
    assert not any(s["id"] == sid for s in client.get("/api/v1/stickers", headers=headers).json())
    assert client.get(f"/api/v1/stickers/{sid}", headers=headers).status_code == 404


# ---------- lazy sync ----------
def test_detail_lazy_syncs_failed_job(client):
    from app import models
    from app.core.db import SessionLocal

    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(2), headers=headers)
    anchor = client.post(f"/api/v1/stickers/{sid}/generate-anchor", headers=headers).json()
    job_id = anchor["images"][0]["jobId"]
    db = SessionLocal()
    try:
        job = db.get(models.BackgroundJob, job_id)
        job.status = "failed"
        job.error_code = "provider_unavailable"
        db.commit()
    finally:
        db.close()
    detail = client.get(f"/api/v1/stickers/{sid}", headers=headers).json()
    assert detail["images"][0]["status"] == "failed"
    assert detail["images"][0]["errorCode"] == "provider_unavailable"


def test_finalize_after_all_succeeded(client):
    from app import models
    from app.core.db import SessionLocal

    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(2), headers=headers)
    db = SessionLocal()
    try:
        for img in db.query(models.StickerImage).filter_by(set_id=sid).all():
            img.status = "succeeded"
            img.asset_id = f"asset-{img.sort_order}"
        db.commit()
    finally:
        db.close()
    res = client.post(f"/api/v1/stickers/{sid}/finalize", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "done"


# ---------- review round fixes ----------
def test_regenerate_anchor_allowed_after_success(client, captured_jobs):
    """R1：锚点已成功的 restPending 态允许重抽（旧 asset 作废，重新入队不带参考图）。"""
    from app import models
    from app.core.db import SessionLocal

    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(3), headers=headers)
    db = SessionLocal()
    try:
        anchor = db.query(models.StickerImage).filter_by(set_id=sid, sort_order=0).one()
        anchor.status = "succeeded"
        anchor.asset_id = "old-anchor-asset"
        db.commit()
    finally:
        db.close()
    captured_jobs.clear()
    res = client.post(f"/api/v1/stickers/{sid}/generate-anchor", headers=headers)
    assert res.status_code == 200, res.text
    assert len(captured_jobs) == 1
    assert "referenceAssetIds" not in captured_jobs[0]["payload"]
    body = res.json()
    assert body["status"] == "generating"
    assert body["images"][0]["status"] == "queued"


def test_generate_anchor_rejects_queued(client):
    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(2), headers=headers)
    client.post(f"/api/v1/stickers/{sid}/generate-anchor", headers=headers)
    res = client.post(f"/api/v1/stickers/{sid}/generate-anchor", headers=headers)
    assert res.status_code == 409


def test_done_append_reuse_flow(client, captured_jobs):
    """R2：done 集合「再补一张」闭环——outline 带回旧 asset → generate-rest 复用秒过、只为新行入队。"""
    from app import models
    from app.core.db import SessionLocal

    headers = login(client)
    sid = make_set(client, headers)["id"]
    items = outline(2)["items"]
    client.put(f"/api/v1/stickers/{sid}/outline", json={"items": items}, headers=headers)
    db = SessionLocal()
    try:
        for img in db.query(models.StickerImage).filter_by(set_id=sid).all():
            img.status = "succeeded"
            img.asset_id = f"asset-{img.sort_order}"
        db.commit()
    finally:
        db.close()
    client.post(f"/api/v1/stickers/{sid}/finalize", headers=headers)
    # 追加一行（前 2 行画面描述保持不变）
    captured_jobs.clear()
    appended = {"items": items + [{"caption": "补充", "framePrompt": "新画面", "slotType": "pace"}]}
    res = client.put(f"/api/v1/stickers/{sid}/outline", json=appended, headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "generating"
    # 复用素材随行带回（status 仍 pending）：新行无 asset，旧位置行有
    assert [(img["status"], img["assetId"]) for img in body["images"]] == [
        ("pending", "asset-0"), ("pending", "asset-1"), ("pending", None),
    ]
    res = client.post(f"/api/v1/stickers/{sid}/generate-rest", headers=headers)
    assert res.status_code == 200, res.text
    # 只有新行真正入队；复用行不重复烧钱
    assert len(captured_jobs) == 1
    assert captured_jobs[0]["payload"]["prompt"] == "新画面"
    assert captured_jobs[0]["payload"]["referenceAssetIds"] == ["asset-0"]
    body = res.json()
    statuses = [img["status"] for img in body["images"]]
    assert statuses == ["succeeded", "succeeded", "queued"]


def test_done_outline_with_running_job_rejected(client):
    """R2 守护：done 集合仍有在跑任务时禁止重排图序，避免旧 job 写入被替换的行。"""
    from app import models
    from app.core.db import SessionLocal

    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(2), headers=headers)
    client.post(f"/api/v1/stickers/{sid}/generate-anchor", headers=headers)
    db = SessionLocal()
    try:
        row = db.get(models.StickerSet, sid)
        row.status = "done"
        db.commit()
    finally:
        db.close()
    res = client.put(f"/api/v1/stickers/{sid}/outline", json=outline(2), headers=headers)
    assert res.status_code == 409


def test_soft_deleted_set_all_endpoints_404(client):
    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(2), headers=headers)
    assert client.delete(f"/api/v1/stickers/{sid}", headers=headers).status_code == 204
    for method, path, body in [
        ("patch", f"/api/v1/stickers/{sid}", {}),
        # outline 有请求体 schema，先过 pydantic 再进路由——给合法 body 才能测到归属
        ("put", f"/api/v1/stickers/{sid}/outline", outline(2)),
        ("post", f"/api/v1/stickers/{sid}/generate-anchor", {}),
        ("post", f"/api/v1/stickers/{sid}/generate-rest", {}),
        ("post", f"/api/v1/stickers/{sid}/finalize", {}),
        ("get", f"/api/v1/stickers/{sid}/archive.zip", None),
    ]:
        if method == "get":
            res = client.get(path, headers=headers)
        else:
            res = getattr(client, method)(path, json=body or {}, headers=headers)
        assert res.status_code == 404, (method, path, res.status_code)


def test_list_pagination_cursor(client):
    headers = login(client)
    for i in range(3):
        make_set(client, headers, theme=f"分页主题 {i}")
    res = client.get("/api/v1/stickers?limit=2", headers=headers)
    assert res.status_code == 200
    first = res.json()
    assert isinstance(first, list) and len(first) == 2
    cursor = res.headers.get("X-Next-Cursor")
    assert cursor, "列表应下发 X-Next-Cursor 游标头"
    res2 = client.get(f"/api/v1/stickers?limit=2&cursor={cursor}", headers=headers)
    second = res2.json()
    assert len(second) >= 1
    assert {s["id"] for s in first}.isdisjoint({s["id"] for s in second})


def test_archive_zip_streams_images(client):
    from app import models
    from app.core.db import SessionLocal
    import io
    import zipfile

    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(2), headers=headers)
    # 造两个真实素材文件
    from PIL import Image

    db = SessionLocal()
    try:
        user = db.query(models.User).first()
        ws = db.query(models.Workspace).first()
        rows = db.query(models.StickerImage).filter_by(set_id=sid).order_by(models.StickerImage.sort_order).all()
        for img in rows:
            buffer = io.BytesIO()
            Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
            raw = buffer.getvalue()
            from app.services.job_runner import _store_asset

            asset = _store_asset(db, user, ws.id, raw, "image/png", f"t{img.sort_order}.png")
            img.asset_id = asset.id
            img.status = "succeeded"
        db.commit()
    finally:
        db.close()
    # zip 端点要求 review/done 终态：直写素材后先把集合推到 review
    db = SessionLocal()
    try:
        row = db.get(models.StickerSet, sid)
        row.status = "review"
        db.commit()
    finally:
        db.close()
    res = client.get(f"/api/v1/stickers/{sid}/archive.zip", headers=headers)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(res.content)) as archive:
        names = sorted(archive.namelist())
    assert names == ["01.png", "02.png"]
