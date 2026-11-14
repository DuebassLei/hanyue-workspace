from app.core.db import SessionLocal
from app import models

import pytest


def login(client, subject):
    response = client.post("/api/v1/auth/login", json={"subject": subject, "displayName": subject})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def create_project(client, headers):
    response = client.post("/api/v1/storyboards", json={"title": "夜行列车"}, headers=headers)
    assert response.status_code == 201
    return response.json()


@pytest.fixture(autouse=True)
def no_auto_dispatch(monkeypatch):
    from app.core import queue

    monkeypatch.setattr(queue, "dispatch", lambda job_id, **kwargs: True)


def test_storyboard_trash_soft_delete_restore_and_permanent_delete(client):
    headers = login(client, "storyboard-trash")
    project = create_project(client, headers)
    scene = client.post(
        f"/api/v1/storyboards/{project['id']}/scenes", json={"title": "站台"}, headers=headers,
    ).json()
    shot = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots", json={"frameDescription": "雨夜"}, headers=headers,
    ).json()

    assert client.delete(f"/api/v1/storyboards/shots/{shot['id']}", headers=headers).status_code == 204
    assert client.get(f"/api/v1/storyboards/{project['id']}", headers=headers).json()["scenes"][0]["shots"] == []
    trash = client.get("/api/v1/storyboards/trash", headers=headers).json()
    assert [item["id"] for item in trash["shots"]] == [shot["id"]]

    assert client.post(f"/api/v1/storyboards/shots/{shot['id']}/restore", headers=headers).status_code == 204
    restored = client.get(f"/api/v1/storyboards/{project['id']}", headers=headers).json()
    assert restored["scenes"][0]["shots"][0]["id"] == shot["id"]

    assert client.delete(f"/api/v1/storyboards/{project['id']}", headers=headers).status_code == 204
    assert client.get("/api/v1/storyboards", headers=headers).json() == []
    assert client.post(f"/api/v1/storyboards/shots/{shot['id']}/restore", headers=headers).status_code == 409
    assert client.post(f"/api/v1/storyboards/scenes/{scene['id']}/restore", headers=headers).status_code == 409
    assert client.post(f"/api/v1/storyboards/{project['id']}/restore", headers=headers).status_code == 204
    assert client.get(f"/api/v1/storyboards/{project['id']}", headers=headers).status_code == 200

    assert client.delete(f"/api/v1/storyboards/{project['id']}/permanent", headers=headers).status_code == 409
    assert client.delete(f"/api/v1/storyboards/{project['id']}", headers=headers).status_code == 204
    assert client.delete(f"/api/v1/storyboards/{project['id']}/permanent", headers=headers).status_code == 204
    with SessionLocal() as db:
        assert db.get(models.StoryboardProject, project["id"]) is None
        assert db.get(models.StoryboardScene, scene["id"]) is None
        assert db.get(models.StoryboardShot, shot["id"]) is None


def test_deleted_storyboard_job_cannot_write_back(client):
    from app.services import job_runner

    headers = login(client, "storyboard-trash-job")
    project = create_project(client, headers)
    scene = client.post(
        f"/api/v1/storyboards/{project['id']}/scenes", json={"title": "站台"}, headers=headers,
    ).json()
    shot = client.post(
        f"/api/v1/storyboards/scenes/{scene['id']}/shots", json={"frameDescription": "雨夜"}, headers=headers,
    ).json()
    queued = client.post(f"/api/v1/storyboards/shots/{shot['id']}/generate-sketch", json={}, headers=headers)
    assert queued.status_code == 202
    assert client.delete(f"/api/v1/storyboards/shots/{shot['id']}", headers=headers).status_code == 204

    with SessionLocal() as db:
        job = db.get(models.BackgroundJob, queued.json()["jobId"])
        job.status = "succeeded"
        job.result = {"assetIds": ["asset-after-delete"]}
        db.commit()
        job_runner._apply_storyboard_success(db, job, ["asset-after-delete"], job.payload or {})
        row = db.get(models.StoryboardShot, shot["id"])
        assert row.sketch_asset_id is None
        assert row.sketch_status == "queued"
