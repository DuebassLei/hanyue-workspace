#!/usr/bin/env python
"""一次性导出脚本：`builtin_skills.yaml` + 代码常量 → `writing_rules_factory.json`。

为什么需要它
-----------
出厂数据迁移（计划 2026-11-20-writing-rules-management / Task T1）要求规则**逐字**落到
`skill_writing_rules` 表，YAML 退役为派生文件。本脚本在开发期运行一次，把：

  - YAML 中每个内置 Skill 的 manifest 规则型 key → 拆成一条条规则行
    （payload 经 `writing_rules.validate_rule` 规范化，与运行期入库形状完全一致）；
  - 分散在代码常量里的提示词/词表 → `prompt_rules` 出厂 key（内容取常量原文，禁止改写）。

生成 `app/resources/writing_rules_factory.json`——出厂数据的唯一权威源。
此后 YAML 不再是输入：改规则走「创作规则」页或直接改 JSON（并重跑
`export_factory_to_builtin.py` 同步派生文件）。

用法（在 apps/api 目录下）：

    python scripts/export_builtin_to_factory.py            # 生成/覆盖出厂 JSON

脚本不做 --check：YAML 是一次性输入，退役后本脚本保留仅作迁移档案。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import yaml  # noqa: E402

from app.services import (  # noqa: E402
    agent_runtime,
    ai_article,
    ai_gateway,
    article_workflow,
    writing_rules,
)

YAML_RESOURCE = API_DIR / "app" / "resources" / "skills" / "builtin_skills.yaml"
FACTORY_RESOURCE = API_DIR / "app" / "resources" / "writing_rules_factory.json"

# 原 agent_runtime.generate_article 里的注入字面量（逐字迁移，不改写）
OUTLINE_INJECT_TEXT = "参考大纲（结构可微调，不要照抄标题）："

# 单行类规则排序号恒为 0；列表类在各自类型内从 0 递增
SINGLE_TYPES = frozenset(
    {"tone", "prompt_template", "theme", "target_length", "fact_policy", "image_policy", "repair_policy"}
)


def _manifest_to_rules(manifest: dict) -> list[dict]:
    """manifest 规则型 key → 原始规则行（键序贴近 YAML 阅读顺序）。"""
    raw: list[dict] = []
    if "tone" in manifest:
        raw.append({"ruleType": "tone", "payload": {"text": manifest["tone"]}})
    for i, text in enumerate(manifest.get("styleRules") or [], start=1):
        raw.append({"ruleType": "style_rule", "ruleKey": f"sr-{i}", "payload": {"text": text}})
    if "defaultTheme" in manifest:
        raw.append({"ruleType": "theme", "payload": {"text": manifest["defaultTheme"]}})
    if "targetLength" in manifest:
        raw.append({"ruleType": "target_length", "payload": {"value": manifest["targetLength"]}})
    for i, step in enumerate(manifest.get("workflow") or [], start=1):
        raw.append(
            {
                "ruleType": "workflow_step",
                "ruleKey": f"wf-{i}",
                "payload": {"tool": step["tool"], "args": dict(step.get("args") or {})},
            }
        )
    for item in manifest.get("reviewRules") or []:
        raw.append({"ruleType": "review_rule", "ruleKey": item["id"], "payload": dict(item)})
    for manifest_key, rule_type in (
        ("factPolicy", "fact_policy"),
        ("imagePolicy", "image_policy"),
        ("repairPolicy", "repair_policy"),
    ):
        if manifest_key in manifest:
            raw.append({"ruleType": rule_type, "payload": dict(manifest[manifest_key])})
    for i, item in enumerate(manifest.get("exemplars") or [], start=1):
        raw.append({"ruleType": "exemplar", "ruleKey": f"ex-{i}", "payload": dict(item)})

    # 规范化：payload 形状与运行期写入完全一致（策略块过 Schema、文本去空白）
    normalized = [writing_rules.validate_rule(rule) for rule in raw]
    counters: dict[str, int] = {}
    for rule in normalized:
        rule_type = rule["ruleType"]
        if rule_type in SINGLE_TYPES:
            rule["sortOrder"] = 0
        else:
            rule["sortOrder"] = counters.get(rule_type, 0)
            counters[rule_type] = rule["sortOrder"] + 1
    return normalized


def _prompt_rules() -> list[dict]:
    """prompt_rules 出厂 key：内容取代码常量原文（迁移前位置见各 description）。"""
    return [
        {
            "key": "article.output_contract",
            "category": "contract",
            "title": "文章输出契约",
            "description": "AI 成文的不可覆盖输出契约（原 ai_article.ARTICLE_OUTPUT_CONTRACT）",
            "content": ai_article.ARTICLE_OUTPUT_CONTRACT,
            "payload": {},
            "enabled": True,
        },
        {
            "key": "gateway.system_rules",
            "category": "system",
            "title": "生成网关系统规则",
            "description": "所有生成入口共用的系统级约束（原 ai_gateway.SYSTEM_RULES）",
            "content": ai_gateway.SYSTEM_RULES,
            "payload": {},
            "enabled": True,
        },
        {
            "key": "agent.outline_system",
            "category": "outline",
            "title": "大纲系统提示词",
            "description": "create_outline 工具的系统提示词（原 agent_runtime.OUTLINE_SYSTEM_PROMPT）",
            "content": agent_runtime.OUTLINE_SYSTEM_PROMPT,
            "payload": {},
            "enabled": True,
        },
        {
            "key": "agent.outline_inject",
            "category": "workflow_note",
            "title": "大纲注入指引",
            "description": "generate_article 拼prompt 时的参考大纲注入字面量（原 agent_runtime 内联文案）",
            "content": OUTLINE_INJECT_TEXT,
            "payload": {},
            "enabled": True,
        },
        {
            "key": "tone_lexicon.sentence_openers",
            "category": "tone_lexicon",
            "title": "段首套话词表",
            "description": "AI 味检查的段首套话（原 article_workflow._SENTENCE_START_PHRASES）",
            "content": "",
            "payload": {
                "window": article_workflow._LEAD_WINDOW,
                "phrases": list(article_workflow._SENTENCE_START_PHRASES),
            },
            "enabled": True,
        },
        {
            "key": "tone_lexicon.buzzwords",
            "category": "tone_lexicon",
            "title": "商业黑话词表",
            "description": "AI 味检查的词级规则（原 article_workflow._WORD_RULES）",
            "content": "",
            "payload": {"rules": [{"id": rid, "word": word} for rid, word in article_workflow._WORD_RULES]},
            "enabled": True,
        },
        {
            "key": "tone_lexicon.first_then",
            "category": "tone_lexicon",
            "title": "首先其次规则",
            "description": "AI 味检查的「首先/其次」罗列腔（原 article_workflow 内联规则）",
            "content": "",
            "payload": {"words": ["首先", "其次"], "ruleId": "first-then"},
            "enabled": True,
        },
    ]


def build_factory() -> dict:
    if not YAML_RESOURCE.exists():
        raise SystemExit(f"缺少迁移源文件：{YAML_RESOURCE}")
    raw = yaml.safe_load(YAML_RESOURCE.read_text(encoding="utf-8")) or {}
    items = raw.get("skills")
    if not isinstance(items, list) or not items:
        raise SystemExit(f"{YAML_RESOURCE} 缺少 skills 列表")

    skills: list[dict] = []
    seen: set[str] = set()
    for item in items:
        key = item["key"]
        if key in seen:
            raise SystemExit(f"内置 Skill key 重复：{key}")
        seen.add(key)
        skills.append(
            {
                "key": key,
                "version": item.get("version", "1.0.0"),
                "name": item["name"],
                "description": item.get("description", ""),
                "category": item.get("category", "flow"),
                "tools": list(item.get("tools") or []),
                "rules": _manifest_to_rules(item.get("manifest") or {}),
            }
        )
    return {"version": 1, "skills": skills, "prompt_rules": _prompt_rules()}


def main() -> None:
    factory = build_factory()
    total_rules = sum(len(skill["rules"]) for skill in factory["skills"])
    print(f"skills: {len(factory['skills'])}，规则行: {total_rules}，prompt_rules: {len(factory['prompt_rules'])}")
    FACTORY_RESOURCE.write_text(
        json.dumps(factory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"已写入 {FACTORY_RESOURCE}")


if __name__ == "__main__":
    main()
