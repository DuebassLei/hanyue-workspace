"""创作规则管理 API 的边界与语义测试（T4）。

覆盖矩阵（计划 §T4）：
- 成员（只有 writing_rules.use）可读写本人副本、不能写全局（403）；
- writing_rules.manage（superadmin / 管理员角色）可写全局；
- 契约规则缺字段 422 且库不变；
- reset 需 manage；
- 预览接口对内置与个人副本分别产出预期文本片段；
- R4：全局整组替换传空数组 = 全部禁用，不删行。
"""

from __future__ import annotations

from sqlalchemy import select

from app import models
from app.core.db import SessionLocal
from app.services import prompt_rules, writing_rules

from .conftest import login


def _get_rules(client, headers, key, scope="global"):
    return client.get(f"/api/v1/writing-rules/skills/{key}?scope={scope}", headers=headers)


def _put_rules(client, headers, key, rules, scope="global"):
    return client.put(
        f"/api/v1/writing-rules/skills/{key}/rules?scope={scope}",
        headers=headers,
        json={"rules": rules},
    )


def _tone_rule(text, key="default", **extra):
    rule = {"ruleType": "tone", "ruleKey": key, "payload": {"text": text}}
    rule.update(extra)
    return rule


# ---------------- 列表与读取 ----------------


def test_skill_list_contains_factory_skills_with_counts(client):
    headers = login(client)
    res = client.get("/api/v1/writing-rules/skills", headers=headers)
    assert res.status_code == 200, res.text
    items = {item["skillKey"]: item for item in res.json()}
    assert "article-drafting" in items
    assert items["article-drafting"]["globalCount"] > 0
    assert "name" in items["article-drafting"] and "category" in items["article-drafting"]


def test_get_global_rules_returns_rows_and_effective_manifest(client):
    headers = login(client)
    res = _get_rules(client, headers, "article-drafting")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["scope"] == "global"
    assert body["rules"], "出厂导入后全局规则应为非空"
    manifest = body["effectiveManifest"]
    assert manifest.get("tone"), "合成结果应带出语气"
    counts = body["counts"]
    assert counts.get("tone") == 1
    unknown = client.get("/api/v1/writing-rules/skills/no-such-skill", headers=headers)
    assert unknown.status_code == 404


# ---------------- 个人副本读写（member 权限边界） ----------------


def test_member_can_edit_personal_copy_but_not_global(client):
    headers = login(client, subject="rules-member", role="member")

    # 读没问题（writing_rules.use 人人有）
    assert _get_rules(client, headers, "article-drafting").status_code == 200

    # 写个人副本：200 且生效（tone 是单行类，ruleKey 固定 default）
    res = _put_rules(client, headers, "article-drafting", [_tone_rule("个人语气平实")], scope="personal")
    assert res.status_code == 200, res.text
    personal = res.json()
    assert [r["payload"]["text"] for r in personal["rules"] if r["ruleType"] == "tone"] == ["个人语气平实"]
    assert personal["effectiveManifest"]["tone"] == "个人语气平实"

    # 写全局：403，且库内全局行未被改动
    denied = _put_rules(client, headers, "article-drafting", [_tone_rule("越权语气")], scope="global")
    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"
    with SessionLocal() as db:
        rows = writing_rules.list_rule_rows(db, skill_key="article-drafting", owner_user_id=None)
        tones = [r for r in rows if r.rule_type == "tone"]
        assert tones and tones[0].payload.get("text") != "越权语气"


def test_superadmin_can_write_global_rules(client):
    headers = login(client)
    res = _put_rules(client, headers, "article-drafting", [_tone_rule("管理员定的全局语气")], scope="global")
    assert res.status_code == 200, res.text
    assert res.json()["effectiveManifest"]["tone"] == "管理员定的全局语气"


def test_global_empty_replace_disables_rows_instead_of_deleting(client):
    """R4：全局「清空」= 全部禁用落行，不是删行（删了会被启动导入恢复）。"""
    headers = login(client)
    before = _get_rules(client, headers, "article-drafting").json()["rules"]
    assert before

    res = _put_rules(client, headers, "article-drafting", [], scope="global")
    assert res.status_code == 200, res.text
    after = res.json()["rules"]
    assert len(after) == len(before), "行数不变，只是全部禁用"
    assert all(not r["enabled"] for r in after)
    assert res.json()["effectiveManifest"].get("tone") is None, "禁用后不产出语气，也不回退 manifest"

    # 恢复现场：按出厂重建
    reset = client.post("/api/v1/writing-rules/skills/article-drafting/reset", headers=headers)
    assert reset.status_code == 200
    restored = reset.json()["rules"]
    assert any(r["enabled"] for r in restored)


# ---------------- 单条增改删 ----------------


def test_single_rule_add_patch_delete_roundtrip(client):
    headers = login(client, subject="rules-crud", role="member")
    key = "article-drafting"

    added = client.post(
        f"/api/v1/writing-rules/skills/{key}/rules?scope=personal",
        headers=headers,
        json={"ruleType": "style_rule", "ruleKey": "sr-test", "payload": {"text": "不用 emoji"}},
    )
    assert added.status_code == 201, added.text
    rule_id = added.json()["id"]

    patched = client.patch(
        f"/api/v1/writing-rules/skills/{key}/rules/{rule_id}?scope=personal",
        headers=headers,
        json={"enabled": False},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["enabled"] is False

    bad_patch = client.patch(
        f"/api/v1/writing-rules/skills/{key}/rules/{rule_id}?scope=personal",
        headers=headers,
        json={"payload": {"text": ""}},  # style_rule 空文本 → 422
    )
    assert bad_patch.status_code == 422
    assert bad_patch.json()["code"] == "invalid_rule"
    with SessionLocal() as db:
        row = db.get(models.SkillWritingRule, rule_id)
        assert row.payload["text"] == "不用 emoji", "校验失败时库不变"
        assert row.enabled is False, "422 的 payload 更新不影响之前成功的 enabled"

    # 不能跨 scope 改别人的行：全局行对 member 不可见也不可改
    with SessionLocal() as db:
        first_global = db.scalars(
            select(models.SkillWritingRule).where(
                models.SkillWritingRule.skill_key == key,
                models.SkillWritingRule.owner_user_id.is_(None),
            )
        ).first()
    hijack = client.patch(
        f"/api/v1/writing-rules/skills/{key}/rules/{first_global.id}",
        headers=headers,
        json={"enabled": False},
    )
    assert hijack.status_code == 403  # member 改全局 scope 被权限拦截

    deleted = client.delete(f"/api/v1/writing-rules/skills/{key}/rules/{rule_id}?scope=personal", headers=headers)
    assert deleted.status_code == 204
    with SessionLocal() as db:
        assert db.get(models.SkillWritingRule, rule_id) is None


def test_patch_validates_payload_and_keeps_row_on_failure(client):
    headers = login(client)
    key = "article-drafting"
    added = client.post(
        f"/api/v1/writing-rules/skills/{key}/rules?scope=global",
        headers=headers,
        json={
            "ruleType": "review_rule",
            "ruleKey": "rv-api-test",
            "payload": {"id": "rv-api-test", "kind": "min_chars", "value": 10, "field": "markdown"},
        },
    )
    assert added.status_code == 201, added.text
    rule_id = added.json()["id"]
    bad = client.patch(
        f"/api/v1/writing-rules/skills/{key}/rules/{rule_id}",
        headers=headers,
        json={"payload": {"id": "rv-api-test", "kind": "unknown_kind", "value": 1}},
    )
    assert bad.status_code == 422
    assert bad.json()["code"] == "invalid_rule"
    cleanup = client.delete(f"/api/v1/writing-rules/skills/{key}/rules/{rule_id}?scope=global", headers=headers)
    assert cleanup.status_code == 204


def test_reset_requires_manage_and_restores_factory(client):
    member = login(client, subject="rules-reset", role="member")
    denied = client.post("/api/v1/writing-rules/skills/article-drafting/reset", headers=member)
    assert denied.status_code == 403

    admin = login(client)
    # 先改掉出厂语气，再重置，确认被覆盖回出厂值
    client.put(
        "/api/v1/writing-rules/skills/article-drafting/rules?scope=global",
        headers=admin,
        json={"rules": [_tone_rule("被覆盖的语气")]},
    )
    res = client.post("/api/v1/writing-rules/skills/article-drafting/reset", headers=admin)
    assert res.status_code == 200, res.text
    factory_text = next(
        r["payload"]["text"] for r in res.json()["rules"] if r["ruleType"] == "tone"
    )
    assert factory_text != "被覆盖的语气"
    with SessionLocal() as db:
        import json as jsonlib

        factory = jsonlib.load(open("app/resources/writing_rules_factory.json", encoding="utf-8"))
        expected = next(
            r["payload"]["text"]
            for s in factory["skills"]
            if s["key"] == "article-drafting"
            for r in s["rules"]
            if r["ruleType"] == "tone"
        )
        assert factory_text == expected, "reset 后与出厂 JSON 逐字一致"


def test_reset_unknown_skill_returns_422(client):
    headers = login(client)
    res = client.post("/api/v1/writing-rules/skills/no-such-skill/reset", headers=headers)
    assert res.status_code == 422


# ---------------- 提示词与词表 ----------------


def test_prompt_rules_list_and_contract_validation(client):
    headers = login(client)
    res = client.get("/api/v1/writing-rules/prompts", headers=headers)
    assert res.status_code == 200, res.text
    items = {item["key"]: item for item in res.json()}
    assert "article.output_contract" in items
    assert items["article.output_contract"]["source"] == "factory"

    # 契约缺字段 → 422 contract_field_missing，库不变
    broken = dict(items["article.output_contract"])
    broken["content"] = broken["content"].replace("coverPrompt", "封面提示词")
    res_bad = client.put(
        "/api/v1/writing-rules/prompts/article.output_contract",
        headers=headers,
        json={"content": broken["content"]},
    )
    assert res_bad.status_code == 422
    assert res_bad.json()["code"] == "contract_field_missing"
    with SessionLocal() as db:
        row = db.scalar(select(models.PromptRule).where(models.PromptRule.key == "article.output_contract"))
        assert "coverPrompt" in row.content, "校验失败时库不变"
        assert row.version == 1

    # 完整契约 → 200，读取立即生效（写接口已失效缓存）
    ok = client.put(
        "/api/v1/writing-rules/prompts/article.output_contract",
        headers=headers,
        json={"content": items["article.output_contract"]["content"] + "\n（管理员补充）"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["version"] == 2
    with SessionLocal() as db:
        from app.services import ai_gateway  # noqa: F401 确保模块就绪

        live = prompt_rules.get_prompt_rule(db, "article.output_contract", default="")
        assert "管理员补充" in live
    # 恢复现场：写接口已把 version 顶到 2，直接落库还原（含 version），避免污染共享测试库
    with SessionLocal() as db:
        row = db.scalar(select(models.PromptRule).where(models.PromptRule.key == "article.output_contract"))
        row.content = items["article.output_contract"]["content"]
        row.version = 1
        db.commit()
    prompt_rules.invalidate_cache()


def test_prompt_rules_member_cannot_write(client):
    headers = login(client, subject="rules-prompt", role="member")
    denied = client.put(
        "/api/v1/writing-rules/prompts/gateway.system_rules",
        headers=headers,
        json={"enabled": False},
    )
    assert denied.status_code == 403


def test_prompt_rule_create_and_delete_only_user_source(client):
    headers = login(client)
    created = client.post(
        "/api/v1/writing-rules/prompts",
        headers=headers,
        json={
            "key": "custom.repair_note",
            "category": "workflow_note",
            "title": "自定义修复备注",
            "content": "修复时注意保留小标题结构。",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["source"] == "user"

    dup = client.post(
        "/api/v1/writing-rules/prompts",
        headers=headers,
        json={"key": "custom.repair_note", "category": "workflow_note"},
    )
    assert dup.status_code == 409
    assert dup.json()["code"] == "prompt_rule_exists"

    bad_key = client.post(
        "/api/v1/writing-rules/prompts",
        headers=headers,
        json={"key": "a", "category": "workflow_note"},
    )
    assert bad_key.status_code == 422

    # 出厂规则不可删除，自定义规则可删
    locked = client.delete("/api/v1/writing-rules/prompts/gateway.system_rules", headers=headers)
    assert locked.status_code == 409
    assert locked.json()["code"] == "factory_rule_locked"
    gone = client.delete("/api/v1/writing-rules/prompts/custom.repair_note", headers=headers)
    assert gone.status_code == 204


# ---------------- 预览 ----------------


def test_preview_article_global_and_personal_produce_expected_fragments(client):
    headers = login(client, subject="rules-preview", role="member")
    # 全局预览：带输出契约
    global_preview = client.get(
        "/api/v1/writing-rules/preview",
        headers=headers,
        params={"skillKey": "article-drafting", "scope": "global", "kind": "article"},
    )
    assert global_preview.status_code == 200, global_preview.text
    body = global_preview.json()
    assert "coverPrompt" in body["text"], "文章预览应包含输出契约片段"
    assert body["ruleSources"], "应记录所用规则的 key 与版本"

    # 个人副本改语气后，个人预览应带个人语气、全局预览不变
    client.put(
        "/api/v1/writing-rules/skills/article-drafting/rules?scope=personal",
        headers=headers,
        json={"rules": [_tone_rule("预览专用个人语气")]},
    )
    personal_preview = client.get(
        "/api/v1/writing-rules/preview",
        headers=headers,
        params={"skillKey": "article-drafting", "scope": "personal", "kind": "article"},
    )
    assert "预览专用个人语气" in personal_preview.json()["text"]
    fresh_global = client.get(
        "/api/v1/writing-rules/preview",
        headers=headers,
        params={"skillKey": "article-drafting", "scope": "global", "kind": "article"},
    )
    assert "预览专用个人语气" not in fresh_global.json()["text"]


def test_preview_outline_and_repair_kinds(client):
    headers = login(client)
    outline = client.get(
        "/api/v1/writing-rules/preview",
        headers=headers,
        params={"skillKey": "article-drafting", "kind": "outline"},
    )
    assert outline.status_code == 200
    assert "大纲" in outline.json()["text"], "大纲预览应来自 agent.outline_system"

    repair = client.get(
        "/api/v1/writing-rules/preview",
        headers=headers,
        params={"skillKey": "article-drafting", "kind": "repair", "sample": "草稿正文片段"},
    )
    assert repair.status_code == 200
    text = repair.json()["text"]
    assert "重试" in text and "草稿样本" in text
