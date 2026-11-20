"""写作规则表基座（T0）：单行校验、manifest 合成、个人/全局覆盖、原子写入、提示词规则缓存、权限点。

规则表是「创作规则」管理页与运行时 manifest 的单一数据源。这里盯住三条边界：
- 写入路径先整组校验再落库，任何一行非法都不能把库改坏一半；
- 个人副本只在对应类型上覆盖全局，不能因为改了 tone 就把全局的风格规则整批吞掉；
- 提示词规则有缓存，管理端写入后必须能立即读到新值。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app import models
from app.core.db import SessionLocal
from app.services import prompt_rules, writing_rules
from app.services.skill_manifest import validate_manifest
from app.services.writing_rules import RuleInvalid

from .conftest import login


def _entry(manifest: dict | None = None) -> models.SkillRegistryEntry:
    return models.SkillRegistryEntry(
        skill_key="wr-test", source="custom", display_name="写作规则测试", manifest=manifest or {}
    )


def _rule(rule_type: str, payload: dict, key: str = "default", **extra) -> dict:
    rule = {"ruleType": rule_type, "ruleKey": key, "payload": payload}
    rule.update(extra)
    return rule


def _fresh_skill_key() -> str:
    return f"wr-{uuid.uuid4().hex[:10]}"


# ---------------- 1. validate_rule ----------------


def test_validate_rule_normalizes_valid_rows():
    rule = writing_rules.validate_rule(_rule("tone", {"text": "  语气平实  "}))
    assert rule == {"ruleType": "tone", "ruleKey": "default", "payload": {"text": "语气平实"}, "sortOrder": 0, "enabled": True}

    step = writing_rules.validate_rule(
        _rule("workflow_step", {"tool": "create_outline", "args": {"maxSections": 4}}, key="wf-2")
    )
    assert step["ruleKey"] == "wf-2"
    assert step["payload"]["args"] == {"maxSections": 4}
    assert writing_rules.validate_rule(_rule("target_length", {"value": 1200}))["payload"] == {"value": 1200}
    with pytest.raises(RuleInvalid, match="未知规则类型"):
        writing_rules.validate_rule(_rule("theme", {"text": "ink-green"}))


@pytest.mark.parametrize(
    "bad",
    [
        _rule("tone", {}),  # tone 缺 text
        _rule("tone", {"text": "   "}),
        _rule("workflow_step", {"tool": "", "args": {}}, key="wf-1"),  # 工具为空
        _rule("workflow_step", {"args": {}}, key="wf-1"),  # 缺工具
        _rule("review_rule", {"id": "r1", "kind": "starts_with", "value": "x"}, key="r1"),  # kind 非法
        _rule("target_length", {"value": "1200"}),  # 非 int
        _rule("target_length", {"value": True}),
        _rule("target_length", {"value": 20}),  # 越界
        _rule("theme", {"text": "neon-pink"}),  # 非预设
        _rule("fact_policy", {"unknown": True}),
        _rule("image_policy", {"coverAspectRatio": "2:1"}),
        _rule("repair_policy", {"retries": 2}),
        _rule("style_rule", {}),  # 列表类缺文本
        _rule("exemplar", {"title": "", "excerpt": "正文"}, key="ex-1"),  # 空 title
        _rule("not_a_type", {"text": "x"}),
    ],
)
def test_validate_rule_rejects_bad_rows(bad):
    with pytest.raises(RuleInvalid):
        writing_rules.validate_rule(bad)


def test_validate_rule_messages_are_chinese():
    with pytest.raises(RuleInvalid) as exc:
        writing_rules.validate_rule(_rule("tone", {}))
    message = str(exc.value)
    assert message and any("一" <= ch <= "鿿" for ch in message)


# ---------------- 2. compose_manifest ----------------


def test_compose_manifest_empty_table_returns_metadata_only():
    entry = _entry({"tone": "旧语气", "styleRules": ["旧规则"], "customMeta": 1})
    assert writing_rules.compose_manifest(entry, []) == {
        "tone": "旧语气",
        "styleRules": ["旧规则"],
        "customMeta": 1,
    }


def test_compose_manifest_merges_rows_and_overrides_manifest():
    entry = _entry({"tone": "旧语气", "input": {"goal": "string"}})
    rows = [
        models.SkillWritingRule(rule_type="style_rule", rule_key="sr-1", payload={"text": "第二条"}, sort_order=20),
        models.SkillWritingRule(rule_type="style_rule", rule_key="sr-0", payload={"text": "第一条"}, sort_order=10),
        models.SkillWritingRule(rule_type="tone", rule_key="default", payload={"text": "新语气"}),
        models.SkillWritingRule(rule_type="target_length", rule_key="default", payload={"value": 1500}),
        models.SkillWritingRule(
            rule_type="review_rule",
            rule_key="r1",
            # 落库行是 validate_rule 的规范化结果：message 为空时已被去掉
            payload={"id": "r1", "kind": "contains", "value": "总结", "field": "markdown"},
            sort_order=0,
        ),
        models.SkillWritingRule(
            rule_type="review_rule",
            rule_key="r2",
            payload={"id": "r2", "kind": "min_chars", "value": 300, "field": "markdown"},
            sort_order=0,
        ),
        models.SkillWritingRule(rule_type="fact_policy", rule_key="default", payload={"requireSources": True}),
    ]
    manifest = writing_rules.compose_manifest(entry, rows)
    assert manifest["tone"] == "新语气", "manifest 里残留的 tone 必须被规则表覆盖"
    assert manifest["styleRules"] == ["第一条", "第二条"]
    assert manifest["targetLength"] == 1500
    assert [r["id"] for r in manifest["reviewRules"]] == ["r1", "r2"]
    assert manifest["reviewRules"][1]["value"] == 300
    assert all("message" not in r for r in manifest["reviewRules"])
    assert manifest["factPolicy"]["requireSources"] is True
    assert manifest["factPolicy"]["forbidUnverifiedQuotes"] is True, "策略块应规范化（补齐默认值）"
    assert manifest["input"] == {"goal": "string"}


def test_compose_manifest_skips_disabled_rows():
    entry = _entry()
    rows = [
        models.SkillWritingRule(rule_type="tone", rule_key="default", payload={"text": "关掉的"}, enabled=False),
        models.SkillWritingRule(rule_type="style_rule", rule_key="sr-1", payload={"text": "关掉"}, enabled=False, sort_order=1),
        models.SkillWritingRule(rule_type="style_rule", rule_key="sr-2", payload={"text": "留着"}, sort_order=2),
    ]
    manifest = writing_rules.compose_manifest(entry, rows)
    assert "tone" not in manifest, "规则表里唯一的 tone 被禁用，不应回退到 manifest 残留"
    assert manifest["styleRules"] == ["留着"]


def test_compose_manifest_maps_every_rule_type():
    entry = _entry({"tone": "旧语气", "styleRules": ["旧"]})
    rows = [
        models.SkillWritingRule(rule_type="prompt_template", rule_key="default", payload={"text": "你是主笔"}),
        models.SkillWritingRule(rule_type="theme", rule_key="default", payload={"text": "mono"}),
        models.SkillWritingRule(rule_type="workflow_step", rule_key="wf-1", payload={"tool": "create_outline", "args": {}}),
        models.SkillWritingRule(rule_type="image_policy", rule_key="default", payload={"maxInlineImages": 2}),
        models.SkillWritingRule(rule_type="repair_policy", rule_key="default", payload={"retries": 1, "requireContract": True}),
        models.SkillWritingRule(rule_type="exemplar", rule_key="ex-1", payload={"title": "示例", "excerpt": "开头"}),
    ]
    manifest = writing_rules.compose_manifest(entry, rows)
    assert manifest["promptTemplate"] == "你是主笔"
    assert "defaultTheme" not in manifest, "已废弃规则不再参与合成"
    assert manifest["workflow"] == [{"tool": "create_outline", "args": {}}]
    assert manifest["imagePolicy"]["maxInlineImages"] == 2
    assert manifest["repairPolicy"] == {"retries": 1, "requireContract": True}
    assert manifest["exemplars"] == [{"title": "示例", "excerpt": "开头"}]
    # tone / styleRules 规则表里没有 → 迁移期仍带出 manifest 残留；有规则的类型不残留旧值
    assert manifest["tone"] == "旧语气" and manifest["styleRules"] == ["旧"]


@pytest.mark.parametrize("key", ["defaultTheme", "theme", "themeId", "theme_id"])
def test_retired_layout_fields_are_rejected_on_write_and_filtered_on_read(key):
    from app.services.skill_manifest import ManifestInvalid

    with pytest.raises(ManifestInvalid, match="已废弃"):
        validate_manifest({key: "ink-green", "customMeta": 1})
    entry = _entry({key: "ink-green", "customMeta": 1})
    assert writing_rules.compose_manifest(entry, []) == {"customMeta": 1}
    assert writing_rules.effective_manifest(None, entry) == {"customMeta": 1}
    assert validate_manifest({"customMeta": 1}) == {"customMeta": 1}


# ---------------- 3. 全局 / 个人覆盖 ----------------


def test_effective_rows_personal_overrides_single_and_replaces_lists(client):
    key = _fresh_skill_key()
    with SessionLocal() as db:
        writing_rules.replace_rules(
            db,
            skill_key=key,
            owner_user_id=None,
            rules=[
                _rule("tone", {"text": "全局语气"}),
                _rule("style_rule", {"text": "全局规则 A"}, key="sr-1"),
                _rule("style_rule", {"text": "全局规则 B"}, key="sr-2"),
                _rule("target_length", {"value": 1200}),
            ],
        )
        db.commit()
        writing_rules.replace_rules(
            db,
            skill_key=key,
            owner_user_id="user-1",
            rules=[_rule("tone", {"text": "个人语气"}), _rule("style_rule", {"text": "个人唯一规则"}, key="sr-1")],
        )
        db.commit()

        rows = writing_rules.effective_rows(db, key, "user-1")
        manifest = writing_rules.compose_manifest(_entry({"input": {"goal": "string"}}), rows)
        assert manifest["tone"] == "个人语气"
        assert manifest["styleRules"] == ["个人唯一规则"], "个人列表非空时整体替换全局"
        assert manifest["targetLength"] == 1200, "个人没写的单行类沿用全局"

        # 没有个人副本的用户只看全局
        assert writing_rules.effective_rows(db, key, None) is not None
        global_only = writing_rules.compose_manifest(_entry(), writing_rules.effective_rows(db, key, "user-2"))
        assert global_only["tone"] == "全局语气"
        assert global_only["styleRules"] == ["全局规则 A", "全局规则 B"]


# ---------------- 4. replace_rules 原子性 ----------------


def test_replace_rules_rejects_whole_batch_and_keeps_old_rows(client):
    key = _fresh_skill_key()
    with SessionLocal() as db:
        result = writing_rules.replace_rules(
            db, skill_key=key, owner_user_id=None, rules=[_rule("tone", {"text": "旧语气"}), _rule("target_length", {"value": 900})]
        )
        db.commit()
        assert result == {"count": 2, "types": {"tone": 1, "target_length": 1}}

        with pytest.raises(RuleInvalid):
            writing_rules.replace_rules(
                db,
                skill_key=key,
                owner_user_id=None,
                rules=[_rule("tone", {"text": "新语气"}), _rule("target_length", {"value": "随便写"})],
            )
        db.rollback()

        rows = writing_rules.effective_rows(db, key, None)
        manifest = writing_rules.compose_manifest(_entry(), rows)
        assert manifest["tone"] == "旧语气"
        assert manifest["targetLength"] == 900
        assert len(writing_rules.list_rule_rows(db, skill_key=key, owner_user_id=None)) == 2


def test_upsert_rule_overwrites_same_key_and_delete_removes(client):
    key = _fresh_skill_key()
    with SessionLocal() as db:
        writing_rules.upsert_rule(db, skill_key=key, owner_user_id=None, rule=_rule("style_rule", {"text": "一"}, key="sr-1"))
        view = writing_rules.upsert_rule(db, skill_key=key, owner_user_id=None, rule=_rule("style_rule", {"text": "二"}, key="sr-1"))
        db.commit()
        assert view["ruleKey"] == "sr-1" and view["payload"]["text"] == "二"
        assert len(writing_rules.list_rule_rows(db, skill_key=key, owner_user_id=None)) == 1
        assert {"ruleType", "ruleKey", "payload", "sortOrder", "enabled"} <= set(writing_rules.rule_view(
            writing_rules.list_rule_rows(db, skill_key=key, owner_user_id=None)[0]
        ))

        writing_rules.delete_rule(db, skill_key=key, owner_user_id=None, rule_type="style_rule", rule_key="sr-1")
        db.commit()
        assert writing_rules.list_rule_rows(db, skill_key=key, owner_user_id=None) == []


# ---------------- 5. prompt_rules ----------------


def test_prompt_rule_defaults_and_invalidate(client):
    key = f"test.prompt.{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        assert prompt_rules.get_prompt_rule(db, key, default="兜底") == "兜底"
        assert prompt_rules.get_prompt_payload(db, key) == {}
        # 第一次读未命中也进缓存
        assert prompt_rules.get_prompt_rule(db, key, default="兜底") == "兜底"

        db.add(models.PromptRule(key=key, category="system", content="第一版", payload={"a": 1}))
        db.commit()

        prompt_rules.invalidate_cache()
        assert prompt_rules.get_prompt_rule(db, key) == "第一版"
        assert prompt_rules.get_prompt_payload(db, key) == {"a": 1}

        row = db.scalar(select(models.PromptRule).where(models.PromptRule.key == key))
        row.content = "第二版"
        db.commit()
        prompt_rules.invalidate_cache()
        assert prompt_rules.get_prompt_rule(db, key) == "第二版", "写入后 invalidate 必须立即生效"


def test_disabled_prompt_rule_falls_back_to_default(client):
    key = f"test.prompt.{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        db.add(models.PromptRule(key=key, category="system", content="停用内容", enabled=False))
        db.commit()
        prompt_rules.invalidate_cache()
        assert prompt_rules.get_prompt_rule(db, key, default="兜底") == "兜底"


# ---------------- 6. 权限点 ----------------


def test_writing_rules_permissions_in_catalog():
    from app.core.rbac import DEFAULT_ROLE_MATRIX, PERMISSION_CATALOG

    codes = {item[0] for item in PERMISSION_CATALOG}
    assert {"writing_rules.use", "writing_rules.manage"} <= codes
    assert "writing_rules.use" in DEFAULT_ROLE_MATRIX["member"]
    assert "writing_rules.manage" not in DEFAULT_ROLE_MATRIX["member"]


def test_member_role_grants_use_but_not_manage(client):
    # 用 conftest.login 显式给 member：本文件的登录也会被 business_role 夹具换成 superadmin，
    # 只有显式角色才能验出「默认矩阵里 member 只拿 use」
    headers = login(client, subject=f"wr-member-{uuid.uuid4().hex[:6]}", role="member")
    body = client.get("/api/v1/rbac/me", headers=headers).json()
    assert "writing_rules.use" in body["permissions"]
    assert "writing_rules.manage" not in body["permissions"]

    member_id = None
    with SessionLocal() as db:
        member_id = db.scalar(select(models.Role.id).where(models.Role.role_key == "member"))
        held = set(db.scalars(select(models.RolePermission.permission_code).where(models.RolePermission.role_id == member_id)).all())
    assert "writing_rules.use" in held
    assert "writing_rules.manage" not in held


def test_writing_rules_page_seeded_in_system_group():
    from app.services.rbac_service import MENU_SEED

    system = next(group for group in MENU_SEED if group.key == "system")
    keys = [item.key for item in system.children]
    assert "writing-rules" in keys
    page = next(item for item in system.children if item.key == "writing-rules")
    assert page.permission == "writing_rules.use"
    assert keys.index("writing-rules") < keys.index("settings"), "创作规则放在「设置」前"


# ---------------- 7. manifest exemplars ----------------


def test_manifest_accepts_bounded_exemplars():
    three = [{"title": f"标题{i}", "excerpt": f"开头{i}"} for i in range(3)]
    assert validate_manifest({"exemplars": three})["exemplars"] == three
    assert validate_manifest({"exemplars": []}) == {"exemplars": []}

    four = [{"title": f"标题{i}", "excerpt": "开头"} for i in range(4)]
    with pytest.raises(Exception):
        validate_manifest({"exemplars": four})
    with pytest.raises(Exception):
        validate_manifest({"exemplars": [{"title": "  ", "excerpt": "正文"}]})


def test_replace_rules_enforces_review_rule_limit(client):
    """review_rule 此前漏在 _LIST_LIMITS 外，能塞进超过 MAX_REVIEW_RULES 条。

    上限对齐 manifest 的 MAX_REVIEW_RULES（20），第 21 条必须整组拒绝。
    """
    from app.services.skill_manifest import MAX_REVIEW_RULES

    key = _fresh_skill_key()
    rules = [
        _rule(
            "review_rule",
            {"id": f"rv-{index}", "kind": "not_contains", "value": "禁用词"},
            key=f"rv-{index}",
        )
        for index in range(MAX_REVIEW_RULES + 1)
    ]
    with SessionLocal() as db:
        with pytest.raises(RuleInvalid):
            writing_rules.replace_rules(db, skill_key=key, owner_user_id=None, rules=rules)
        db.rollback()
        assert writing_rules.list_rule_rows(db, skill_key=key, owner_user_id=None) == []

        # 刚好到上限时可以写入
        ok = writing_rules.replace_rules(
            db, skill_key=key, owner_user_id=None, rules=rules[:MAX_REVIEW_RULES]
        )
        db.commit()
        assert ok["types"] == {"review_rule": MAX_REVIEW_RULES}
