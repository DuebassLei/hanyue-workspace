"""热点采集挂成通用定时脚本任务：provisioning 服务的幂等语义。

这里只测「建得对、重复跑不重复建、不覆盖人调过的东西」——采集本身在
test_hot_topics.py，脚本契约在 test_script_examples.py，都不重复。
"""

from __future__ import annotations

import uuid

import pytest


def _user(db, *, with_password: bool = False):
    from app import models
    from app.core.security import hash_password

    user = models.User(
        auth_subject=f"hot-sched-{uuid.uuid4().hex[:8]}",
        display_name="hot-sched",
        timezone="Asia/Shanghai",
        is_active=True,
    )
    if with_password:
        user.email = f"{uuid.uuid4().hex[:8]}@example.com"
        user.password_hash = hash_password("Test-passw0rd!")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def session(client):
    """依赖 client：建库与应用启动都由它负责（本用例只操作表，不建应用）。"""
    from app.core.db import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_ensure_creates_published_script_and_task(session):
    import hashlib

    from app import models
    from app.resources.script_examples import HOT_BOARD_REFRESH
    from app.services import hot_schedule

    owner = _user(session)
    result = hot_schedule.ensure_hot_schedule(session, owner=owner)

    assert result["scriptCreated"] is True
    assert result["taskCreated"] is True
    assert result["published"] is True
    assert result["publishedVersion"] == 1
    assert result["cron"] == hot_schedule.DEFAULT_CRON
    assert result["enabled"] is True
    assert result["nextRunAt"], "启用的任务必须算好下次触发时间"

    script = session.get(models.ScheduledScript, result["scriptId"])
    assert script.published_code == HOT_BOARD_REFRESH
    assert script.timeout_seconds >= hot_schedule.DEFAULT_TIMEOUT_SECONDS
    assert script.published_hash == hashlib.sha256(
        HOT_BOARD_REFRESH.encode("utf-8")
    ).hexdigest()

    # 执行只认快照：版本行必须存在且与 published_hash 一致，否则投递时报 snapshot_missing
    version = session.query(models.ScheduledScriptVersion).filter_by(
        script_id=script.id, version=script.published_version
    ).one()
    assert version.code == HOT_BOARD_REFRESH

    task = session.get(models.ScheduledTask, result["taskId"])
    assert task.task_type == f"user-script.{script.id}"
    assert task.scope == "system"
    assert task.owner_user_id == owner.id


def test_ensure_is_idempotent(session):
    from app import models
    from app.services import hot_schedule

    owner = _user(session)
    first = hot_schedule.ensure_hot_schedule(session, owner=owner)
    second = hot_schedule.ensure_hot_schedule(session, owner=owner)

    assert second["scriptId"] == first["scriptId"]
    assert second["taskId"] == first["taskId"]
    assert second["scriptCreated"] is False
    assert second["taskCreated"] is False
    assert second["published"] is False, "代码没变不该再发版本（版本号是快照定位键）"
    assert second["publishedVersion"] == 1

    tasks = session.query(models.ScheduledTask).filter_by(
        task_type=f"user-script.{first['scriptId']}"
    ).all()
    assert len(tasks) == 1


def test_ensure_republishes_only_when_code_changes(session):
    from app.services import hot_schedule

    owner = _user(session)
    first = hot_schedule.ensure_hot_schedule(session, owner=owner)

    changed = hot_schedule.ensure_hot_schedule(
        session, owner=owner, code=hot_schedule.HOT_BOARD_REFRESH + "\n# 改过一版\n"
    )
    assert changed["published"] is True
    assert changed["publishedVersion"] == first["publishedVersion"] + 1

    forced = hot_schedule.ensure_hot_schedule(session, owner=owner, force_publish=True)
    assert forced["published"] is True
    assert forced["publishedVersion"] == changed["publishedVersion"] + 1


def test_existing_schedule_is_not_overwritten(session):
    """人调过的时间不能被重复执行冲掉；显式传 cron 才改。"""
    from app import models
    from app.services import hot_schedule

    owner = _user(session)
    first = hot_schedule.ensure_hot_schedule(session, owner=owner, cron="0 9 * * *")
    assert first["cron"] == "0 9 * * *"

    # 用户自己把时间改成分 15 一次，再跑一次 provisioning
    task = session.get(models.ScheduledTask, first["taskId"])
    task.cron = "*/15 * * * *"
    task.params = {"platforms": ["微博", "知乎"]}
    session.commit()

    again = hot_schedule.ensure_hot_schedule(session, owner=owner)
    session.refresh(task)
    assert again["cron"] == "*/15 * * * *"
    assert task.params == {"platforms": ["微博", "知乎"]}

    # 显式指定才覆盖
    updated = hot_schedule.ensure_hot_schedule(session, owner=owner, cron="30 7-21 * * *")
    session.refresh(task)
    assert updated["cron"] == "30 7-21 * * *"
    assert task.cron == "30 7-21 * * *"
    assert models.ScheduledTask.__tablename__ == "scheduled_tasks"


def test_disabled_task_is_not_reenabled(session):
    from app import models
    from app.services import hot_schedule

    owner = _user(session)
    first = hot_schedule.ensure_hot_schedule(session, owner=owner)
    task = session.get(models.ScheduledTask, first["taskId"])
    task.enabled = False
    task.next_run_at = None
    session.commit()

    again = hot_schedule.ensure_hot_schedule(session, owner=owner)
    session.refresh(task)
    assert again["enabled"] is False
    assert task.enabled is False
    assert task.next_run_at is None


def test_default_owner_prefers_password_account(session):
    """脚本要挂在一个真实账号下：有密码的（人登录的）优先，其次是老账号。"""
    from app.services import hot_schedule

    _user(session, with_password=True)
    owner = hot_schedule.default_owner(session)
    assert owner.password_hash, "有密码账号时应优先选它，而不是开发态账号"
