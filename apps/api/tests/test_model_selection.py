"""模型选择：默认优先 / 降级、显式钉死、失效忽略、越权拒绝、入队快照。

设计共识：
- 不选择 → 用户级默认模型排候选链首位，后面仍可自动降级；
- 显式选中 → 钉死，失败明确报错并带供应商原因，绝不偷偷换模型；
- 默认模型配置失效 → 忽略并回报 defaultModelIgnored，不清除偏好；
- 显式传入越权/不存在的 modelId → 400 model_not_found；
- 小说队列任务入队时快照模型，重启重投不受之后改默认影响。
"""

from __future__ import annotations

import json

import pytest

PREFIX = "/api/v1"


def _headers(client, subject):
    res = client.post(f"{PREFIX}/auth/login", json={"subject": subject, "displayName": subject})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _connection(client, headers, name):
    res = client.post(
        f"{PREFIX}/models/connections",
        json={
            "displayName": name,
            "baseUrl": "https://api.example.com/v1",
            "apiKey": "sk-test-key",
            "providerType": "openai_compatible",
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _catalog(client, headers, connection_id, code, capabilities=None):
    res = client.post(
        f"{PREFIX}/models/catalog",
        json={
            "connectionId": connection_id,
            "modelCode": code,
            "capabilities": capabilities or ["text.generate", "text.stream"],
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


def _sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.split("\n\n"):
        name = data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if name and data:
            events.append((name, json.loads(data)))
    return events


def _fake_stream(monkeypatch, seen, fail_models=(), detail="subscription has expired"):
    from app.services import ai_gateway

    async def fake(db, **kwargs):
        code = kwargs["model_code"]
        seen.append(code)
        if code in fail_models:
            yield {
                "event": "generation.error",
                "data": {
                    "code": "provider_unavailable",
                    "message": f"供应商返回 HTTP 400：{code} {detail}",
                    "retryable": False,
                },
            }
            return
        yield {"event": "generation.delta", "data": {"text": f"来自 {code}"}}
        yield {"event": "generation.done", "data": {}}

    monkeypatch.setattr(ai_gateway, "stream_text", fake)


def _generate(client, headers, **extra):
    with client.stream(
        "POST",
        f"{PREFIX}/ai/text-generations",
        json={"taskType": "summarize", "prompt": "说点什么", **extra},
        headers=headers,
    ) as response:
        assert response.status_code == 200, response.text
        return _sse("".join(response.iter_text()))


# ---------------- 偏好与目录 ----------------

def test_preference_partial_update_and_catalog_metadata(client):
    headers = _headers(client, "select-pref")
    conn = _connection(client, headers, "主连接")
    entry = _catalog(client, headers, conn, "model-a")

    dark = client.patch(f"{PREFIX}/preferences", json={"theme": "dark"}, headers=headers)
    assert dark.status_code == 200
    assert (dark.json()["theme"], dark.json()["source"]) == ("dark", "server")

    # 只设默认模型，不能把主题冲掉
    updated = client.patch(
        f"{PREFIX}/preferences",
        json={"defaultTextModelId": entry["id"]},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["theme"] == "dark"
    assert updated.json()["defaultTextModelId"] == entry["id"]
    assert "model-a" in (updated.json()["defaultTextModelLabel"] or "")

    listed = client.get(f"{PREFIX}/models/catalog", headers=headers).json()
    row = next(item for item in listed if item["id"] == entry["id"])
    assert row["isDefault"] is True
    assert row["isTextModel"] is True
    assert row["connectionName"] == "主连接"
    assert row["connectionStatus"] == "untested"

    # 清除默认也不能改主题
    cleared = client.patch(
        f"{PREFIX}/preferences", json={"defaultTextModelId": ""}, headers=headers
    )
    assert cleared.status_code == 200
    assert cleared.json()["defaultTextModelId"] is None
    assert cleared.json()["theme"] == "dark"


def test_preference_rejects_foreign_or_unknown_default(client):
    owner = _headers(client, "select-owner")
    other = _headers(client, "select-other")
    foreign_conn = _connection(client, other, "别人的连接")
    foreign = _catalog(client, other, foreign_conn, "foreign-model")

    bad = client.patch(
        f"{PREFIX}/preferences",
        json={"defaultTextModelId": foreign["id"]},
        headers=owner,
    )
    assert bad.status_code == 400
    assert bad.json()["code"] == "model_not_found"

    missing = client.patch(
        f"{PREFIX}/preferences", json={"defaultTextModelId": "not-exist"}, headers=owner
    )
    assert missing.status_code == 400
    assert missing.json()["code"] == "model_not_found"


# ---------------- 默认优先 / 降级 / 显式钉死 ----------------

def test_default_model_is_first_and_still_falls_back(client, monkeypatch):
    headers = _headers(client, "select-default")
    conn_a = _connection(client, headers, "连接A")
    conn_b = _connection(client, headers, "连接B")
    entry_a = _catalog(client, headers, conn_a, "model-a")
    entry_b = _catalog(client, headers, conn_b, "model-b")

    # 默认指向创建顺序靠后的 model-b，验证它确实被提到首位
    assert client.patch(
        f"{PREFIX}/preferences",
        json={"defaultTextModelId": entry_b["id"]},
        headers=headers,
    ).status_code == 200

    seen: list[str] = []
    _fake_stream(monkeypatch, seen)
    events = _generate(client, headers)
    started = dict(events)["generation.started"]
    assert started["candidates"][0] == "model-b"
    assert seen == ["model-b"]
    assert dict(events)["generation.model"]["modelCode"] == "model-b"

    # 默认模型失败仍自动降级，且生成记录落的是实际使用的模型
    seen.clear()
    _fake_stream(monkeypatch, seen, fail_models={"model-b"})
    events = _generate(client, headers)
    by_name = dict(events)
    generation_id = by_name["generation.started"]["generationId"]
    assert seen == ["model-b", "model-a"]
    assert "generation.delta" in by_name
    assert by_name["generation.model"]["modelCode"] == "model-a"
    assert by_name["generation.model"]["fallbackUsed"] is True
    assert by_name["generation.done"]["fallbackUsed"] is True

    from app import models
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        row = db.get(models.AiGeneration, generation_id)
        assert row.model_id == entry_a["id"], "降级后必须记实际模型，不能假装用的是默认"
        assert row.request_summary["modelCode"] == "model-a"
        assert row.request_summary["fallbackUsed"] is True


def test_explicit_model_is_pinned_and_errors_include_provider_reason(client, monkeypatch):
    headers = _headers(client, "select-pinned")
    conn_a = _connection(client, headers, "连接A")
    conn_b = _connection(client, headers, "连接B")
    _catalog(client, headers, conn_a, "model-a")
    entry_b = _catalog(client, headers, conn_b, "model-b")

    seen: list[str] = []
    _fake_stream(monkeypatch, seen, fail_models={"model-b"})
    events = _generate(client, headers, modelId=entry_b["id"])
    by_name = dict(events)

    assert seen == ["model-b"], "显式选中禁止降级到 model-a"
    assert "generation.delta" not in by_name
    error = by_name["generation.error"]
    assert "subscription has expired" in error["message"]
    assert error["modelCode"] == "model-b"
    assert by_name["generation.started"]["pinned"] is True


def test_explicit_foreign_model_returns_400(client):
    owner = _headers(client, "select-owner-2")
    other = _headers(client, "select-other-2")
    foreign_conn = _connection(client, other, "别人的连接")
    foreign = _catalog(client, other, foreign_conn, "foreign-model")

    res = client.post(
        f"{PREFIX}/ai/text-generations",
        json={"taskType": "summarize", "prompt": "说点什么", "modelId": foreign["id"]},
        headers=owner,
    )
    assert res.status_code == 400
    assert res.json()["code"] == "model_not_found"


def test_invalid_default_is_ignored_with_reason_and_preference_kept(client, monkeypatch):
    headers = _headers(client, "select-invalid")
    conn_a = _connection(client, headers, "连接A")
    conn_b = _connection(client, headers, "连接B")
    entry_a = _catalog(client, headers, conn_a, "model-a")
    entry_b = _catalog(client, headers, conn_b, "model-b")

    client.patch(
        f"{PREFIX}/preferences",
        json={"defaultTextModelId": entry_b["id"]},
        headers=headers,
    )
    disabled = client.patch(
        f"{PREFIX}/models/catalog/{entry_b['id']}",
        json={"enabled": False},
        headers=headers,
    )
    assert disabled.status_code == 200

    seen: list[str] = []
    _fake_stream(monkeypatch, seen)
    events = _generate(client, headers)
    by_name = dict(events)
    ignored = by_name["generation.started"]["defaultModelIgnored"]
    assert ignored["reason"] == "disabled"
    assert ignored["modelId"] == entry_b["id"]
    assert seen == ["model-a"], "失效默认应被跳过，按原候选链生成"
    assert by_name["generation.model"]["modelCode"] == "model-a"

    pref = client.get(f"{PREFIX}/preferences", headers=headers).json()
    assert pref["defaultTextModelId"] == entry_b["id"], "失效只忽略，不清除偏好"
    assert pref["defaultTextModelIgnored"]["reason"] == "disabled"

    # 重新启用后默认自动恢复
    client.patch(
        f"{PREFIX}/models/catalog/{entry_b['id']}", json={"enabled": True}, headers=headers
    )
    seen.clear()
    _fake_stream(monkeypatch, seen)
    events = _generate(client, headers)
    assert seen == ["model-b"], "默认恢复后应重新排首位"


# ---------------- 小说任务快照 ----------------

def _novel_project(client, headers):
    res = client.post(
        f"{PREFIX}/novels",
        json={"title": "试作", "sourceText": "第一章 开始\n\n甲说了话。\n\n第二章 继续\n乙走了。", "rightsConfirmed": True},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    project = res.json()
    chapters = client.post(f"{PREFIX}/novels/{project['id']}/split", headers=headers)
    assert chapters.status_code == 200, chapters.text
    return project, chapters.json()[0]


@pytest.fixture(autouse=True)
def no_auto_dispatch(monkeypatch):
    from app.core import queue

    monkeypatch.setattr(queue, "dispatch", lambda job_id, **kwargs: True)


def test_novel_job_snapshots_model_at_enqueue(client, monkeypatch):
    from app import models
    from app.core.db import SessionLocal

    headers = _headers(client, "select-snapshot")
    conn_a = _connection(client, headers, "连接A")
    conn_b = _connection(client, headers, "连接B")
    entry_a = _catalog(client, headers, conn_a, "model-a")
    entry_b = _catalog(client, headers, conn_b, "model-b")
    project, chapter = _novel_project(client, headers)

    client.patch(
        f"{PREFIX}/preferences",
        json={"defaultTextModelId": entry_a["id"]},
        headers=headers,
    )

    # 显式选择：payload 记 id + modelExplicit=True
    queued = client.post(
        f"{PREFIX}/novels/{project['id']}/chapters/{chapter['id']}/rewrite",
        json={"modelId": entry_b["id"]},
        headers=headers,
    )
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["jobId"]
    with SessionLocal() as db:
        payload = db.get(models.BackgroundJob, job_id).payload
    assert payload["modelId"] == entry_b["id"]
    assert payload["modelExplicit"] is True

    # 放行上一个任务后再入队，避免 job_in_progress 干扰快照断言
    with SessionLocal() as db:
        db.get(models.BackgroundJob, job_id).status = "succeeded"
        db.commit()

    # 默认快照：入队时把「当时的默认」写死，之后改默认不影响已排队任务
    queued2 = client.post(
        f"{PREFIX}/novels/{project['id']}/chapters/{chapter['id']}/rewrite",
        headers=headers,
    )
    assert queued2.status_code == 202, queued2.text
    job2 = queued2.json()["jobId"]
    with SessionLocal() as db:
        payload2 = db.get(models.BackgroundJob, job2).payload
    assert payload2["modelId"] == entry_a["id"]
    assert payload2["modelExplicit"] is False

    client.patch(
        f"{PREFIX}/preferences",
        json={"defaultTextModelId": entry_b["id"]},
        headers=headers,
    )
    with SessionLocal() as db:
        still = db.get(models.BackgroundJob, job2).payload
    assert still["modelId"] == entry_a["id"], "改默认不能影响已入队任务"


def test_novel_job_result_reports_actual_model(client, monkeypatch):
    """任务结果如实回报实际模型 / 是否降级 / 默认失效原因。"""
    from app import models
    from app.core.db import SessionLocal
    from app.services import job_runner

    headers = _headers(client, "select-result")
    conn_a = _connection(client, headers, "连接A")
    conn_b = _connection(client, headers, "连接B")
    entry_a = _catalog(client, headers, conn_a, "model-a")
    entry_b = _catalog(client, headers, conn_b, "model-b")
    project, chapter = _novel_project(client, headers)

    # 创建顺序是 model-a 在前；让它 400，验证降级到 model-b 并如实回报
    seen: list[str] = []
    _fake_stream(monkeypatch, seen, fail_models={"model-a"})

    queued = client.post(
        f"{PREFIX}/novels/{project['id']}/chapters/{chapter['id']}/rewrite",
        headers=headers,
    )
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["jobId"]
    job_runner.run_job(job_id)

    with SessionLocal() as db:
        job = db.get(models.BackgroundJob, job_id)
        assert job.status == "succeeded", job.error_message
        result = job.result
    assert seen == ["model-a", "model-b"]
    assert result["modelCode"] == "model-b"
    assert result["modelId"] == entry_b["id"]
    assert result["fallbackUsed"] is True
    assert result["pinned"] is False
