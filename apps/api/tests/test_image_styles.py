"""风格库：启用列表、管理 CRUD，以及稿件锁定基础风格快照。"""

from __future__ import annotations

PREFIX = "/api/v1"


def _login(client, subject, role="superadmin"):
    from tests.conftest import login

    return login(client, subject=subject, role=role)


def _style(client, headers, **overrides):
    body = {
        "name": "水彩",
        "purpose": "base",
        "prompt": "水彩插画，柔和光线",
        "enabled": True,
    }
    body.update(overrides)
    res = client.post(f"{PREFIX}/image-styles", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _content(client, headers, title="稿件"):
    res = client.post(f"{PREFIX}/content", json={"title": title}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _prompt_tpl(title, content, *, prompt_type="base", enabled=True):
    """直接落一条统一提示词模板。

    风格库已并入 prompt_templates，稿件锁定基础风格读的是这张表；这里不走
    /prompts 写接口（其类型字段由需求 02 的 C 负责），避免本用例依赖尚未落地的代码。
    """
    from app import models
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        tpl = models.PromptTemplate(
            owner_user_id=None,
            title=title,
            content=content,
            prompt_type=prompt_type,
            enabled=enabled,
            source="manual",
        )
        db.add(tpl)
        db.commit()
        return tpl.id


def test_member_sees_only_enabled_and_cannot_include_disabled(client):
    admin = _login(client, "styles-admin")
    member = _login(client, "styles-member", role="member")
    _style(client, admin, name="启用", prompt="启用提示词", sortOrder=1)
    _style(client, admin, name="停用", prompt="停用提示词", enabled=False, sortOrder=0)

    listed = client.get(f"{PREFIX}/image-styles", params={"purpose": "base"}, headers=member)
    assert listed.status_code == 200, listed.text
    names = [item["name"] for item in listed.json()]
    assert names == ["启用"]
    assert "prompt" in listed.json()[0]

    denied = client.get(
        f"{PREFIX}/image-styles",
        params={"purpose": "base", "includeDisabled": "1"},
        headers=member,
    )
    assert denied.status_code == 403

    # 非字面量 "1" 不算，普通用户仍只看见启用项
    ignored = client.get(
        f"{PREFIX}/image-styles",
        params={"purpose": "base", "includeDisabled": "true"},
        headers=member,
    )
    assert ignored.status_code == 200
    assert [item["name"] for item in ignored.json()] == ["启用"]


def test_invalid_purpose_is_400(client):
    headers = _login(client, "styles-purpose")
    res = client.get(f"{PREFIX}/image-styles", params={"purpose": "poster"}, headers=headers)
    assert res.status_code == 400
    assert res.json()["code"] == "invalid_purpose"


def test_admin_crud_and_include_disabled(client):
    headers = _login(client, "styles-crud")
    created = _style(client, headers, name="封面风", purpose="cover", prompt="封面提示词", sortOrder=2)
    assert created["purpose"] == "cover"
    assert created["enabled"] is True
    assert created["summary"] == ""

    disabled = client.patch(
        f"{PREFIX}/image-styles/{created['id']}",
        json={"enabled": False, "summary": "封面用"},
        headers=headers,
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False
    assert disabled.json()["summary"] == "封面用"

    hidden = client.get(f"{PREFIX}/image-styles", params={"purpose": "cover"}, headers=headers)
    # 停用项不出现；列表里允许存在启动播种的出厂封面风格
    assert created["id"] not in [item["id"] for item in hidden.json()]
    shown = client.get(
        f"{PREFIX}/image-styles",
        params={"purpose": "cover", "includeDisabled": "1"},
        headers=headers,
    )
    assert shown.status_code == 200
    assert created["id"] in [item["id"] for item in shown.json()]

    missing = client.patch(
        f"{PREFIX}/image-styles/missing", json={"name": "不存在"}, headers=headers
    )
    assert missing.status_code == 404

    deleted = client.delete(f"{PREFIX}/image-styles/{created['id']}", headers=headers)
    assert deleted.status_code == 204
    assert (
        client.delete(f"{PREFIX}/image-styles/{created['id']}", headers=headers).status_code
        == 404
    )


def test_delete_marks_snapshot_deleted_but_keeps_prompt(client):
    headers = _login(client, "styles-snapshot")
    style_id = _prompt_tpl("锁定风", "必须保留的原文")
    item = _content(client, headers)

    locked = client.patch(
        f"{PREFIX}/content/{item['id']}/image-style",
        json={"styleId": style_id},
        headers=headers,
    )
    assert locked.status_code == 200, locked.text
    body = locked.json()
    assert body["baseStyleId"] == style_id
    assert body["baseStyleName"] == "锁定风"
    assert body["baseStylePrompt"] == "必须保留的原文"
    assert body["baseStyleDeleted"] is False

    # 改提示词不回写已有稿件快照
    from sqlalchemy import update

    from app import models
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        db.execute(
            update(models.PromptTemplate)
            .where(models.PromptTemplate.id == style_id)
            .values(title="改名后", content="新的提示词")
        )
        db.commit()
    still = client.get(f"{PREFIX}/content/{item['id']}", headers=headers)
    assert still.json()["item"]["baseStyleName"] == "锁定风"
    assert still.json()["item"]["baseStylePrompt"] == "必须保留的原文"

    # 提示词删除时由删除侧标记快照失效（与 image_styles 删除同语义），文本仍保留
    with SessionLocal() as db:
        db.execute(
            update(models.ContentItem)
            .where(models.ContentItem.base_style_id == style_id)
            .values(base_style_deleted=True)
        )
        db.commit()
    after = client.get(f"{PREFIX}/content/{item['id']}", headers=headers)
    snap = after.json()["item"]
    assert snap["baseStyleDeleted"] is True
    assert snap["baseStylePrompt"] == "必须保留的原文"
    assert snap["baseStyleName"] == "锁定风"

    listed = client.get(f"{PREFIX}/content", params={"fields": "list"}, headers=headers)
    row = next(item_row for item_row in listed.json() if item_row["id"] == item["id"])
    assert row["baseStyleDeleted"] is True
    assert row["baseStylePrompt"] == "必须保留的原文"


def test_lock_clears_and_rejects_bad_styles(client):
    owner = _login(client, "styles-lock-owner")
    other = _login(client, "styles-lock-other")
    base_id = _prompt_tpl("基础", "基础提示词")
    cover_id = _prompt_tpl("封面", "封面提示词", prompt_type="cover")
    off_id = _prompt_tpl("停用基础", "停用提示词", enabled=False)
    item = _content(client, owner)
    foreign = _content(client, other, title="别人的")

    cleared = client.patch(
        f"{PREFIX}/content/{item['id']}/image-style",
        json={"styleId": None},
        headers=owner,
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["baseStyleId"] is None
    assert cleared.json()["baseStyleName"] == ""
    assert cleared.json()["baseStylePrompt"] == ""
    assert cleared.json()["baseStyleDeleted"] is False

    not_base = client.patch(
        f"{PREFIX}/content/{item['id']}/image-style",
        json={"styleId": cover_id},
        headers=owner,
    )
    assert not_base.status_code == 400
    assert not_base.json()["code"] == "style_not_base"

    disabled = client.patch(
        f"{PREFIX}/content/{item['id']}/image-style",
        json={"styleId": off_id},
        headers=owner,
    )
    assert disabled.status_code == 400
    assert disabled.json()["code"] == "style_disabled"

    missing = client.patch(
        f"{PREFIX}/content/{item['id']}/image-style",
        json={"styleId": "no-such-style"},
        headers=owner,
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "style_not_found"

    stolen = client.patch(
        f"{PREFIX}/content/{foreign['id']}/image-style",
        json={"styleId": base_id},
        headers=owner,
    )
    assert stolen.status_code == 404

    omitted = client.patch(f"{PREFIX}/content/{item['id']}/image-style", json={}, headers=owner)
    assert omitted.status_code == 422


def test_lock_base_style_reads_unified_prompt_template(client):
    """基础风格快照来源已迁到 prompt_templates：名称取 title，提示词取 content。"""
    headers = _login(client, "styles-unified-source")
    tpl_id = _prompt_tpl("水彩基础", "水彩，柔光，画面避免出现：文字")
    item = _content(client, headers)

    locked = client.patch(
        f"{PREFIX}/content/{item['id']}/image-style",
        json={"styleId": tpl_id},
        headers=headers,
    )
    assert locked.status_code == 200, locked.text
    assert locked.json()["baseStyleId"] == tpl_id
    assert locked.json()["baseStyleName"] == "水彩基础"
    assert locked.json()["baseStylePrompt"] == "水彩，柔光，画面避免出现：文字"

    # 旧 image_styles 记录不再是可锁定的风格来源
    legacy = _style(client, headers, name="旧风格", prompt="旧提示词")
    rejected = client.patch(
        f"{PREFIX}/content/{item['id']}/image-style",
        json={"styleId": legacy["id"]},
        headers=headers,
    )
    assert rejected.status_code == 404
    assert rejected.json()["code"] == "style_not_found"


def test_blank_name_and_prompt_are_400(client):
    headers = _login(client, "styles-blank")
    blank_name = client.post(
        f"{PREFIX}/image-styles",
        json={"name": "   ", "purpose": "base", "prompt": "有内容"},
        headers=headers,
    )
    assert blank_name.status_code == 400
    assert blank_name.json()["code"] == "invalid_name"

    blank_prompt = client.post(
        f"{PREFIX}/image-styles",
        json={"name": "有名称", "purpose": "inline", "prompt": "  "},
        headers=headers,
    )
    assert blank_prompt.status_code == 400
    assert blank_prompt.json()["code"] == "invalid_prompt"

    empty = client.post(
        f"{PREFIX}/image-styles",
        json={"name": "", "purpose": "base", "prompt": "有内容"},
        headers=headers,
    )
    assert empty.status_code == 422

    created = _style(client, headers)
    patched_name = client.patch(
        f"{PREFIX}/image-styles/{created['id']}",
        json={"name": "  "},
        headers=headers,
    )
    assert patched_name.status_code == 400
    assert patched_name.json()["code"] == "invalid_name"

    patched_prompt = client.patch(
        f"{PREFIX}/image-styles/{created['id']}",
        json={"prompt": "  "},
        headers=headers,
    )
    assert patched_prompt.status_code == 400
    assert patched_prompt.json()["code"] == "invalid_prompt"


def test_factory_cover_style_seeded_at_startup(client):
    headers = _login(client, "styles-factory")
    listed = client.get(f"{PREFIX}/image-styles", params={"purpose": "cover"}, headers=headers)
    assert listed.status_code == 200, listed.text
    rows = [item for item in listed.json() if item["name"] == "公众号爆款封面·Q版女仆桶"]
    assert len(rows) == 1, "出厂封面风格应随启动恰好播种一条"
    style = rows[0]
    assert style["enabled"] is True
    assert style["sortOrder"] == 0
    assert "2.35:1" in style["prompt"]
    assert "chibi" in style["prompt"]
    # 出厂模板的负向句已拆入独立字段，不再混杂在正向 prompt 里
    assert "画面避免出现" not in style["prompt"]
    assert "小字、细节过多的背景" in style["negativeContent"]
    assert "【主标题" in style["prompt"]
    # 不能超管理端 PATCH 的 2000 上限，否则管理员在 UI 里点保存就 422
    assert len(style["prompt"]) <= 2000


def test_factory_seed_idempotent_and_respects_edits(client):
    from app.core.db import SessionLocal
    from app.modules.image_styles import FACTORY_STYLES, seed_factory_styles

    headers = _login(client, "styles-factory-seed")
    factory = FACTORY_STYLES[0]

    with SessionLocal() as db:
        seed_factory_styles(db)  # 重复播种：存在同名行就不再插
    listed = client.get(f"{PREFIX}/image-styles", params={"purpose": "cover"}, headers=headers)
    rows = [item for item in listed.json() if item["name"] == factory["name"]]
    assert len(rows) == 1

    # 管理员改过提示词后重启播种，不回滚编辑、不新增副本
    edited = client.patch(
        f"{PREFIX}/image-styles/{rows[0]['id']}",
        json={"prompt": "管理员改过的封面提示词"},
        headers=headers,
    )
    assert edited.status_code == 200, edited.text
    with SessionLocal() as db:
        seed_factory_styles(db)
    after = client.get(f"{PREFIX}/image-styles", params={"purpose": "cover"}, headers=headers)
    rows = [item for item in after.json() if item["name"] == factory["name"]]
    assert len(rows) == 1
    assert rows[0]["prompt"] == "管理员改过的封面提示词"

    # 恢复出厂原文，避免影响依赖原提示词的其他用例
    restored = client.patch(
        f"{PREFIX}/image-styles/{rows[0]['id']}",
        json={"prompt": factory["prompt"]},
        headers=headers,
    )
    assert restored.status_code == 200
