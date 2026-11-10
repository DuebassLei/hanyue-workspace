"""真 keyset 分页与线程池启动恢复的回归测试。

旧实现只在内存里找 cursor，翻到第 2 页就再也读不到全表第 limit+2 条之后的数据。
这里用 7 条数据 + limit=2 逐页走完，验证能遍历完整集合且不重复。
"""

from __future__ import annotations

import asyncio
from urllib.parse import quote

# 保持与 app/core/pagination.py 一致；不在收集阶段导入 app 包，避免提前绑定 core.db
CURSOR_HEADER = "X-Next-Cursor"

PREFIX = "/api/v1"


def _login(client, subject):
    res = client.post(f"{PREFIX}/auth/login", json={"subject": subject})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def test_content_keyset_pagination_walks_full_dataset(client):
    """选题库下线后，keyset 分页的核心保障由 /content 承担。"""
    headers = _login(client, "pagination-content")
    created = []
    for index in range(7):
        res = client.post(f"{PREFIX}/content", json={"title": f"分页稿件-{index}"}, headers=headers)
        assert res.status_code == 201, res.text
        created.append(res.json()["id"])

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        url = f"{PREFIX}/content?limit=2"
        if cursor:
            url += f"&cursor={quote(cursor)}"
        res = client.get(url, headers=headers)
        assert res.status_code == 200, res.text
        page = res.json()
        assert isinstance(page, list), "裸数组响应形状必须保持不变"
        seen.extend(item["id"] for item in page)
        cursor = res.headers.get(CURSOR_HEADER)
        if not cursor:
            break
    else:
        raise AssertionError("分页未在 10 页内结束，可能出现了死循环")

    assert len(seen) == 7, f"未遍历到全部数据：{seen}"
    assert len(set(seen)) == 7, "分页出现了重复记录"
    assert set(seen) == set(created), "分页漏掉了记录"


def test_jobs_keyset_pagination_header_and_invalid_cursor(client):
    headers = _login(client, "pagination-jobs")
    for index in range(5):
        res = client.post(
            f"{PREFIX}/ai/image-generations",
            json={"prompt": f"分页配图-{index}", "count": 1},
            headers=headers,
        )
        assert res.status_code == 202, res.text

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        url = f"{PREFIX}/jobs?limit=2"
        if cursor:
            url += f"&cursor={quote(cursor)}"
        res = client.get(url, headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert set(("items", "nextCursor", "backend")).issubset(body)
        seen.extend(item["id"] for item in body["items"])
        assert res.headers.get(CURSOR_HEADER) == body["nextCursor"]
        cursor = body["nextCursor"]
        if not cursor:
            break

    assert len(seen) == 5, f"未遍历到全部任务：{seen}"
    assert len(set(seen)) == 5

    bad = client.get(f"{PREFIX}/jobs?limit=2&cursor=not-a-real-cursor", headers=headers)
    assert bad.status_code == 400
    assert bad.json()["code"] == "invalid_cursor"


def test_threadpool_startup_recovers_queued_jobs(monkeypatch):
    """无 Redis 时 API 启动必须自己重投 queued 任务；Redis 模式交给 Worker。"""
    from app import main as main_module
    from app.core import queue

    calls: list[str] = []
    monkeypatch.setattr(main_module, "init_db", lambda: None)

    async def _startup():
        async with main_module.lifespan(None):
            pass

    monkeypatch.setattr(queue, "backend_name", lambda: "threadpool")
    monkeypatch.setattr(queue, "recover_pending", lambda: calls.append("threadpool") or 0)
    asyncio.run(_startup())
    assert calls == ["threadpool"]

    calls.clear()
    monkeypatch.setattr(queue, "backend_name", lambda: "redis")
    monkeypatch.setattr(queue, "recover_pending", lambda: calls.append("redis") or 0)
    asyncio.run(_startup())
    assert calls == [], "Redis 模式下 API 不应重复投递，交给 Worker"
