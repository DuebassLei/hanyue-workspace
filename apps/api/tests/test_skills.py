"""Skill 维护链路：归属、信任、工具校验、manifest 结构、seed 生命周期与运行时解析。

这里覆盖的是设置页真实会走的路径（创建 → 启用 → 编辑 → 定制 → 删除），
而不是只验证单个函数，避免再次出现“路由被同名端点遮蔽”这类只在联调时暴露的问题。
"""

from __future__ import annotations

from sqlalchemy import select

from app import models
from app.core.db import SessionLocal
from app.core.deps import default_workspace
from app.services import ai_article, skill_registry
from app.services.skill_manifest import validate_manifest


def login(client, subject="skill-user"):
    res = client.post(
        "/api/v1/auth/login", json={"subject": subject, "displayName": subject}
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _create_style(client, headers, key: str, **overrides):
    payload = {
        "skillKey": key,
        "displayName": f"{key} 风格",
        "description": "测试用风格",
        "allowedToolIds": ["generate_article"],
        "manifest": {"tone": "克制", "styleRules": ["少用形容词"], "defaultTheme": "blue-business"},
    }
    payload.update(overrides)
    return client.post("/api/v1/skills", json=payload, headers=headers)


def _me(client, headers) -> dict:
    return client.get("/api/v1/auth/me", headers=headers).json()


def test_created_skill_is_owned_trusted_and_enableable(client):
    headers = login(client, "skill-owner")
    res = _create_style(client, headers, "owner-style")
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["source"] == "custom"
    assert body["trustStatus"] == "trusted"
    assert body["riskLevel"] == "reversible_write"

    enabled = client.patch(
        f"/api/v1/skills/{body['id']}",
        json={"enabled": True, "grantedToolIds": ["generate_article"]},
        headers=headers,
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["enabled"] is True
    assert enabled.json()["grantedToolIds"] == ["generate_article"]

    # 启用走工作区入口时行为一致
    disabled = client.patch(
        f"/api/v1/workspaces/current/skills/{body['id']}",
        json={"enabled": False},
        headers=headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False


def test_created_skill_is_invisible_to_other_users(client):
    alice = login(client, "skill-alice")
    bob = login(client, "skill-bob")
    created = _create_style(client, alice, "alice-private-style")
    assert created.status_code == 201

    bob_keys = [s["skillKey"] for s in client.get("/api/v1/skills", headers=bob).json()]
    assert "alice-private-style" not in bob_keys
    # 无法启停 / 删除 / 编辑他人的 Skill
    assert client.patch(
        f"/api/v1/skills/{created.json()['id']}",
        json={"enabled": True},
        headers=bob,
    ).status_code == 403
    assert client.delete(
        f"/api/v1/skills/{created.json()['id']}", headers=bob
    ).status_code == 404


def test_skill_rejects_forbidden_and_unknown_tools(client):
    headers = login(client, "skill-tools")
    forbidden = _create_style(
        client, headers, "tool-forbidden", allowedToolIds=["run_shell"]
    )
    assert forbidden.status_code == 409
    assert forbidden.json()["code"] == "forbidden_tool"

    unknown = _create_style(
        client, headers, "tool-unknown", allowedToolIds=["no_such_tool"]
    )
    assert unknown.status_code == 409
    assert unknown.json()["code"] == "unknown_tool"


def test_same_skill_key_allowed_per_user(client):
    alice = login(client, "key-alice")
    bob = login(client, "key-bob")
    assert _create_style(client, alice, "shared-style-key").status_code == 201
    assert _create_style(client, bob, "shared-style-key").status_code == 201
    assert _create_style(client, alice, "shared-style-key").status_code == 409


def test_manifest_schema_rejects_bad_values(client):
    headers = login(client, "skill-manifest")
    bad_tone = _create_style(client, headers, "bad-tone", manifest={"tone": {"x": 1}})
    assert bad_tone.status_code == 422
    assert bad_tone.json()["code"] == "invalid_manifest"

    bad_rules = _create_style(
        client, headers, "bad-rules", manifest={"styleRules": "not-a-list"}
    )
    assert bad_rules.status_code == 422

    bad_theme = _create_style(
        client, headers, "bad-theme", manifest={"defaultTheme": "no-such-theme"}
    )
    assert bad_theme.status_code == 422


def test_checksum_is_consistent_across_seed_and_fork(client):
    headers = login(client, "skill-checksum")
    builtin = next(
        s
        for s in client.get("/api/v1/skills", headers=headers).json()
        if s["skillKey"] == "style-culture-emotion"
    )
    expected = skill_registry.compute_checksum(
        skill_key=builtin["skillKey"],
        version=builtin["version"],
        manifest=builtin["manifest"],
        allowed_tool_ids=builtin["allowedToolIds"],
    )
    assert builtin["checksum"] == expected

    fork_res = client.post(
        f"/api/v1/skills/{builtin['id']}/customize", json={}, headers=headers
    )
    assert fork_res.status_code == 201, fork_res.text
    fork = fork_res.json()
    assert fork["checksum"] == expected

    changed = client.put(
        f"/api/v1/skills/{fork['id']}",
        json={"manifest": {"tone": "换一种语气", "defaultTheme": "mono"}},
        headers=headers,
    )
    assert changed.status_code == 200
    assert changed.json()["checksum"] != expected
    # 编辑响应必须保留「已定制」标记，前端据此展示“恢复默认”
    assert changed.json()["customized"] is True


def test_get_skills_does_not_rewrite_builtin_rows(client):
    """读接口不再执行 seed：手工修正的内置行不会被下一次刷新覆盖。"""
    headers = login(client, "skill-seed-read")
    with SessionLocal() as db:
        entry = db.scalar(
            select(models.SkillRegistryEntry).where(
                models.SkillRegistryEntry.skill_key == "topic-research",
                models.SkillRegistryEntry.owner_user_id.is_(None),
            )
        )
        original = entry.description
        entry.description = "手工修正的描述"
        db.commit()

    try:
        skills = client.get("/api/v1/skills", headers=headers).json()
        row = next(s for s in skills if s["skillKey"] == "topic-research")
        assert row["description"] == "手工修正的描述"
    finally:
        with SessionLocal() as db:
            entry = db.scalar(
                select(models.SkillRegistryEntry).where(
                    models.SkillRegistryEntry.skill_key == "topic-research",
                    models.SkillRegistryEntry.owner_user_id.is_(None),
                )
            )
            entry.description = original
            db.commit()


def test_sync_removes_retired_builtin_and_grants(client):
    headers = login(client, "skill-sync")
    me = _me(client, headers)
    with SessionLocal() as db:
        user = db.get(models.User, me["id"])
        workspace = default_workspace(db, user)
        entry = models.SkillRegistryEntry(
            source="builtin",
            owner_user_id=None,
            skill_key="style-retired-test",
            display_name="已下线风格",
            manifest={},
            checksum="deadbeef",
            trust_status="trusted",
            allowed_tool_ids=[],
            risk_level="read_only",
        )
        db.add(entry)
        db.flush()
        db.add(
            models.WorkspaceSkill(
                workspace_id=workspace.id, skill_entry_id=entry.id, enabled=True
            )
        )
        db.commit()
        entry_id = entry.id

    with SessionLocal() as db:
        counts = skill_registry.sync_builtin_skills(db)
    assert counts["removed"] >= 1

    with SessionLocal() as db:
        assert db.get(models.SkillRegistryEntry, entry_id) is None
        assert db.scalar(
            select(models.WorkspaceSkill).where(
                models.WorkspaceSkill.skill_entry_id == entry_id
            )
        ) is None


def test_article_resolution_ignores_untrusted_and_other_users(client):
    headers = login(client, "skill-resolve")
    me = _me(client, headers)
    with SessionLocal() as db:
        db.add(
            models.SkillRegistryEntry(
                source="custom",
                owner_user_id=me["id"],
                skill_key="untrusted-article-style",
                display_name="未校验风格",
                manifest={"tone": "不该被使用"},
                checksum="untrusted",
                trust_status="untrusted",
                allowed_tool_ids=["generate_article"],
                risk_level="reversible_write",
            )
        )
        db.commit()

    with SessionLocal() as db:
        resolved = ai_article.resolve_skill(db, me["id"], "untrusted-article-style")
        assert resolved is not None
        assert resolved.skill_key == "article-drafting"
        # 他人创建的自定义 Skill 不会进入解析结果，回退到默认内置 Skill
        fallback = ai_article.resolve_skill(db, "someone-else", "alice-private-style")
        assert fallback is None or fallback.skill_key == "article-drafting"


def test_legacy_ownerless_custom_skill_is_hidden(client):
    """旧版创建路径留下的无归属行不再展示，也解析不到（等待 repair 脚本清理）。"""
    headers = login(client, "skill-legacy")
    with SessionLocal() as db:
        db.add(
            models.SkillRegistryEntry(
                source="custom",
                owner_user_id=None,
                skill_key="legacy-global-style",
                display_name="遗留全局风格",
                manifest={"tone": "旧数据"},
                checksum="legacy",
                trust_status="untrusted",
                allowed_tool_ids=["generate_article"],
                risk_level="reversible_write",
            )
        )
        db.commit()

    try:
        keys = [s["skillKey"] for s in client.get("/api/v1/skills", headers=headers).json()]
        assert "legacy-global-style" not in keys
        with SessionLocal() as db:
            resolved = ai_article.resolve_skill(db, "any-user", "legacy-global-style")
            assert resolved is None or resolved.skill_key == "article-drafting"
    finally:
        with SessionLocal() as db:
            row = db.scalar(
                select(models.SkillRegistryEntry).where(
                    models.SkillRegistryEntry.skill_key == "legacy-global-style"
                )
            )
            if row:
                db.delete(row)
                db.commit()


def test_owner_fork_wins_over_builtin_at_runtime(client):
    headers = login(client, "skill-fork-runtime")
    me = _me(client, headers)
    builtin = next(
        s
        for s in client.get("/api/v1/skills", headers=headers).json()
        if s["skillKey"] == "style-food-travel"
    )
    fork = client.post(
        f"/api/v1/skills/{builtin['id']}/customize",
        json={"manifest": {"tone": "本人定制语气", "defaultTheme": "warm-orange"}},
        headers=headers,
    ).json()

    with SessionLocal() as db:
        resolved = ai_article.resolve_skill(db, me["id"], "style-food-travel")
        assert resolved is not None
        assert resolved.id == fork["id"]
        assert resolved.manifest["tone"] == "本人定制语气"


# ---------------- manifest 扩展字段：workflow / reviewRules / factPolicy / imagePolicy / repairPolicy ----------------

FULL_POLICY_MANIFEST = {
    "tone": "克制",
    "workflow": [
        {"tool": "generate_article", "args": {}},
        {"tool": "create_outline", "args": {"maxSections": 5, "strict": True}},
    ],
    "reviewRules": [
        {"id": "no-absolute", "kind": "not_contains", "value": "100%有效", "message": "含绝对化承诺"},
        {
            "id": "has-body",
            "kind": "min_chars",
            "field": "markdown",
            "value": 80,
            "message": "正文过短",
        },
    ],
    "factPolicy": {
        "requireSources": True,
        "forbidUnverifiedQuotes": False,
        "placeholder": "〔待核实〕",
        "extraInstructions": ["数字必须标注来源"],
    },
    "imagePolicy": {
        "coverAspectRatio": "3:4",
        "inlineAspectRatio": "1:1",
        "maxInlineImages": 4,
        "allowAiImages": False,
        "promptStyleSuffix": "clean editorial style",
    },
    "repairPolicy": {"retries": 1, "requireContract": True},
}


def _raise_manifest(payload: dict) -> Exception | None:
    from app.services.skill_manifest import ManifestInvalid, validate_manifest

    try:
        validate_manifest(payload)
    except ManifestInvalid as exc:
        return exc
    return None


def test_manifest_keeps_optional_policy_fields():
    manifest = validate_manifest(FULL_POLICY_MANIFEST)
    assert [step["tool"] for step in manifest["workflow"]] == [
        "generate_article",
        "create_outline",
    ]
    assert manifest["workflow"][1]["args"] == {"maxSections": 5, "strict": True}
    assert [rule["id"] for rule in manifest["reviewRules"]] == ["no-absolute", "has-body"]
    assert manifest["reviewRules"][1]["field"] == "markdown"
    assert manifest["factPolicy"]["requireSources"] is True
    assert manifest["factPolicy"]["extraInstructions"] == ["数字必须标注来源"]
    assert manifest["imagePolicy"]["maxInlineImages"] == 4
    assert manifest["imagePolicy"]["promptStyleSuffix"] == "clean editorial style"
    assert manifest["repairPolicy"] == {"retries": 1, "requireContract": True}


def test_manifest_without_policy_fields_has_no_keys():
    manifest = validate_manifest({"tone": "克制", "styleRules": ["少用形容词"]})
    for key in ("workflow", "reviewRules", "factPolicy", "imagePolicy", "repairPolicy"):
        assert key not in manifest
    assert validate_manifest({}) == {}
    assert validate_manifest(None) == {}


def test_manifest_rejects_invalid_policy_values():
    assert _raise_manifest(
        {"reviewRules": [{"id": "r1", "kind": "starts_with", "value": "x"}]}
    ) is not None
    assert _raise_manifest(
        {"workflow": [{"tool": "generate_article", "args": {}} for _ in range(13)]}
    ) is not None
    assert _raise_manifest({"imagePolicy": {"coverAspectRatio": "2:1"}}) is not None
    assert _raise_manifest({"imagePolicy": {"inlineAspectRatio": "square"}}) is not None
    # 额外守卫：id 重复、未知子字段、参数类型越界
    assert _raise_manifest(
        {
            "reviewRules": [
                {"id": "dup", "kind": "contains", "value": "a"},
                {"id": "dup", "kind": "contains", "value": "b"},
            ]
        }
    ) is not None
    assert _raise_manifest({"factPolicy": {"unknown": True}}) is not None
    assert _raise_manifest(
        {"workflow": [{"tool": "generate_article", "args": {"nested": {"a": 1}}}]}
    ) is not None
    assert _raise_manifest({"repairPolicy": {"retries": 2}}) is not None


def test_validate_workflow_requires_declared_tools(client):
    from app.core.deps import ApiError

    manifest = validate_manifest(FULL_POLICY_MANIFEST)
    skill_registry.validate_workflow(manifest, ["generate_article", "create_outline"])
    try:
        skill_registry.validate_workflow(manifest, ["generate_article"])
    except ApiError as exc:
        assert exc.detail["code"] == "tool_not_in_skill"
    else:
        raise AssertionError("create_outline 未声明，应该失败")

    # 已声明但从未登记的工具（模拟声明与 catalog 漂移）
    drift = validate_manifest(
        {"workflow": [{"tool": "no_such_tool_registered_here", "args": {}}]}
    )
    try:
        skill_registry.validate_workflow(drift, ["no_such_tool_registered_here"])
    except ApiError as exc:
        assert exc.detail["code"] == "unknown_tool"
    else:
        raise AssertionError("未登记工具应该失败")


def test_fork_merges_manifest_by_top_level_key(client):
    headers = login(client, "skill-fork-merge")
    builtin = next(
        s
        for s in client.get("/api/v1/skills", headers=headers).json()
        if s["skillKey"] == "article-drafting"
    )
    assert builtin["manifest"]["reviewRules"]
    assert [s["tool"] for s in builtin["manifest"]["workflow"]][:2] == [
        "create_outline",
        "generate_article",
    ]

    fork_res = client.post(
        f"/api/v1/skills/{builtin['id']}/customize",
        json={"manifest": {"tone": "更口语的定制语气"}},
        headers=headers,
    )
    assert fork_res.status_code == 201, fork_res.text
    manifest = fork_res.json()["manifest"]
    assert manifest["tone"] == "更口语的定制语气"
    assert manifest["reviewRules"] == builtin["manifest"]["reviewRules"]
    assert manifest["workflow"] == builtin["manifest"]["workflow"]
    assert manifest["imagePolicy"] == builtin["manifest"]["imagePolicy"]
    assert fork_res.json()["checksum"] != builtin["checksum"]
