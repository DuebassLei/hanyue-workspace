"""手绘风格库：列表、详情、资源直出与提示词合成。"""

from __future__ import annotations

PREFIX = "/api/v1"


def _login(client, subject="handdraw-user"):
    res = client.post(f"{PREFIX}/auth/login", json={"subject": subject})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def test_meta_requires_auth(client):
    res = client.get(f"{PREFIX}/handdraw/meta")
    assert res.status_code == 401


def test_meta_reports_library_and_attribution(client):
    headers = _login(client, "handdraw-meta")
    res = client.get(f"{PREFIX}/handdraw/meta", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["styleCount"] >= 300
    assert data["colorCount"] >= 30
    assert data["attribution"]["author"] == "yang0"
    assert data["attribution"]["repository"] == "https://github.com/yang0/handraw-style"


def test_list_styles_paginates_and_reports_groups(client):
    headers = _login(client, "handdraw-list")
    res = client.get(f"{PREFIX}/handdraw/styles?page=1&pageSize=8", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert len(data["items"]) == 8
    assert data["total"] >= 300
    assert data["pages"] == -(-data["total"] // 8)
    assert data["groups"]
    first = data["items"][0]
    assert first["number"]
    assert first["generationName"]
    assert first["activationSource"]


def test_list_styles_filters_by_keyword_and_group(client):
    headers = _login(client, "handdraw-filter")
    res = client.get(f"{PREFIX}/handdraw/styles?group=FA", headers=headers)
    assert res.status_code == 200, res.text
    assert all(item["groupKey"] == "FA" for item in res.json()["items"])

    res = client.get(f"{PREFIX}/handdraw/styles?q=FA-001", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["items"][0]["number"] == "FA-001"


def test_style_detail_accepts_legacy_number(client):
    headers = _login(client, "handdraw-detail")
    res = client.get(f"{PREFIX}/handdraw/styles/FA-001", headers=headers)
    assert res.status_code == 200, res.text
    canonical = res.json()
    legacy = canonical["legacyNumber"]
    assert legacy

    res = client.get(f"{PREFIX}/handdraw/styles/{legacy}", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["number"] == canonical["number"]


def test_style_detail_rejects_unknown_number(client):
    headers = _login(client, "handdraw-missing")
    res = client.get(f"{PREFIX}/handdraw/styles/ZZ-999", headers=headers)
    assert res.status_code == 400
    assert res.json()["code"] == "invalid_style"


def test_style_reference_returns_webp(client):
    headers = _login(client, "handdraw-ref")
    res = client.get(f"{PREFIX}/handdraw/styles/FA-001/reference", headers=headers)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "image/webp"
    assert len(res.content) > 0


def test_colors_and_swatch(client):
    headers = _login(client, "handdraw-colors")
    res = client.get(f"{PREFIX}/handdraw/colors", headers=headers)
    assert res.status_code == 200, res.text
    colors = res.json()
    assert len(colors) >= 30
    first = colors[0]
    assert first["id"].startswith("C-")
    assert first["nameZh"]

    res = client.get(f"{PREFIX}/handdraw/colors/{first['id']}/swatch", headers=headers)
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/webp"


def test_compose_chinese_prompt(client):
    headers = _login(client, "handdraw-compose-zh")
    res = client.post(
        f"{PREFIX}/handdraw/compose",
        headers=headers,
        json={"styleNumber": "FA-001", "theme": "春日庭院的猫", "colorId": "C-01"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["language"] == "zh"
    assert data["promptZh"].startswith(f"风格名称：{data['style']['generationName']}。")
    assert "主题：春日庭院的猫。" in data["promptZh"]
    assert "FA-001" not in data["promptZh"]
    assert "C-01" not in data["promptZh"]
    assert data["color"]["id"] == "C-01"
    assert data["style"]["referenceAuthor"] in data["promptZh"]


def test_compose_english_prompt_and_accent_color(client):
    headers = _login(client, "handdraw-compose-en")
    res = client.post(
        f"{PREFIX}/handdraw/compose",
        headers=headers,
        json={
            "styleNumber": "FA-001",
            "theme": "a cat in a spring garden",
            "colorId": "C-01",
            "accentColorId": "C-02",
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["language"] == "en"
    assert data["promptEn"].startswith("Style name: ")
    assert "Theme: a cat in a spring garden." in data["promptEn"]
    assert "primary tone" in data["promptEn"]


def test_compose_unknown_model_falls_back_to_reference(client):
    headers = _login(client, "handdraw-compose-fallback")
    res = client.post(
        f"{PREFIX}/handdraw/compose",
        headers=headers,
        json={"styleNumber": "FA-001", "theme": "春日庭院", "model": "uncalibrated-model-x"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["needReference"] is True
    assert "reference-image" in data["activationSource"]


def test_compose_rejects_empty_theme(client):
    headers = _login(client, "handdraw-compose-empty")
    res = client.post(
        f"{PREFIX}/handdraw/compose",
        headers=headers,
        json={"styleNumber": "FA-001", "theme": "  "},
    )
    assert res.status_code == 400
    assert res.json()["code"] == "invalid_theme"


def test_builtin_reference_attached_only_when_needed(client):
    from app.services import job_runner

    strong = job_runner._builtin_reference("FA-001", "gpt-image-2", 4)
    assert strong is None

    fallback = job_runner._builtin_reference("FA-001", "uncalibrated-model-x", 4)
    assert fallback is not None
    payload, mime, name = fallback
    assert mime == "image/webp"
    assert name == "FA-001.webp"
    assert len(payload) > 0


def test_builtin_reference_fails_loudly(client):
    import pytest

    from app.services import job_runner

    with pytest.raises(job_runner.JobFailure) as excinfo:
        job_runner._builtin_reference("ZZ-999", "gpt-image-2", 4)
    assert excinfo.value.code == "style_not_found"

    with pytest.raises(job_runner.JobFailure) as excinfo:
        job_runner._builtin_reference("FA-001", "uncalibrated-model-x", 0)
    assert excinfo.value.code == "too_many_references"

