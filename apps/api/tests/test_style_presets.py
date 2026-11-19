"""地基契约：统一风格注册表（服务层 / 只读路由）与新增 schema 字段序列化。"""

from __future__ import annotations

from datetime import datetime, timezone

from app import schemas
from app.services import style_presets

PREFIX = "/api/v1"

_EXPECTED_IDS = {
    "manga", "realistic", "guoman", "watercolor",
    "humanist_photo", "minimal_tech", "flat_illustration",
    "handdraw_note", "info_card", "retro_nostalgia",
}


def test_registry_has_ten_builtin_presets():
    rows = style_presets.list_presets()
    assert {row["id"] for row in rows} == _EXPECTED_IDS
    assert len(rows) == len(_EXPECTED_IDS)
    # 角色 4 档的中文风格词取自 character_images.STYLE_WORDS
    manga = style_presets.get_preset("manga")
    assert manga is not None
    assert manga.prompt_suffix == "日系漫画风格，干净线条，赛璐璐上色"
    # 贴图档的 prompt_suffix 取自 sticker_styles.STYLES
    info_card = style_presets.get_preset("info_card")
    assert info_card is not None
    assert "infographic card layout" in info_card.prompt_suffix


def test_list_presets_filters_by_applicable_to():
    comics = style_presets.list_presets("comic_panel")
    assert {row["id"] for row in comics} == {"manga", "realistic", "guoman", "watercolor"}
    assert style_presets.list_presets("unknown_scene") == []
    assert style_presets.get_preset("does-not-exist") is None


def test_style_presets_route_is_readonly_and_authenticated(client):
    from tests.conftest import login

    headers = login(client, "preset-reader", role="member")

    res = client.get(f"{PREFIX}/style-presets", headers=headers)
    assert res.status_code == 200, res.text
    rows = res.json()
    assert len(rows) == 10
    # CamelModel 的别名序列化
    assert rows[0]["promptSuffix"]
    assert isinstance(rows[0]["applicableTo"], list)

    filtered = client.get(
        f"{PREFIX}/style-presets",
        params={"applicableTo": "comic_panel"},
        headers=headers,
    )
    assert filtered.status_code == 200
    assert len(filtered.json()) == 4

    # 任何登录用户可读，未登录不可读；也没有写接口
    assert client.get(f"{PREFIX}/style-presets").status_code == 401
    assert client.post(f"{PREFIX}/style-presets", json={}, headers=headers).status_code == 405


def test_new_schema_fields_serialize_to_camel_case():
    img = schemas.ImageGenerationIn(
        prompt="a cat", negativePrompt="blurry", seed=12345
    )
    dumped = img.dump()
    assert dumped["negativePrompt"] == "blurry"
    assert dumped["seed"] == 12345

    now = datetime.now(timezone.utc)
    out = schemas.PromptTemplateOut(
        id="p1", title="t", category="通用", content="正向",
        tags=[], promptType="cover", negativeContent="低清晰度",
        summary="摘要", sortOrder=2, enabled=False,
        previewAssetIds=["a1"], source="ai",
        createdAt=now, updatedAt=now,
    )
    dumped = out.dump()
    assert dumped["promptType"] == "cover"
    assert dumped["negativeContent"] == "低清晰度"
    assert dumped["previewAssetIds"] == ["a1"]
    assert dumped["source"] == "ai"
    assert dumped["createdAt"] == now

    project = schemas.StoryboardProjectCreate(
        styleConfig={"style_id": "manga", "seed": 42}
    )
    assert project.dump()["styleConfig"]["style_id"] == "manga"
    assert schemas.StoryboardProjectPatch(styleConfig={}).style_config == {}
    assert schemas.StoryboardProjectOut.model_fields["style_config"].default is None


def test_prompt_template_new_columns_round_trip(client):
    """ORM 新列与 Out schema 对齐：库里读回的默认值能正常序列化。"""
    from sqlalchemy import select

    from app import models
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        row = models.PromptTemplate(
            owner_user_id=None,  # 新记录为全局
            title="全局提示词",
            category="通用",
            content="正向词",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        assert row.prompt_type == "normal"
        assert row.source == "manual"
        assert row.enabled is True
        assert row.preview_asset_ids == []
        assert row.owner_user_id is None
        out = schemas.PromptTemplateOut.model_validate(row, from_attributes=True)
        assert out.dump()["promptType"] == "normal"
        assert out.dump()["source"] == "manual"
        db.delete(db.scalar(select(models.PromptTemplate).where(models.PromptTemplate.id == row.id)))
        db.commit()


def test_request_batch_negative_and_seed_capability_fallback(monkeypatch):
    """能力探测：默认走文本兜底且如实标注；显式支持时才放独立字段。"""
    import httpx

    from app.services import image_provider

    captured: list[dict] = []

    def fake_request(method, url, **kwargs):
        captured.append(kwargs)
        return httpx.Response(200, json={"data": []}, request=httpx.Request(method, url))

    monkeypatch.setattr(image_provider.ssrf, "safe_request", fake_request)
    provider = {"apiKey": "k", "baseUrl": "http://example.test", "modelCode": "m"}

    # 不支持（默认）：负向词拼进 prompt 尾部，seed 忽略，二者都标注。
    meta: dict = {}
    image_provider._request_batch(
        provider, prompt="a cat", size="1024x1024", batch=1,
        reference_images=None, negative_prompt="blurry", seed=42, merge_meta=meta,
    )
    body = captured[-1]["json_body"]
    assert body["prompt"] == "a cat, avoid: blurry"
    assert "negative_prompt" not in body and "seed" not in body
    assert meta == {"negative_prompt_merged": True, "seed_ignored": True}

    # 显式支持：独立字段透传，prompt 不被改写。
    supported = {
        **provider,
        image_provider.CAPABILITY_NEGATIVE_PROMPT: True,
        image_provider.CAPABILITY_SEED: True,
    }
    meta2: dict = {}
    image_provider._request_batch(
        supported, prompt="a cat", size="1024x1024", batch=1,
        reference_images=None, negative_prompt="blurry", seed=42, merge_meta=meta2,
    )
    body2 = captured[-1]["json_body"]
    assert body2["prompt"] == "a cat"
    assert body2["negative_prompt"] == "blurry"
    assert body2["seed"] == 42
    assert meta2 == {"seed_applied": True}

    # 不传新参数时请求体与改动前一致（无多余键）。
    image_provider._request_batch(
        provider, prompt="a cat", size="1024x1024", batch=1, reference_images=None
    )
    assert set(captured[-1]["json_body"]) == {
        "model", "prompt", "n", "size", "response_format"
    }
