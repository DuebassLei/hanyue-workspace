"""出厂数据（T1）：`writing_rules_factory.json` 导入与条目换源。

出厂 JSON 是全局规则的权威副本，`builtin_skills.json` 只是它的派生文件。这里盯住：
- 启动导入是「缺什么建什么」：二次启动不新增、不覆盖管理员改过的行；
- 删除一行后重启会从出厂恢复（这正是「按出厂重建」与 R4 的前提）；
- 迁移是逐字的：每个 Skill 的规则条数与 YAML 原条目一致，Style 类还带 `DEFAULT_ARTICLE_PROMPT`；
- 派生文件与出厂 JSON 合成结果一致，YAML 已退役。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select

from app import models
from app.core.db import SessionLocal
from app.services import ai_article, article_workflow, ai_gateway, skill_registry, writing_rules, writing_rules_factory
from app.services import agent_runtime

from .conftest import login

RESOURCES = Path(skill_registry.BASE_DIR) / "app" / "resources"


def _factory() -> dict:
    return json.loads(writing_rules_factory.FACTORY_RESOURCE.read_text(encoding="utf-8"))


def _by_key() -> dict[str, dict]:
    return {item["key"]: item for item in _factory()["skills"]}


def _global_count(db, skill_key: str) -> int:
    return len(writing_rules.list_rule_rows(db, skill_key=skill_key, owner_user_id=None))


# ---------------- 1. 出厂 JSON 内容 ----------------


def test_factory_lists_every_builtin_skill():
    factory = _factory()
    specs = skill_registry.get_builtin_specs()
    assert factory["version"] == 1
    assert [s["key"] for s in factory["skills"]] == [spec.key for spec in specs]
    assert {"topic-research", "article-drafting", "publish-prep"} <= set(_by_key())
    style_keys = [spec.key for spec in specs if spec.category == "style"]
    assert len(style_keys) == 11
    assert set(style_keys) <= set(_by_key())


def test_factory_rule_counts_match_yaml_source():
    """迁移计数对照（原 YAML 条目数 → 规则行数）。"""
    skills = _by_key()
    # YAML `manifest: {}` 的 flow 没有规则
    assert skills["topic-research"]["rules"] == []
    assert skills["publish-prep"]["rules"] == []

    drafting = skills["article-drafting"]["rules"]
    counts: dict[str, int] = {}
    for rule in drafting:
        counts[rule["ruleType"]] = counts.get(rule["ruleType"], 0) + 1
    assert counts == {
        "target_length": 1,
        "tone": 1,
        "prompt_template": 1,  # 成文流程也落模板行，Agent 路径才可配
        "style_rule": 5,  # 原 styleRules 5 条
        "theme": 1,
        "workflow_step": 5,  # 原 workflow 5 步
        "review_rule": 2,  # 原 reviewRules 2 条
        "image_policy": 1,
        "fact_policy": 1,
        "repair_policy": 1,
    }
    assert len(drafting) == 19

    for key, skill in skills.items():
        if not key.startswith("style-"):
            continue
        types = [rule["ruleType"] for rule in skill["rules"]]
        assert types.count("tone") == 1 and types.count("theme") == 1
        # 模板必须逐 skill 成行：只靠 _build_builtin_manifest 注入的话，
        # 规则表里没有 prompt_template，管理页改不动、effective_manifest 也读不到。
        assert types.count("prompt_template") == 1

    # 原 YAML 里每个 style 的 styleRules 条数（逐 key）
    expected_style_rules = {
        "style-tech-article": 10,
        "style-news-and-gov": 11,
        "style-culture-emotion": 10,
        "style-finance-analysis": 10,
        "style-knowledge-popsci": 10,
        "style-fashion-lifestyle": 9,
        "style-food-travel": 10,
        "style-people-profile": 10,
        "style-film-tv-review": 10,
        "style-activity-marketing": 10,
        "style-wawa-tutorial": 12,
    }
    assert set(expected_style_rules) == {k for k in skills if k.startswith("style-")}
    for key, count in expected_style_rules.items():
        assert sum(1 for r in skills[key]["rules"] if r["ruleType"] == "style_rule") == count, key


def test_factory_rules_are_in_yaml_order_and_normalizable():
    drafting = _by_key()["article-drafting"]["rules"]
    style_keys = [r["ruleKey"] for r in drafting if r["ruleType"] == "style_rule"]
    assert style_keys == [f"sr-{i}" for i in range(1, 6)]
    assert all(r["sortOrder"] == i for i, r in enumerate(r for r in drafting if r["ruleType"] == "style_rule"))
    assert [r["ruleKey"] for r in drafting if r["ruleType"] == "workflow_step"] == [f"wf-{i}" for i in range(1, 6)]
    assert [r["ruleKey"] for r in drafting if r["ruleType"] == "review_rule"] == ["has-body", "no-absolute"]
    assert [r["payload"]["tool"] for r in drafting if r["ruleType"] == "workflow_step"] == [
        "create_outline",
        "generate_article",
        "review_article",
        "check_ai_tone",
        "plan_images",
    ]
    for skill in _factory()["skills"]:
        for rule in skill["rules"]:
            normalized = writing_rules.validate_rule(rule)
            assert normalized["ruleType"] == rule["ruleType"]
            assert normalized["ruleKey"] == rule["ruleKey"]


def test_every_writing_skill_has_prompt_template_rule():
    """模板必须逐 skill 成行，且与出厂常量逐字一致。

    回归点：导出脚本曾漏掉 `promptTemplate` 映射，style 类只靠
    `_build_builtin_manifest` 注入派生快照——规则表里没有 `prompt_template`，
    「写作模板」就在管理页消失、也改不动。
    """
    skills = _by_key()
    template = skill_registry.DEFAULT_ARTICLE_PROMPT
    targets = [spec.key for spec in skill_registry.get_builtin_specs() if spec.category == "style"]
    targets.append("article-drafting")  # Agent 成文走的 flow，同样要可配
    assert len(targets) == 12

    for key in targets:
        rows = [r for r in skills[key]["rules"] if r["ruleType"] == "prompt_template"]
        assert len(rows) == 1, f"{key} 应有且仅有一行 prompt_template"
        assert rows[0]["ruleKey"] == "default"
        assert rows[0]["payload"]["text"] == template, key
        assert rows[0]["enabled"] is True and rows[0]["sortOrder"] == 0

    # 不生文的流程不落模板行：管理页不该出现无意义的分组
    for key in ("topic-research", "publish-prep"):
        assert "prompt_template" not in [r["ruleType"] for r in skills[key]["rules"]], key


def test_factory_prompt_rules_match_code_constants():
    prompts = {item["key"]: item for item in _factory()["prompt_rules"]}
    assert prompts["article.output_contract"]["content"] == ai_article.ARTICLE_OUTPUT_CONTRACT
    assert prompts["gateway.system_rules"]["content"] == ai_gateway.SYSTEM_RULES
    assert prompts["agent.outline_system"]["content"] == agent_runtime.OUTLINE_SYSTEM_PROMPT
    assert prompts["agent.outline_inject"]["content"] == "参考大纲（结构可微调，不要照抄标题）："
    assert prompts["agent.outline_inject"]["category"] == "workflow_note"
    assert prompts["article.output_contract"]["category"] == "contract"
    assert prompts["gateway.system_rules"]["category"] == "system"
    assert prompts["agent.outline_system"]["category"] == "outline"

    openers = prompts["tone_lexicon.sentence_openers"]["payload"]
    assert openers == {"window": article_workflow._LEAD_WINDOW, "phrases": list(article_workflow._SENTENCE_START_PHRASES)}
    buzz = prompts["tone_lexicon.buzzwords"]["payload"]
    assert buzz == {"rules": [{"id": rid, "word": word} for rid, word in article_workflow._WORD_RULES]}
    assert prompts["tone_lexicon.first_then"]["payload"] == {"words": ["首先", "其次"], "ruleId": "first-then"}

    # 后续任务才建的 key 本次不建
    assert "article.repair_prompt" not in prompts
    assert "tone_lexicon.patterns" not in prompts


def test_yaml_source_is_retired():
    assert not (RESOURCES / "skills" / "builtin_skills.yaml").exists()
    assert skill_registry.BUILTIN_RESOURCE.name == "builtin_skills.json"
    assert skill_registry.BUILTIN_RESOURCE.exists()


def test_derived_builtin_json_matches_factory():
    from scripts import export_factory_to_builtin

    assert export_factory_to_builtin.check() == [], "builtin_skills.json 与出厂 JSON 派生结果不一致；重跑导出脚本"


# ---------------- 2. 启动导入 ----------------


def test_import_factory_seeds_every_skill_and_prompt(client):
    with SessionLocal() as db:
        for skill in _factory()["skills"]:
            rows = writing_rules.list_rule_rows(db, skill_key=skill["key"], owner_user_id=None)
            assert len(rows) == len(skill["rules"]), skill["key"]
            assert all(row.source == "factory" for row in rows)
        wanted = {item["key"] for item in _factory()["prompt_rules"]}
        stored = set(db.scalars(select(models.PromptRule.key)).all())
        assert wanted <= stored
        for rule in db.scalars(select(models.PromptRule).where(models.PromptRule.key.in_(wanted))).all():
            assert rule.source == "factory" and rule.version == 1


def test_import_factory_is_idempotent(client):
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(models.SkillWritingRule))
        result = writing_rules_factory.import_factory(db)
        after = db.scalar(select(func.count()).select_from(models.SkillWritingRule))
    assert after == before
    assert result["rules_created"] == 0 and result["prompts_created"] == 0


def test_import_factory_keeps_admin_edits_then_restores_deleted(client):
    """R3：已存在行一律不动；删除后重启（只缺什么建什么）会恢复。"""
    key = "article-drafting"
    with SessionLocal() as db:
        row = db.scalars(
            select(models.SkillWritingRule).where(
                models.SkillWritingRule.skill_key == key,
                models.SkillWritingRule.owner_user_id.is_(None),
                models.SkillWritingRule.rule_type == "tone",
            )
        ).one()
        original = row.payload["text"]
        row.payload = {"text": "管理员改过的语气"}
        row.source = "user"
        db.commit()

    with SessionLocal() as db:
        writing_rules_factory.import_factory(db)
        tone = db.scalars(
            select(models.SkillWritingRule).where(
                models.SkillWritingRule.skill_key == key,
                models.SkillWritingRule.owner_user_id.is_(None),
                models.SkillWritingRule.rule_type == "tone",
            )
        ).one()
        assert tone.payload["text"] == "管理员改过的语气", "only_missing 不能覆盖管理员改动"

        db.execute(
            delete(models.SkillWritingRule).where(
                models.SkillWritingRule.skill_key == key,
                models.SkillWritingRule.owner_user_id.is_(None),
                models.SkillWritingRule.rule_type == "tone",
            )
        )
        db.commit()
        assert not db.scalars(
            select(models.SkillWritingRule).where(
                models.SkillWritingRule.skill_key == key,
                models.SkillWritingRule.rule_type == "tone",
                models.SkillWritingRule.owner_user_id.is_(None),
            )
        ).all()

    with SessionLocal() as db:
        result = writing_rules_factory.import_factory(db)
        tone = db.scalars(
            select(models.SkillWritingRule).where(
                models.SkillWritingRule.skill_key == key,
                models.SkillWritingRule.owner_user_id.is_(None),
                models.SkillWritingRule.rule_type == "tone",
            )
        ).one()
        assert tone.payload["text"] == original
        assert tone.source == "factory"
        assert result["rules_created"] == 1


def test_reset_skill_rules_to_factory_restores_single_skill(client):
    key = "style-tech-article"
    expected = len(_by_key()[key]["rules"])
    with SessionLocal() as db:
        row = writing_rules.list_rule_rows(db, skill_key=key, owner_user_id=None)[0]
        row.payload = {"text": "改坏了"}
        row.source = "user"
        db.commit()
        writing_rules.delete_rule(
            db, skill_key=key, owner_user_id=None, rule_type="theme", rule_key="default"
        )
        db.commit()
        other_before = _global_count(db, "article-drafting")

        result = writing_rules_factory.reset_skill_rules_to_factory(db, key)
        rows = writing_rules.list_rule_rows(db, skill_key=key, owner_user_id=None)
        assert len(rows) == expected == result["count"]
        assert all(row.source == "factory" for row in rows)
        assert _global_count(db, "article-drafting") == other_before, "只重建指定 Skill"
        tone = next(row for row in rows if row.rule_type == "tone")
        assert tone.payload["text"] == _by_key()[key]["rules"][0]["payload"]["text"]


def test_reset_keeps_personal_rows(client):
    key = "style-news-and-gov"
    with SessionLocal() as db:
        writing_rules.replace_rules(
            db, skill_key=key, owner_user_id="reset-personal", rules=[{"ruleType": "tone", "payload": {"text": "个人"}}]
        )
        db.commit()
        writing_rules_factory.reset_skill_rules_to_factory(db, key)
        personal = writing_rules.list_rule_rows(db, skill_key=key, owner_user_id="reset-personal")
        assert len(personal) == 1 and personal[0].payload["text"] == "个人"


def test_import_factory_rejects_malformed_resource(monkeypatch, tmp_path):
    bad = tmp_path / "factory.json"
    bad.write_text(json.dumps({"version": 1, "skills": "nope"}), encoding="utf-8")
    monkeypatch.setattr(writing_rules_factory, "FACTORY_RESOURCE", bad)
    with pytest.raises(RuntimeError):
        writing_rules_factory.load_factory()
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(RuntimeError):
        writing_rules_factory.load_factory()


# ---------------- 3. fork_builtin 复制全局规则 ----------------


def test_fork_builtin_copies_global_rules_to_personal(client):
    headers = login(client, "factory-fork")
    me = client.get("/api/v1/auth/me", headers=headers).json()
    with SessionLocal() as db:
        entry = db.scalars(
            select(models.SkillRegistryEntry).where(
                models.SkillRegistryEntry.skill_key == "style-tech-article",
                models.SkillRegistryEntry.owner_user_id.is_(None),
            )
        ).one()
        global_rows = writing_rules.list_rule_rows(db, skill_key="style-tech-article", owner_user_id=None)

    res = client.post(
        f"/api/v1/skills/{entry.id}/customize",
        headers=headers,
        json={"manifest": {"tone": "个人语气"}},
    )
    assert res.status_code == 201, res.text

    with SessionLocal() as db:
        personal = writing_rules.list_rule_rows(db, skill_key="style-tech-article", owner_user_id=me["id"])
        # 预检：本用例若重复运行，先清掉上次成果
        assert len(personal) == len(global_rows)
        assert all(row.source == "user" for row in personal)
        assert {(r.rule_type, r.rule_key) for r in personal} == {(r.rule_type, r.rule_key) for r in global_rows}
        assert {r.sort_order for r in personal} == {r.sort_order for r in global_rows}
        glob = {(r.rule_type, r.rule_key): r for r in global_rows}
        for row in personal:
            assert row.payload == glob[(row.rule_type, row.rule_key)].payload
            assert row.enabled == glob[(row.rule_type, row.rule_key)].enabled
        # 清理：个人副本按唯一约束只能一条，删掉条目与规则
        for row in personal:
            db.delete(row)
        fork = db.scalar(
            select(models.SkillRegistryEntry).where(
                models.SkillRegistryEntry.skill_key == "style-tech-article",
                models.SkillRegistryEntry.owner_user_id == me["id"],
            )
        )
        if fork is not None:
            db.delete(fork)
        db.commit()


def test_fork_builtin_merges_with_existing_personal_rows(client):
    """先在规则页维护过个人副本行、再 fork 内置风格时的合并语义。

    没有这层去重会撞 (skill_key, owner, rule_type, rule_key) 唯一约束直接 500；
    同时也不能用全局值覆盖用户自己改过的行（用户的改动优先）。
    """
    headers = login(client, "factory-fork-merge")
    me = client.get("/api/v1/auth/me", headers=headers).json()
    key = "style-tech-article"
    with SessionLocal() as db:
        entry = db.scalars(
            select(models.SkillRegistryEntry).where(
                models.SkillRegistryEntry.skill_key == key,
                models.SkillRegistryEntry.owner_user_id.is_(None),
            )
        ).one()
        entry_id = entry.id
        global_rows = writing_rules.list_rule_rows(db, skill_key=key, owner_user_id=None)

    # 1) 用户先写一条自己的语气（不 fork 也能写个人副本，这是「创作规则」页的正常路径）
    added = client.post(
        f"/api/v1/writing-rules/skills/{key}/rules?scope=personal",
        headers=headers,
        json={"ruleType": "tone", "ruleKey": "default", "payload": {"text": "我先改过的语气"}},
    )
    assert added.status_code == 201, added.text

    # 2) fork 内置风格：应成功（回归点：此前 500 IntegrityError）
    forked = client.post(
        f"/api/v1/skills/{entry_id}/customize",
        headers=headers,
        json={"manifest": {"tone": "fork 提交的语气"}},
    )
    assert forked.status_code == 201, forked.text

    # 3) 规则集合补齐到与全局一致，但用户改过的那条保持原值
    with SessionLocal() as db:
        personal = writing_rules.list_rule_rows(db, skill_key=key, owner_user_id=me["id"])
        assert {(r.rule_type, r.rule_key) for r in personal} == {
            (r.rule_type, r.rule_key) for r in global_rows
        }
        tone = next(row for row in personal if row.rule_type == "tone")
        assert tone.payload == {"text": "我先改过的语气"}

        # 清理
        for row in personal:
            db.delete(row)
        fork = db.scalar(
            select(models.SkillRegistryEntry).where(
                models.SkillRegistryEntry.skill_key == key,
                models.SkillRegistryEntry.owner_user_id == me["id"],
            )
        )
        if fork is not None:
            db.delete(fork)
        db.commit()


# ---------------- 4. 条目换源后语义不变 ----------------

def test_builtin_entries_keep_yaml_manifest_semantics(client):
    with SessionLocal() as db:
        drafting = db.scalars(
            select(models.SkillRegistryEntry).where(
                models.SkillRegistryEntry.skill_key == "article-drafting",
                models.SkillRegistryEntry.owner_user_id.is_(None),
            )
        ).one()
        manifest = drafting.manifest
        assert manifest["targetLength"] == 1200
        assert manifest["defaultTheme"] == "ink-green"
        assert len(manifest["styleRules"]) == 5
        assert len(manifest["workflow"]) == 5
        assert [r["id"] for r in manifest["reviewRules"]] == ["has-body", "no-absolute"]
        assert manifest["imagePolicy"]["coverAspectRatio"] == "3:4"
        assert manifest["factPolicy"] == {"requireSources": False, "forbidUnverifiedQuotes": True, "placeholder": "〔待核实〕", "extraInstructions": []} or manifest["factPolicy"]["forbidUnverifiedQuotes"] is True
        # 成文流程的模板同样来自规则行（值为出厂常量，行为与迁移前一致）
        assert manifest["promptTemplate"] == skill_registry.DEFAULT_ARTICLE_PROMPT
        assert manifest["input"] == {"goal": "string"} and manifest["output"] == {"draft": "object"}

        research = db.scalars(
            select(models.SkillRegistryEntry).where(
                models.SkillRegistryEntry.skill_key == "topic-research",
                models.SkillRegistryEntry.owner_user_id.is_(None),
            )
        ).one()
        assert set(research.manifest) == {"input", "output", "riskLevel"}, "YAML manifest: {} 的 flow 仍是 {}"

        tech = db.scalars(
            select(models.SkillRegistryEntry).where(
                models.SkillRegistryEntry.skill_key == "style-tech-article",
                models.SkillRegistryEntry.owner_user_id.is_(None),
            )
        ).one()
        assert tech.manifest["promptTemplate"] == skill_registry.DEFAULT_ARTICLE_PROMPT
        assert len(tech.manifest["styleRules"]) == 10
        assert tech.manifest["defaultTheme"] == "blue-business"
        assert "workflow" not in tech.manifest


def test_prompt_template_rule_overrides_manifest_snapshot(client):
    """规则行优先于条目 manifest 快照；个人没写时沿用全局出厂行。

    此前 style 的模板只存在派生快照里，管理员在规则页既看不到也改不动，
    `effective_manifest` 永远返回常量——这条用例锁住修复后的口径。
    """
    key = "style-news-and-gov"
    owner = "prompt-template-owner"
    with SessionLocal() as db:
        entry = db.scalars(
            select(models.SkillRegistryEntry).where(
                models.SkillRegistryEntry.skill_key == key,
                models.SkillRegistryEntry.owner_user_id.is_(None),
            )
        ).one()
        assert writing_rules.effective_manifest(db, entry, None)["promptTemplate"] == (
            skill_registry.DEFAULT_ARTICLE_PROMPT
        )

        writing_rules.replace_rules(
            db,
            skill_key=key,
            owner_user_id=owner,
            rules=[{"ruleType": "prompt_template", "payload": {"text": "个人模板：{tone}"}}],
        )
        db.commit()
        try:
            effective = writing_rules.effective_manifest(db, entry, owner)
            assert effective["promptTemplate"] == "个人模板：{tone}"
            # 没有个人行的其他用户照旧读全局出厂行
            other = writing_rules.effective_manifest(db, entry, "another-owner")
            assert other["promptTemplate"] == skill_registry.DEFAULT_ARTICLE_PROMPT
        finally:
            for row in writing_rules.list_rule_rows(db, skill_key=key, owner_user_id=owner):
                db.delete(row)
            db.commit()


def test_derived_manifest_roundtrips_through_registry_builder():
    """`builtin_skills.json` 的 manifest 必须是 `_build_builtin_manifest` 的不动点。"""
    for spec in skill_registry.get_builtin_specs():
        assert skill_registry._build_builtin_manifest(spec) == spec.manifest
