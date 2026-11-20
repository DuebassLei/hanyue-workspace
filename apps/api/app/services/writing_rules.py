"""写作规则表（`skill_writing_rules`）的纯逻辑层。

职责边界
--------
- 这里只管「规则行 ↔ manifest 规则型 key」的换算与校验，不碰 HTTP、不碰 Skill 条目生命周期；
- **不负责判断鼠标下的 skill_key 是不是「当前生效的那个」**。用户可以拥有与内置同名的自定义
  Skill（owner 行优先于内置行），调用方必须先解析出生效条目，再把它的 `skill_key`
  传进来，否则会读到另一条 Skill 的规则。
- manifest 里的规则型 key 以规则表为准：规则表里有这个类型就覆盖，没有才回退 manifest 残留
  （兼容迁移期的出厂 YAML 与尚未迁走的旧数据）。

「有效规则」的口径（`effective_rows`）
--------------------------------------
个人副本是**按类型**覆盖，而不是整份拷贝：
- 单行类（tone / prompt_template / target_length / fact_policy / image_policy /
  repair_policy）：个人有该类型就用个人的，没有沿用全局；
- 列表类（style_rule / workflow_step / review_rule / exemplar）：个人该类型的**启用行非空**
  时整套替换全局，否则沿用全局。先过滤禁用行再判断「非空」，这样用户「把某类规则全部关掉」
  表达的就是「沿用全局」，而不是被迫先把全局抄一份再逐条禁用。
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from pydantic import ValidationError
from sqlalchemy import delete, select

from .. import models
from .skill_manifest import (
    MAX_EXEMPLARS,
    MAX_EXEMPLAR_EXCERPT,
    MAX_EXEMPLAR_TITLE,
    MAX_REVIEW_RULES,
    MAX_RULES,
    MAX_RULE_LENGTH,
    MAX_TONE,
    MAX_WORKFLOW_STEPS,
    FactPolicy,
    ImagePolicy,
    ManifestInvalid,
    RepairPolicy,
    ReviewRule,
    SkillManifest,
    validate_manifest,
)

RULE_TYPES: tuple[str, ...] = (
    "tone",
    "style_rule",
    "prompt_template",
    "target_length",
    "workflow_step",
    "review_rule",
    "fact_policy",
    "image_policy",
    "repair_policy",
    "exemplar",
)

# 规则类型的中文名，管理页分组标题与校验消息共用
RULE_TYPE_LABELS: dict[str, str] = {
    "tone": "语气",
    "style_rule": "风格规则",
    "prompt_template": "提示词模板",
    "target_length": "目标字数",
    "workflow_step": "工作流步骤",
    "review_rule": "审核规则",
    "fact_policy": "事实策略",
    "image_policy": "配图策略",
    "repair_policy": "修复策略",
    "exemplar": "开头范例",
}

# 单行类：一个 Skill 每类至多一行，个人副本按类型覆盖全局
SINGLE_TYPES = frozenset(
    {"tone", "prompt_template", "target_length", "fact_policy", "image_policy", "repair_policy"}
)
# 列表类：多条，合成成 manifest 的一个数组
LIST_TYPES = frozenset(set(RULE_TYPES) - SINGLE_TYPES)

MAX_RULE_KEY = 64
MAX_SWR_PAYLOAD_CHARS = 8000
DEFAULT_RULE_KEY = "default"

_TEXT_MAX = {
    "tone": MAX_TONE,
    "style_rule": MAX_RULE_LENGTH,
    "exemplar": None,  # 拆成 title/excerpt 两段校验
}
_LIST_LIMITS = {
    "style_rule": MAX_RULES,
    "workflow_step": MAX_WORKFLOW_STEPS,
    "exemplar": MAX_EXEMPLARS,
    # 审稿规则此前漏了限额：manifest 校验只在上限校验时才生效，
    # 成批写入时可以塞进超过 MAX_REVIEW_RULES 条，这里补齐。
    "review_rule": MAX_REVIEW_RULES,
}


class RuleInvalid(ValueError):
    """规则行非法；消息为中文，调用方直接转 422 `invalid_rule`。"""


class RuleNotFound(LookupError):
    """要删/改的规则行不存在；调用方转 404 `rule_not_found`。"""


# ---------------- 规范化 ----------------


def _label(rule_type: str) -> str:
    return RULE_TYPE_LABELS.get(rule_type, rule_type)


def _fail(rule_type: str, message: str) -> RuleInvalid:
    return RuleInvalid(f"「{_label(rule_type)}」规则无效：{message}")


def _schema_errors(rule_type: str, exc: ValidationError) -> RuleInvalid:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"]) or "payload"
        message = err["msg"].removeprefix("Value error, ")
        parts.append(f"{loc}：{message}")
    return _fail(rule_type, "；".join(parts))


def _require_text(payload: dict, rule_type: str, field: str = "text") -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _fail(rule_type, f"缺少 {field}（不能为空）")
    text = value.strip()
    limit = _TEXT_MAX.get(rule_type)
    if limit is not None and len(text) > limit:
        raise _fail(rule_type, f"{field} 不能超过 {limit} 字")
    return text


def _check_single(rule_type: str, payload: dict) -> dict:
    if rule_type == "tone":
        return {"text": _require_text(payload, rule_type)}
    if rule_type == "prompt_template":
        return {"text": _require_text(payload, rule_type)}
    if rule_type == "target_length":
        value = payload.get("value")
        if isinstance(value, bool) or not isinstance(value, int):
            raise _fail(rule_type, "value 必须是整数")
        return {"value": value}
    if rule_type in ("fact_policy", "image_policy", "repair_policy"):
        model = {"fact_policy": FactPolicy, "image_policy": ImagePolicy, "repair_policy": RepairPolicy}[rule_type]
        try:
            return model.model_validate(payload).model_dump(exclude_none=True)
        except ValidationError as exc:
            raise _schema_errors(rule_type, exc) from exc
    raise _fail(rule_type, "不是单行类规则")


def _check_list_item(rule_type: str, payload: dict, rule_key: str) -> dict:
    if rule_type == "style_rule":
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise _fail(rule_type, "缺少文本（text 不能为空）")
        return {"text": text.strip()}
    if rule_type == "workflow_step":
        tool = payload.get("tool")
        if not isinstance(tool, str) or not tool.strip():
            raise _fail(rule_type, "缺少工具名（tool 不能为空）")
        return {"tool": tool.strip(), "args": dict(payload.get("args") or {})}
    if rule_type == "review_rule":
        data = dict(payload)
        data.setdefault("id", rule_key)
        if data.get("id") != rule_key:
            raise _fail(rule_type, f"payload.id 与规则键不一致（{data.get('id')} ≠ {rule_key}）")
        try:
            return ReviewRule.model_validate(data).model_dump(exclude_none=True)
        except ValidationError as exc:
            raise _schema_errors(rule_type, exc) from exc
    if rule_type == "exemplar":
        title = payload.get("title")
        if not isinstance(title, str) or not title.strip():
            raise _fail(rule_type, "缺少标题（title 不能为空）")
        title = title.strip()
        if len(title) > MAX_EXEMPLAR_TITLE:
            raise _fail(rule_type, f"标题不能超过 {MAX_EXEMPLAR_TITLE} 字")
        excerpt = payload.get("excerpt")
        if excerpt is None:
            excerpt = ""
        if not isinstance(excerpt, str):
            raise _fail(rule_type, "excerpt 必须是字符串")
        excerpt = excerpt.strip()
        if len(excerpt) > MAX_EXEMPLAR_EXCERPT:
            raise _fail(rule_type, f"摘录不能超过 {MAX_EXEMPLAR_EXCERPT} 字")
        return {"title": title, "excerpt": excerpt}
    raise _fail(rule_type, "不是列表类规则")


def _normalize_payload(rule_type: str, rule_key: str, payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise _fail(rule_type, "payload 必须是对象")
    if rule_type in SINGLE_TYPES:
        return _check_single(rule_type, payload)
    return _check_list_item(rule_type, payload, rule_key)


def validate_rule(rule: dict) -> dict:
    """校验单行规则，返回规范化后的 {ruleType, ruleKey, payload, sortOrder, enabled}。

    单行类 payload 必须是该类型的结构；列表类必须带 payload 内的业务键。
    失败抛 RuleInvalid（中文消息）。
    """
    if not isinstance(rule, dict):
        raise RuleInvalid("规则必须是对象")
    raw_type = rule.get("ruleType", rule.get("rule_type"))
    if not isinstance(raw_type, str) or not raw_type.strip():
        raise RuleInvalid("缺少规则类型 ruleType")
    rule_type = raw_type.strip()
    if rule_type not in RULE_TYPES:
        raise RuleInvalid(f"未知规则类型「{rule_type}」")

    raw_key = rule.get("ruleKey", rule.get("rule_key"))
    rule_key = raw_key.strip() if isinstance(raw_key, str) else ""
    if not rule_key:
        if rule_type in SINGLE_TYPES:
            rule_key = DEFAULT_RULE_KEY
        else:
            message = "列表类规则必须提供规则键 ruleKey"
            if rule_type == "exemplar":
                message += "（如 ex-1）"
            elif rule_type == "workflow_step":
                message += "（如 wf-1）"
            raise _fail(rule_type, message)
    if len(rule_key) > MAX_RULE_KEY:
        raise _fail(rule_type, f"规则键不能超过 {MAX_RULE_KEY} 字符")

    if "sortOrder" in rule or "sort_order" in rule:
        raw_sort = rule["sortOrder"] if "sortOrder" in rule else rule["sort_order"]
        if isinstance(raw_sort, bool) or not isinstance(raw_sort, int):
            raise _fail(rule_type, "sortOrder 必须是整数")
        sort_order = raw_sort
    else:
        sort_order = 0

    enabled = rule.get("enabled", True)
    if not isinstance(enabled, bool):
        raise _fail(rule_type, "enabled 必须是布尔值")

    payload = _normalize_payload(rule_type, rule_key, rule.get("payload", {}))

    # 兜底走一遍 manifest 校验：字数区间、提示词长度等全局约束
    # 都只在这里写一份，避免与 manifest 校验各维护一套。
    try:
        validate_manifest(_payload_to_manifest(rule_type, payload))
    except ManifestInvalid as exc:
        raise _fail(rule_type, str(exc)) from exc

    return {
        "ruleType": rule_type,
        "ruleKey": rule_key,
        "payload": payload,
        "sortOrder": sort_order,
        "enabled": enabled,
    }


# ---------------- 行 ↔ manifest ----------------


def _payload_to_manifest(rule_type: str, payload: dict) -> dict:
    """单行 → manifest 片段，用于复用 SkillManifest 做全局约束校验。"""
    if rule_type in ("tone", "prompt_template"):
        key = {"tone": "tone", "prompt_template": "promptTemplate"}[rule_type]
        return {key: payload.get("text")}
    if rule_type == "target_length":
        return {"targetLength": payload.get("value")}
    if rule_type == "fact_policy":
        return {"factPolicy": payload}
    if rule_type == "image_policy":
        return {"imagePolicy": payload}
    if rule_type == "repair_policy":
        return {"repairPolicy": payload}
    if rule_type == "exemplar":
        return {"exemplars": [payload]}
    if rule_type == "style_rule":
        return {"styleRules": [payload.get("text") or "x"]}
    if rule_type == "workflow_step":
        return {"workflow": [payload]}
    if rule_type == "review_rule":
        return {"reviewRules": [payload]}
    return {}


def _ordered(rows: Iterable) -> list:
    return sorted(rows, key=lambda row: (row.sort_order or 0, row.created_at or 0, row.id))


def compose_manifest(entry, rows: list) -> dict:
    """合成规则型 key，并与 entry.manifest 元数据合并。

    rows 应是已经按 owner 优先级挑选好的行（见 `effective_rows`）。口径：
    - 规则表里**有**某一类型的行，就以规则行（仅启用行）为准，manifest 里同类型残留被覆盖；
      该类型全部被禁用时产出空数组 / 不产出，**不回退** manifest——用户关掉就该是关掉；
    - 规则表里**没有**某一类型，manifest 残留（迁移期历史数据）先按 Schema 规范化再原样带出。
    """
    manifest = {
        key: value for key, value in (getattr(entry, "manifest", None) or {}).items()
        if key not in {"defaultTheme", "theme", "theme_id", "themeId"}
    }
    # 未 flush 的新对象 enabled 还是 None（列默认值要到 INSERT 才生效），按「启用」处理
    ordered = _ordered(row for row in rows if row.rule_type in RULE_TYPES)
    present = {row.rule_type for row in ordered}
    by_type: dict[str, list[dict]] = {}
    for row in ordered:
        if row.enabled is not False:
            by_type.setdefault(row.rule_type, []).append(row.payload or {})

    for rule_type in present:
        payloads = by_type.get(rule_type, [])
        key = next(iter(_MANIFEST_KEYS_BY_TYPE[rule_type]))
        if rule_type in ("tone", "prompt_template"):
            text = payloads[0].get("text") if payloads else ""
            manifest.pop(key, None)
            if text:
                manifest[key] = text
        elif rule_type == "target_length":
            value = payloads[0].get("value") if payloads else None
            manifest.pop(key, None)
            if isinstance(value, int) and not isinstance(value, bool):
                manifest[key] = value
        elif rule_type in _POLICY_MODELS:
            # 存的是规范化后的完整策略块；再走一遍 Schema 兜住手工改库写进的半截数据
            manifest.pop(key, None)
            if payloads:
                try:
                    manifest[key] = _POLICY_MODELS[rule_type].model_validate(
                        payloads[0]
                    ).model_dump(exclude_none=True)
                except ValidationError:
                    manifest[key] = dict(payloads[0])
        elif rule_type == "style_rule":
            manifest["styleRules"] = [p["text"] for p in payloads if (p.get("text") or "").strip()]
        elif rule_type == "workflow_step":
            manifest["workflow"] = [{"tool": p.get("tool"), "args": dict(p.get("args") or {})} for p in payloads]
        elif rule_type == "review_rule":
            manifest["reviewRules"] = [dict(p) for p in payloads]
        elif rule_type == "exemplar":
            manifest["exemplars"] = [dict(p) for p in payloads]

    # 规则表里没有的类型：manifest 残留先按 Schema 规范化（补齐策略默认值、去掉 None）。
    # 非法残留不阻断合成——读路径不该因脏数据 500。
    carried_keys = set().union(
        *(_MANIFEST_KEYS_BY_TYPE[rule_type] for rule_type in RULE_TYPES if rule_type not in present)
    )
    carried = {key: value for key, value in manifest.items() if key in carried_keys}
    if carried:
        try:
            cleaned = validate_manifest(carried)
        except ManifestInvalid:
            cleaned = carried
        for key in carried:
            if key in cleaned:
                manifest[key] = cleaned[key]
    return manifest


_MANIFEST_KEYS_BY_TYPE: dict[str, set[str]] = {
    "tone": {"tone"},
    "prompt_template": {"promptTemplate"},
    "target_length": {"targetLength"},
    "fact_policy": {"factPolicy"},
    "image_policy": {"imagePolicy"},
    "repair_policy": {"repairPolicy"},
    "style_rule": {"styleRules"},
    "workflow_step": {"workflow"},
    "review_rule": {"reviewRules"},
    "exemplar": {"exemplars"},
}
_POLICY_MODELS = {"fact_policy": FactPolicy, "image_policy": ImagePolicy, "repair_policy": RepairPolicy}


# ---------------- 查询与写入 ----------------


def list_rule_rows(db, *, skill_key: str, owner_user_id: str | None) -> list:
    """同一 owner 范围内的全部规则行（含禁用），按类型 + 排序返回。"""
    stmt = (
        select(models.SkillWritingRule)
        .where(models.SkillWritingRule.skill_key == skill_key)
        .order_by(
            models.SkillWritingRule.rule_type,
            models.SkillWritingRule.sort_order,
            models.SkillWritingRule.created_at,
        )
    )
    if owner_user_id is None:
        stmt = stmt.where(models.SkillWritingRule.owner_user_id.is_(None))
    else:
        stmt = stmt.where(models.SkillWritingRule.owner_user_id == owner_user_id)
    return list(db.scalars(stmt).all())


def effective_rows(db, skill_key: str, owner_user_id: str | None) -> list:
    """个人行覆盖全局：单行类按类型覆盖；列表类个人启用行非空则整体替换。"""
    global_rows = list_rule_rows(db, skill_key=skill_key, owner_user_id=None)
    if not owner_user_id:
        return global_rows
    personal_rows = list_rule_rows(db, skill_key=skill_key, owner_user_id=owner_user_id)
    if not personal_rows:
        return global_rows

    personal_enabled_types = {row.rule_type for row in personal_rows if row.enabled is not False}
    result: list = []
    for row in global_rows:
        if row.rule_type in personal_enabled_types:
            continue
        result.append(row)
    result.extend(personal_rows)
    return result


def effective_manifest(db, entry, owner_user_id: str | None = None) -> dict:
    """运行期读 manifest 的统一入口：条目元数据 + 规则表合成的规则型 key。

    T2 之后下游（ai_article / agent_runtime / content）一律经这里取规则，
    不再直接读 entry.manifest 的规则型 key。db 为 None 时退回条目 manifest
    （纯单测场景）；entry 为 None 返回空 dict，调用方走各自的内置兜底。
    """
    if entry is None:
        return {}
    if db is None:
        manifest = entry.manifest if isinstance(entry.manifest, dict) else {}
        return {
            key: value for key, value in manifest.items()
            if key not in {"defaultTheme", "theme", "theme_id", "themeId"}
        }
    rows = effective_rows(db, entry.skill_key, owner_user_id)
    return compose_manifest(entry, rows)


def _validate_batch(rules: Sequence[dict]) -> list[dict]:
    if not isinstance(rules, (list, tuple)):
        raise RuleInvalid("规则必须是数组")
    normalized = [validate_rule(rule) for rule in rules]
    seen: set[tuple[str, str]] = set()
    for rule in normalized:
        pair = (rule["ruleType"], rule["ruleKey"])
        if pair in seen:
            raise RuleInvalid(f"「{_label(rule['ruleType'])}」规则键重复：{rule['ruleKey']}")
        seen.add(pair)
    single_seen: set[str] = set()
    for rule in normalized:
        rule_type = rule["ruleType"]
        if rule_type in SINGLE_TYPES:
            if rule["ruleKey"] != DEFAULT_RULE_KEY:
                raise RuleInvalid(f"「{_label(rule_type)}」是单行类，规则键只能是 default")
            if rule_type in single_seen:
                raise RuleInvalid(f"「{_label(rule_type)}」只能有一行")
            single_seen.add(rule_type)
        limit = _LIST_LIMITS.get(rule_type)
        if limit is not None and sum(1 for item in normalized if item["ruleType"] == rule_type) > limit:
            raise RuleInvalid(f"「{_label(rule_type)}」最多 {limit} 条")
    return normalized


def _to_row(
    skill_key: str,
    owner_user_id: str | None,
    rule: dict,
    *,
    source: str,
) -> models.SkillWritingRule:
    payload = rule["payload"]
    # payload 列是 JSON 不是 JSONB，超大值也不会被后端拒绝；这里先挡住明显的灌库
    rough = len(str(payload))
    if rough > MAX_SWR_PAYLOAD_CHARS:
        raise RuleInvalid(f"「{_label(rule['ruleType'])}」内容过长（{rough} 字符）")
    return models.SkillWritingRule(
        skill_key=skill_key,
        owner_user_id=owner_user_id,
        rule_type=rule["ruleType"],
        rule_key=rule["ruleKey"],
        payload=payload,
        sort_order=rule.get("sortOrder", 0),
        enabled=rule.get("enabled", True),
        source=source,
    )


def _scope(db, skill_key: str, owner_user_id: str | None):
    stmt = delete(models.SkillWritingRule).where(models.SkillWritingRule.skill_key == skill_key)
    if owner_user_id is None:
        return stmt.where(models.SkillWritingRule.owner_user_id.is_(None))
    return stmt.where(models.SkillWritingRule.owner_user_id == owner_user_id)


def replace_rules(
    db,
    *,
    skill_key: str,
    owner_user_id: str | None,
    rules: list[dict],
    enabled_default=True,
) -> dict:
    """整组写入：先全部校验，再删同 owner 旧行、插新行；同一事务，失败全部不落。

    不提交事务——由调用方（路由 / 出厂导入）决定提交还是回滚。
    """
    if not isinstance(skill_key, str) or not skill_key.strip():
        raise RuleInvalid("缺少 skillKey")
    normalized = _validate_batch(rules)
    # enabled 缺省按整组默认值落库：没写 enabled 的规则沿用 enabled_default（出厂导入 True，
    # 历史迁移可传 False）；显式写了 enabled 的以规则为准（validate_rule 已把它规范成布尔）
    rows = [
        _to_row(
            skill_key,
            owner_user_id,
            {**rule, "enabled": rule["enabled"] if "enabled" in raw else enabled_default},
            source="user",
        )
        for rule, raw in zip(normalized, rules)
    ]
    db.execute(_scope(db, skill_key, owner_user_id))
    db.flush()
    db.add_all(rows)
    db.flush()
    types: dict[str, int] = {}
    for rule in normalized:
        types[rule["ruleType"]] = types.get(rule["ruleType"], 0) + 1
    return {"count": len(rows), "types": types}


def upsert_rule(db, *, skill_key: str, owner_user_id: str | None, rule: dict) -> dict:
    """单条新增/修改（按 type + key 定位）；不提交事务。"""
    if not isinstance(skill_key, str) or not skill_key.strip():
        raise RuleInvalid("缺少 skillKey")
    normalized = validate_rule(rule)
    stmt = select(models.SkillWritingRule).where(
        models.SkillWritingRule.skill_key == skill_key,
        models.SkillWritingRule.rule_type == normalized["ruleType"],
        models.SkillWritingRule.rule_key == normalized["ruleKey"],
    )
    if owner_user_id is None:
        stmt = stmt.where(models.SkillWritingRule.owner_user_id.is_(None))
    else:
        stmt = stmt.where(models.SkillWritingRule.owner_user_id == owner_user_id)
    existing = db.scalars(stmt).first()
    if existing is not None:
        existing.payload = normalized["payload"]
        existing.sort_order = normalized["sortOrder"]
        existing.enabled = normalized["enabled"]
        db.flush()
        return rule_view(existing)

    limit = _LIST_LIMITS.get(normalized["ruleType"])
    if limit is not None:
        same_type = [
            row
            for row in list_rule_rows(db, skill_key=skill_key, owner_user_id=owner_user_id)
            if row.rule_type == normalized["ruleType"]
        ]
        if len(same_type) >= limit:
            raise RuleInvalid(f"「{_label(normalized['ruleType'])}」最多 {limit} 条")
    row = _to_row(skill_key, owner_user_id, normalized, source="user")
    db.add(row)
    db.flush()
    return rule_view(row)


def delete_rule(db, *, skill_key: str, owner_user_id: str | None, rule_type: str, rule_key: str) -> None:
    """删除一条规则；不存在抛 RuleNotFound；不提交事务。"""
    stmt = select(models.SkillWritingRule).where(
        models.SkillWritingRule.skill_key == skill_key,
        models.SkillWritingRule.rule_type == rule_type,
        models.SkillWritingRule.rule_key == rule_key,
    )
    if owner_user_id is None:
        stmt = stmt.where(models.SkillWritingRule.owner_user_id.is_(None))
    else:
        stmt = stmt.where(models.SkillWritingRule.owner_user_id == owner_user_id)
    row = db.scalars(stmt).first()
    if row is None:
        raise RuleNotFound(f"规则不存在：{rule_type}/{rule_key}")
    db.delete(row)
    db.flush()


def rule_view(row) -> dict:
    """camelCase 可序列化 dict（前端/API 共用形状）。"""
    return {
        "id": row.id,
        "skillKey": row.skill_key,
        "ownerUserId": row.owner_user_id,
        "ruleType": row.rule_type,
        "ruleKey": row.rule_key,
        "payload": dict(row.payload or {}),
        "sortOrder": row.sort_order or 0,
        "enabled": bool(row.enabled),
        "source": row.source,
    }
