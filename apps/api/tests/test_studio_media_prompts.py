"""成稿台的封面/插图提示词建议、素材选图，以及排期与内容日历的联动。

覆盖三类行为：
- AI 拟提示词：模型输出要被整成可用条目（去重、裁长、段号对齐回真实段落）；
- 从素材图库选图：不触发生图，图直接进正文；
- 排期：成稿台排的期要出现在内容日历里，取消排期也要一起撤掉。
"""

import io
import json

import pytest
from PIL import Image

from tests.conftest import login

MARKDOWN = """# 测试标题

第一段正文写得足够长，用来给封面提示词提供素材，也用来验证插图插在段落之后的位置是否正确。

第二段继续展开，说明这件事为什么值得做，以及做完之后读者能得到什么具体的好处。

第三段收尾。
"""


# ---------------- 辅助 ----------------


def _article(client, subject: str):
    headers = login(client, subject=subject)
    response = client.post(
        "/api/v1/studio/articles/new",
        json={"topic": "测试选题", "material": "素材", "markdown": MARKDOWN, "titles": False},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return headers, response.json()


def _upload(client, headers, name: str = "pick.png") -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (24, 24), (30, 80, 120)).save(buffer, format="PNG")
    buffer.seek(0)
    response = client.post(
        "/api/v1/assets", files={"file": (name, buffer, "image/png")}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _fake_model(monkeypatch, payload: dict):
    """替身模型网关：回固定 JSON，并记录收到的 system/prompt。"""
    from app.services import ai_gateway

    seen: dict = {}

    async def fake_generate_text(db, **kwargs):
        seen.update(kwargs)
        return {
            "text": json.dumps(payload, ensure_ascii=False),
            "modelCode": "test-double",
            "provider": "test-double",
            "errorCode": None,
        }

    monkeypatch.setattr(ai_gateway, "generate_text", fake_generate_text)
    return seen


# ---------------- 提示词建议 ----------------


def test_cover_suggestions_are_normalized(client, monkeypatch):
    headers, article = _article(client, "prompt-cover")
    seen = _fake_model(
        monkeypatch,
        {
            "prompts": [
                {"prompt": "俯拍桌面上的笔记与咖啡", "note": "写实感"},
                {"prompt": "俯拍桌面上的笔记与咖啡", "note": "重复一条，应当去重"},
                {"prompt": "  扁平插画风格的抽象色块  ", "note": ""},
                {"prompt": "x" * 900},
            ]
        },
    )

    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/media/prompt-suggestions",
        json={"scope": "cover", "count": 3},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    prompts = [item["prompt"] for item in items]
    # 重复的提示词被去掉；条数受 count 限制
    assert prompts[0] == "俯拍桌面上的笔记与咖啡"
    assert prompts.count("俯拍桌面上的笔记与咖啡") == 1
    assert prompts[1] == "扁平插画风格的抽象色块"
    assert len(items) == 3
    assert items[0]["note"] == "写实感"
    # 提示词有长度上限，前端输入框也是这个上限
    assert all(len(item["prompt"]) <= 600 for item in items)
    assert len(items[2]["prompt"]) == 600
    # 确实把标题与内容概要喂给了模型
    assert seen["task_type"] == "studio_media_prompt"
    assert "测试标题" in seen["prompt"]


def test_inline_suggestions_for_one_block_bind_to_that_block(client, monkeypatch):
    headers, article = _article(client, "prompt-inline-one")
    blocks = article["blocks"]
    target = blocks[1]["id"]
    _fake_model(
        monkeypatch,
        {"prompts": [{"prompt": "一张说明流程的示意插画", "note": "对应流程那一句"}]},
    )

    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/media/prompt-suggestions",
        json={"scope": "inline", "blockId": target, "count": 2},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["blockId"] == target
    assert items[0]["blockNumber"] == 2


def test_inline_suggestions_map_block_numbers_and_drop_out_of_range(client, monkeypatch):
    headers, article = _article(client, "prompt-inline-all")
    blocks = article["blocks"]
    _fake_model(
        monkeypatch,
        {
            "prompts": [
                {"block": 1, "prompt": "配图一", "note": ""},
                {"block": 99, "prompt": "越界的段号，应当丢掉", "note": ""},
                {"block": 2, "prompt": "配图二", "note": ""},
            ]
        },
    )

    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/media/prompt-suggestions",
        json={"scope": "inline", "count": 4},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["blockNumber"] for item in items] == [1, 2]
    assert items[0]["blockId"] == blocks[0]["id"]
    assert items[1]["blockId"] == blocks[1]["id"]


def test_prompt_suggestion_rejects_unknown_scope(client):
    headers, article = _article(client, "prompt-bad-scope")
    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/media/prompt-suggestions",
        json={"scope": "whatever"},
        headers=headers,
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "invalid_scope"


def test_prompt_suggestion_fails_loudly_without_model(client, monkeypatch):
    """模型没配时必须明确报错，不能回一份空数组当「没有建议」。"""
    from app.services import ai_gateway

    headers, article = _article(client, "prompt-no-model")

    async def fake_generate_text(db, **kwargs):
        return {"text": "", "errorCode": "provider_unavailable"}

    monkeypatch.setattr(ai_gateway, "generate_text", fake_generate_text)
    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/media/prompt-suggestions",
        json={"scope": "cover"},
        headers=headers,
    )
    assert response.status_code == 502, response.text
    body = response.json()
    assert body["code"] == "prompt_suggest_failed"
    assert "模型管理" in body["message"]


# ---------------- 素材选图 ----------------


def test_pick_inline_inserts_asset_after_block(client):
    headers, article = _article(client, "inline-pick")
    asset_id = _upload(client, headers)
    blocks = article["blocks"]
    target = blocks[0]["id"]

    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/inline/pick",
        json={"assetId": asset_id, "blockId": target},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()

    diagrams = body["inlineDiagrams"]
    assert len(diagrams) == 1
    assert diagrams[0]["status"] == "ready"
    assert diagrams[0]["assetId"] == asset_id
    assert diagrams[0]["blockId"] == target

    # 正文里紧跟着目标段落插了一个 media 块
    texts = [block["text"] for block in body["blocks"]]
    index = next(i for i, block in enumerate(body["blocks"]) if block["id"] == target)
    assert body["blocks"][index + 1]["kind"] == "media"
    assert asset_id in texts[index + 1]


def test_pick_inline_rejects_unknown_block_or_asset(client):
    headers, article = _article(client, "inline-pick-bad")
    asset_id = _upload(client, headers)

    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/inline/pick",
        json={"assetId": asset_id, "blockId": "not-a-block"},
        headers=headers,
    )
    assert response.status_code == 404, response.text

    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/inline/pick",
        json={"assetId": "not-an-asset", "blockId": article["blocks"][0]["id"]},
        headers=headers,
    )
    assert response.status_code == 404, response.text


def test_pick_inline_rejects_other_users_asset(client):
    headers, article = _article(client, "inline-pick-owner")
    other_headers, _other = _article(client, "inline-pick-other")
    foreign_asset = _upload(client, other_headers)

    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/inline/pick",
        json={"assetId": foreign_asset, "blockId": article["blocks"][0]["id"]},
        headers=headers,
    )
    assert response.status_code == 404, response.text


# ---------------- 排期 ↔ 内容日历 ----------------


def test_schedule_shows_up_in_content_calendar(client):
    headers, article = _article(client, "schedule-calendar")
    when = "2031-03-05T08:30:00+00:00"

    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/schedule",
        json={"at": when},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["scheduledFor"]

    calendar = client.get("/api/v1/calendar", headers=headers).json()
    mine = [entry for entry in calendar if entry["studioArticleId"] == article["id"]]
    assert len(mine) == 1
    assert mine[0]["title"] == article["title"]
    assert mine[0]["status"] == "planned"


def test_reschedule_keeps_single_calendar_entry(client):
    headers, article = _article(client, "schedule-reschedule")
    for when in ("2031-03-05T08:30:00+00:00", "2031-03-06T09:00:00+00:00"):
        client.post(
            f"/api/v1/studio/articles/{article['id']}/schedule",
            json={"at": when},
            headers=headers,
        )

    calendar = client.get("/api/v1/calendar", headers=headers).json()
    mine = [entry for entry in calendar if entry["studioArticleId"] == article["id"]]
    assert len(mine) == 1
    assert mine[0]["plannedAt"].startswith("2031-03-06")


def test_clear_schedule_removes_calendar_entry(client):
    headers, article = _article(client, "schedule-clear")
    client.post(
        f"/api/v1/studio/articles/{article['id']}/schedule",
        json={"at": "2031-03-05T08:30:00+00:00"},
        headers=headers,
    )
    client.post(
        f"/api/v1/studio/articles/{article['id']}/schedule",
        json={"at": None},
        headers=headers,
    )

    calendar = client.get("/api/v1/calendar", headers=headers).json()
    assert not [entry for entry in calendar if entry["studioArticleId"] == article["id"]]


def test_manual_calendar_entry_is_not_removed_by_studio_schedule(client):
    """日历里手排的条目没有 studioArticleId，成稿台排期不该把它带走。"""
    headers, article = _article(client, "schedule-manual")
    created = client.post(
        "/api/v1/calendar",
        json={"title": "手排的一期", "plannedAt": "2031-04-01T08:00:00+00:00"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    manual_id = created.json()["id"]

    client.post(
        f"/api/v1/studio/articles/{article['id']}/schedule",
        json={"at": "2031-03-05T08:30:00+00:00"},
        headers=headers,
    )
    client.post(
        f"/api/v1/studio/articles/{article['id']}/schedule",
        json={"at": None},
        headers=headers,
    )

    calendar = client.get("/api/v1/calendar", headers=headers).json()
    assert any(entry["id"] == manual_id for entry in calendar)


def test_calendar_entry_of_other_user_is_untouched(client):
    headers, article = _article(client, "schedule-isolate")
    other_headers, _other = _article(client, "schedule-isolate-other")
    client.post(
        f"/api/v1/studio/articles/{article['id']}/schedule",
        json={"at": "2031-03-05T08:30:00+00:00"},
        headers=headers,
    )

    calendar = client.get("/api/v1/calendar", headers=other_headers).json()
    assert not [entry for entry in calendar if entry.get("studioArticleId") == article["id"]]


@pytest.mark.parametrize("scope", ["cover", "inline"])
def test_prompt_suggestion_route_requires_ownership(client, scope):
    headers, article = _article(client, f"suggest-owner-{scope}")
    other_headers, _other = _article(client, f"suggest-other-{scope}")
    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/media/prompt-suggestions",
        json={"scope": scope},
        headers=other_headers,
    )
    assert response.status_code == 404, response.text


# ---------------- 排版预览里的图片 ----------------


def test_resolve_asset_refs_replaces_asset_protocol():
    from app.services import studio_render

    out = studio_render.resolve_asset_refs("![](asset://abc123) 正文", "http://127.0.0.1:8000/")
    assert "asset://" not in out
    assert "http://127.0.0.1:8000/api/v1/assets/abc123/raw?" in out
    # 正文里没有 asset:// 时原样返回
    assert studio_render.resolve_asset_refs("普通正文", "http://x/") == "普通正文"


def test_render_preview_shows_inline_image_instead_of_broken_placeholder(client):
    """稿纸里的插图在排版预览里也要看得见：asset:// 只是内部记号，要换成直链。"""
    headers, article = _article(client, "render-inline-image")
    asset_id = _upload(client, headers, "inline.png")
    blocks = article["blocks"]
    client.post(
        f"/api/v1/studio/articles/{article['id']}/inline/pick",
        json={"assetId": asset_id, "blockId": blocks[0]["id"]},
        headers=headers,
    )

    response = client.post(
        f"/api/v1/studio/articles/{article['id']}/render", json={}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    html = body["html"]
    assert "图片地址无效" not in html
    assert "asset://" not in html
    assert f"/api/v1/assets/{asset_id}/raw?" in html
