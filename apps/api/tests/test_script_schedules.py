"""受信任 Python 定时脚本：CRUD、静态校验、动态任务类型与子进程执行契约。"""

from __future__ import annotations

import ast
import uuid

import pytest
from sqlalchemy import select

from .conftest import login

PREFIX = "/api/v1"


def _script_code() -> str:
    return "log('params', get_params())\nlog('ctx', get_context())\n"


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


@pytest.mark.parametrize(
    ("code", "error_code"),
    [
        ("import os\n", "script_import_forbidden"),
        ("value = open('x')\n", "script_function_forbidden"),
        ("value = getattr(log, 'write', None)\n", "script_function_forbidden"),
        ("__import__('os')\n", "script_import_forbidden"),
        ("from requests import get\n", "script_import_forbidden"),
    ],
)
def test_publish_rejects_forbidden_code(client, code, error_code):
    headers = login(client, subject=f"script-static-{uuid.uuid4().hex[:6]}")
    script = _create_script(client, headers, "静态校验拒绝", code=code)

    response = client.post(
        f"{PREFIX}/schedules/scripts/{script['id']}/publish", headers=headers, json={}
    )

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["code"] == error_code


@pytest.mark.parametrize(
    "code",
    [
        "from datetime import datetime\n",
        "from collections import defaultdict\n",
        _script_code() + "from datetime import datetime\n",
    ],
)
def test_publish_allows_whitelisted_imports(client, code):
    headers = login(client, subject=f"script-allow-{uuid.uuid4().hex[:6]}")
    script = _create_script(client, headers, "静态校验允许", code=code)

    response = client.post(
        f"{PREFIX}/schedules/scripts/{script['id']}/publish", headers=headers, json={}
    )

    assert response.status_code == 200, response.text


def test_publish_increments_version_and_snapshot(client):
    headers = login(client, subject=f"script-version-{uuid.uuid4().hex[:6]}")
    script = _create_script(client, headers, "版本快照")

    first = _publish_script(client, headers, script["id"], code="log(1)\n")
    second = _publish_script(client, headers, script["id"], code="log(2)\n")

    assert first["publishedVersion"] == 1
    assert first["publishedCode"] == "log(1)\n"
    assert first["publishedHash"]
    assert second["publishedVersion"] == 2
    assert second["publishedCode"] == "log(2)\n"
    assert second["publishedHash"] != first["publishedHash"]


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
    assert task["scope"] == "user"
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
    headers = login(client, subject=f"script-run-{uuid.uuid4().hex[:6]}")
    script = _publish_script(client, headers, _create_script(client, headers, "子进程")["id"])

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
    assert result["exitCode"] == 0
    assert result["summary"] == "脚本执行完成"
    assert "params" in result["stdout"]
    assert "hello" in result["stdout"]
    assert "ctx" in result["stdout"]
    assert result["stderr"] == ""


def test_validate_script_rejects_manually_constructed_name():
    from app.services import script_runtime

    tree = ast.Module(
        body=[ast.Expr(value=ast.Name(id="open", ctx=ast.Load()))],
        type_ignores=[],
    )
    ast.fix_missing_locations(tree)
    source = ast.unparse(tree)

    with pytest.raises(script_runtime.ScriptValidationError) as error:
        script_runtime.validate_script(source)

    assert error.value.code == "script_function_forbidden"


def test_validate_script_rejects_non_object_runtime_params():
    from app.services import scheduled_tasks, script_runtime

    assert script_runtime.validate_published("log(get_params())\n") is None
    spec = scheduled_tasks.dynamic_task_type("user-script.test")
    assert scheduled_tasks.validate_params(spec, None) == {}

    with pytest.raises(scheduled_tasks.ScheduleError) as error:
        scheduled_tasks.validate_params(spec, [])

    assert error.value.code == "invalid_params"
