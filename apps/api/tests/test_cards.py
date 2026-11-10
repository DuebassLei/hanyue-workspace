"""图文卡片接口：分页模式、画幅尺寸与自定义模板归属隔离。"""

from __future__ import annotations

PREFIX = "/api/v1"


def _login(client, subject="cards-user"):
    res = client.post(f"{PREFIX}/auth/login", json={"subject": subject})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _paginate(client, headers, **payload):
    res = client.post(f"{PREFIX}/cards/paginate", json=payload, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def test_manual_hr_splits_one_card_per_segment(client):
    headers = _login(client, "cards-hr")
    body = _paginate(
        client,
        headers,
        markdown="# 第一张\n\n正文 A\n\n---\n\n## 第二张\n\n正文 B\n\n---\n\n## 第三张\n\n正文 C",
        paginationMode="manual-hr",
    )
    assert body["status"] == "ok"
    assert body["totalCards"] == 3
    assert [card["index"] for card in body["cards"]] == [1, 2, 3]
    assert all(card["total"] == 3 for card in body["cards"])
    assert body["cards"][0]["isCover"] is True
    assert body["renderSpec"]["canvasWidth"] == 360
    assert body["renderSpec"]["exportWidth"] == 1080


def test_by_h2_breaks_at_heading(client):
    headers = _login(client, "cards-h2")
    body = _paginate(
        client,
        headers,
        markdown="# 总标题\n\n导语\n\n## 第一节\n\n内容一\n\n## 第二节\n\n内容二",
        paginationMode="by-h2",
    )
    assert body["totalCards"] == 3
    assert body["cards"][0]["blocks"][0]["type"] == "h1"
    assert body["cards"][1]["blocks"][0]["type"] == "h2"


def test_smart_auto_flows_overflow_blocks(client):
    headers = _login(client, "cards-smart")
    paragraph = "这是一段用于撑满卡片高度的长文本，" * 6
    markdown = "\n\n".join([f"## 段落 {i}\n\n{paragraph}" for i in range(6)])
    body = _paginate(client, headers, markdown=markdown, paginationMode="smart-auto")
    assert body["totalCards"] >= 2
    for card in body["cards"]:
        assert card["blocks"], "每张卡片都应有内容块"
        assert card["maxAvailableHeight"] >= 160


def test_empty_markdown_returns_placeholder(client):
    headers = _login(client, "cards-empty")
    body = _paginate(client, headers, markdown="   ")
    assert body["totalCards"] == 1
    assert body["cards"][0]["blocks"][0]["id"] == "empty-placeholder"


def test_custom_canvas_size(client):
    headers = _login(client, "cards-size")
    body = _paginate(
        client,
        headers,
        markdown="# 自定义画幅",
        presetId="square-1-1",
        customWidth=420,
        customHeight=520,
    )
    spec = body["renderSpec"]
    assert spec["canvasWidth"] == 420
    assert spec["canvasHeight"] == 520
    assert spec["exportWidth"] == 1260
    assert spec["exportHeight"] == 1560


def test_paginate_rejects_unknown_mode_and_bad_size(client):
    headers = _login(client, "cards-invalid")
    bad_mode = client.post(
        f"{PREFIX}/cards/paginate",
        json={"markdown": "# x", "paginationMode": "unknown"},
        headers=headers,
    )
    assert bad_mode.status_code == 422

    bad_size = client.post(
        f"{PREFIX}/cards/paginate",
        json={"markdown": "# x", "customWidth": 10},
        headers=headers,
    )
    assert bad_size.status_code == 422


def test_requires_auth(client):
    assert client.post(f"{PREFIX}/cards/paginate", json={"markdown": "# x"}).status_code == 401


def test_custom_template_crud_and_isolation(client):
    owner = _login(client, "cards-owner")
    other = _login(client, "cards-other")

    created = client.post(
        f"{PREFIX}/cards/templates",
        json={
            "name": "我的暗色模板",
            "subtitle": "含底纹 · swiss-grid",
            "config": {"bgColorHex": "#101010", "patternType": "grid", "paddingX": 32},
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    template = created.json()
    assert template["name"] == "我的暗色模板"
    assert template["config"]["paddingX"] == 32

    listed = client.get(f"{PREFIX}/cards/templates", headers=owner)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [template["id"]]

    # 其他用户既看不到，也不能改删
    assert client.get(f"{PREFIX}/cards/templates", headers=other).json() == []
    assert (
        client.patch(
            f"{PREFIX}/cards/templates/{template['id']}",
            json={"name": "被改名"},
            headers=other,
        ).status_code
        == 404
    )
    assert (
        client.delete(f"{PREFIX}/cards/templates/{template['id']}", headers=other).status_code
        == 404
    )

    patched = client.patch(
        f"{PREFIX}/cards/templates/{template['id']}",
        json={"name": "改名后的模板"},
        headers=owner,
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "改名后的模板"
    assert patched.json()["config"]["bgColorHex"] == "#101010"

    assert (
        client.delete(f"{PREFIX}/cards/templates/{template['id']}", headers=owner).status_code
        == 204
    )
    assert client.get(f"{PREFIX}/cards/templates", headers=owner).json() == []


def test_presets_endpoint_matches_contract(client):
    headers = _login(client, "cards-presets")
    body = client.get(f"{PREFIX}/cards/presets", headers=headers).json()
    assert [preset["id"] for preset in body["presets"]] == [
        "xhs-3-4",
        "douyin-9-16",
        "square-1-1",
        "landscape-4-3",
    ]
    assert len(body["templates"]) == 8
