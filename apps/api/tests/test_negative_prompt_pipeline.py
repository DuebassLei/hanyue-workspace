"""负向词 / seed 从 HTTP 入队到任务 payload 的贯通（地基 §5 最后一环）。"""

from __future__ import annotations

PREFIX = "/api/v1"


def test_negative_prompt_and_seed_are_enqueued(client):
    from app import models
    from app.core.db import SessionLocal
    from tests.conftest import login

    headers = login(client, subject="negative-pipeline")
    res = client.post(
        f"{PREFIX}/ai/image-generations",
        json={
            "prompt": "一只猫",
            "negativePrompt": "低清, 水印",
            "seed": 12345,
            "count": 1,
        },
        headers=headers,
    )
    assert res.status_code == 202, res.text
    job_id = res.json()["jobId"]

    # 直接读任务行：无论本地线程池是否已把任务跑失败，入队 payload 都应保留这两个键。
    with SessionLocal() as db:
        job = db.get(models.BackgroundJob, job_id)
        assert job is not None
        assert job.payload["negativePrompt"] == "低清, 水印"
        assert job.payload["seed"] == 12345


def test_negative_prompt_and_seed_default_to_none(client):
    from app import models
    from app.core.db import SessionLocal
    from tests.conftest import login

    headers = login(client, subject="negative-pipeline-default")
    res = client.post(
        f"{PREFIX}/ai/image-generations", json={"prompt": "一只猫", "count": 1}, headers=headers
    )
    assert res.status_code == 202, res.text
    with SessionLocal() as db:
        job = db.get(models.BackgroundJob, res.json()["jobId"])
        assert job is not None
        assert job.payload["negativePrompt"] is None
        assert job.payload["seed"] is None
