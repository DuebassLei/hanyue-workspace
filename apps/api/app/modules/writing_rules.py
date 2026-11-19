"""创作规则管理 API：Skill 规则行（全局 / 个人副本）与全局提示词规则的读写。

权限模型（计划 §6/§7）：
- `writing_rules.use`（router 级，人人有）：全部读接口 + 编辑「我的副本」；
- `writing_rules.manage`（仅超管/管理员）：写全局规则、按出厂重建、写提示词规则。

R4 口径：全局整组替换传空数组 = 全部禁用落行，而不是删行——
删掉的行会在下次启动被 `import_factory(only_missing=True)` 从出厂恢复。
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from .. import models, schemas
from ..core.deps import ApiError, CurrentUser, DbSession, not_found
from ..core.rbac import AccessDep, require_permission
from ..services import (
    agent_runtime,
    ai_article,
    prompt_rules,
    skill_registry,
    writing_rules,
    writing_rules_factory,
)

router = APIRouter(tags=["writing-rules"], dependencies=[require_permission("writing_rules.use")])

# 输出契约规则的必需字段（§6）：保存 contract 类规则时缺一不可
_CONTRACT_FIELDS = ("title", "markdown", "summary", "coverPrompt", "inlinePrompts")
_PROMPT_CATEGORIES = ("contract", "system", "outline", "repair", "tone_lexicon", "workflow_note")

_DEFAULT_REPAIR_PROMPT = (
    "下面是一篇公众号文章草稿，请修复后重新输出符合输出契约的完整 JSON："
    "保持原文的立场、素材与结构，只修复契约违规项，不要改写观点。"
)


# ---------------------------------------------------------------- helpers


def _owner_for_scope(scope: str, user) -> str | None:
    if scope == "global":
        return None
    if scope == "personal":
        return user.id
    raise ApiError("invalid_scope", "scope 只能是 global 或 personal", status_code=422)


def _require_write_access(access, scope: str) -> None:
    """global 写入需要 manage；personal 写入只要 use（router 级已保证）。"""
    if scope == "global" and not access.can("writing_rules.manage"):
        raise ApiError(
            "permission_denied",
            "修改全局规则需要「创作规则管理」权限；如需调整，请维护「我的副本」",
            status_code=403,
        )


def _translate(service_call, *args, **kwargs):
    """服务层异常 → API 错误码：invalid_rule 422 / rule_not_found 404。"""
    try:
        return service_call(*args, **kwargs)
    except writing_rules.RuleInvalid as exc:
        raise ApiError("invalid_rule", str(exc), status_code=422) from exc
    except writing_rules.RuleNotFound as exc:
        raise ApiError("rule_not_found", str(exc), status_code=404) from exc


def _entry_for(db, skill_key: str, owner_user_id: str | None):
    stmt = select(models.SkillRegistryEntry).where(models.SkillRegistryEntry.skill_key == skill_key)
    if owner_user_id is None:
        stmt = stmt.where(models.SkillRegistryEntry.owner_user_id.is_(None))
    else:
        stmt = stmt.where(models.SkillRegistryEntry.owner_user_id == owner_user_id)
    return db.scalars(stmt).first()


def _require_entry(db, skill_key: str, owner_user_id: str | None):
    """取条目做元数据来源；个人副本不存在时回退内置条目（合成只需要元数据）。"""
    entry = _entry_for(db, skill_key, owner_user_id)
    if entry is None and owner_user_id is not None:
        entry = _entry_for(db, skill_key, None)
    if entry is None:
        raise not_found("Skill")
    return entry


def _skill_rules_view(db, user, skill_key: str, scope: str) -> schemas.SkillRulesOut:
    """GET / PUT / reset 共用的返回形状：规则行 + 分组计数 + 合成后的有效 manifest。"""
    owner = _owner_for_scope(scope, user)
    entry = _require_entry(db, skill_key, owner)
    rows = writing_rules.list_rule_rows(db, skill_key=skill_key, owner_user_id=owner)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.rule_type] = counts.get(row.rule_type, 0) + 1
    return schemas.SkillRulesOut(
        skill_key=skill_key,
        scope=scope,
        rules=[schemas.WritingRuleOut(**writing_rules.rule_view(row)) for row in rows],
        counts=counts,
        effective_manifest=writing_rules.effective_manifest(db, entry, owner),
    )


def _row_for_scope(db, skill_key: str, rule_id: str, owner: str | None):
    """按 id 取规则行，并校验它属于该 skill 与该 scope；否则 404（不泄露他行存在性）。"""
    row = db.get(models.SkillWritingRule, rule_id)
    if (
        row is None
        or row.skill_key != skill_key
        or (owner is None and row.owner_user_id is not None)
        or (owner is not None and row.owner_user_id != owner)
    ):
        raise not_found("规则")
    return row


def _check_contract_content(content: str) -> None:
    missing = [field for field in _CONTRACT_FIELDS if field not in (content or "")]
    if missing:
        raise ApiError(
            "contract_field_missing",
            f"输出契约缺少必需字段：{'、'.join(missing)}",
            status_code=422,
        )


def _assert_workflow_tools(db, skill_key: str, owner: str | None, rules: list[dict]) -> None:
    """workspace 工作流里的 tool 必须落在该 Skill 的 allowed_tool_ids 内。

    计划 §4 要求写前校验：否则管理员能存下一条运行到一半才失败的流程，
    报错发生在生成时、和「保存成功」的反馈相互矛盾。
    """
    tools = {
        (rule.get("payload") or {}).get("tool")
        for rule in rules
        if rule.get("ruleType") == "workflow_step"
    }
    tools.discard(None)
    if not tools:
        return
    entry = _require_entry(db, skill_key, owner)
    allowed = set(entry.allowed_tool_ids or [])
    unknown = sorted(tool for tool in tools if tool not in allowed)
    if unknown:
        raise ApiError(
            "tool_not_in_skill",
            f"工作流里的工具不在该 Skill 的授权工具内：{'、'.join(unknown)}",
            status_code=409,
        )


# ---------------------------------------------------------------- Skill 规则行


@router.get("/writing-rules/skills", response_model=list[schemas.WritingSkillSummaryOut])
def list_rule_skills(user: CurrentUser, db: DbSession) -> list[schemas.WritingSkillSummaryOut]:
    """可编辑的 Skill 列表（内置全局 + 我的个人副本），带两侧规则计数。"""
    entries = db.scalars(
        select(models.SkillRegistryEntry)
        .where(models.SkillRegistryEntry.owner_user_id.is_(None))
        .order_by(models.SkillRegistryEntry.skill_key)
    ).all()
    categories = {}
    try:
        for spec in skill_registry.get_builtin_specs():
            categories[spec.key] = spec.category
    except RuntimeError:
        categories = {}

    def _counts(owner_user_id: str | None) -> dict[str, int]:
        stmt = (
            select(models.SkillWritingRule.skill_key, func.count())
            .where(models.SkillWritingRule.owner_user_id.is_(None) if owner_user_id is None else models.SkillWritingRule.owner_user_id == owner_user_id)
            .group_by(models.SkillWritingRule.skill_key)
        )
        return dict(db.execute(stmt).all())

    global_counts = _counts(None)
    personal_counts = _counts(user.id)
    personal_keys = {
        row.skill_key
        for row in db.scalars(
            select(models.SkillRegistryEntry).where(models.SkillRegistryEntry.owner_user_id == user.id)
        ).all()
    }

    return [
        schemas.WritingSkillSummaryOut(
            skill_key=entry.skill_key,
            name=entry.display_name,
            category=categories.get(entry.skill_key, ""),
            global_count=global_counts.get(entry.skill_key, 0),
            personal_count=personal_counts.get(entry.skill_key, 0),
            has_personal_copy=entry.skill_key in personal_keys,
        )
        for entry in entries
    ]


@router.get("/writing-rules/skills/{skill_key}", response_model=schemas.SkillRulesOut)
def get_skill_rules(
    skill_key: str,
    user: CurrentUser,
    db: DbSession,
    scope: str = Query(default="global", pattern="^(global|personal)$"),
) -> schemas.SkillRulesOut:
    return _skill_rules_view(db, user, skill_key, scope)


@router.put("/writing-rules/skills/{skill_key}/rules", response_model=schemas.SkillRulesOut)
def replace_skill_rules(
    skill_key: str,
    payload: schemas.WritingRulesReplaceIn,
    user: CurrentUser,
    db: DbSession,
    access: AccessDep,
    scope: str = Query(default="global", pattern="^(global|personal)$"),
) -> schemas.SkillRulesOut:
    """整组替换（表单保存）。全局需要 manage；全局空数组按 R4 落「全部禁用」。"""
    owner = _owner_for_scope(scope, user)
    _require_write_access(access, scope)

    rules = [rule.dump() for rule in payload.rules]
    _assert_workflow_tools(db, skill_key, owner, rules)
    if scope == "global" and not rules:
        # R4：删除行会被启动导入从出厂恢复，所以「清空」语义是全部禁用
        current = writing_rules.list_rule_rows(db, skill_key=skill_key, owner_user_id=None)
        if not current:
            raise not_found("Skill")
        for row in current:
            row.enabled = False
        db.commit()
        prompt_rules.invalidate_cache(db)
    else:
        _translate(writing_rules.replace_rules, db, skill_key=skill_key, owner_user_id=owner, rules=rules)
        db.commit()
        prompt_rules.invalidate_cache(db)
    return _skill_rules_view(db, user, skill_key, scope)


@router.post("/writing-rules/skills/{skill_key}/rules", response_model=schemas.WritingRuleOut, status_code=201)
def create_skill_rule(
    skill_key: str,
    payload: schemas.WritingRuleIn,
    user: CurrentUser,
    db: DbSession,
    access: AccessDep,
    scope: str = Query(default="global", pattern="^(global|personal)$"),
) -> schemas.WritingRuleOut:
    """单条新增（同 type+key 已存在则覆盖其内容）。"""
    owner = _owner_for_scope(scope, user)
    _require_write_access(access, scope)
    rule = payload.dump()
    _assert_workflow_tools(db, skill_key, owner, [rule])
    row = _translate(writing_rules.upsert_rule, db, skill_key=skill_key, owner_user_id=owner, rule=rule)
    db.commit()
    prompt_rules.invalidate_cache(db)
    return schemas.WritingRuleOut(**row)


@router.patch("/writing-rules/skills/{skill_key}/rules/{rule_id}", response_model=schemas.WritingRuleOut)
def patch_skill_rule(
    skill_key: str,
    rule_id: str,
    payload: schemas.WritingRulePatchIn,
    user: CurrentUser,
    db: DbSession,
    access: AccessDep,
    scope: str = Query(default="global", pattern="^(global|personal)$"),
) -> schemas.WritingRuleOut:
    """单条修改（payload / enabled / sortOrder）；payload 变更会整行重新校验。"""
    owner = _owner_for_scope(scope, user)
    _require_write_access(access, scope)
    row = _row_for_scope(db, skill_key, rule_id, owner)

    merged = {
        "ruleType": row.rule_type,
        "ruleKey": row.rule_key,
        "payload": payload.payload if payload.payload is not None else dict(row.payload or {}),
        "sortOrder": payload.sort_order if payload.sort_order is not None else row.sort_order or 0,
        "enabled": payload.enabled if payload.enabled is not None else bool(row.enabled),
    }
    normalized = _translate(writing_rules.validate_rule, merged)
    _assert_workflow_tools(db, skill_key, owner, [normalized])
    row.payload = normalized["payload"]
    row.sort_order = normalized["sortOrder"]
    row.enabled = normalized["enabled"]
    db.commit()
    prompt_rules.invalidate_cache(db)
    return schemas.WritingRuleOut(**writing_rules.rule_view(row))


@router.delete("/writing-rules/skills/{skill_key}/rules/{rule_id}", status_code=204)
def delete_skill_rule(
    skill_key: str,
    rule_id: str,
    user: CurrentUser,
    db: DbSession,
    access: AccessDep,
    scope: str = Query(default="global", pattern="^(global|personal)$"),
) -> None:
    owner = _owner_for_scope(scope, user)
    _require_write_access(access, scope)
    row = _row_for_scope(db, skill_key, rule_id, owner)
    db.delete(row)
    db.commit()
    prompt_rules.invalidate_cache(db)


@router.post("/writing-rules/skills/{skill_key}/reset", response_model=schemas.SkillRulesOut)
def reset_skill_rules(
    skill_key: str,
    user: CurrentUser,
    db: DbSession,
    access: AccessDep,
) -> schemas.SkillRulesOut:
    """按出厂重建该 Skill 的全局规则（会覆盖管理员改动，前端需二次确认）。"""
    if not access.can("writing_rules.manage"):
        raise ApiError("permission_denied", "按出厂重建需要「创作规则管理」权限", status_code=403)
    _translate(writing_rules_factory.reset_skill_rules_to_factory, db, skill_key)
    prompt_rules.invalidate_cache(db)
    return _skill_rules_view(db, user, skill_key, "global")


# ---------------------------------------------------------------- 提示词与词表


def _prompt_view(row: models.PromptRule) -> schemas.PromptRuleOut:
    return schemas.PromptRuleOut(
        key=row.key,
        category=row.category,
        title=row.title or "",
        description=row.description or "",
        content=row.content or "",
        payload=dict(row.payload or {}),
        enabled=bool(row.enabled),
        source=row.source,
        version=row.version or 1,
    )


@router.get("/writing-rules/prompts", response_model=list[schemas.PromptRuleOut])
def list_prompt_rules(user: CurrentUser, db: DbSession) -> list[schemas.PromptRuleOut]:
    rows = db.scalars(select(models.PromptRule).order_by(models.PromptRule.key)).all()
    return [_prompt_view(row) for row in rows]


@router.put("/writing-rules/prompts/{key:path}", response_model=schemas.PromptRuleOut)
def update_prompt_rule(
    key: str,
    payload: schemas.PromptRuleUpsertIn,
    user: CurrentUser,
    db: DbSession,
    access: AccessDep,
) -> schemas.PromptRuleOut:
    if not access.can("writing_rules.manage"):
        raise ApiError("permission_denied", "修改提示词规则需要「创作规则管理」权限", status_code=403)
    row = db.scalar(select(models.PromptRule).where(models.PromptRule.key == key))
    if row is None:
        raise not_found("提示词规则")

    new_content = payload.content if payload.content is not None else (row.content or "")
    if row.category == "contract":
        _check_contract_content(new_content)
    if payload.content is not None:
        row.content = payload.content
    if payload.payload is not None:
        row.payload = payload.payload
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.title is not None:
        row.title = payload.title
    if payload.description is not None:
        row.description = payload.description
    row.version = (row.version or 1) + 1
    db.commit()
    prompt_rules.invalidate_cache(db)
    return _prompt_view(row)


@router.post("/writing-rules/prompts", response_model=schemas.PromptRuleOut, status_code=201)
def create_prompt_rule(
    payload: schemas.PromptRuleCreateIn,
    user: CurrentUser,
    db: DbSession,
    access: AccessDep,
) -> schemas.PromptRuleOut:
    if not access.can("writing_rules.manage"):
        raise ApiError("permission_denied", "新增提示词规则需要「创作规则管理」权限", status_code=403)
    key = payload.key.strip()
    if len(key) < 3 or any(ch.isspace() for ch in key) or ".." in key:
        raise ApiError("invalid_rule", "key 需为 3 字符以上、不含空白与「..」", status_code=422)
    if payload.category not in _PROMPT_CATEGORIES:
        raise ApiError(
            "invalid_rule",
            f"category 只能是：{'、'.join(_PROMPT_CATEGORIES)}",
            status_code=422,
        )
    if db.scalar(select(models.PromptRule.id).where(models.PromptRule.key == key)):
        raise ApiError("prompt_rule_exists", f"规则 key 已存在：{key}", status_code=409)
    if payload.category == "contract":
        _check_contract_content(payload.content)
    row = models.PromptRule(
        key=key,
        category=payload.category,
        title=payload.title,
        description=payload.description,
        content=payload.content,
        payload=payload.payload,
        enabled=payload.enabled,
        source="user",
    )
    db.add(row)
    db.commit()
    prompt_rules.invalidate_cache(db)
    return _prompt_view(row)


@router.delete("/writing-rules/prompts/{key:path}", status_code=204)
def delete_prompt_rule(
    key: str,
    user: CurrentUser,
    db: DbSession,
    access: AccessDep,
) -> None:
    if not access.can("writing_rules.manage"):
        raise ApiError("permission_denied", "删除提示词规则需要「创作规则管理」权限", status_code=403)
    row = db.scalar(select(models.PromptRule).where(models.PromptRule.key == key))
    if row is None:
        raise not_found("提示词规则")
    if row.source != "user":
        raise ApiError("factory_rule_locked", "出厂规则不可删除，可改为停用", status_code=409)
    db.delete(row)
    db.commit()
    prompt_rules.invalidate_cache(db)


# ---------------------------------------------------------------- 预览


@router.get("/writing-rules/preview", response_model=schemas.RulesPreviewOut)
def preview_rules(
    user: CurrentUser,
    db: DbSession,
    skillKey: str = Query(...),
    scope: str = Query(default="global", pattern="^(global|personal)$"),
    kind: str = Query(default="article", pattern="^(article|outline|repair)$"),
    sample: str = Query(default=""),
) -> schemas.RulesPreviewOut:
    """「最终提示词」预览（不调模型）：把当前规则表合成结果直接拼给用户看。"""
    owner = _owner_for_scope(scope, user)
    entry = _require_entry(db, skillKey, owner)

    keys_used: list[str] = []
    if kind == "article":
        text = ai_article.build_system_prompt(entry, db=db, owner_user_id=owner)
        keys_used = ["article.output_contract"]
    elif kind == "outline":
        text = prompt_rules.get_prompt_rule(
            db, "agent.outline_system", default=agent_runtime.OUTLINE_SYSTEM_PROMPT
        )
        keys_used = ["agent.outline_system"]
    else:  # repair
        policy = (writing_rules.effective_manifest(db, entry, owner).get("repairPolicy")) or {}
        base = prompt_rules.get_prompt_rule(db, "article.repair_prompt", default=_DEFAULT_REPAIR_PROMPT)
        lines = [base, f"修复策略：最多重试 {policy.get('retries', 1)} 次"]
        if policy.get("requireContract", True):
            lines.append("修复后的输出必须继续满足全部契约字段。")
        text = "\n".join(lines)
        keys_used = ["article.repair_prompt"]
        if sample:
            text = f"{text}\n\n草稿样本：\n{sample[:2000]}"

    return schemas.RulesPreviewOut(
        skill_key=skillKey,
        scope=scope,
        kind=kind,
        text=text,
        rule_sources=prompt_rules.describe_rules(db, keys_used),
    )
