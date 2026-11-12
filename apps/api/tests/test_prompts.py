"""提示词库接口：CRUD、标签清洗与按用户隔离。"""

from __future__ import annotations

PREFIX = "/api/v1"


def _login(client, subject="prompts-user"):
    res = client.post(f"{PREFIX}/auth/login", json={"subject": subject})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def test_prompt_crud(client):
    headers = _login(client, "prompts-crud")

    created = client.post(
        f"{PREFIX}/prompts",
        json={
            "title": "水彩窗台橘猫",
            "category": "插画",
            "content": "一只橘猫坐在木质窗台上晒太阳，水彩插画风格，柔和光线",
            "tags": ["水彩", " 插画 ", "水彩", ""],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    prompt = created.json()
    assert prompt["title"] == "水彩窗台橘猫"
    assert prompt["category"] == "插画"
    # 空白与重复标签会被清洗
    assert prompt["tags"] == ["水彩", "插画"]

    listed = client.get(f"{PREFIX}/prompts", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [prompt["id"]]

    patched = client.patch(
        f"{PREFIX}/prompts/{prompt['id']}",
        json={"title": "窗台橘猫（改）", "category": "", "tags": ["治愈"]},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["title"] == "窗台橘猫（改）"
    # 分类留空回落到默认分类，不会出现空分类
    assert patched.json()["category"] == "通用"
    assert patched.json()["tags"] == ["治愈"]

    assert client.delete(f"{PREFIX}/prompts/{prompt['id']}", headers=headers).status_code == 204
    assert client.get(f"{PREFIX}/prompts", headers=headers).json() == []


def test_prompt_owner_isolation(client):
    owner = _login(client, "prompts-owner")
    other = _login(client, "prompts-other")

    created = client.post(
        f"{PREFIX}/prompts",
        json={"title": "私有提示词", "content": "只对创建者可见"},
        headers=owner,
    )
    assert created.status_code == 201, created.text
    prompt_id = created.json()["id"]

    assert client.get(f"{PREFIX}/prompts", headers=other).json() == []
    assert (
        client.patch(
            f"{PREFIX}/prompts/{prompt_id}", json={"title": "被改名"}, headers=other
        ).status_code
        == 404
    )
    assert client.delete(f"{PREFIX}/prompts/{prompt_id}", headers=other).status_code == 404
    assert len(client.get(f"{PREFIX}/prompts", headers=owner).json()) == 1


def test_prompt_rejects_empty_content(client):
    headers = _login(client, "prompts-invalid")

    blank = client.post(
        f"{PREFIX}/prompts",
        json={"title": "空内容", "content": "   "},
        headers=headers,
    )
    assert blank.status_code == 400
    assert blank.json()["code"] == "invalid_prompt"

    missing = client.post(
        f"{PREFIX}/prompts",
        json={"title": "缺内容", "content": ""},
        headers=headers,
    )
    assert missing.status_code == 422


def test_prompt_requires_auth(client):
    assert client.get(f"{PREFIX}/prompts").status_code == 401
    assert client.post(f"{PREFIX}/prompts", json={"title": "x", "content": "y"}).status_code == 401
