"""统一提示词库：全局只读、管理员维护、新字段 CRUD 与 AI 沉淀。"""

from __future__ import annotations

PREFIX = "/api/v1"


def _login(client, subject, role="superadmin"):
    from tests.conftest import login

    return login(client, subject=subject, role=role)


def _create(client, headers, **overrides):
    body = {
        "title": "水彩窗台橘猫",
        "category": "插画",
        "content": "一只橘猫坐在木质窗台上晒太阳，水彩插画风格，柔和光线",
        "tags": ["水彩", " 插画 ", "水彩", ""],
    }
    body.update(overrides)
    res = client.post(f"{PREFIX}/prompts", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def test_prompt_crud_with_all_new_fields(client):
    admin = _login(client, "prompts-crud")
    prompt = _create(
        client,
        admin,
        promptType="cover",
        negativeContent="低清, 水印",
        summary="橘猫封面",
        sortOrder=7,
        enabled=False,
        previewAssetIds=["asset-1", "asset-2"],
        source="manual",
    )
    assert prompt["title"] == "水彩窗台橘猫"
    assert prompt["category"] == "插画"
    assert prompt["tags"] == ["水彩", "插画"]  # 空白与重复标签清洗
    assert prompt["promptType"] == "cover"
    assert prompt["negativeContent"] == "低清, 水印"
    assert prompt["summary"] == "橘猫封面"
    assert prompt["sortOrder"] == 7
    assert prompt["enabled"] is False
    assert prompt["previewAssetIds"] == ["asset-1", "asset-2"]
    assert prompt["source"] == "manual"

    listed = client.get(f"{PREFIX}/prompts", headers=admin)
    assert prompt["id"] in [item["id"] for item in listed.json()]

    patched = client.patch(
        f"{PREFIX}/prompts/{prompt['id']}",
        json={
            "title": "窗台橘猫（改）",
            "category": "",
            "tags": ["治愈"],
            "promptType": "illustration",
            "negativeContent": "模糊",
            "summary": "改后摘要",
            "sortOrder": 3,
            "enabled": True,
            "previewAssetIds": ["asset-3"],
        },
        headers=admin,
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["title"] == "窗台橘猫（改）"
    assert body["category"] == "通用"  # 空分类回落默认
    assert body["tags"] == ["治愈"]
    assert body["promptType"] == "illustration"
    assert body["negativeContent"] == "模糊"
    assert body["summary"] == "改后摘要"
    assert body["sortOrder"] == 3
    assert body["enabled"] is True
    assert body["previewAssetIds"] == ["asset-3"]

    assert client.delete(f"{PREFIX}/prompts/{prompt['id']}", headers=admin).status_code == 204
    assert prompt["id"] not in [
        item["id"] for item in client.get(f"{PREFIX}/prompts", headers=admin).json()
    ]


def test_all_members_read_all_prompts_but_only_admin_manages(client):
    admin = _login(client, "prompts-admin-2")
    member = _login(client, "prompts-member-2", role="member")
    prompt = _create(client, admin, title="管理员建的全局提示词")

    # 全员可读，不再按 owner 隔离
    listed = client.get(f"{PREFIX}/prompts", headers=member)
    assert listed.status_code == 200, listed.text
    assert prompt["id"] in [item["id"] for item in listed.json()]

    # 增删改要求 image_styles.manage
    assert client.post(
        f"{PREFIX}/prompts", json={"title": "越权建", "content": "x"}, headers=member
    ).status_code == 403
    assert client.patch(
        f"{PREFIX}/prompts/{prompt['id']}", json={"title": "越权改"}, headers=member
    ).status_code == 403
    assert client.delete(f"{PREFIX}/prompts/{prompt['id']}", headers=member).status_code == 403

    assert client.patch(
        f"{PREFIX}/prompts/missing", json={"title": "不存在"}, headers=admin
    ).status_code == 404
    assert client.delete(f"{PREFIX}/prompts/missing", headers=admin).status_code == 404


def test_prompt_type_and_enabled_filters(client):
    admin = _login(client, "prompts-filter")
    normal = _create(client, admin, title="普通")
    cover = _create(client, admin, title="封面", promptType="cover")
    disabled = _create(client, admin, title="停用配图", promptType="illustration", enabled=False)

    def ids(**params):
        res = client.get(f"{PREFIX}/prompts", params=params, headers=admin)
        assert res.status_code == 200, res.text
        return {item["id"] for item in res.json()}

    all_ids = ids()
    assert {normal["id"], cover["id"], disabled["id"]} <= all_ids
    assert cover["id"] in ids(promptType="cover")
    assert normal["id"] in ids(promptType="normal")
    assert disabled["id"] in ids(promptType="illustration")
    assert disabled["id"] not in ids(promptType="normal")
    assert {normal["id"], cover["id"]} <= ids(enabled="true")
    assert disabled["id"] in ids(enabled="false")
    assert disabled["id"] not in ids(enabled="true")

    bad = client.get(f"{PREFIX}/prompts", params={"promptType": "poster"}, headers=admin)
    assert bad.status_code == 400
    assert bad.json()["code"] == "invalid_prompt_type"


def test_deposit_ai_draft_prompts_is_idempotent(client):
    admin = _login(client, "prompts-deposit")
    body = {
        "title": "AI 成文标题",
        "coverPrompt": "科幻城市夜景封面",
        "inlinePrompts": ["街道特写", "  ", "人物近景"],
    }
    first = client.post(f"{PREFIX}/prompts/from-draft", json=body, headers=admin)
    assert first.status_code == 201, first.text
    rows = first.json()
    assert [row["promptType"] for row in rows] == ["cover", "illustration", "illustration"]
    assert all(row["source"] == "ai" for row in rows)
    assert [row["content"] for row in rows] == [
        "科幻城市夜景封面", "街道特写", "人物近景"
    ]

    again = client.post(f"{PREFIX}/prompts/from-draft", json=body, headers=admin)
    assert again.status_code == 201, again.text
    assert again.json() == []  # 同标题重复存入不产生副本
    ai_rows = [
        item for item in client.get(f"{PREFIX}/prompts", headers=admin).json()
        if item["source"] == "ai"
    ]
    assert len(ai_rows) == 3
    assert {item["id"] for item in ai_rows} == {item["id"] for item in rows}

    member = _login(client, "prompts-deposit-member", role="member")
    assert client.post(f"{PREFIX}/prompts/from-draft", json=body, headers=member).status_code == 403


def test_prompt_rejects_empty_content(client):
    headers = _login(client, "prompts-invalid")
    blank = client.post(
        f"{PREFIX}/prompts", json={"title": "空内容", "content": "   "}, headers=headers
    )
    assert blank.status_code == 400
    assert blank.json()["code"] == "invalid_prompt"

    missing = client.post(
        f"{PREFIX}/prompts", json={"title": "缺内容", "content": ""}, headers=headers
    )
    assert missing.status_code == 422


def test_prompt_requires_auth(client):
    assert client.get(f"{PREFIX}/prompts").status_code == 401
    assert client.post(f"{PREFIX}/prompts", json={"title": "x", "content": "y"}).status_code == 401
