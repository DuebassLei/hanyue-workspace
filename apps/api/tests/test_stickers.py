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
    """记录每次入队的 payload（单发 enqueue 与链式 enqueue_chain 都算一单）。"""
    from app.core import queue

    calls: list[dict] = []
    original = queue.enqueue
    original_chain = queue.enqueue_chain

    def spy(db, **kwargs):
        calls.append(kwargs)
        return original(db, **kwargs)

    def chain_spy(db, *, payloads, **kwargs):
        for item in payloads:
            calls.append({"payload": item})
        return original_chain(db, payloads=payloads, **kwargs)

    monkeypatch.setattr(queue, "enqueue", spy)
    monkeypatch.setattr(queue, "enqueue_chain", chain_spy)
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
    assert "modelId" not in payload


def test_image_model_is_remembered_and_enqueued(client, captured_jobs):
    headers = login(client)
    sid = make_set(client, headers, styleId="minimal_tech")["id"]
    res = client.patch(
        f"/api/v1/stickers/{sid}/image-model",
        json={"modelId": "img-model-1"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["imageModelId"] == "img-model-1"
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(2), headers=headers)
    captured_jobs.clear()
    res = client.post(f"/api/v1/stickers/{sid}/generate-anchor", headers=headers)
    assert res.status_code == 200, res.text
    assert captured_jobs[0]["payload"]["modelId"] == "img-model-1"
    detail = client.get(f"/api/v1/stickers/{sid}", headers=headers).json()
    assert detail["imageModelId"] == "img-model-1"

    res = client.patch(
        f"/api/v1/stickers/{sid}/image-model",
        json={"modelId": ""},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["imageModelId"] is None


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
        # 必须取集合 owner：全量跑时库里有别的测试用户，取 first() 会让 zip 的归属校验全跳过
        owner_id = db.get(models.StickerSet, sid).owner_user_id
        user = db.get(models.User, owner_id)
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


# ---------- serial chain (queue.enqueue_chain) ----------
def _chain_jobs(db, group_key):
    from app import models
    from sqlalchemy import select

    rows = db.scalars(
        select(models.BackgroundJob)
        .where(models.BackgroundJob.queue == "ai-image")
        .order_by(models.BackgroundJob.created_at, models.BackgroundJob.id)
    ).all()
    return [r for r in rows if (r.payload or {}).get("stickerChainKey") == group_key]


def test_enqueue_chain_dispatches_first_only(client):
    """链式入队只立即投递首单，其余保持 queued 等接力。"""
    from app.core import queue
    from app.core.db import SessionLocal

    dispatched: list[str] = []
    original = queue.dispatch
    queue.dispatch = lambda job_id, **kw: dispatched.append(job_id) or True
    try:
        headers = login(client)
        sid = make_set(client, headers)["id"]
        client.put(f"/api/v1/stickers/{sid}/outline", json=outline(4), headers=headers)
        db = SessionLocal()
        try:
            payloads = [
                {"prompt": f"p{i}", "count": 1, "stickerSetId": sid, "stickerChainKey": f"sticker:{sid}"}
                for i in range(3)
            ]
            ids = queue.enqueue_chain(
                db, owner_user_id=user_id_of(db), queue="ai-image",
                kind="image-generation", payloads=payloads,
            )
            assert len(dispatched) == 1 and dispatched[0] == ids[0]
            jobs = _chain_jobs(db, f"sticker:{sid}")
            assert [j.status for j in jobs] == ["queued", "queued", "queued"] or \
                   [j.status for j in jobs][0] in ("queued", "running")
            # 接力：模拟首单落定，应投递第二单
            dispatched.clear()
            first = db.get(__import__("app.models", fromlist=["BackgroundJob"]).BackgroundJob, ids[0])
            first.status = "succeeded"
            db.commit()
            queue.chain_next_if_done(payloads[0])
            assert dispatched == [ids[1]]
        finally:
            db.execute(__import__("sqlalchemy").delete(__import__("app.models", fromlist=["BackgroundJob"]).BackgroundJob).where(
                __import__("app.models", fromlist=["BackgroundJob"]).BackgroundJob.id.in_(ids)))
            db.commit(); db.close()
    finally:
        queue.dispatch = original


def user_id_of(db):
    from app import models
    return db.query(models.User).first().id


def test_recover_pending_chains_head_only(client):
    """重启恢复：同链多个 queued 只补投最早一单，不把串行链打回并发。"""
    from app import models
    from app.core import queue
    from app.core.db import SessionLocal
    from datetime import datetime, timedelta, timezone

    headers = login(client)
    sid = make_set(client, headers)["id"]
    key = f"sticker:{sid}"
    db = SessionLocal()
    try:
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        ids = []
        for i in range(3):
            job = models.BackgroundJob(
                owner_user_id=user_id_of(db), queue="ai-image", kind="image-generation",
                payload={"prompt": f"p{i}", "stickerSetId": sid, "stickerChainKey": key},
                status="queued",
            )
            db.add(job)
            db.flush()
            job.created_at = old + timedelta(seconds=i)
            ids.append(job.id)
        # 本测试进程里 make_set 等任务都晚于 BOOT_TIME，会被恢复逻辑排除；
        # 造一条更早的普通 queued 任务当「历史遗留」，否则整轮恢复无事可做。
        plain = models.BackgroundJob(
            owner_user_id=user_id_of(db), queue="ai-image", kind="image-generation",
            payload={"prompt": "legacy"}, status="queued",
        )
        db.add(plain)
        db.flush()
        plain.created_at = old
        plain_ids = [plain.id]
        db.commit()
        dispatched: list[str] = []
        original = queue.dispatch
        queue.dispatch = lambda job_id, **kw: dispatched.append(job_id) or True
        try:
            queue.recover_pending()
        finally:
            queue.dispatch = original
        # 链成员只允许投出最早一单（其余靠接力）；本测试造的普通历史任务必须被补投
        chain_dispatched = [j for j in dispatched if j in ids]
        assert chain_dispatched == [ids[0]], f"链内应只投链头，实际 {chain_dispatched}"
        assert plain.id in dispatched, "历史普通任务应被补投"
    finally:
        import sqlalchemy
        db.execute(sqlalchemy.delete(models.BackgroundJob).where(
            models.BackgroundJob.id.in_(ids + plain_ids)))
        db.commit()
        db.close()


def test_retry_failed_uses_chain(client, captured_jobs):
    """retry-failed 批量重试：多张走链式入队且带锚点参考；无失败张报业务码。"""
    from app import models
    from app.core.db import SessionLocal

    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(3), headers=headers)
    db = SessionLocal()
    try:
        rows = db.query(models.StickerImage).filter_by(set_id=sid).order_by(models.StickerImage.sort_order).all()
        rows[0].status, rows[0].asset_id = "succeeded", "anchor-x"
        for r in rows[1:]:
            r.status, r.error_code = "failed", "provider_error"
        db.commit()
    finally:
        db.close()
    captured_jobs.clear()
    res = client.post(f"/api/v1/stickers/{sid}/retry-failed", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "generating"
    assert [img["status"] for img in body["images"]] == ["succeeded", "queued", "queued"]
    assert len(captured_jobs) == 2
    for call in captured_jobs:
        assert call["payload"]["referenceAssetIds"] == ["anchor-x"]
        assert call["payload"]["stickerChainKey"] == f"sticker:{sid}"
    # 再点一次：没有 failed 了
    res2 = client.post(f"/api/v1/stickers/{sid}/retry-failed", headers=headers)
    assert res2.status_code == 409
    assert res2.json()["code"] == "nothing_to_retry"


def test_retry_failed_chain_starts_at_anchor(client, captured_jobs):
    """锚点也失败时：锚点作链头留在串行链里（不带参考图），其余失败张带锚点参考。"""
    from app import models
    from app.core.db import SessionLocal

    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(3), headers=headers)
    db = SessionLocal()
    try:
        rows = db.query(models.StickerImage).filter_by(set_id=sid).order_by(models.StickerImage.sort_order).all()
        for r in rows:
            r.status, r.error_code = "failed", "provider_error"
        rows[0].asset_id = "stale-anchor"  # 锚点失败：旧 asset 不可当参考
        db.commit()
    finally:
        db.close()
    captured_jobs.clear()
    res = client.post(f"/api/v1/stickers/{sid}/retry-failed", headers=headers)
    assert res.status_code == 200, res.text
    # 三张全部重试（不漏张），且全部同一条链——锚点单独单发会与链头并发打供应商
    assert len(captured_jobs) == 3
    for call in captured_jobs:
        assert call["payload"]["stickerChainKey"] == f"sticker:{sid}"
    anchor_call = captured_jobs[0]["payload"]
    assert anchor_call["stickerImageId"], "链头应是锚点那一行"
    assert "referenceAssetIds" not in anchor_call, "锚点带不了自己的参考图"
    for call in captured_jobs[1:]:
        assert "referenceAssetIds" not in call["payload"], "锚点未成功时不得拿旧 asset 当参考"


def test_patch_image_prompt_keeps_other_rows_and_asset(client, captured_jobs):
    """第 3 步改单张描述：只动这一行；不勾重画时旧素材保留、不入队。"""
    from app import models
    from app.core.db import SessionLocal

    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(3), headers=headers)
    db = SessionLocal()
    try:
        rows = db.query(models.StickerImage).filter_by(set_id=sid).order_by(models.StickerImage.sort_order).all()
        rows[0].status, rows[0].asset_id = "succeeded", "anchor-asset"
        rows[1].status, rows[1].asset_id = "succeeded", "second-asset"
        # 模拟真实链路：这行确实按当前描述生成过
        rows[1].generated_prompt = rows[1].frame_prompt
        target_id = rows[1].id
        db.commit()
    finally:
        db.close()
    captured_jobs.clear()
    res = client.patch(
        f"/api/v1/stickers/{sid}/images/{target_id}",
        json={"framePrompt": "新的画面描述", "caption": "新配文"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    images = {img["id"]: img for img in res.json()["images"]}
    assert images[target_id]["framePrompt"] == "新的画面描述"
    assert images[target_id]["caption"] == "新配文"
    assert images[target_id]["promptApplied"] is False, "改了描述未重画必须显式标记"
    assert images[target_id]["assetId"] == "second-asset", "不重画不得丢掉已出素材"
    assert captured_jobs == [], "只保存描述不应入队"
    # 其他行原样保留
    untouched = [img for img in res.json()["images"] if img["id"] != target_id]
    assert [img["framePrompt"] for img in untouched] == ["画面描述 0", "画面描述 2"]


def test_patch_image_prompt_regenerate_requeues_only_that_row(client, captured_jobs):
    """改描述并勾重画：只该行入队，旧 asset 摘掉，其余张不动。"""
    from app import models
    from app.core.db import SessionLocal

    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(3), headers=headers)
    db = SessionLocal()
    try:
        rows = db.query(models.StickerImage).filter_by(set_id=sid).order_by(models.StickerImage.sort_order).all()
        rows[0].status, rows[0].asset_id = "succeeded", "anchor-asset"
        rows[2].status, rows[2].asset_id = "succeeded", "third-asset"
        target_id = rows[1].id
        db.commit()
    finally:
        db.close()
    captured_jobs.clear()
    res = client.patch(
        f"/api/v1/stickers/{sid}/images/{target_id}",
        json={"framePrompt": "换成雨夜街口", "regenerate": True},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "generating"
    images = {img["id"]: img for img in body["images"]}
    assert images[target_id]["status"] == "queued"
    assert images[target_id]["assetId"] is None, "描述变了的旧素材必须作废"
    assert images[target_id]["framePrompt"] == "换成雨夜街口"
    assert images[target_id]["promptApplied"] is True
    assert len(captured_jobs) == 1
    payload = captured_jobs[0]["payload"]
    assert payload["prompt"] == "换成雨夜街口"
    assert payload["referenceAssetIds"] == ["anchor-asset"]
    # 其余行保持原状态
    assert images[rows[0].id]["status"] == "succeeded"
    assert images[rows[2].id]["status"] == "succeeded"


def test_patch_image_prompt_rejects_while_generating(client):
    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(2), headers=headers)
    anchor = client.post(f"/api/v1/stickers/{sid}/generate-anchor", headers=headers).json()
    target_id = anchor["images"][0]["id"]
    res = client.patch(
        f"/api/v1/stickers/{sid}/images/{target_id}",
        json={"framePrompt": "改不了"},
        headers=headers,
    )
    assert res.status_code == 409
    assert res.json()["code"] == "invalid_transition"


def test_patch_image_regenerate_requires_anchor(client):
    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(2), headers=headers)
    detail = client.get(f"/api/v1/stickers/{sid}", headers=headers).json()
    target_id = detail["images"][1]["id"]
    res = client.patch(
        f"/api/v1/stickers/{sid}/images/{target_id}",
        json={"framePrompt": "锚点还没出图就重画", "regenerate": True},
        headers=headers,
    )
    assert res.status_code == 409
    assert res.json()["message"] == "需先生成锚点图"


def test_retry_after_prompt_edit_uses_latest_prompt(client, captured_jobs):
    """改了描述暂未重画：之后的单张重试会按最新描述入队（不是旧 payload）。"""
    from app import models
    from app.core.db import SessionLocal

    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(2), headers=headers)
    db = SessionLocal()
    try:
        rows = db.query(models.StickerImage).filter_by(set_id=sid).order_by(models.StickerImage.sort_order).all()
        rows[0].status, rows[0].asset_id = "succeeded", "anchor-asset"
        rows[1].status = "failed"
        rows[1].generated_prompt = rows[1].frame_prompt
        db.commit()
        target_id = rows[1].id
    finally:
        db.close()
    patched = client.patch(
        f"/api/v1/stickers/{sid}/images/{target_id}",
        json={"framePrompt": "只改描述不重画"},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["images"][1]["promptApplied"] is False
    captured_jobs.clear()
    res = client.post(f"/api/v1/stickers/{sid}/images/{target_id}/retry", headers=headers)
    assert res.status_code == 200, res.text
    assert len(captured_jobs) == 1
    assert captured_jobs[0]["payload"]["prompt"] == "只改描述不重画"
    assert res.json()["images"][1]["promptApplied"] is True


def test_patch_image_prompt_owned_by_other_user_404(client):
    headers = login(client, "sticker-owner-patch")
    other = login(client, "sticker-other-patch")
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(2), headers=headers)
    detail = client.get(f"/api/v1/stickers/{sid}", headers=headers).json()
    target_id = detail["images"][0]["id"]
    res = client.patch(
        f"/api/v1/stickers/{sid}/images/{target_id}",
        json={"framePrompt": "越权"},
        headers=other,
    )
    assert res.status_code == 404


def test_stalled_chain_is_resumed_not_deadlocked(client):
    """链只剩排队成员（接力断了）时：守卫不能永久 409，应把链头补投起来。"""
    from app import models
    from app.core import queue
    from app.core.db import SessionLocal

    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(3), headers=headers)
    db = SessionLocal()
    try:
        rows = db.query(models.StickerImage).filter_by(set_id=sid).order_by(models.StickerImage.sort_order).all()
        rows[0].status, rows[0].asset_id = "succeeded", "anchor-asset"
        rows[1].status = "failed"
        db.commit()
        owner = db.get(models.StickerSet, sid).owner_user_id
        stalled = models.BackgroundJob(
            owner_user_id=owner,
            queue="ai-image",
            kind="image-generation",
            payload={"prompt": "p", "stickerSetId": sid, "stickerChainKey": f"sticker:{sid}"},
            status="queued",
        )
        db.add(stalled)
        db.commit()
        job_ids = [stalled.id]
    finally:
        db.close()
    dispatched: list[str] = []
    original = queue.dispatch
    queue.dispatch = lambda job_id, **kw: dispatched.append(job_id) or True
    try:
        res = client.post(f"/api/v1/stickers/{sid}/retry-failed", headers=headers)
        # 依然是 409（这一轮不入新链），但停摆的成员必须被补投，否则用户永远推进不了
        assert res.status_code == 409, res.text
        assert dispatched == job_ids, f"停摆链应补投链头，实际 {dispatched}"
    finally:
        queue.dispatch = original
        import sqlalchemy
        from app.core.db import SessionLocal as SL

        db = SL()
        db.execute(sqlalchemy.delete(models.BackgroundJob).where(models.BackgroundJob.id.in_(job_ids)))
        db.commit()
        db.close()


def test_generate_rest_rejects_second_chain(client):
    """已有链在跑（running）时再发 generate-rest 必须 409，避免同集合两个 running。"""
    from app import models
    from app.core.db import SessionLocal

    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(3), headers=headers)
    db = SessionLocal()
    try:
        rows = db.query(models.StickerImage).filter_by(set_id=sid).order_by(models.StickerImage.sort_order).all()
        rows[0].status, rows[0].asset_id = "succeeded", "anchor-asset"
        rows[1].status = "failed"
        db.commit()
        owner = db.get(models.StickerSet, sid).owner_user_id
        # 造一条同链 running 任务，模拟「上一次批量生成正在跑」
        db.add(
            models.BackgroundJob(
                owner_user_id=owner,
                queue="ai-image",
                kind="image-generation",
                payload={"prompt": "p", "stickerSetId": sid, "stickerChainKey": f"sticker:{sid}"},
                status="running",
            )
        )
        db.commit()
        job_ids = [j.id for j in db.query(models.BackgroundJob).filter_by(queue="ai-image").all()]
    finally:
        db.close()
    try:
        res = client.post(f"/api/v1/stickers/{sid}/generate-rest", headers=headers)
        assert res.status_code == 409, res.text
        assert res.json()["code"] == "chain_active"
        res2 = client.post(f"/api/v1/stickers/{sid}/retry-failed", headers=headers)
        assert res2.status_code == 409
        assert res2.json()["code"] == "chain_active"
    finally:
        import sqlalchemy
        from app.core.db import SessionLocal as SL

        db = SL()
        db.execute(sqlalchemy.delete(models.BackgroundJob).where(models.BackgroundJob.id.in_(job_ids)))
        db.commit()
        db.close()


def test_anchor_success_invalidates_cached_style_signature(client):
    """锚点换图后必须作废 anchor_desc 缓存，否则其余张会按旧锚点的画风对齐。"""
    from app import models
    from app.core.db import SessionLocal
    from app.services.job_runner import _apply_sticker_success

    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(3), headers=headers)
    db = SessionLocal()
    try:
        sticker_set = db.get(models.StickerSet, sid)
        sticker_set.meta_json = {"edits_unsupported": True, "anchor_desc": "flat vector, muted teal"}
        job = models.BackgroundJob(
            owner_user_id=sticker_set.owner_user_id,
            queue="ai-image",
            kind="image-generation",
            payload={"stickerSetId": sid},
            status="running",
        )
        db.add(job)
        db.commit()
        anchor = db.query(models.StickerImage).filter_by(set_id=sid, sort_order=0).one()
        anchor_id, anchor.job_id = anchor.id, job.id
        db.commit()
        _apply_sticker_success(db, job, ["new-anchor"], anchor_id)
    finally:
        db.close()
    db = SessionLocal()
    try:
        meta = db.get(models.StickerSet, sid).meta_json or {}
        assert "anchor_desc" not in meta, "锚点换图后旧画风签名必须失效"
        assert meta.get("edits_unsupported") is True, "不得连带抹掉其他缓存键"
    finally:
        db.close()


def test_late_anchor_reference_filled_at_run_time(client):
    """锚点失败时入队的链成员，轮到执行时若锚点已成功，应补上参考图保持整组同风格。"""
    from app import models
    from app.core.db import SessionLocal
    from app.services.job_runner import _fill_late_anchor_reference

    headers = login(client)
    sid = make_set(client, headers)["id"]
    client.put(f"/api/v1/stickers/{sid}/outline", json=outline(3), headers=headers)
    db = SessionLocal()
    try:
        rows = db.query(models.StickerImage).filter_by(set_id=sid).order_by(models.StickerImage.sort_order).all()
        # 入队那一刻：锚点尚未成功，后续行没有参考图
        payload = {"prompt": "p", "stickerSetId": sid, "stickerImageId": rows[1].id}
        assert _fill_late_anchor_reference(db, payload) == payload
        # 轮到执行时锚点已成功 → 补上参考图
        rows[0].status, rows[0].asset_id = "succeeded", "anchor-ok"
        db.commit()
        filled = _fill_late_anchor_reference(db, payload)
        assert filled["referenceAssetIds"] == ["anchor-ok"]
        assert payload == {"prompt": "p", "stickerSetId": sid, "stickerImageId": rows[1].id}, "不得改写原 payload"
        # 锚点自己那一行永远不会给自己当参考
        anchor_payload = {"prompt": "p", "stickerSetId": sid, "stickerImageId": rows[0].id}
        assert "referenceAssetIds" not in _fill_late_anchor_reference(db, anchor_payload)
    finally:
        db.close()
