"""定时任务：时间语义、CAS 抢占、并发判定、失败自动暂停、对账（docs/scheduled-tasks.md §6）。

原则同其他用例：不等待真实时间、不打外网。调度语义靠直接调服务层函数验证，
APScheduler 装进去的那层在 conftest 里被关掉（SCHEDULER_ENABLED=false）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

PREFIX = "/api/v1"


def _login(client, subject="sched-user"):
    response = client.post(f"{PREFIX}/auth/login", json={"subject": subject})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _db():
    from app.core.db import SessionLocal

    return SessionLocal()


def _task_of(task_id: str):
    from app import models

    db = _db()
    try:
        return db.get(models.ScheduledTask, task_id)
    finally:
        db.close()


@pytest.fixture(autouse=True)
def no_dispatch(monkeypatch):
    """让 job 停在队列里：测试自己决定什么时候执行，避免线程池抢跑。

    必须是 autouse：定时任务投递的是真实的「拉热点」job，一旦被线程池捞起来
    就会去打 7 个外部接口——CI 不该依赖外网可用性。
    """
    from app.core import queue

    monkeypatch.setattr(queue, "dispatch", lambda *a, **k: False)


@pytest.fixture()
def user_task(monkeypatch):
    """注册一个 user 类型的测试任务类型，并在库里建一条任务。

    类型注册表是进程级全局的，用完必须摘掉——否则其他用例的 /types 会多出一项。
    """
    from app import models
    from app.core.db import SessionLocal, init_db
    from app.services import scheduled_tasks as st

    init_db()  # 这条夹具不经过 client，自己保证表已建好
    spec = st.TaskType(
        key="test.noop",
        title="测试任务",
        scope="user",
        queue="hot-topics",
        kind="hot_topics.refresh",
        params_model=st.task_type("hot_topics.refresh").params_model,
        build_payload=st.task_type("hot_topics.refresh").build_payload,
        description="仅测试用",
        default_cron="0 3 * * *",
    )
    st.TASK_TYPES[spec.key] = spec
    db = SessionLocal()
    try:
        # auth_subject 有唯一约束：每个用例用独立 subject，避免互相踩
        user = models.User(auth_subject=f"sched-owner-{uuid4().hex[:10]}", display_name="测试")
        db.add(user)
        db.commit()
        task = models.ScheduledTask(
            scope="user",
            owner_user_id=user.id,
            task_type=spec.key,
            name="测试任务",
            params={},
            schedule_kind="cron",
            cron="0 8 * * *",
            timezone="Asia/Shanghai",
        )
        st.compute_next_run(task)
        db.add(task)
        db.commit()
        db.refresh(task)
        yield task
    finally:
        st.TASK_TYPES.pop(spec.key, None)
        db.close()


# ══════════════════════════════════════════════════════════════════════
# 纯函数：时间语义
# ══════════════════════════════════════════════════════════════════════


def test_cron_and_interval_validation():
    from app.services import scheduled_tasks as st

    trigger = st.build_trigger("cron", "0 8,12,18 * * *", None, "Asia/Shanghai")
    assert trigger is not None

    with pytest.raises(st.ScheduleError) as cron_err:
        st.build_trigger("cron", "99 8 * * *", None, "Asia/Shanghai")
    assert cron_err.value.code == "invalid_cron"

    with pytest.raises(st.ScheduleError) as tz_err:
        st.build_trigger("cron", "0 8 * * *", None, "Mars/Olympus")
    assert tz_err.value.code == "invalid_timezone"

    # 间隔下限：防误配成「每秒拉一次外网」，不静默截断而是明确报错
    with pytest.raises(st.ScheduleError) as small:
        st.build_trigger("interval", "", 30, "UTC")
    assert small.value.code == "interval_too_small"

    with pytest.raises(st.ScheduleError) as large:
        st.build_trigger("interval", "", 8 * 24 * 3600, "UTC")
    assert large.value.code == "interval_too_large"

    assert st.build_trigger("interval", "", 1800, "UTC") is not None


def test_preview_returns_ascending_local_times():
    from app.services import scheduled_tasks as st

    times = st.preview("cron", "0 8,12,18 * * *", None, "Asia/Shanghai", count=3)
    assert len(times) == 3
    parsed = [datetime.fromisoformat(item) for item in times]
    assert parsed == sorted(parsed)
    # 三个时间点都带上 +08:00 偏移，前端按本地时间显示才不会差 8 小时
    assert all(item.endswith("+08:00") for item in times)


def test_next_run_strictly_advances_at_fire_point():
    """回调可能正好在触发点那一毫秒执行：不得算出同一个时间，否则原地自循环。"""
    from app.services import scheduled_tasks as st

    task = type("T", (), {})()
    task.schedule_kind = "cron"
    task.cron = "0 18 * * *"
    task.interval_seconds = None
    task.timezone = "Asia/Shanghai"

    fire_point = datetime(2026, 11, 16, 18, 0, tzinfo=timezone(timedelta(hours=8)))
    for now in (fire_point, fire_point + timedelta(milliseconds=1), fire_point - timedelta(seconds=5)):
        nxt = st.next_run_after(task, fire_point, now)
        assert nxt > fire_point.astimezone(timezone.utc)


def test_params_validation_reports_field():
    from app.services import scheduled_tasks as st

    spec = st.task_type("hot_topics.refresh")
    assert st.validate_params(spec, {}) == {}
    assert st.validate_params(spec, {"platforms": ["微博"]}) == {"platforms": ["微博"]}
    with pytest.raises(st.ScheduleError) as err:
        st.validate_params(spec, {"platforms": "微博"})
    assert err.value.code == "invalid_params"


def test_duplicate_type_registration_fails_loudly():
    """覆盖式注册会让「改 A 影响 B」变得无法排查，所以必须启动即报错。"""
    from app.services import scheduled_tasks as st

    spec = st.TASK_TYPES["hot_topics.refresh"]
    with pytest.raises(RuntimeError):
        st.register(spec)


def test_types_endpoint_exposes_renderable_fields(client):
    headers = _login(client)
    res = client.get(f"{PREFIX}/schedules/types", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    hot = next(item for item in body["items"] if item["key"] == "hot_topics.refresh")
    assert hot["scope"] == "system"
    field = hot["params"]["fields"][0]
    # 前端只认这一层归一化结构，不该自己解 anyOf
    assert field["name"] == "platforms"
    assert field["type"] == "array"
    assert field["items"] == "string"
    assert field["nullable"] is True


# ══════════════════════════════════════════════════════════════════════
# 接口：CRUD / 权限 / 预览
# ══════════════════════════════════════════════════════════════════════


def test_create_system_task_is_seeded_once_and_duplicate_rejected(client):
    headers = _login(client, "sched-seed")
    listing = client.get(f"{PREFIX}/schedules", headers=headers).json()["items"]
    hot = [item for item in listing if item["taskType"] == "hot_topics.refresh"]
    # 登录即播种：拉热点这条 system 任务应当已经存在
    assert len(hot) == 1
    assert hot[0]["scope"] == "system"
    assert hot[0]["scheduleLabel"] == "每天 08:00、12:00、18:00"
    assert hot[0]["nextRunAt"] is not None

    dup = client.post(
        f"{PREFIX}/schedules",
        headers=headers,
        json={"taskType": "hot_topics.refresh", "scheduleKind": "cron", "cron": "0 9 * * *"},
    )
    assert dup.status_code == 409, dup.text
    assert dup.json()["code"] == "schedule_exists"


def test_create_rejects_bad_cron_before_persisting(client):
    headers = _login(client, "sched-bad-cron")
    before = len(client.get(f"{PREFIX}/schedules", headers=headers).json()["items"])
    res = client.post(
        f"{PREFIX}/schedules",
        headers=headers,
        json={"taskType": "hot_topics.refresh", "scheduleKind": "cron", "cron": "bogus"},
    )
    assert res.status_code == 400
    assert res.json()["code"] in {"invalid_cron", "schedule_exists"}
    after = len(client.get(f"{PREFIX}/schedules", headers=headers).json()["items"])
    assert after == before


def test_preview_endpoint(client):
    headers = _login(client, "sched-preview")
    res = client.post(
        f"{PREFIX}/schedules/preview",
        headers=headers,
        json={"scheduleKind": "cron", "cron": "*/30 * * * *", "timezone": "Asia/Shanghai"},
    )
    assert res.status_code == 200, res.text
    assert len(res.json()["times"]) == 3

    bad = client.post(
        f"{PREFIX}/schedules/preview",
        headers=headers,
        json={"scheduleKind": "cron", "cron": "0 99 * * *"},
    )
    assert bad.status_code == 400
    assert bad.json()["code"] == "invalid_cron"


def test_patch_updates_next_run_and_disable_clears_it(client):
    headers = _login(client, "sched-patch")
    listing = client.get(f"{PREFIX}/schedules", headers=headers).json()["items"]
    task_id = next(i["id"] for i in listing if i["taskType"] == "hot_topics.refresh")

    res = client.patch(
        f"{PREFIX}/schedules/{task_id}",
        headers=headers,
        json={"cron": "0 */2 * * *", "timezone": "UTC"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["cron"] == "0 */2 * * *"
    assert res.json()["scheduleLabel"] == "每 2 小时第 00 分"
    assert res.json()["nextRunAt"] is not None

    off = client.patch(f"{PREFIX}/schedules/{task_id}", headers=headers, json={"enabled": False})
    assert off.status_code == 200, off.text
    assert off.json()["enabled"] is False
    assert off.json()["nextRunAt"] is None

    on = client.patch(f"{PREFIX}/schedules/{task_id}", headers=headers, json={"enabled": True})
    assert on.json()["enabled"] is True
    assert on.json()["nextRunAt"] is not None


def test_system_task_read_only_when_configured(client, monkeypatch):
    from app.modules import schedules as schedules_module

    headers = _login(client, "sched-readonly")
    listing = client.get(f"{PREFIX}/schedules", headers=headers).json()["items"]
    task_id = next(i["id"] for i in listing if i["taskType"] == "hot_topics.refresh")

    monkeypatch.setattr(schedules_module, "_system_writable", lambda: False)
    res = client.patch(f"{PREFIX}/schedules/{task_id}", headers=headers, json={"cron": "0 5 * * *"})
    assert res.status_code == 403
    assert res.json()["code"] == "forbidden"
    # 只读时任务本身仍然可见（热点数据是全局共享的）
    assert client.get(f"{PREFIX}/schedules", headers=headers).json()["items"]


def test_system_write_mode_parsing(monkeypatch):
    from app.modules import schedules as schedules_module

    settings = schedules_module.settings
    monkeypatch.setattr(settings, "scheduler_system_write", "any")
    assert schedules_module._system_writable() is True
    monkeypatch.setattr(settings, "scheduler_system_write", "never")
    assert schedules_module._system_writable() is False
    monkeypatch.setattr(settings, "scheduler_system_write", "dev")
    assert schedules_module._system_writable() is (not settings.is_production)


# ══════════════════════════════════════════════════════════════════════
# 触发语义
# ══════════════════════════════════════════════════════════════════════


def test_fire_dispatches_job_and_records_run(user_task, monkeypatch):
    from app import models
    from app.services import scheduled_tasks as st

    result = st.fire(user_task.id)
    assert result["action"] == "dispatched", result

    db = _db()
    try:
        run = db.get(models.ScheduledTaskRun, result["runId"])
        assert run.source == "schedule"
        assert run.status == "queued"
        assert run.job_id
        job = db.get(models.BackgroundJob, run.job_id)
        assert job.queue == "hot-topics"
        assert job.kind == "hot_topics.refresh"
        assert job.payload["scheduledRunId"] == run.id
        # next_run_at 已推进到下一次，界面上的「下次触发」不会停在过去
        task = db.get(models.ScheduledTask, user_task.id)
        assert st.as_utc(task.next_run_at) > st.as_utc(run.triggered_at)
    finally:
        db.close()


def test_cas_rejects_stale_token(user_task):
    """两个副本读到同一个触发点：只有第一个 CAS 成功，第二个必须放弃。"""
    from app.services import scheduled_tasks as st

    db = _db()
    try:
        task = db.get(type(user_task), user_task.id)
        observed = task.next_run_at
        first = st.claim_trigger(db, task.id, observed, st.next_run_after(task, observed))
        second = st.claim_trigger(db, task.id, observed, st.next_run_after(task, observed))
        assert first is True
        assert second is False
    finally:
        db.close()


def test_fire_skips_when_previous_run_still_running(user_task):
    from app import models
    from app.services import scheduled_tasks as st

    db = _db()
    try:
        db.add(
            models.ScheduledTaskRun(
                task_id=user_task.id,
                triggered_at=st.now_utc(),
                source="schedule",
                status="running",
                job_id=None,
            )
        )
        db.commit()
    finally:
        db.close()

    result = st.fire(user_task.id)
    assert result["action"] == "skipped"
    assert "仍在执行" in result["reason"]


def test_zombie_run_does_not_block_forever(user_task):
    """卡在 queued 的 job 超过租约后必须放行，否则任务永远静默跳过。"""
    from app import models
    from app.services import scheduled_tasks as st

    stale_at = st.now_utc() - timedelta(seconds=task_lease() + 120)
    db = _db()
    try:
        job = models.BackgroundJob(
            owner_user_id=user_task.owner_user_id,
            queue="hot-topics",
            kind="hot_topics.refresh",
            payload={},
            status="queued",
        )
        db.add(job)
        db.flush()
        db.add(
            models.ScheduledTaskRun(
                task_id=user_task.id,
                triggered_at=stale_at,
                source="schedule",
                status="queued",
                job_id=job.id,
            )
        )
        # 任务本身的下次触发也回到过去，确保本次 fire 走的是正常路径
        task = db.get(models.ScheduledTask, user_task.id)
        task.next_run_at = st.now_utc() - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    result = st.fire(user_task.id)
    assert result["action"] == "dispatched", result


def task_lease() -> int:
    from app.core.config import get_settings

    return get_settings().job_lease_seconds


def test_consecutive_failures_auto_pause(user_task):
    from app import models
    from app.core.config import get_settings
    from app.services import scheduled_tasks as st

    limit = get_settings().scheduler_max_consecutive_failures
    assert limit > 0  # 这条用例的前提

    db = _db()
    try:
        task = db.get(models.ScheduledTask, user_task.id)
        for _ in range(limit):
            st._apply_task_outcome(db, task, "failed")
            db.commit()
        db.refresh(task)
        assert task.enabled is False
        assert task.consecutive_failures >= limit
        assert "自动暂停" in task.paused_reason
        assert task.next_run_at is None
    finally:
        db.close()


def test_terminal_write_syncs_run(user_task, monkeypatch):
    """job 写终态是唯一入口，runs 的 status 必须跟着走，否则界面永远「执行中」。"""
    from app import models
    from app.services import job_runner, scheduled_tasks as st

    monkeypatch.setattr(
        job_runner.hot_topics,
        "refresh",
        lambda db, **kwargs: {"day": "2026-11-16", "items": 12, "clusters": 5, "notes": []},
    )

    result = st.fire(user_task.id)
    assert result["action"] == "dispatched"
    job_runner.run_job(result["jobId"])

    db = _db()
    try:
        run = db.get(models.ScheduledTaskRun, result["runId"])
        assert run.status == "succeeded"
        assert run.duration_ms is not None
        task = db.get(models.ScheduledTask, user_task.id)
        assert task.last_status == "succeeded"
        assert task.consecutive_failures == 0
    finally:
        db.close()


def test_hot_topics_failure_marks_job_failed(user_task, monkeypatch):
    """全源失败必须是 failed，绝不能变成「跑完了，0 条」的假成功。"""
    from app import models
    from app.services import job_runner, scheduled_tasks as st

    def boom(db, **kwargs):
        raise job_runner.hot_topics.HotTopicsError("all_sources_failed", "全部采集源失败")

    monkeypatch.setattr(job_runner.hot_topics, "refresh", boom)

    result = st.fire(user_task.id)
    job_runner.run_job(result["jobId"])

    db = _db()
    try:
        job = db.get(models.BackgroundJob, result["jobId"])
        assert job.status == "failed"
        assert job.error_code == "all_sources_failed"
        run = db.get(models.ScheduledTaskRun, result["runId"])
        assert run.status == "failed"
        assert run.error_code == "all_sources_failed"
    finally:
        db.close()


def test_manual_run_does_not_consume_schedule_slot(client):
    headers = _login(client, "sched-manual")
    listing = client.get(f"{PREFIX}/schedules", headers=headers).json()["items"]
    task = next(i for i in listing if i["taskType"] == "hot_topics.refresh")

    res = client.post(f"{PREFIX}/schedules/{task['id']}/run", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["action"] == "dispatched"
    assert body["schedule"]["nextRunAt"] == task["nextRunAt"]  # 手动不推进定时槽位

    runs = client.get(f"{PREFIX}/schedules/{task['id']}/runs", headers=headers).json()
    assert runs["items"][0]["source"] == "manual"


def test_runs_pagination_and_status_filter(client):
    headers = _login(client, "sched-runs")
    listing = client.get(f"{PREFIX}/schedules", headers=headers).json()["items"]
    task_id = next(i["id"] for i in listing if i["taskType"] == "hot_topics.refresh")

    for _ in range(3):
        client.post(f"{PREFIX}/schedules/{task_id}/run", headers=headers)

    page = client.get(f"{PREFIX}/schedules/{task_id}/runs?limit=2", headers=headers)
    assert page.status_code == 200
    assert len(page.json()["items"]) == 2
    assert page.json()["nextCursor"]

    only_manual = client.get(
        f"{PREFIX}/schedules/{task_id}/runs?status=skipped", headers=headers
    ).json()
    assert all(item["status"] == "skipped" for item in only_manual["items"])


def test_reconcile_fixes_stuck_run(user_task):
    from app import models
    from app.services import scheduled_tasks as st

    db = _db()
    try:
        job = models.BackgroundJob(
            owner_user_id=user_task.owner_user_id,
            queue="hot-topics",
            kind="hot_topics.refresh",
            payload={},
            status="failed",
            error_code="all_sources_failed",
            error_message="上轮遗留",
        )
        db.add(job)
        db.flush()
        run = models.ScheduledTaskRun(
            task_id=user_task.id,
            triggered_at=st.now_utc() - timedelta(hours=2),
            source="schedule",
            status="running",
            job_id=job.id,
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()

    db = _db()
    try:
        report = st.reconcile_runs(db)
        assert report["fixed"] >= 1
        fixed = db.get(models.ScheduledTaskRun, run_id)
        assert fixed.status == "failed"
        assert fixed.error_code == "all_sources_failed"
    finally:
        db.close()


def test_reconcile_marks_missing_job(user_task):
    from app import models
    from app.services import scheduled_tasks as st

    db = _db()
    try:
        run = models.ScheduledTaskRun(
            task_id=user_task.id,
            triggered_at=st.now_utc() - timedelta(hours=1),
            source="schedule",
            status="queued",
            job_id="ghost-job-id",
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()

    db = _db()
    try:
        report = st.reconcile_runs(db)
        assert report["missing"] >= 1
        fixed = db.get(models.ScheduledTaskRun, run_id)
        assert fixed.status == "failed"
        assert fixed.error_code == "job_missing"
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════
# user 级任务：热点选题简报（界面增删改查的落脚点）
# ══════════════════════════════════════════════════════════════════════


def test_types_endpoint_lists_system_and_user_types(client):
    headers = _login(client, "sched-types-both")
    body = client.get(f"{PREFIX}/schedules/types", headers=headers).json()
    scopes = {item["key"]: item["scope"] for item in body["items"]}
    assert scopes["hot_topics.refresh"] == "system"
    assert scopes["hot_topics.digest"] == "user"

    digest = next(item for item in body["items"] if item["key"] == "hot_topics.digest")
    fields = {field["name"]: field for field in digest["params"]["fields"]}
    # 赛道候选随字段下发，前端不该自己维护一份词表
    assert fields["tracks"]["options"], "赛道路径没有下发给前端"
    assert fields["tracks"]["type"] == "array"
    assert fields["create_drafts"]["type"] == "boolean"
    assert fields["limit"]["default"] == 3


def test_user_task_crud_via_api(client):
    """界面上的「新建 / 编辑 / 删除」走的就是这三条路径。"""
    headers = _login(client, "sched-crud-user")
    created = client.post(
        f"{PREFIX}/schedules",
        headers=headers,
        json={
            "taskType": "hot_topics.digest",
            "name": "AI 赛道早报",
            "params": {"tracks": ["AI 与科技"], "min_score": 70, "limit": 2},
            "scheduleKind": "cron",
            "cron": "30 8 * * *",
            "timezone": "Asia/Shanghai",
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["scope"] == "user"
    assert task["editable"] is True
    assert task["params"] == {"tracks": ["AI 与科技"], "min_score": 70, "limit": 2, "create_drafts": True}
    assert task["scheduleLabel"] == "每天 08:30"
    assert task["nextRunAt"]

    task_id = task["id"]
    listing = client.get(f"{PREFIX}/schedules", headers=headers).json()["items"]
    assert any(item["id"] == task_id for item in listing)

    patched = client.patch(
        f"{PREFIX}/schedules/{task_id}",
        headers=headers,
        json={"name": "AI 赛道早报（改）", "cron": "0 9 * * *", "params": {"tracks": ["AI 与科技"], "limit": 1}},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "AI 赛道早报（改）"
    assert patched.json()["params"]["limit"] == 1
    assert patched.json()["nextRunAt"]

    ran = client.post(f"{PREFIX}/schedules/{task_id}/run", headers=headers)
    assert ran.status_code == 200, ran.text
    assert ran.json()["action"] == "dispatched"

    deleted = client.delete(f"{PREFIX}/schedules/{task_id}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True
    after = client.get(f"{PREFIX}/schedules", headers=headers).json()["items"]
    assert all(item["id"] != task_id for item in after)
    # 账本跟着任务一起删，不留孤儿 run
    assert client.get(f"{PREFIX}/schedules/{task_id}/runs", headers=headers).status_code == 404


def test_user_task_is_invisible_to_other_users(client):
    owner = _login(client, "sched-owner-a")
    other = _login(client, "sched-owner-b")
    created = client.post(
        f"{PREFIX}/schedules",
        headers=owner,
        json={"taskType": "hot_topics.digest", "scheduleKind": "cron", "cron": "0 7 * * *"},
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]

    assert all(
        item["id"] != task_id
        for item in client.get(f"{PREFIX}/schedules", headers=other).json()["items"]
    )
    # 别人的任务连存在性都不暴露
    assert client.patch(f"{PREFIX}/schedules/{task_id}", headers=other, json={"cron": "0 6 * * *"}).status_code == 404
    assert client.delete(f"{PREFIX}/schedules/{task_id}", headers=other).status_code == 404


def test_digest_rejects_unknown_track(client):
    headers = _login(client, "sched-bad-track")
    res = client.post(
        f"{PREFIX}/schedules",
        headers=headers,
        json={
            "taskType": "hot_topics.digest",
            "params": {"tracks": ["不存在的赛道"]},
            "scheduleKind": "cron",
            "cron": "0 8 * * *",
        },
    )
    assert res.status_code == 400, res.text
    assert res.json()["code"] == "invalid_params"
    assert "未知赛道" in res.json()["message"]


def _cluster(db, *, title, track, score, day):
    from app import models

    cluster = models.HotCluster(
        fingerprint=f"fp-{uuid4().hex[:8]}",
        title_norm=title,
        title=title,
        track=track,
        first_seen_day=day,
        last_seen_day=day,
        platforms=["微博"],
        platform_count=1,
        item_count=1,
        score=score,
        grade="S" if score >= 80 else "A",
        score_detail={"total": score},
        status="new",
        captured_at=f"{day} 08:00",
    )
    db.add(cluster)
    db.flush()
    db.add(
        models.HotItem(
            item_uid=f"uid-{uuid4().hex[:10]}",
            day=day,
            platform="微博",
            rank=1,
            title=title,
            title_norm=title,
            url="",
            cluster_id=cluster.id,
        )
    )
    db.commit()
    return cluster


def test_digest_creates_drafts_and_dedupes(monkeypatch):
    from app import models
    from app.core.deps import default_workspace
    from app.services import hot_topics

    # 故意用一个很旧的日期：latest_day 取的是 max(day)，用未来日期会让
    # 其他用例的「最近一天」指到这批造出来的数据上（实测会连挂三条热点用例）
    day = "2000-01-01"
    db = _db()
    try:
        user = models.User(auth_subject=f"digest-{uuid4().hex[:10]}", display_name="简报用户")
        db.add(user)
        db.commit()
        workspace = default_workspace(db, user)

        tech_high = _cluster(db, title="某大模型发布新版本", track="AI 与科技", score=88, day=day)
        tech_low = _cluster(db, title="某厂商小幅更新", track="AI 与科技", score=40, day=day)
        ent = _cluster(db, title="某剧官宣定档", track="娱乐与情感", score=92, day=day)

        monkeypatch.setattr(hot_topics, "latest_day", lambda _db: day)

        first = hot_topics.build_digest(
            db, user, workspace, tracks=["AI 与科技"], min_score=60, limit=5, create_drafts=True
        )
        assert first["day"] == day
        assert [item["clusterId"] for item in first["items"]] == [tech_high.id]
        assert first["draftsCreated"] == 1
        # 低分与别的赛道都不该出现
        assert all(item["clusterId"] not in {tech_low.id, ent.id} for item in first["items"])

        # 简报不碰簇的全局状态：这两列是热点页的事，不是定时任务的事
        for cluster_id in (tech_high.id, tech_low.id, ent.id):
            row = db.get(models.HotCluster, cluster_id)
            assert row.content_item_id is None
            assert row.status == "new"

        draft = db.scalar(select_draft(db, user.id, "某大模型发布新版本"))
        assert draft is not None
        assert draft.workspace_id == workspace.id
        assert draft.current_version_id

        second = hot_topics.build_digest(
            db, user, workspace, tracks=["AI 与科技"], min_score=60, limit=5, create_drafts=True
        )
        assert second["draftsCreated"] == 0
        assert second["draftsReused"] == 1

        # 关掉建稿时只出简报，不新增任何稿件
        before = _draft_count(db, user.id)
        third = hot_topics.build_digest(
            db, user, workspace, tracks=None, min_score=0, limit=10, create_drafts=False
        )
        assert third["draftsCreated"] == 0
        assert _draft_count(db, user.id) == before

        # 没有命中不是错误：今天没有这个赛道的热点是正常结果
        empty = hot_topics.build_digest(
            db, user, workspace, tracks=["财经与商业"], min_score=99, limit=3, create_drafts=True
        )
        assert empty["total"] == 0
    finally:
        db.close()


def select_draft(db, owner_id: str, title: str):
    from app import models
    from sqlalchemy import select

    return (
        select(models.ContentItem)
        .where(
            models.ContentItem.owner_user_id == owner_id,
            models.ContentItem.title == title[:200],
        )
        .limit(1)
    )


def _draft_count(db, owner_id: str) -> int:
    from app import models
    from sqlalchemy import func, select

    return db.scalar(
        select(func.count(models.ContentItem.id)).where(models.ContentItem.owner_user_id == owner_id)
    ) or 0


def test_digest_without_any_hot_data_fails_loudly(monkeypatch):
    from app import models
    from app.core.deps import default_workspace
    from app.services import hot_topics

    db = _db()
    try:
        user = models.User(auth_subject=f"digest-empty-{uuid4().hex[:10]}", display_name="空库用户")
        db.add(user)
        db.commit()
        workspace = default_workspace(db, user)
        monkeypatch.setattr(hot_topics, "latest_day", lambda _db: "")
        with pytest.raises(hot_topics.HotTopicsError) as err:
            hot_topics.build_digest(db, user, workspace)
        assert err.value.code == "no_hot_data"
    finally:
        db.close()


def test_digest_job_wiring(client, monkeypatch):
    """调度投递 → topic-digest 队列 → handler 把参数原样交给 build_digest。"""
    from app.services import job_runner

    captured: dict = {}

    def fake_digest(db, user, workspace, **kwargs):
        captured.update(kwargs)
        captured["owner"] = user.id
        return {"day": "2026-11-17", "total": 0, "draftsCreated": 0, "draftsReused": 0, "items": []}

    monkeypatch.setattr(job_runner.hot_topics, "build_digest", fake_digest)

    headers = _login(client, "sched-digest-wire")
    created = client.post(
        f"{PREFIX}/schedules",
        headers=headers,
        json={
            "taskType": "hot_topics.digest",
            "params": {"tracks": ["AI 与科技"], "min_score": 65, "limit": 2, "create_drafts": False},
            "scheduleKind": "cron",
            "cron": "0 7 * * *",
        },
    )
    assert created.status_code == 201, created.text

    ran = client.post(f"{PREFIX}/schedules/{created.json()['id']}/run", headers=headers)
    assert ran.status_code == 200, ran.text
    job_id = ran.json()["jobId"]

    job_runner.run_job(job_id)

    assert captured["tracks"] == ["AI 与科技"]
    assert captured["min_score"] == 65
    assert captured["limit"] == 2
    assert captured["create_drafts"] is False

    # 历史里要能看出「这次产出了什么」，只知道成功没有意义
    runs = client.get(f"{PREFIX}/schedules/{created.json()['id']}/runs", headers=headers).json()
    latest = runs["items"][0]
    assert latest["status"] == "succeeded"
    assert latest["resultSummary"] == "挑出 0 条 · 新建草稿 0 篇"


# ══════════════════════════════════════════════════════════════════════
# 通用脚本任务：manifest 声明驱动 + 脚本执行链路
# ══════════════════════════════════════════════════════════════════════


def test_script_type_is_manifest_driven_and_runnable(client):
    """manifest 里声明的脚本类型应自动出现在类型清单，且能走完整执行链路。

    这验证了「加一种定时任务 = 一个脚本 + 一条 manifest」的核心承诺：
    类型清单字段来自声明、参数被校验、脚本 function 被执行、摘要回写进历史。
    """
    from app.services import job_runner, scheduled_tasks as st

    assert "script.health_check" in st.TASK_TYPES

    headers = _login(client, "sched-script-run")
    types = client.get(f"{PREFIX}/schedules/types", headers=headers).json()["items"]
    script_type = next(t for t in types if t["key"] == "script.health_check")
    field_names = [f["name"] for f in script_type["params"]["fields"]]
    assert field_names == ["label", "verbose"]

    created = client.post(
        f"{PREFIX}/schedules",
        headers=headers,
        json={
            "taskType": "script.health_check",
            "name": "每周巡检",
            "params": {"label": "核心链路", "verbose": True},
            "scheduleKind": "cron",
            "cron": "0 9 * * 1",
        },
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]

    ran = client.post(f"{PREFIX}/schedules/{task_id}/run", headers=headers)
    assert ran.status_code == 200, ran.text
    job = _db().get(__import__("app").models.BackgroundJob, ran.json()["jobId"])
    assert job.queue == "script"
    assert job.kind == "script.health_check"

    job_runner.run_job(job.id)

    runs = client.get(f"{PREFIX}/schedules/{task_id}/runs", headers=headers).json()
    latest = runs["items"][0]
    assert latest["status"] == "succeeded"
    assert latest["resultSummary"] == "核心链路 · 正常"
