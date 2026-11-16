"""视频创作（图文成片）：项目 CRUD、分镜解析、渲染失败路径与降级口径。"""

import io
import json

import pytest
from PIL import Image

from .conftest import login


def _image_asset(client, headers, name="pic.png"):
    buf = io.BytesIO()
    Image.new("RGB", (64, 96), (20, 90, 60)).save(buf, format="PNG")
    res = client.post(
        "/api/v1/assets",
        headers=headers,
        files={"file": (name, buf.getvalue(), "image/png")},
    )
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


def _create_project(client, headers, **extra):
    body = {"title": "测试视频", "sourceText": "第一句话讲开场。" * 8, **extra}
    res = client.post("/api/v1/videos", headers=headers, json=body)
    assert res.status_code == 201, res.text
    return res.json()


# ---------- parse_script ----------

def test_parse_script_normalizes():
    from app.services import video_script

    raw = json.dumps(
        {
            "title": "十分钟看懂",
            "shots": [
                {"narration": "开场白", "imagePrompt": "城市夜景", "motion": "推"},
                {"narration": "第二句", "imagePrompt": "", "motion": "PAN_LEFT"},
                {"narration": "  ", "imagePrompt": "空镜头", "motion": "weird"},
            ],
        },
        ensure_ascii=False,
    )
    result = video_script.parse_script(raw)
    assert result["title"] == "十分钟看懂"
    assert len(result["shots"]) == 2  # 空旁白镜头被拦下
    assert result["shots"][0]["motion"] == "zoom_in"
    assert result["shots"][1]["motion"] == "pan_left"


def test_parse_script_rejects_garbage():
    from app.services import video_script

    with pytest.raises(video_script.ScriptError):
        video_script.parse_script("这不是JSON")
    with pytest.raises(video_script.ScriptError):
        video_script.parse_script('{"shots": []}')


# ---------- tts_provider ----------

def test_tts_voice_and_rate_validation():
    from app.services import tts_provider

    assert tts_provider.normalize_voice(None) == tts_provider.DEFAULT_VOICE
    assert tts_provider.normalize_voice("zh-CN-YunxiNeural") == "zh-CN-YunxiNeural"
    with pytest.raises(tts_provider.TTSUnavailable):
        tts_provider.normalize_voice("'; rm -rf /")
    assert tts_provider.normalize_rate("-20%") == "-20%"
    with pytest.raises(tts_provider.TTSUnavailable):
        tts_provider.normalize_rate("fast")


def test_tts_empty_input_fails_before_network():
    from app.services import tts_provider

    with pytest.raises(tts_provider.TTSUnavailable) as exc:
        tts_provider.synthesize("   ")
    assert exc.value.code == "empty_input"


def test_tts_status_available():
    from app.services import tts_provider

    st = tts_provider.status()
    assert st["available"] is True


# ---------- API：项目与镜头 ----------

def test_video_project_crud_and_shots(client):
    headers = login(client, "video-crud")
    project = _create_project(client, headers)
    pid = project["id"]
    assert project["settings"]["aspectRatio"] == "9:16"

    # 非法画幅直接 422/400
    res = client.patch(
        f"/api/v1/videos/{pid}", headers=headers,
        json={"settings": {"aspectRatio": "4:5"}},
    )
    assert res.status_code in (400, 422)

    res = client.post(
        f"/api/v1/videos/{pid}/shots", headers=headers,
        json={"narration": "镜头一", "imagePrompt": "山间晨雾", "motion": "zoom_out"},
    )
    assert res.status_code == 201, res.text
    shot_id = res.json()["id"]

    res = client.patch(
        f"/api/v1/videos/shots/{shot_id}", headers=headers,
        json={"narration": "改过的旁白"},
    )
    assert res.status_code == 200
    assert res.json()["narration"] == "改过的旁白"

    # 指定图片素材
    asset_id = _image_asset(client, headers)
    res = client.patch(
        f"/api/v1/videos/shots/{shot_id}", headers=headers,
        json={"imageAssetId": asset_id},
    )
    assert res.status_code == 200
    assert res.json()["imageAssetId"] == asset_id
    assert res.json()["imageUrl"]

    # 排序
    res2 = client.post(
        f"/api/v1/videos/{pid}/shots", headers=headers,
        json={"narration": "镜头二", "imagePrompt": "海边日落"},
    )
    second_id = res2.json()["id"]
    res = client.post(
        f"/api/v1/videos/{pid}/shots/reorder", headers=headers,
        json=[second_id, shot_id],
    )
    assert res.status_code == 200
    assert [s["id"] for s in res.json()["shots"]] == [second_id, shot_id]

    # 排序列表不完整 → 明确拒绝
    res = client.post(f"/api/v1/videos/{pid}/shots/reorder", headers=headers, json=[second_id])
    assert res.status_code == 400
    assert res.json()["code"] == "invalid_order"

    # 删除镜头后 sort_order 连续
    res = client.delete(f"/api/v1/videos/shots/{second_id}", headers=headers)
    assert res.status_code == 204
    detail = client.get(f"/api/v1/videos/{pid}", headers=headers).json()
    assert [s["sortOrder"] for s in detail["shots"]] == [0]

    # 软删项目
    assert client.delete(f"/api/v1/videos/{pid}", headers=headers).status_code == 204
    assert client.get(f"/api/v1/videos/{pid}", headers=headers).status_code == 404


def test_video_create_from_content_item(client):
    headers = login(client, "video-content")
    created = client.post(
        "/api/v1/content", headers=headers, json={"title": "稿件转视频"}
    )
    assert created.status_code in (200, 201), created.text
    content_id = created.json()["id"]
    version = client.post(
        f"/api/v1/content/{content_id}/versions", headers=headers,
        json={"title": "稿件转视频", "body": "这是一篇完整的稿件正文，用来转视频。"},
    )
    assert version.status_code == 201, version.text
    res = client.post(
        "/api/v1/videos", headers=headers,
        json={"contentItemId": content_id},
    )
    assert res.status_code == 201, res.text
    assert "稿件正文" in res.json()["sourceText"]

    empty = client.post("/api/v1/videos", headers=headers, json={})
    assert empty.status_code == 400
    assert empty.json()["code"] == "empty_source"


def test_video_render_requires_shots_and_ffmpeg(client, monkeypatch):
    headers = login(client, "video-render-guard")
    project = _create_project(client, headers)
    pid = project["id"]

    # 无镜头 → 明确拒绝
    res = client.post(f"/api/v1/videos/{pid}/render", headers=headers, json={})
    assert res.status_code == 400
    assert res.json()["code"] == "no_shots"

    client.post(
        f"/api/v1/videos/{pid}/shots", headers=headers,
        json={"narration": "只有一句旁白", "imagePrompt": "画面"},
    )

    # ffmpeg 缺失 → 明确报错不排队
    from app.services import video_render

    def missing():
        raise video_render.RenderError("ffmpeg_missing", "未安装")

    monkeypatch.setattr(video_render, "ffmpeg_bin", missing)
    res = client.post(f"/api/v1/videos/{pid}/render", headers=headers, json={})
    assert res.status_code == 400
    assert res.json()["code"] == "ffmpeg_missing"


def test_video_handler_no_provider_fails_explicitly(client, monkeypatch):
    """生图供应商不可用时任务明确 failed，指明镜头，不产出残缺片。"""
    headers = login(client, "video-handler")
    project = _create_project(client, headers)
    pid = project["id"]
    client.post(
        f"/api/v1/videos/{pid}/shots", headers=headers,
        json={"narration": "第一个镜头的旁白", "imagePrompt": "画面一"},
    )

    from app import models
    from app.core import queue as queue_module
    from app.services import image_provider, job_runner

    monkeypatch.setattr(queue_module, "dispatch", lambda job_id, **kw: False)
    res = client.post(f"/api/v1/videos/{pid}/render", headers=headers, json={})
    if res.status_code != 202:
        # 本机没有 ffmpeg 时跳过执行链路断言
        assert res.json()["code"] == "ffmpeg_missing"
        pytest.skip("本机无 ffmpeg，跳过执行链路")
    job_id = res.json()["jobId"]

    def no_image(*args, **kwargs):
        raise image_provider.ImageProviderUnavailable(
            "image_provider_unavailable", "未接入图像生成供应商"
        )

    monkeypatch.setattr(image_provider, "generate_images", no_image)

    job_runner.run_job(job_id)

    from app.core.db import SessionLocal

    session = SessionLocal()
    try:
        job = session.get(models.BackgroundJob, job_id)
        assert job.status == "failed"
        assert job.error_code == "image_provider_unavailable"
        assert "第 1 个镜头" in (job.error_message or "")
        project_row = session.get(models.VideoProject, pid)
        assert project_row.render_asset_id is None
    finally:
        session.close()


def test_video_breakdown_without_model(client, monkeypatch):
    headers = login(client, "video-breakdown")
    project = _create_project(client, headers)
    res = client.post(f"/api/v1/videos/{project['id']}/breakdown", headers=headers, json={})
    assert res.status_code == 400
    assert res.json()["code"] in ("provider_unavailable", "model_selection_invalid")


def test_video_provider_status_shape(client):
    headers = login(client, "video-status")
    res = client.get("/api/v1/video-provider/status", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["tts"]["available"] is True
    assert "ffmpeg" in data and "images" in data
