"""图像处理回归：水印真实生效、options 明确报错、输出格式一致、条漫分格多图产出。

顶层不导入 app 包，避免 pytest 收集阶段提前绑定 core.db 的 engine。
"""

from __future__ import annotations

import io
import time

import pytest
from PIL import Image, ImageDraw

PREFIX = "/api/v1"


@pytest.fixture
def svc():
    from app.services import image_service

    return image_service


def _solid_bytes(color="white") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (200, 100), color).save(buf, format="PNG")
    return buf.getvalue()


def _comic_bytes() -> bytes:
    """三个色块 + 两段白色间隔的条漫，期望切出 3 格。"""
    buf = io.BytesIO()
    image = Image.new("RGB", (200, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 180, 80), fill=(200, 50, 50))
    draw.rectangle((20, 110, 180, 170), fill=(50, 200, 50))
    draw.rectangle((20, 200, 180, 260), fill=(50, 50, 200))
    image.save(buf, format="PNG")
    return buf.getvalue()


def _login(client, subject):
    res = client.post(f"{PREFIX}/auth/login", json={"subject": subject})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _wait_job(client, headers, job_id, timeout=10.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"{PREFIX}/jobs/{job_id}", headers=headers).json()
        if last["status"] in ("succeeded", "failed", "cancelled"):
            return last
        time.sleep(0.1)
    return last


def test_watermark_actually_draws_text(svc):
    outputs = svc.apply_operation(
        _solid_bytes(), "watermark", {"text": "TEST-WM", "format": "png"}
    )
    assert len(outputs) == 1
    result = Image.open(io.BytesIO(outputs[0][0])).convert("RGB")
    bottom = result.crop((0, 60, 200, 100)).tobytes()
    assert bottom != b"\xff" * len(bottom), "水印仍是空操作"


def test_unknown_output_format_is_rejected_not_silently_rewritten(svc):
    with pytest.raises(svc.ImageValidationError, match="不支持的输出格式"):
        svc.apply_operation(_solid_bytes(), "convert", {"format": "gif"})
    output = svc.apply_operation(_solid_bytes(), "convert", {"format": "png"})
    assert output[0][1] == "image/png"


def test_invalid_options_are_validation_errors(svc):
    cases = [
        ("thumbnail", {"maxSize": "abc"}),
        ("resize", {"width": "not-a-number"}),
        ("crop", {"aspectRatio": "0:1"}),
        ("split_panels", {"minGap": "oops"}),
    ]
    for operation, options in cases:
        with pytest.raises(svc.ImageValidationError):
            svc.apply_operation(_solid_bytes(), operation, options)


def test_split_panels_produces_multiple_real_images(svc):
    outputs = svc.apply_operation(
        _comic_bytes(), "split_panels", {"minGap": 6, "format": "png"}
    )
    assert len(outputs) == 3, f"应切出 3 格，实际 {len(outputs)}"
    for data, mime, size in outputs:
        assert mime == "image/png"
        panel = Image.open(io.BytesIO(data))
        panel.load()
        assert panel.size == size
        assert panel.height > 10


def test_split_panels_without_gutters_fails_explicitly(svc):
    with pytest.raises(svc.ImageValidationError, match="未检测到"):
        svc.apply_operation(_solid_bytes(), "split_panels", {"minGap": 4})


def test_async_split_panels_persists_every_panel(client):
    headers = _login(client, "panels-user")
    asset = client.post(
        f"{PREFIX}/assets",
        files={"file": ("comic.png", _comic_bytes(), "image/png")},
        headers=headers,
    ).json()
    res = client.post(
        f"{PREFIX}/image-jobs/async",
        json={
            "assetId": asset["id"],
            "operation": "split_panels",
            "options": {"minGap": 6, "format": "png"},
        },
        headers=headers,
    )
    assert res.status_code == 202, res.text
    job = _wait_job(client, headers, res.json()["jobId"])
    assert job["status"] == "succeeded", job
    assert len(job["result"]["assetIds"]) == 3
    assert job["result"]["panelCount"] == 3
