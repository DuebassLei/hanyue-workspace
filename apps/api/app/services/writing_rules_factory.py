"""出厂数据导入（`skill_writing_rules` / `prompt_rules` 的初始化与重建）。

职责边界
--------
- `writing_rules_factory.json` 是出厂数据的**唯一权威源**（由
  `scripts/export_builtin_to_factory.py` 从 YAML 一次性迁移生成）；
  `app/resources/skills/builtin_skills.json` 只是它的派生文件（条目元数据 +
  规则合成后的 manifest，由 `scripts/export_factory_to_builtin.py` 生成、`--check` 校验）。
- 启动导入（`import_factory`）是「缺什么建什么」（R3）：只对库中不存在的
  (skill_key, rule_type, rule_key) 全局行与不存在 key 的 prompt_rules 建行；
  已存在的一律不动——管理员在「创作规则」页改过的全局规则不会在重启时被冲掉。
  副作用即 R4 的前提：删除一行后重启会从出厂恢复，所以界面上的「全部关掉」
  必须落禁用行而不是删行（那是 T4 的写入路径职责）。
- `only_missing=False` 用于显式覆盖：仅覆盖 `source=factory` 的行，界面创建的
  `source=user` 行保留。「按出厂重建」走 `reset_skill_rules_to_factory`（单 Skill 粒度）。

启动顺序：`main.py` lifespan 里 `import_factory` 必须先于 `sync_builtin_skills`——
规则行先到位，条目同步（读派生 JSON）不依赖规则行，但保持先规则后条目的确定性。
"""

from __future__ import annotations

import json

from sqlalchemy import delete, select

from .. import models
from ..core.config import BASE_DIR
from . import writing_rules

FACTORY_RESOURCE = BASE_DIR / "app" / "resources" / "writing_rules_factory.json"


def load_factory() -> dict:
    """读取并结构校验出厂数据；任何畸形都直接 RuntimeError（启动即失败，不静默少数据）。"""
    if not FACTORY_RESOURCE.exists():
        raise RuntimeError(f"缺少出厂数据文件：{FACTORY_RESOURCE}")
    try:
        data = json.loads(FACTORY_RESOURCE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"出厂数据文件不是合法 JSON：{exc}") from exc
    _validate(data)
    return data


def _validate(data) -> None:
    if not isinstance(data, dict):
        raise RuntimeError("出厂数据必须是 JSON 对象")
    if data.get("version") != 1:
        raise RuntimeError("出厂数据 version 必须是 1")
    skills = data.get("skills")
    if not isinstance(skills, list) or not skills:
        raise RuntimeError("出厂数据缺少 skills 列表")
    seen: set[str] = set()
    for item in skills:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not item["key"]:
            raise RuntimeError("skills 条目缺少 key")
        if item["key"] in seen:
            raise RuntimeError(f"出厂 Skill key 重复：{item['key']}")
        seen.add(item["key"])
        if not isinstance(item.get("rules"), list):
            raise RuntimeError(f"出厂 Skill「{item['key']}」缺少 rules 列表")
    prompts = data.get("prompt_rules")
    if not isinstance(prompts, list):
        raise RuntimeError("出厂数据缺少 prompt_rules 列表")
    prompt_keys: set[str] = set()
    for item in prompts:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not item["key"]:
            raise RuntimeError("prompt_rules 条目缺少 key")
        if item["key"] in prompt_keys:
            raise RuntimeError(f"出厂提示词 key 重复：{item['key']}")
        prompt_keys.add(item["key"])


def _rule_row(skill_key: str, rule: dict) -> models.SkillWritingRule:
    return models.SkillWritingRule(
        skill_key=skill_key,
        owner_user_id=None,
        rule_type=rule["ruleType"],
        rule_key=rule["ruleKey"],
        payload=dict(rule.get("payload") or {}),
        sort_order=rule.get("sortOrder", 0),
        enabled=bool(rule.get("enabled", True)),
        source="factory",
    )


def import_factory(db, *, only_missing: bool = True) -> dict:
    """把出厂 JSON 导入规则表与提示词表；幂等，返回建行计数。

    only_missing=True（启动路径）：只建缺失行。
    only_missing=False：缺失行照建；已存在的 factory 行按出厂覆盖；user 行保留。
    不提交的调用方需自行回滚——这里统一 commit（启动与测试都期望落库）。
    """
    data = load_factory()
    rules_created = 0
    for skill in data["skills"]:
        existing = {
            (row.rule_type, row.rule_key): row
            for row in writing_rules.list_rule_rows(db, skill_key=skill["key"], owner_user_id=None)
        }
        for rule in skill["rules"]:
            pair = (rule["ruleType"], rule["ruleKey"])
            row = existing.get(pair)
            if row is not None:
                if only_missing or row.source != "factory":
                    continue
                row.payload = dict(rule.get("payload") or {})
                row.sort_order = rule.get("sortOrder", 0)
                row.enabled = bool(rule.get("enabled", True))
                continue
            db.add(_rule_row(skill["key"], rule))
            rules_created += 1

    prompts_created = 0
    for prompt in data["prompt_rules"]:
        row = db.scalar(select(models.PromptRule).where(models.PromptRule.key == prompt["key"]))
        if row is not None:
            if only_missing or row.source != "factory":
                continue
            row.category = prompt["category"]
            row.title = prompt.get("title", "")
            row.description = prompt.get("description", "")
            row.content = prompt.get("content", "")
            row.payload = dict(prompt.get("payload") or {})
            row.enabled = bool(prompt.get("enabled", True))
            continue
        db.add(
            models.PromptRule(
                key=prompt["key"],
                category=prompt["category"],
                title=prompt.get("title", ""),
                description=prompt.get("description", ""),
                content=prompt.get("content", ""),
                payload=dict(prompt.get("payload") or {}),
                enabled=bool(prompt.get("enabled", True)),
                source="factory",
                version=1,
            )
        )
        prompts_created += 1

    db.commit()
    return {"rules_created": rules_created, "prompts_created": prompts_created}


def reset_skill_rules_to_factory(db, skill_key: str) -> dict:
    """「按出厂重建」：删掉该 Skill 的全部全局规则行（含管理员改过的），按出厂重建。

    只动指定的 skill_key，其他 Skill 与个人副本不受影响；返回 {"count": 重建行数}。
    """
    data = load_factory()
    skill = next((item for item in data["skills"] if item["key"] == skill_key), None)
    if skill is None:
        raise writing_rules.RuleInvalid(f"「{skill_key}」不在出厂数据中，无法按出厂重建")

    db.execute(
        delete(models.SkillWritingRule).where(
            models.SkillWritingRule.skill_key == skill_key,
            models.SkillWritingRule.owner_user_id.is_(None),
        )
    )
    db.flush()
    for rule in skill["rules"]:
        db.add(_rule_row(skill_key, rule))
    db.commit()
    return {"count": len(skill["rules"])}
