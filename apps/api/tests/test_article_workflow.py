"""写作检查工具（article_workflow）的纯函数测试：不连模型、不起服务。"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.services import article_workflow, agent_runtime
from app.services.article_workflow import (
    check_ai_tone,
    extract_facts,
    plan_images,
    retrieve_content,
    review_article,
)


# ---------------------------------------------------------------- review_article


def test_review_not_contains_hit():
    draft = {"title": "标题", "markdown": "这是一篇很棒的文章。", "summary": ""}
    rules = [{"id": "no-great", "kind": "not_contains", "value": "很棒", "field": "markdown"}]
    result = review_article(draft, rules)
    assert result["ok"] is False
    assert result["violations"][0]["id"] == "no-great"
    assert result["violations"][0]["field"] == "markdown"


def test_review_min_chars_pass():
    draft = {"title": "标题", "markdown": "一二三四五六七八九十", "summary": ""}
    rules = [{"id": "min", "kind": "min_chars", "value": 5, "field": "markdown"}]
    assert review_article(draft, rules) == {"ok": True, "violations": []}


def test_review_bad_rules_are_skipped():
    draft = {"title": "标题", "markdown": "正文", "summary": ""}
    rules = [
        "不是字典",
        {"id": "x", "kind": "unknown", "value": 1},
        {"id": "y", "kind": "not_contains", "value": 123},
        {"id": "z", "kind": "min_chars", "value": "abc"},
        None,
    ]
    assert review_article(draft, rules)["ok"] is True
    assert review_article(draft, None)["ok"] is True


def test_review_placeholder_discontinuous_fails():
    draft = {"title": "t", "markdown": "开头 {{image1}} 中间 {{image3}} 结尾", "summary": ""}
    result = review_article(draft, [])
    assert result["ok"] is False
    assert [v["id"] for v in result["violations"]] == ["image-placeholders"]


def test_review_placeholder_duplicate_fails():
    draft = {"title": "t", "markdown": "{{image1}} {{image1}}", "summary": ""}
    result = review_article(draft, [])
    assert result["ok"] is False
    assert result["violations"][0]["id"] == "image-placeholders"


def test_review_placeholder_with_caption_passes():
    draft = {"title": "t", "markdown": "{{image1|一张图}} 正文 {{image2}}", "summary": ""}
    assert review_article(draft, [])["ok"] is True


def test_review_no_placeholder_passes():
    draft = {"title": "t", "markdown": "纯文字，没有图片占位。", "summary": ""}
    assert review_article(draft, []) == {"ok": True, "violations": []}


# ---------------------------------------------------------------- check_ai_tone


def test_ai_tone_paragraph_opener_hit():
    markdown = "第一段正常。\n\n综上所述，这件事值得关注。"
    hits = check_ai_tone(markdown)["hits"]
    openers = [h for h in hits if h["ruleId"] == "sentence-opener"]
    assert openers and openers[0]["excerpt"] == "综上所述"
    assert markdown[openers[0]["offset"] : openers[0]["offset"] + 4] == "综上所述"


def test_ai_tone_mid_paragraph_additionally_not_hit():
    hits = check_ai_tone("先讲一个场景，更长一点的铺垫也要写足。此外还有别的细节。")["hits"]
    assert all(h["excerpt"] != "此外" for h in hits)


def test_ai_tone_buzzword_hit():
    markdown = "这家工厂是行业的重要赋能平台。"
    hits = check_ai_tone(markdown)["hits"]
    buzz = [h for h in hits if h["excerpt"] == "赋能"]
    assert buzz and markdown[buzz[0]["offset"] : buzz[0]["offset"] + 2] == "赋能"


def test_ai_tone_first_then_and_offsets_are_substrings():
    markdown = "首先要做调研，其次要写方案。\n\n抓手很重要。"
    hits = check_ai_tone(markdown)["hits"]
    for hit in hits:
        assert markdown[hit["offset"] : hit["offset"] + len(hit["excerpt"])] == hit["excerpt"]
    assert {h["ruleId"] for h in hits} >= {"first-then", "buzzword-leverage"}


# ---------------------------------------------------------------- extract_facts


def test_extract_facts_numbers_and_quotes():
    markdown = "他说「这件事必须慢慢来，急不得」。转化率提升 12%，市场规模达到 3.5 亿，2024 年落地。"
    facts = extract_facts(markdown)
    assert "12%" in facts["numbers"]
    assert "3.5 亿" in facts["numbers"]
    assert "2024 年" in facts["numbers"]
    assert "这件事必须慢慢来，急不得" in facts["quotes"]
    assert len(facts["numbers"]) == len(set(facts["numbers"]))


def test_extract_facts_limits():
    markdown = " ".join(f"{i}%" for i in range(1, 51))
    assert len(extract_facts(markdown)["numbers"]) <= 30


# ---------------------------------------------------------------- plan_images


def test_plan_images_truncates_without_calling_it_a_mismatch():
    """条数一致但超过上限，只截断，不算错配。"""
    markdown = "{{image1}} 前文 {{image2}} 中段 {{image3}}"
    prompts = ["p1", "p2", "p3"]
    plan = plan_images(markdown, prompts, "cover prompt", {"maxInlineImages": 1})
    assert len(plan["inline"]) == 1
    assert plan["inline"][0] == {"index": 1, "prompt": "p1", "ratio": "1:1"}
    assert plan["issue"] is None
    assert plan["cover"] == {"prompt": "cover prompt", "ratio": "3:4"}


def test_plan_images_flags_real_count_mismatch():
    plan = plan_images("{{image1}} {{image2}}", ["only-one"], "cover", None)
    assert plan["issue"] == "placeholder_mismatch"
    assert len(plan["inline"]) == 1


def test_plan_images_allow_ai_images_passthrough():
    plan = plan_images("{{image1}}", ["p1"], None, {"allowAiImages": False})
    assert plan["allowAiImages"] is False
    assert plan["cover"] is None
    assert plan["issue"] is None
    assert len(plan["inline"]) == 1


def test_plan_images_defaults_without_policy():
    plan = plan_images("无占位正文", [], None, None)
    assert plan == {"allowAiImages": True, "cover": None, "inline": [], "issue": None}


# ---------------------------------------------------------------- retrieve_content


@pytest.fixture()
def memory_db(monkeypatch):
    """SQLite 内存会话：只建本测试用到的 content_items 表，不碰开发库/测试库。"""
    from app import models

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    models.ContentItem.__table__.create(engine)
    models.ContentVersion.__table__.create(engine)
    models.AgentToolCall.__table__.create(engine)
    models.AgentEvent.__table__.create(engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(engine) as db:
        db.add_all(
            [
                models.ContentItem(
                    id="c1", workspace_id="ws-a", owner_user_id="u1", title="新能源汽车出海观察",
                    created_at=now, updated_at=now,
                ),
                models.ContentItem(
                    id="c2", workspace_id="ws-a", owner_user_id="u1", title="AI 100%_干货",
                    created_at=now, updated_at=now + timedelta(seconds=1),
                ),
                models.ContentItem(
                    id="c3", workspace_id="ws-b", owner_user_id="u1", title="新能源汽车别人的稿子",
                    created_at=now, updated_at=now + timedelta(seconds=2),
                ),
            ]
        )
        db.commit()
        yield db
    engine.dispose()


def test_retrieve_only_workspace_and_latest_first(memory_db):
    result = retrieve_content(memory_db, "ws-a", "")
    assert result["count"] == 2
    assert [i["id"] for i in result["items"]] == ["c2", "c1"]
    assert set(result["items"][0]) == {"id", "title"}


def test_retrieve_query_like_escaped(memory_db):
    result = retrieve_content(memory_db, "ws-a", "新能源")
    assert [i["id"] for i in result["items"]] == ["c1"]
    # % 与 _ 按字面匹配，不当作通配符
    assert [i["id"] for i in retrieve_content(memory_db, "ws-a", "100%_")["items"]] == ["c2"]
    assert retrieve_content(memory_db, "ws-a", "%")["count"] == 1


def test_retrieve_limit_capped(memory_db, monkeypatch):
    assert retrieve_content(memory_db, "ws-a", "", limit=100)["count"] == 2
    assert len(retrieve_content(memory_db, "ws-a", "", limit=1)["items"]) == 1


# ---------------------------------------------------------------- Agent 接入


def test_system_prompt_includes_fact_and_image_policy_only_when_present():
    from types import SimpleNamespace

    from app.services.ai_article import build_system_prompt

    drafting = SimpleNamespace(
        manifest={
            "tone": "平实",
            "styleRules": ["不用 emoji"],
            "factPolicy": {"forbidUnverifiedQuotes": True, "placeholder": "〔待核实〕"},
            "imagePolicy": {
                "maxInlineImages": 3,
                "coverAspectRatio": "3:4",
                "inlineAspectRatio": "1:1",
                "allowAiImages": False,
            },
        }
    )
    prompt = build_system_prompt(drafting)
    assert "〔待核实〕" in prompt
    assert "不要编造引语" in prompt
    assert "最多 3 张" in prompt
    assert "3:4" in prompt
    assert "不要为封面或插图编写 AI 生图提示词" in prompt

    style_only = SimpleNamespace(manifest={"tone": "克制", "styleRules": ["禁赋能"]})
    bare = build_system_prompt(style_only)
    assert "事实约束" not in bare
    assert "配图约束" not in bare
    assert "克制" in bare


def test_two_workflow_skills_fail_the_run():
    from app import models

    class Session:
        def __init__(self, rows):
            self.rows = rows

        def get(self, model, key):
            return self.rows.get(key)

    flow_a = models.SkillRegistryEntry(
        skill_key="article-drafting",
        trust_status="trusted",
        manifest={"workflow": [{"tool": "generate_article", "args": {}}]},
    )
    flow_b = models.SkillRegistryEntry(
        skill_key="article-drafting-tech",
        trust_status="trusted",
        manifest={"workflow": [{"tool": "generate_article", "args": {}}]},
    )
    style = models.SkillRegistryEntry(
        skill_key="style-tech-article",
        trust_status="trusted",
        manifest={"tone": "克制"},
    )
    db = Session({"a": flow_a, "b": flow_b, "s": style})
    run = type(
        "Run",
        (),
        {"permission_snapshot": {"skillIds": ["a", "s"]}, "goal": "写"},
    )()
    assert agent_runtime.resolve_workflow_skill(db, run).skill_key == "article-drafting"

    conflict = type(
        "Run",
        (),
        {"permission_snapshot": {"skillIds": ["a", "b"]}, "goal": "写"},
    )()
    with pytest.raises(agent_runtime.WorkflowConflict) as caught:
        agent_runtime.resolve_workflow_skill(db, conflict)
    assert caught.value.keys == ["article-drafting", "article-drafting-tech"]

    steps = agent_runtime.steps_from_workflow(flow_a, "写一篇稿")
    assert steps[0]["tool"] == "generate_article"
    assert steps[0]["args"]["prompt"] == "写一篇稿"
    with pytest.raises(agent_runtime.AgentRuntimeError):
        agent_runtime.assert_workflow_authorized(steps, ["review_article"])


def test_plan_steps_appends_review_checks():
    steps = agent_runtime.plan_steps(
        "写篇文章", ["generate_article", "review_article", "check_ai_tone", "plan_images", "retrieve_content"]
    )
    assert [s["tool"] for s in steps] == [
        "generate_article",
        "review_article",
        "check_ai_tone",
        "plan_images",
    ]


def test_plan_steps_unchanged_without_review():
    from app.services import agent_policy

    assert agent_policy.tool_risk("review_article") == agent_policy.READ_ONLY
    assert [s["tool"] for s in agent_runtime.plan_steps("写", ["generate_article"])] == [
        "generate_article"
    ]


def test_writing_check_returns_no_draft_when_run_has_no_version(monkeypatch):
    class Run:
        id = "run-none"
        workspace_id = "ws-a"
        goal = "检查"

    monkeypatch.setattr(agent_runtime, "_find_recent_version_id", lambda db, run: None)
    result = asyncio.run(
        agent_runtime._execute_tool(None, Run(), "review_article", {})  # type: ignore[arg-type]
    )
    assert result == {"error": "no_draft"}


def test_writing_check_rejects_other_workspace_version(memory_db, monkeypatch):
    from app import models

    result = asyncio.run(
        agent_runtime._execute_tool(
            memory_db,
            type("Run", (), {"id": "r", "workspace_id": "ws-a", "goal": "g"})(),  # type: ignore[arg-type]
            "check_ai_tone",
            {"contentVersionId": "v-other"},
        )
    )
    assert result == {"error": "not_found"}


def test_writing_check_on_own_version(memory_db):
    from app import models

    item = models.ContentItem(
        id="it1", workspace_id="ws-a", owner_user_id="u1", title="标题", created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    version = models.ContentVersion(id="v1", content_item_id="it1", title="标题", body="综上所述，赋能一切。")
    memory_db.add_all([item, version])
    memory_db.commit()
    run = type("Run", (), {"id": "r1", "workspace_id": "ws-a", "goal": "g", "permission_snapshot": {}})()

    tone = asyncio.run(agent_runtime._execute_tool(memory_db, run, "check_ai_tone", {}))  # type: ignore[arg-type]
    assert tone["versionId"] == "v1"
    assert tone["hits"]

    review = asyncio.run(
        agent_runtime._execute_tool(memory_db, run, "review_article", {"contentVersionId": "v1"})  # type: ignore[arg-type]
    )
    assert review["ok"] is True

    facts = asyncio.run(agent_runtime._execute_tool(memory_db, run, "extract_facts", {}))  # type: ignore[arg-type]
    assert facts == {"numbers": [], "quotes": [], "versionId": "v1"}

    plan = asyncio.run(agent_runtime._execute_tool(memory_db, run, "plan_images", {}))  # type: ignore[arg-type]
    assert plan["inline"] == [] and plan["cover"] is None and plan["issue"] is None
