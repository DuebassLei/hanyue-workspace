"""Skill 注册表：内置定义的加载、种子同步、自定义 Skill 生命周期与授权。

单一入口原则：
- 内置 Skill 只来自 app/resources/skills/builtin_skills.json——它是出厂数据
  app/resources/writing_rules_factory.json 的派生文件（规则行合成回 manifest 的形态），
  由 scripts/export_factory_to_builtin.py 生成、--check 校验，手改不生效；
  规则的权威源是 skill_writing_rules 表（owner 为空的全局行）；
- 所有写入路径（种子、创建、定制、编辑）共用同一个 checksum 算法；
- 风险等级由声明工具推断，不由调用方硬编码；
- “启用”只表示允许 Agent 自动调用，写作风格的手动选用由 resolve_visible 控制可见性。

这一层不依赖任何路由模块，便于 ai_article / agent_runtime / 设置页共用。
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import case, or_, select

from .. import models, schemas
from ..core import pagination
from ..core.config import BASE_DIR
from ..core.deps import ApiError, default_workspace, not_found
from . import agent_policy, writing_rules
from .skill_manifest import ManifestInvalid, validate_manifest

BUILTIN_RESOURCE = BASE_DIR / "app" / "resources" / "skills" / "builtin_skills.json"

# 公众号成文的统一输出契约。所有写作风格共用，只替换 {tone} 与风格规则。
# 唯一一份：内置资源、自定义 Skill 的兜底、ai_article 都从这里取，避免提示词漂移。
DEFAULT_ARTICLE_PROMPT = (
    "你是资深公众号主笔。严格只输出一个 JSON 对象，不要 Markdown 代码块、不要解释文字。"
    "JSON 结构：{\"title\":\"标题\",\"markdown\":\"正文（标准 Markdown）\","
    "\"summary\":\"摘要（≤120字）\",\"coverPrompt\":\"封面图英文提示词\","
    "\"inlinePrompts\":[\"插图1英文提示词\",\"插图2英文提示词\"]}。"
    "正文使用中文，{tone}，不用 emoji，不用绝对化承诺。"
    "正文使用标准 Markdown，提示块使用 > [!NOTE] 等 callout 引用语法。"
    "需要视觉卡时使用 svg-table、svg-bars、svg-steps、svg-timeline、svg-breakdown、"
    "svg-compare、svg-concept、svg-phone 代码围栏，围栏内用 | 分列数据，不使用冒号扩展区块。"
    "封面图提示词用英文，描述画面主体、风格、色调。"
    "插图提示词数量与正文中 {{imageN}} 标记数量一致。"
)
DEFAULT_TONE = "语气平实有深度，结构清晰，善用小标题和引用"
DEFAULT_STYLE_RULES = [
    "不用 emoji",
    "不用绝对化承诺（如「100%有效」）",
    "善用 ## 小标题拆分段落",
    "关键信息用 > 引用块突出",
]


class BuiltinSkillSpec(BaseModel):
    """`builtin_skills.json` 中一条内置 Skill 的结构。"""

    model_config = ConfigDict(populate_by_name=True)

    key: str
    version: str = "1.0.0"
    name: str
    description: str = ""
    category: str = "flow"
    tools: list[str] = Field(default_factory=list)
    manifest: dict = Field(default_factory=dict)


def _builtin_keys() -> set[str]:
    return {spec.key for spec in get_builtin_specs()}


def max_tool_risk(tool_ids: list[str]) -> str:
    """按声明工具推断风险等级：取最高风险，空声明视为只读。"""
    risks = [agent_policy.tool_risk(tool_id) for tool_id in tool_ids or []]
    if not risks:
        return agent_policy.READ_ONLY
    return max(risks, key=lambda risk: agent_policy.RISK_ORDER[risk])


def validate_tool_declarations(tool_ids: list[str]) -> list[str]:
    """拒绝未登记工具与禁止级工具，并去重保序。"""
    unknown = [t for t in tool_ids if t not in agent_policy.TOOL_CATALOG]
    if unknown:
        raise ApiError("unknown_tool", f"未登记的工具：{', '.join(unknown)}", status_code=409)
    forbidden = [t for t in tool_ids if agent_policy.tool_risk(t) == agent_policy.FORBIDDEN]
    if forbidden:
        raise ApiError(
            "forbidden_tool",
            f"禁止级工具不能写入 Skill 声明：{', '.join(forbidden)}",
            status_code=409,
        )
    cleaned: list[str] = []
    for tool_id in tool_ids:
        if tool_id not in cleaned:
            cleaned.append(tool_id)
    return cleaned


def validate_workflow(manifest: dict | None, allowed_tool_ids: list[str] | None) -> None:
    """workflow 的每一步必须是该 Skill 自身声明的工具，且工具已登记。

    schema 层不 import agent_policy；工具登记检查放在这里，写入路径统一调用。
    """
    steps = (manifest or {}).get("workflow") or []
    if not steps:
        return
    declared = set(allowed_tool_ids or [])
    outside = [step["tool"] for step in steps if step["tool"] not in declared]
    if outside:
        raise ApiError(
            "tool_not_in_skill",
            f"workflow 只能使用该 Skill 声明的工具，超出：{', '.join(outside)}",
            status_code=409,
        )
    unknown = [step["tool"] for step in steps if step["tool"] not in agent_policy.TOOL_CATALOG]
    if unknown:
        raise ApiError(
            "unknown_tool", f"workflow 引用了未登记的工具：{', '.join(unknown)}", status_code=409
        )


def compute_checksum(
    *,
    skill_key: str,
    version: str,
    manifest: dict | None,
    allowed_tool_ids: list[str] | None,
) -> str:
    """所有写入路径共用的内容指纹；改内容必须改 checksum，改 checksum 必须改 version。"""
    payload = {
        "skill_key": skill_key,
        "version": version,
        "manifest": manifest or {},
        "allowed_tool_ids": list(allowed_tool_ids or []),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _build_builtin_manifest(spec: BuiltinSkillSpec) -> dict:
    manifest = dict(spec.manifest)
    if spec.category == "style":
        manifest.setdefault("promptTemplate", DEFAULT_ARTICLE_PROMPT)
    manifest.setdefault("input", {"goal": "string"})
    manifest.setdefault("output", {"draft": "object"})
    manifest.setdefault("riskLevel", max_tool_risk(spec.tools))
    try:
        return validate_manifest(manifest)
    except ManifestInvalid as exc:
        raise RuntimeError(f"内置 Skill「{spec.key}」manifest 非法：{exc}") from exc


@lru_cache(maxsize=1)
def get_builtin_specs() -> tuple[BuiltinSkillSpec, ...]:
    """加载并校验内置定义；配置错误在启动时直接失败，而不是静默少几个 Skill。"""
    if not BUILTIN_RESOURCE.exists():
        raise RuntimeError(f"缺少内置 Skill 定义文件：{BUILTIN_RESOURCE}（由 export_factory_to_builtin.py 生成）")
    try:
        raw = json.loads(BUILTIN_RESOURCE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{BUILTIN_RESOURCE} 不是合法 JSON：{exc}") from exc
    items = raw.get("skills") if isinstance(raw, dict) else None
    if not isinstance(items, list) or not items:
        raise RuntimeError(f"{BUILTIN_RESOURCE} 缺少 skills 列表")
    specs: list[BuiltinSkillSpec] = []
    seen: set[str] = set()
    for item in items:
        try:
            spec = BuiltinSkillSpec.model_validate(item)
        except ValidationError as exc:
            raise RuntimeError(f"内置 Skill 定义非法：{exc}") from exc
        if spec.key in seen:
            raise RuntimeError(f"内置 Skill key 重复：{spec.key}")
        seen.add(spec.key)
        validate_tool_declarations(spec.tools)
        validate_workflow(_build_builtin_manifest(spec), spec.tools)  # 启动时校验，失败即报错
        specs.append(spec)
    return tuple(specs)


def build_skill_out(
    entry: models.SkillRegistryEntry,
    grant: models.WorkspaceSkill | None,
    *,
    customized: bool | None = None,
) -> schemas.SkillOut:
    if customized is None:
        customized = entry.source == "custom" and entry.skill_key in _builtin_keys()
    return schemas.SkillOut(
        id=entry.id,
        skill_key=entry.skill_key,
        version=entry.version,
        display_name=entry.display_name,
        description=entry.description,
        risk_level=entry.risk_level,
        allowed_tool_ids=entry.allowed_tool_ids or [],
        trust_status=entry.trust_status,
        enabled=bool(grant and grant.enabled),
        granted_tool_ids=grant.granted_tool_ids if grant else [],
        checksum=entry.checksum,
        manifest=entry.manifest or {},
        source=entry.source or "builtin",
        customized=customized,
    )


def _delete_grants(db, entry_id: str) -> None:
    for grant in db.scalars(
        select(models.WorkspaceSkill).where(models.WorkspaceSkill.skill_entry_id == entry_id)
    ).all():
        db.delete(grant)


def sync_builtin_skills(db) -> dict:
    """启动时同步内置 Skill：upsert、清理下线项、清掉重复行。

    不触碰 source=custom 的用户数据；内置项被移除时，其工作区授权一并清理。
    """
    specs = get_builtin_specs()
    keep = {spec.key for spec in specs}
    rows = db.scalars(
        select(models.SkillRegistryEntry).where(
            models.SkillRegistryEntry.owner_user_id.is_(None),
            models.SkillRegistryEntry.source == "builtin",
        )
    ).all()

    by_key: dict[str, list[models.SkillRegistryEntry]] = {}
    for row in rows:
        by_key.setdefault(row.skill_key, []).append(row)

    removed = 0
    # 极旧数据里可能存在同 key 的“无归属自定义”行，会与内置行撞唯一索引；
    # 内置定义优先，先清掉它们（连同授权），再走正常 upsert。
    shadow_rows = db.scalars(
        select(models.SkillRegistryEntry).where(
            models.SkillRegistryEntry.owner_user_id.is_(None),
            models.SkillRegistryEntry.source != "builtin",
            models.SkillRegistryEntry.skill_key.in_(keep),
        )
    ).all()
    for row in shadow_rows:
        _delete_grants(db, row.id)
        db.delete(row)
        removed += 1
    if shadow_rows:
        # 先落删除：SQLAlchemy 同一事务里 INSERT 会排在 DELETE 之前，直接插入会撞唯一索引
        db.commit()

    created = updated = 0
    for spec in specs:
        manifest = _build_builtin_manifest(spec)
        checksum = compute_checksum(
            skill_key=spec.key,
            version=spec.version,
            manifest=manifest,
            allowed_tool_ids=spec.tools,
        )
        targets = by_key.get(spec.key) or []
        if targets:
            entry = targets[0]
            entry.version = spec.version
            entry.display_name = spec.name
            entry.description = spec.description
            entry.category = spec.category
            entry.risk_level = max_tool_risk(spec.tools)
            entry.allowed_tool_ids = list(spec.tools)
            entry.manifest = manifest
            entry.checksum = checksum
            entry.trust_status = "trusted"
            updated += 1
            # 唯一索引生效前可能已有重复行，保留最早一条，其余连同授权删除
            for duplicate in targets[1:]:
                _delete_grants(db, duplicate.id)
                db.delete(duplicate)
        else:
            db.add(
                models.SkillRegistryEntry(
                    source="builtin",
                    owner_user_id=None,
                    skill_key=spec.key,
                    category=spec.category,
                    version=spec.version,
                    display_name=spec.name,
                    description=spec.description,
                    risk_level=max_tool_risk(spec.tools),
                    allowed_tool_ids=list(spec.tools),
                    trust_status="trusted",
                    manifest=manifest,
                    checksum=checksum,
                )
            )
            created += 1

    for key, stale_rows in by_key.items():
        if key in keep:
            continue
        for row in stale_rows:
            _delete_grants(db, row.id)
            db.delete(row)
            removed += 1

    db.commit()
    return {"created": created, "updated": updated, "removed": removed}


def list_visible(
    db, user, *, limit: int, cursor: str | None
) -> tuple[list[schemas.SkillOut], str | None]:
    """当前账号可见的 Skill：全局（内置 + 管理员建的全局自定义）+ 本人自定义。

    个人副本（owner=自己、key 与内置相同）会替换内置原版展示；
    全局自定义 Skill（source=custom、owner 为空）由「创作规则」页维护，
    与内置一样全员可见，不再按旧口径当作无归属垃圾行过滤。
    """
    builtin_keys = _builtin_keys()
    forked_keys = set(
        db.scalars(
            select(models.SkillRegistryEntry.skill_key).where(
                models.SkillRegistryEntry.owner_user_id == user.id,
                models.SkillRegistryEntry.source == "custom",
                models.SkillRegistryEntry.skill_key.in_(builtin_keys),
            )
        ).all()
    )
    stmt = select(models.SkillRegistryEntry).where(
        or_(
            models.SkillRegistryEntry.owner_user_id.is_(None),
            models.SkillRegistryEntry.owner_user_id == user.id,
        )
    )
    entries, next_cursor = pagination.paginate(
        db,
        stmt,
        models.SkillRegistryEntry,
        sort_column=models.SkillRegistryEntry.created_at,
        cursor=cursor,
        limit=limit,
    )
    workspace = default_workspace(db, user)
    grants = {
        grant.skill_entry_id: grant
        for grant in db.scalars(
            select(models.WorkspaceSkill).where(
                models.WorkspaceSkill.workspace_id == workspace.id
            )
        ).all()
    }
    result: list[schemas.SkillOut] = []
    for entry in entries:
        if (
            entry.owner_user_id is None
            and entry.source == "builtin"
            and entry.skill_key in forked_keys
        ):
            continue
        result.append(build_skill_out(entry, grants.get(entry.id)))
    return result, next_cursor


def _visible_to_user(user_id: str):
    return or_(
        models.SkillRegistryEntry.owner_user_id.is_(None),
        models.SkillRegistryEntry.owner_user_id == user_id,
    )


def resolve_visible(db, user_id: str, skill_key: str | None) -> models.SkillRegistryEntry | None:
    """运行时解析 Skill：只看 trusted + 本人可见，本人副本优先。"""
    requested = skill_key or "article-drafting"

    def _find(key: str) -> models.SkillRegistryEntry | None:
        return db.scalar(
            select(models.SkillRegistryEntry)
            .where(
                models.SkillRegistryEntry.skill_key == key,
                models.SkillRegistryEntry.trust_status == "trusted",
                _visible_to_user(user_id),
            )
            .order_by(
                case(
                    (models.SkillRegistryEntry.owner_user_id == user_id, 0),
                    else_=1,
                ),
                models.SkillRegistryEntry.created_at.desc(),
            )
        )

    entry = _find(requested)
    if entry is None and requested != "article-drafting":
        entry = _find("article-drafting")
    return entry


def _owned_custom_skill(db, user, skill_id: str) -> models.SkillRegistryEntry:
    entry = db.get(models.SkillRegistryEntry, skill_id)
    if not entry:
        raise not_found("Skill")
    if entry.source != "custom":
        raise ApiError(
            "skill_not_editable",
            "内置 Skill 不可直接编辑；如需调整请使用「定制内置风格」生成个人副本",
            status_code=403,
        )
    if entry.owner_user_id != user.id:
        # 他人的自定义 Skill 不暴露存在性
        raise not_found("Skill")
    return entry


def create_skill(db, user, payload: schemas.SkillCreateIn) -> schemas.SkillOut:
    existing = db.scalar(
        select(models.SkillRegistryEntry).where(
            models.SkillRegistryEntry.skill_key == payload.skill_key,
            or_(
                models.SkillRegistryEntry.owner_user_id.is_(None),
                models.SkillRegistryEntry.owner_user_id == user.id,
            ),
        )
    )
    if existing:
        raise ApiError(
            "skill_key_exists", f"skill_key「{payload.skill_key}」已存在", status_code=409
        )

    tools = validate_tool_declarations(payload.allowed_tool_ids)
    try:
        manifest = validate_manifest(payload.manifest)
    except ManifestInvalid as exc:
        raise ApiError("invalid_manifest", str(exc), status_code=422) from exc
    validate_workflow(manifest, tools)

    entry = models.SkillRegistryEntry(
        source="custom",
        owner_user_id=user.id,
        skill_key=payload.skill_key,
        version=payload.version,
        display_name=payload.display_name,
        description=payload.description,
        risk_level=max_tool_risk(tools),
        allowed_tool_ids=tools,
        trust_status="trusted",
        manifest=manifest,
        checksum=compute_checksum(
            skill_key=payload.skill_key,
            version=payload.version,
            manifest=manifest,
            allowed_tool_ids=tools,
        ),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return build_skill_out(entry, None, customized=False)


def update_skill(db, user, skill_id: str, payload: schemas.SkillUpdateIn) -> schemas.SkillOut:
    entry = _owned_custom_skill(db, user, skill_id)
    if payload.display_name is not None:
        entry.display_name = payload.display_name
    if payload.description is not None:
        entry.description = payload.description
    if payload.version is not None:
        entry.version = payload.version
    if payload.manifest is not None:
        try:
            entry.manifest = validate_manifest(payload.manifest)
        except ManifestInvalid as exc:
            raise ApiError("invalid_manifest", str(exc), status_code=422) from exc
        validate_workflow(entry.manifest, entry.allowed_tool_ids)
    entry.checksum = compute_checksum(
        skill_key=entry.skill_key,
        version=entry.version,
        manifest=entry.manifest or {},
        allowed_tool_ids=entry.allowed_tool_ids or [],
    )
    db.commit()
    db.refresh(entry)

    workspace = default_workspace(db, user)
    grant = db.scalar(
        select(models.WorkspaceSkill).where(
            models.WorkspaceSkill.workspace_id == workspace.id,
            models.WorkspaceSkill.skill_entry_id == entry.id,
        )
    )
    return build_skill_out(entry, grant)


def fork_builtin(db, user, skill_id: str, payload: schemas.SkillUpdateIn) -> schemas.SkillOut:
    """把内置 Skill 复制为当前用户的个人副本；内置原版永不修改。"""
    entry = db.get(models.SkillRegistryEntry, skill_id)
    if not entry:
        raise not_found("Skill")
    if entry.source != "builtin":
        raise ApiError(
            "skill_not_builtin", "仅内置写作风格支持定制；自定义风格请直接编辑", status_code=409
        )
    existing_fork = db.scalar(
        select(models.SkillRegistryEntry).where(
            models.SkillRegistryEntry.skill_key == entry.skill_key,
            models.SkillRegistryEntry.owner_user_id == user.id,
        )
    )
    if existing_fork:
        raise ApiError(
            "skill_key_exists", "该内置风格已有个人定制副本，请直接编辑副本", status_code=409
        )

    # 按顶层字段合并：前端通常只提交 tone/styleRules，
    # 未提交的策略字段（workflow、reviewRules 等）继续沿用内置版本。
    manifest = dict(entry.manifest or {})
    if payload.manifest is not None:
        manifest.update(payload.manifest)
    try:
        manifest = validate_manifest(manifest)
    except ManifestInvalid as exc:
        raise ApiError("invalid_manifest", str(exc), status_code=422) from exc
    validate_workflow(manifest, entry.allowed_tool_ids)

    fork = models.SkillRegistryEntry(
        source="custom",
        owner_user_id=user.id,
        skill_key=entry.skill_key,
        version=payload.version or entry.version,
        display_name=payload.display_name or entry.display_name,
        description=payload.description if payload.description is not None else entry.description,
        risk_level=entry.risk_level,
        allowed_tool_ids=list(entry.allowed_tool_ids or []),
        trust_status="trusted",
        manifest=manifest,
        checksum=compute_checksum(
            skill_key=entry.skill_key,
            version=payload.version or entry.version,
            manifest=manifest,
            allowed_tool_ids=entry.allowed_tool_ids or [],
        ),
    )
    db.add(fork)
    # 个人副本带上全局规则行（source=user）：规则表是规则型 key 的权威源，
    # fork 只复制条目 manifest 的话，T2 之后个人副本会读不到规则。
    # 用户可能先在「创作规则」页直接维护过个人副本行（不 fork 也能写），
    # 这里按 (rule_type, rule_key) 去重：已存在的个人行是用户自己改过的，不能被全局值覆盖。
    existing_pairs = {
        (row.rule_type, row.rule_key)
        for row in writing_rules.list_rule_rows(
            db, skill_key=entry.skill_key, owner_user_id=user.id
        )
    }
    for row in writing_rules.list_rule_rows(db, skill_key=entry.skill_key, owner_user_id=None):
        if (row.rule_type, row.rule_key) in existing_pairs:
            continue
        db.add(
            models.SkillWritingRule(
                skill_key=entry.skill_key,
                owner_user_id=user.id,
                rule_type=row.rule_type,
                rule_key=row.rule_key,
                payload=dict(row.payload or {}),
                sort_order=row.sort_order or 0,
                enabled=bool(row.enabled),
                source="user",
            )
        )
    db.commit()
    db.refresh(fork)
    return build_skill_out(fork, None, customized=True)


def delete_skill(db, user, skill_id: str) -> None:
    entry = _owned_custom_skill(db, user, skill_id)
    _delete_grants(db, entry.id)
    db.delete(entry)
    db.commit()


# ---------------------------------------------------------------- 全局自定义 Skill
#
# 「创作规则」页左侧列表的维护能力：管理员可以新增全局 Skill（source=custom、
# owner=NULL）、改名、删除。与内置条目的关系：
# - key 与内置冲突一律拒绝——sync_builtin_skills 会把撞内置 key 的非内置行当
#   影子数据清掉，允许写入等于埋一条重启即消失的数据；
# - 启动同步只 upsert source=builtin 的行，全局自定义行不会被冲掉。


def _validate_global_skill_key(db, skill_key: str) -> str:
    key = (skill_key or "").strip()
    if len(key) < 3 or any(ch.isspace() for ch in key):
        raise ApiError("invalid_rule", "标识需为 3 字符以上、不含空白", status_code=422)
    if key in _builtin_keys():
        raise ApiError(
            "skill_key_exists", f"「{key}」是内置 Skill 的标识，不能重复使用", status_code=409
        )
    existing = db.scalar(
        select(models.SkillRegistryEntry.id).where(
            models.SkillRegistryEntry.skill_key == key,
            models.SkillRegistryEntry.owner_user_id.is_(None),
        )
    )
    if existing:
        raise ApiError("skill_key_exists", f"标识「{key}」已存在", status_code=409)
    return key


def build_custom_manifest(category: str, tone: str | None = None) -> dict:
    """按分组合成新全局 Skill 的最小可用 manifest。

    风格型共用公众号输出契约（与内置资源、ai_article 同源），保证生成侧不漂移；
    流程型只带输入输出形状，工具后续由设置页授权。
    """
    manifest: dict = {"category": "flow" if category == "flow" else "style"}
    if category != "flow":
        manifest["promptTemplate"] = DEFAULT_ARTICLE_PROMPT
        manifest["tone"] = (tone or "").strip() or DEFAULT_TONE
        manifest["styleRules"] = list(DEFAULT_STYLE_RULES)
    manifest.setdefault("input", {"goal": "string"})
    manifest.setdefault("output", {"draft": "object"})
    manifest.setdefault("riskLevel", max_tool_risk([]))
    try:
        return validate_manifest(manifest)
    except ManifestInvalid as exc:
        raise ApiError("invalid_manifest", str(exc), status_code=422) from exc


def create_global_skill(db, payload) -> models.SkillRegistryEntry:
    """管理员新增全局自定义 Skill；全员可见，规则行在「创作规则」页维护。"""
    if payload.category not in ("flow", "style"):
        raise ApiError("invalid_rule", "category 只能是 flow 或 style", status_code=422)
    key = _validate_global_skill_key(db, payload.skill_key)
    manifest = build_custom_manifest(payload.category, payload.tone)
    tools: list[str] = []
    entry = models.SkillRegistryEntry(
        source="custom",
        owner_user_id=None,
        skill_key=key,
        category=payload.category,
        version="1.0.0",
        display_name=payload.display_name.strip(),
        description=payload.description or "",
        risk_level=max_tool_risk(tools),
        allowed_tool_ids=tools,
        trust_status="trusted",
        manifest=manifest,
        checksum=compute_checksum(
            skill_key=key, version="1.0.0", manifest=manifest, allowed_tool_ids=tools
        ),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def _global_custom_entry(db, skill_key: str) -> models.SkillRegistryEntry:
    entry = db.scalar(
        select(models.SkillRegistryEntry).where(
            models.SkillRegistryEntry.skill_key == skill_key,
            models.SkillRegistryEntry.owner_user_id.is_(None),
        )
    )
    if entry is None:
        raise not_found("Skill")
    if entry.source != "custom":
        raise ApiError(
            "skill_not_editable",
            "内置 Skill 不可改名或删除；如需调整请维护它的规则行",
            status_code=403,
        )
    return entry


def update_global_skill(db, skill_key: str, payload) -> models.SkillRegistryEntry:
    """改全局自定义 Skill 的显示名/描述/语气；内置条目直接 403。"""
    entry = _global_custom_entry(db, skill_key)
    if payload.display_name is not None:
        entry.display_name = payload.display_name.strip() or entry.display_name
    if payload.description is not None:
        entry.description = payload.description
    if payload.tone is not None and entry.category != "flow":
        entry.manifest = dict(entry.manifest or {})
        entry.manifest["tone"] = payload.tone.strip() or DEFAULT_TONE
        entry.manifest = validate_manifest(entry.manifest)
        entry.checksum = compute_checksum(
            skill_key=entry.skill_key,
            version=entry.version,
            manifest=entry.manifest or {},
            allowed_tool_ids=entry.allowed_tool_ids or [],
        )
    db.commit()
    db.refresh(entry)
    return entry


def delete_global_skill(db, skill_key: str) -> None:
    """删除全局自定义 Skill：条目、工作区授权与全部规则行（含个人副本行）一起删。

    没有全局条目后个人副本行也无法解析（_require_entry 会 404），留着的只会是脏数据。
    """
    entry = _global_custom_entry(db, skill_key)
    _delete_grants(db, entry.id)
    for row in db.scalars(
        select(models.SkillWritingRule).where(models.SkillWritingRule.skill_key == skill_key)
    ).all():
        db.delete(row)
    db.delete(entry)
    db.commit()


def set_enabled(
    db,
    *,
    user,
    workspace: models.Workspace,
    entry: models.SkillRegistryEntry,
    enabled: bool,
    granted_tool_ids: list[str] | None,
    approval_policy: str,
) -> models.WorkspaceSkill:
    """启用/停用并写入工作区授权；两个 PATCH 入口共用这一份校验。"""
    if entry.source == "custom" and entry.owner_user_id != user.id:
        raise ApiError("skill_not_editable", "无权操作他人的自定义 Skill", status_code=403)
    if enabled and entry.trust_status != "trusted":
        raise ApiError("untrusted_skill", "来源未校验的 Skill 不允许启用", status_code=409)

    grant = db.scalar(
        select(models.WorkspaceSkill).where(
            models.WorkspaceSkill.workspace_id == workspace.id,
            models.WorkspaceSkill.skill_entry_id == entry.id,
        )
    )
    if grant is None:
        grant = models.WorkspaceSkill(
            workspace_id=workspace.id, skill_entry_id=entry.id, enabled=False
        )
        db.add(grant)
        db.flush()

    if enabled:
        requested = (
            granted_tool_ids
            if granted_tool_ids is not None
            else (entry.allowed_tool_ids or [])
        )
        over = [t for t in requested if t not in (entry.allowed_tool_ids or [])]
        if over:
            raise ApiError(
                "tool_not_in_skill",
                f"工具超出 Skill 声明范围：{', '.join(over)}",
                status_code=409,
            )
        grant.granted_tool_ids = agent_policy.resolve_allowed_tools(
            skill_tools=entry.allowed_tool_ids or [],
            workspace_granted=requested,
        )
    grant.enabled = enabled
    grant.approval_policy = approval_policy
    db.commit()
    db.refresh(grant)
    return grant
