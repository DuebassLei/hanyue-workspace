"""受信任 Python 定时脚本：CRUD、发布契约校验、测试验证与进程内执行。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from .conftest import login

PREFIX = "/api/v1"


def _script_code() -> str:
    return (
        "def run(params, ctx):\n"
        "    log('params', get_params())\n"
        "    log('ctx', get_context())\n"
        "    return {'summary': 'ok'}\n"
    )


def _create_script(client, headers, name: str, *, code: str | None = None):
    response = client.post(
        f"{PREFIX}/schedules/scripts",
        headers=headers,
        json={
            "name": name,
            "description": "测试脚本",
            "draftCode": code if code is not None else _script_code(),
            "timeoutSeconds": 10,
            "enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _publish_script(client, headers, script_id: str, *, code: str | None = None):
    payload = {} if code is None else {"draftCode": code}
    response = client.post(
        f"{PREFIX}/schedules/scripts/{script_id}/publish", headers=headers, json=payload
    )
    assert response.status_code == 200, response.text
    return response.json()


def _job_and_run(task_id: str):
    from app import models
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        run = db.scalar(
            select(models.ScheduledTaskRun).where(models.ScheduledTaskRun.task_id == task_id)
        )
        job = db.get(models.BackgroundJob, run.job_id)
        db.refresh(run)
        db.refresh(job)
        return run, job


def _job(job_id: str):
    from app import models
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        job = db.get(models.BackgroundJob, job_id)
        db.refresh(job)
        return job


def test_member_is_denied_script_management(client):
    headers = login(client, subject="script-member", role="member")
    response = client.get(f"{PREFIX}/schedules/scripts", headers=headers)

    assert response.status_code == 403, response.text
    body = response.json()
    assert body["code"] == "permission_denied"


def test_script_crud_roundtrip(client):
    headers = login(client, subject=f"script-crud-{uuid.uuid4().hex[:6]}")

    created = _create_script(client, headers, "CRUD 脚本")
    fetched = client.get(
        f"{PREFIX}/schedules/scripts/{created['id']}", headers=headers
    ).json()
    assert fetched["draftCode"] == _script_code()
    assert fetched["publishedVersion"] == 0
    assert fetched["enabled"] is True

    updated = client.patch(
        f"{PREFIX}/schedules/scripts/{created['id']}",
        headers=headers,
        json={"name": "CRUD 脚本改名", "draftCode": _script_code(), "timeoutSeconds": 20},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["name"] == "CRUD 脚本改名"
    assert body["draftCode"] == _script_code()
    assert body["timeoutSeconds"] == 20

    listed = client.get(f"{PREFIX}/schedules/scripts", headers=headers).json()["items"]
    assert [item["id"] for item in listed] == [created["id"]]

    deleted = client.delete(
        f"{PREFIX}/schedules/scripts/{created['id']}", headers=headers
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True
    assert (
        client.get(f"{PREFIX}/schedules/scripts/{created['id']}", headers=headers).status_code
        == 404
    )


def test_publish_rejects_syntax_error(client):
    headers = login(client, subject=f"script-syntax-{uuid.uuid4().hex[:6]}")
    script = _create_script(client, headers, "语法错误", code="def run(:\n")

    response = client.post(
        f"{PREFIX}/schedules/scripts/{script['id']}/publish", headers=headers, json={}
    )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "script_compile_failed"


def test_publish_requires_run_contract(client):
    """统一契约：脚本必须定义顶层 run；不再做 import/名称白名单。"""
    headers = login(client, subject=f"script-contract-{uuid.uuid4().hex[:6]}")
    script = _create_script(
        client, headers, "缺 run", code="log('顶层代码直接执行也不行')\n"
    )

    response = client.post(
        f"{PREFIX}/schedules/scripts/{script['id']}/publish", headers=headers, json={}
    )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "script_run_missing"


@pytest.mark.parametrize(
    "code",
    [
        # 进程内全依赖通道：任意 import 都允许（含第三方与 app 自身）
        "import requests\n\ndef run(params, ctx):\n    return {}\n",
        "from datetime import datetime\n\ndef run(params, ctx):\n    return {}\n",
        "from app import models\n\n\ndef run(params, ctx):\n    return {}\n",
    ],
)
def test_publish_allows_full_dependency_imports(client, code):
    headers = login(client, subject=f"script-allow-{uuid.uuid4().hex[:6]}")
    script = _create_script(client, headers, "全依赖 import", code=code)

    response = client.post(
        f"{PREFIX}/schedules/scripts/{script['id']}/publish", headers=headers, json={}
    )

    assert response.status_code == 200, response.text


def test_publish_increments_version_and_snapshot(client):
    headers = login(client, subject=f"script-version-{uuid.uuid4().hex[:6]}")
    script = _create_script(client, headers, "版本快照")

    first = _publish_script(client, headers, script["id"], code=_run_log_code("v1"))
    second = _publish_script(client, headers, script["id"], code=_run_log_code("v2"))

    assert first["publishedVersion"] == 1
    assert first["publishedCode"] == _run_log_code("v1")
    assert first["publishedHash"]
    assert second["publishedVersion"] == 2
    assert second["publishedCode"] == _run_log_code("v2")
    assert second["publishedHash"] != first["publishedHash"]


def _run_log_code(text: str) -> str:
    return f"def run(params, ctx):\n    log({text!r})\n    return {{'summary': {text!r}}}\n"


def test_script_examples_endpoint(client):
    headers = login(client, subject=f"script-examples-{uuid.uuid4().hex[:6]}")
    response = client.get(f"{PREFIX}/schedules/scripts/examples", headers=headers)

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) >= 5
    for item in items:
        assert item["key"] and item["title"] and "def run(" in item["code"]


def test_script_test_runs_draft_and_rolls_back(client):
    """测试验证：测 draft 代码；脚本里建的数据回滚不落库。"""
    from app import models
    from app.core.db import SessionLocal

    headers = login(client, subject=f"script-test-{uuid.uuid4().hex[:6]}")
    good = _create_script(
        client,
        headers,
        "回滚验证",
        code=(
            "def run(params, ctx):\n"
            "    created = create_draft('测试回滚稿件', '正文')\n"
            "    return {'summary': 'created', 'created': created}\n"
        ),
    )

    response = client.post(
        f"{PREFIX}/schedules/scripts/{good['id']}/test", headers=headers, json={}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True, body
    assert body["result"]["summary"] == "created"
    assert body["result"]["created"]["contentItemId"]

    with SessionLocal() as db:
        count = db.scalar(
            select(models.ContentItem.id).where(models.ContentItem.title == "测试回滚稿件")
        )
    assert count is None, "测试验证建的数据必须回滚，不能落库"


def test_script_test_reports_contract_error(client):
    headers = login(client, subject=f"script-test-err-{uuid.uuid4().hex[:6]}")
    script = _create_script(client, headers, "契约报错", code="value = 1\n")

    response = client.post(
        f"{PREFIX}/schedules/scripts/{script['id']}/test", headers=headers, json={}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "script_run_missing"


def test_schedule_test_run_for_builtin_script(client):
    """统一测试入口：内置 manifest 脚本也能回滚试跑。"""
    headers = login(client, subject=f"schedule-test-{uuid.uuid4().hex[:6]}")
    response = client.post(
        f"{PREFIX}/schedules/test-run",
        headers=headers,
        json={"taskType": "script.health_check", "params": {"label": "测试"}},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True, body
    assert "summary" in (body["result"] or {})


def test_schedule_test_run_for_user_script(client):
    headers = login(client, subject=f"schedule-test-user-{uuid.uuid4().hex[:6]}")
    script = _publish_script(client, headers, _create_script(client, headers, "任务级测试")["id"])

    response = client.post(
        f"{PREFIX}/schedules/test-run",
        headers=headers,
        json={"taskType": f"user-script.{script['id']}", "params": {}},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True, body
    assert body["result"]["summary"] == "ok"


def test_types_injects_only_published_enabled_scripts(client):
    headers = login(client, subject=f"script-types-{uuid.uuid4().hex[:6]}")
    published = _publish_script(
        client, headers, _create_script(client, headers, "注入已发布")["id"]
    )
    disabled = _publish_script(
        client, headers, _create_script(client, headers, "不注入停用")["id"]
    )
    disabled = client.patch(
        f"{PREFIX}/schedules/scripts/{disabled['id']}",
        headers=headers,
        json={"enabled": False},
    ).json()

    items = client.get(f"{PREFIX}/schedules/types", headers=headers).json()["items"]
    keys = {item["key"] for item in items}
    assert f"user-script.{published['id']}" in keys
    assert f"user-script.{disabled['id']}" not in keys
    injected = next(item for item in items if item["key"] == f"user-script.{published['id']}")
    assert injected["scope"] == "user"
    assert injected["params"]["fields"][0]["name"] == "params"
    assert injected["params"]["fields"][0]["type"] == "object"


def test_create_user_script_schedule_defaults_params(client):
    headers = login(client, subject=f"script-task-{uuid.uuid4().hex[:6]}")
    script = _publish_script(client, headers, _create_script(client, headers, "定时任务")["id"])

    response = client.post(
        f"{PREFIX}/schedules",
        headers=headers,
        json={
            "taskType": f"user-script.{script['id']}",
            "scheduleKind": "cron",
            "cron": "0 8 * * *",
            "timezone": "Asia/Shanghai",
        },
    )

    assert response.status_code == 201, response.text
    task = response.json()
    assert task["taskType"] == f"user-script.{script['id']}"
    # 统一 system：脚本任务与内置任务同口径，都是全局定时配置
    assert task["scope"] == "system"
    assert task["params"] == {}


def test_run_disabled_script_returns_conflict(client):
    headers = login(client, subject=f"script-disabled-{uuid.uuid4().hex[:6]}")
    script = _publish_script(client, headers, _create_script(client, headers, "停用试跑")["id"])
    disabled = client.patch(
        f"{PREFIX}/schedules/scripts/{script['id']}",
        headers=headers,
        json={"enabled": False},
    ).json()

    response = client.post(
        f"{PREFIX}/schedules/scripts/{disabled['id']}/run", headers=headers, json={}
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "script_disabled"


def test_queued_script_run_keeps_published_version_after_republish(client, monkeypatch):
    headers = login(client, subject=f"script-snapshot-{uuid.uuid4().hex[:6]}")
    script = _publish_script(
        client,
        headers,
        _create_script(client, headers, "固定版本")["id"],
        code=_run_log_code("v1"),
    )

    from app.core import queue

    monkeypatch.setattr(queue, "dispatch", lambda *args, **kwargs: False)
    task = client.post(
        f"{PREFIX}/schedules",
        headers=headers,
        json={"taskType": f"user-script.{script['id']}", "scheduleKind": "interval", "intervalSeconds": 1800},
    )
    assert task.status_code == 201, task.text
    queued = client.post(f"{PREFIX}/schedules/{task.json()['id']}/run", headers=headers)
    assert queued.status_code == 200, queued.text
    job_id = queued.json()["jobId"]

    _publish_script(client, headers, script["id"], code=_run_log_code("v2"))

    from app import models
    from app.core.db import SessionLocal
    from app.services import job_runner, script_runtime

    observed = {}
    monkeypatch.setattr(
        script_runtime,
        "run_script",
        lambda **kwargs: observed.update(kwargs)
        or {"result": {"summary": "ok"}, "stdout": "ok", "stderr": ""},
    )
    job_runner.run_job(job_id)

    assert observed["code"] == _run_log_code("v1")
    with SessionLocal() as db:
        job = db.get(models.BackgroundJob, job_id)
        assert job.payload["scriptVersion"] == 1


def test_manual_run_keeps_job_when_dispatch_blocked(client, monkeypatch):
    headers = login(client, subject=f"script-dispatch-{uuid.uuid4().hex[:6]}")
    script = _publish_script(client, headers, _create_script(client, headers, "阻断投递")["id"])
    from app.core import queue

    def blocked_dispatch(*args, **kwargs):
        blocked_dispatch.called = True
        return False

    blocked_dispatch.called = False
    monkeypatch.setattr(queue, "dispatch", blocked_dispatch)

    task = client.post(
        f"{PREFIX}/schedules",
        headers=headers,
        json={"taskType": f"user-script.{script['id']}", "scheduleKind": "interval", "intervalSeconds": 1800},
    )
    assert task.status_code == 201, task.text

    response = client.post(
        f"{PREFIX}/schedules/{task.json()['id']}/run", headers=headers
    )

    assert response.status_code == 200, response.text
    assert blocked_dispatch.called is True
    run, job = _job_and_run(task.json()["id"])
    assert run.status == "queued"
    assert run.source == "manual"
    assert job.status == "queued"
    assert job.kind == f"user-script.{script['id']}"
    assert job.payload["scriptParams"] == {}


def test_handle_user_script_executes_published_snapshot(client, monkeypatch):
    """统一通道：用户脚本在服务端进程内执行，结果契约与内置脚本一致。"""
    headers = login(client, subject=f"script-run-{uuid.uuid4().hex[:6]}")
    script = _publish_script(client, headers, _create_script(client, headers, "进程内执行")["id"])

    from app.core import queue

    monkeypatch.setattr(queue, "dispatch", lambda *args, **kwargs: False)
    run_response = client.post(
        f"{PREFIX}/schedules/scripts/{script['id']}/run",
        headers=headers,
        json={"params": {"hello": "世界"}},
    )
    assert run_response.status_code == 200, run_response.text

    assert _job(run_response.json()["jobId"]).status == "queued"

    from app.services import job_runner

    job_runner.run_job(run_response.json()["jobId"])
    job = _job(run_response.json()["jobId"])
    assert job.status == "succeeded"
    result = job.result
    assert result["summary"] == "ok"
    assert "params" in result["stdout"]
    assert "hello" in result["stdout"]
    assert "ctx" in result["stdout"]
    assert result["stderr"] == ""
    assert result["scriptVersion"] == 1


def test_validate_published_contract_and_param_validation():
    from app.services import scheduled_tasks, script_runtime

    # 契约：必须定义 run
    assert script_runtime.validate_published(_script_code()) is None
    with pytest.raises(script_runtime.ScriptValidationError) as error:
        script_runtime.validate_published("value = 1\n")
    assert error.value.code == "script_run_missing"

    # 参数校验：params 必须是 JSON 对象
    spec = scheduled_tasks.dynamic_task_type("user-script.test")
    assert scheduled_tasks.validate_params(spec, None) == {}
    with pytest.raises(scheduled_tasks.ScheduleError) as error:
        scheduled_tasks.validate_params(spec, [])
    assert error.value.code == "invalid_params"
