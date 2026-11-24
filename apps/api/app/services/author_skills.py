"""技能中心领域服务：用户技能卡（写作 / 人物 / 知识）的增删改查、蒸馏、导入导出。

与 `skill_registry`（Agent 工具型 Skill）和 `writing_rules`（创作规则）完全解耦：
这里的技能只回答「怎么写、怎么想、依据什么」，不携带工具声明，也不进 Agent 权限体系。

三类技能共用一张卡，`config` 的形状由 `skill_type` 决定，并由本模块统一校验，
避免前端写入任意结构后到生成时才静默失效。
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from ..core.deps import ApiError, not_found
from ..core.ssrf import UnsafeUrl, safe_request
from . import ai_gateway

SKILL_TYPES = ("writing", "persona", "knowledge")
TYPE_LABELS = {"writing": "写作风格", "persona": "人物思维", "knowledge": "领域知识"}
SOURCE_LABELS = {"manual": "手动创建", "distilled": "资料蒸馏", "imported": "导入"}
MAX_ACTIVE_SKILLS = 5
MIN_MATERIAL_CHARS = 200
EXPORT_FORMAT = "hanyue.author-skill/v1"

_NAME_MAX = 80
_SUMMARY_MAX = 400
_TEXT_MAX = 4000
_RULE_MAX = 300
_RULES_MAX = 40
_ITEMS_MAX = 20


# ---------------- 模型网关 ----------------


async def generate_text(db, user_id: str, prompt: str, system_prompt: str | None = None, *, model_id: str | None = None, model_metadata: dict | None = None) -> str:
    """走用户自己的模型配置；没有可用连接时明确失败，不返回占位文本。"""
    try:
        result = await ai_gateway.generate_text(
            db,
            owner_user_id=user_id,
            task_type="article",
            prompt=prompt,
            system_prompt=system_prompt,
            **({"model_id": model_id} if model_id else {}),
        )
    except ai_gateway.InvalidModelSelection as exc:
        raise ApiError(exc.code, exc.message, status_code=400) from exc
    if result.get("errorCode") or not (result.get("text") or "").strip():
        raise ApiError(
            result.get("errorCode") or "empty_generation",
            "模型未能生成内容，请在「系统 → 模型管理」检查连接后重试",
            status_code=503,
            retryable=True,
        )
    if model_metadata is not None:
        model_metadata.update({key: result.get(key) for key in ("modelId", "modelCode", "fallbackUsed")})
    return result["text"].strip()


# ---------------- config 校验 ----------------


def _text(value, field: str, limit: int = _TEXT_MAX, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ApiError("invalid_config", f"{field} 必须是文本", 422)
    value = value.strip()
    if required and not value:
        raise ApiError("invalid_config", f"{field} 不能为空", 422)
    if len(value) > limit:
        raise ApiError("invalid_config", f"{field} 不能超过 {limit} 字", 422)
    return value


def _text_list(value, field: str, limit: int = _RULES_MAX) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ApiError("invalid_config", f"{field} 必须是数组", 422)
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ApiError("invalid_config", f"{field} 的每一项都必须是文本", 422)
        cleaned = item.strip()
        if not cleaned:
            continue
        if len(cleaned) > _RULE_MAX:
            raise ApiError("invalid_config", f"{field} 单条不能超过 {_RULE_MAX} 字", 422)
        items.append(cleaned)
    if len(items) > limit:
        raise ApiError("invalid_config", f"{field} 最多 {limit} 条", 422)
    return items


def _object_list(value, field: str, keys: tuple[str, ...], limit: int = _ITEMS_MAX) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ApiError("invalid_config", f"{field} 必须是数组", 422)
    rows: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            raise ApiError("invalid_config", f"{field} 的每一项都必须是对象", 422)
        row: dict = {}
        for key in keys:
            raw = item.get(key)
            if isinstance(raw, list):
                row[key] = _text_list(raw, f"{field}.{key}")
            else:
                row[key] = _text(raw, f"{field}.{key}", _RULE_MAX)
        if any(row.values()):
            rows.append(row)
    if len(rows) > limit:
        raise ApiError("invalid_config", f"{field} 最多 {limit} 项", 422)
    return rows


def normalize_config(skill_type: str, raw) -> dict:
    """按技能类型收敛 config，未知键一律丢弃，保证编译 prompt 时可预期。"""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ApiError("invalid_config", "config 必须是对象", 422)

    if skill_type == "writing":
        expression = raw.get("expression") if isinstance(raw.get("expression"), dict) else {}
        return {
            "tone": _text(raw.get("tone"), "语气", _RULE_MAX),
            "styleRules": _text_list(raw.get("styleRules"), "写作规则"),
            "openings": _text_list(raw.get("openings"), "开头偏好"),
            "structures": _text_list(raw.get("structures"), "结构偏好"),
            "vocabulary": _text_list(raw.get("vocabulary"), "用词偏好"),
            "taboos": _text_list(raw.get("taboos"), "禁用表达"),
            "materialPolicy": _text(raw.get("materialPolicy"), "素材使用原则"),
            "exemplars": _object_list(raw.get("exemplars"), "范例", ("title", "excerpt")),
            "expression": {
                "sentenceLength": _text(expression.get("sentenceLength"), "句长偏好", _RULE_MAX),
                "punctuation": _text(expression.get("punctuation"), "标点习惯", _RULE_MAX),
            },
        }

    if skill_type == "persona":
        expression = raw.get("expression") if isinstance(raw.get("expression"), dict) else {}
        return {
            "mentalModels": _object_list(
                raw.get("mentalModels"), "心智模型", ("name", "description", "application")
            ),
            "heuristics": _text_list(raw.get("heuristics"), "决策启发"),
            "values": _text_list(raw.get("values"), "价值取向"),
            "boundaries": _text_list(raw.get("boundaries"), "诚实边界"),
            "expression": {
                "style": _text(expression.get("style"), "表达风格", _RULE_MAX),
                "tone": _text(expression.get("tone"), "表达语气", _RULE_MAX),
                "habits": _text_list(expression.get("habits"), "表达习惯"),
            },
        }

    return {
        "overview": _text(raw.get("overview"), "知识概览"),
        "capabilities": _object_list(
            raw.get("capabilities"), "能力卡", ("name", "cognition", "application", "examples")
        ),
        "glossary": _object_list(raw.get("glossary"), "术语表", ("term", "definition")),
        "cases": _object_list(raw.get("cases"), "案例", ("title", "summary")),
        "counterexamples": _text_list(raw.get("counterexamples"), "反例"),
    }


def _bullet(items: list[str], prefix: str = "- ") -> str:
    return "\n".join(f"{prefix}{item}" for item in items)


def compile_skill(skill: models.AuthorSkill) -> str:
    """把一张技能卡渲染成可直接进 system prompt 的指令块。"""
    config = skill.config or {}
    head = f"【{TYPE_LABELS.get(skill.skill_type, '技能')}：{skill.name}】"
    if skill.summary:
        head += f"\n定位：{skill.summary}"

    if skill.skill_type == "writing":
        parts = [head]
        if config.get("tone"):
            parts.append(f"语气：{config['tone']}")
        expression = config.get("expression") or {}
        if expression.get("sentenceLength") or expression.get("punctuation"):
            parts.append(
                "语言习惯：" + "；".join(
                    value for value in (expression.get("sentenceLength"), expression.get("punctuation")) if value
                )
            )
        if config.get("styleRules"):
            parts.append("写作规则：\n" + _bullet(config["styleRules"], "1. "))
        if config.get("openings"):
            parts.append("开头偏好：" + "、".join(config["openings"]))
        if config.get("structures"):
            parts.append("结构偏好：" + "、".join(config["structures"]))
        if config.get("vocabulary"):
            parts.append("优先用词：" + "、".join(config["vocabulary"]))
        if config.get("taboos"):
            parts.append("禁用表达：" + "、".join(config["taboos"]))
        if config.get("materialPolicy"):
            parts.append("素材原则：" + config["materialPolicy"])
        if config.get("exemplars"):
            examples = [
                f"- {row.get('title', '范例')}：{row.get('excerpt', '')}".rstrip("：")
                for row in config["exemplars"]
            ]
            parts.append("风格范例：\n" + "\n".join(examples))
        return "\n".join(parts)

    if skill.skill_type == "persona":
        parts = [head]
        if config.get("mentalModels"):
            models_text = "\n".join(
                f"- {row.get('name', '')}：{row.get('description', '')}"
                + (f"（应用：{row['application']}）" if row.get("application") else "")
                for row in config["mentalModels"]
            )
            parts.append("心智模型：\n" + models_text)
        if config.get("heuristics"):
            parts.append("决策启发：\n" + _bullet(config["heuristics"]))
        expression = config.get("expression") or {}
        if expression.get("style") or expression.get("tone"):
            parts.append(
                "表达特征：" + "；".join(
                    value for value in (expression.get("style"), expression.get("tone")) if value
                )
            )
        if expression.get("habits"):
            parts.append("表达习惯：" + "、".join(expression["habits"]))
        if config.get("values"):
            parts.append("价值取向：" + "、".join(config["values"]))
        if config.get("boundaries"):
            parts.append("诚实边界（不得越界断言）：" + "；".join(config["boundaries"]))
        return "\n".join(parts)

    parts = [head]
    if config.get("overview"):
        parts.append("知识概览：" + config["overview"])
    if config.get("capabilities"):
        cards = []
        for row in config["capabilities"]:
            line = f"- {row.get('name', '能力')}"
            if row.get("cognition"):
                line += f"\n  认知：{row['cognition']}"
            if row.get("examples"):
                line += f"\n  实例：{'；'.join(row['examples'])}"
            if row.get("application"):
                line += f"\n  应用：{row['application']}"
            cards.append(line)
        parts.append("能力卡：\n" + "\n".join(cards))
    if config.get("glossary"):
        parts.append(
            "术语表：\n" + _bullet([f"{row['term']}：{row['definition']}" for row in config["glossary"]])
        )
    if config.get("cases"):
        parts.append(
            "案例：\n" + _bullet([f"{row['title']}：{row['summary']}" for row in config["cases"]])
        )
    if config.get("counterexamples"):
        parts.append("反例（避免这些做法）：" + "；".join(config["counterexamples"]))
    return "\n".join(parts)


# ---------------- 读取与校验 ----------------


def get_skill(db: Session, user_id: str, skill_id: str) -> models.AuthorSkill:
    skill = db.get(models.AuthorSkill, skill_id)
    if not skill or skill.owner_user_id != user_id:
        raise not_found("技能")
    return skill


def list_skills(db: Session, user_id: str, skill_type: str | None = None) -> list[models.AuthorSkill]:
    if skill_type and skill_type not in SKILL_TYPES:
        raise ApiError("invalid_skill_type", "技能类型只能是写作、人物或知识", 422)
    stmt = select(models.AuthorSkill).where(models.AuthorSkill.owner_user_id == user_id)
    if skill_type:
        stmt = stmt.where(models.AuthorSkill.skill_type == skill_type)
    return list(
        db.scalars(
            stmt.order_by(models.AuthorSkill.updated_at.desc(), models.AuthorSkill.id.desc())
        )
    )


def build_system_prompt(db: Session, user_id: str, skill_ids: list[str] | None) -> str:
    """编译选中的技能。越权或已删除的技能直接报错，不静默降级成默认风格。"""
    if not skill_ids:
        return ""
    unique_ids = list(dict.fromkeys(skill_id for skill_id in skill_ids if skill_id))
    if len(unique_ids) > MAX_ACTIVE_SKILLS:
        raise ApiError("too_many_skills", f"一次最多启用 {MAX_ACTIVE_SKILLS} 个技能", 422)
    blocks = []
    for skill_id in unique_ids:
        skill = get_skill(db, user_id, skill_id)
        if skill.status != "ready":
            raise ApiError("skill_not_ready", f"技能「{skill.name}」尚不可用", 409)
        blocks.append(compile_skill(skill))
    return "以下技能规则必须遵守；与用户本次指令冲突时，以用户指令为准。\n\n" + "\n\n".join(blocks)


def _check_name(db: Session, user_id: str, name: str, exclude_id: str | None = None) -> str:
    name = _text(name, "技能名称", _NAME_MAX, required=True)
    stmt = select(models.AuthorSkill.id).where(
        models.AuthorSkill.owner_user_id == user_id, models.AuthorSkill.name == name
    )
    if exclude_id:
        stmt = stmt.where(models.AuthorSkill.id != exclude_id)
    if db.scalar(stmt):
        raise ApiError("skill_name_exists", f"已存在名为「{name}」的技能", 409)
    return name


def _revision(db: Session, skill: models.AuthorSkill, source: str, note: str = "") -> None:
    db.add(
        models.AuthorSkillRevision(
            skill_id=skill.id,
            owner_user_id=skill.owner_user_id,
            version=skill.version,
            summary=skill.summary,
            config=skill.config,
            source=source,
            note=note[:200],
        )
    )


# ---------------- 写操作 ----------------


def create_skill(
    db: Session,
    user_id: str,
    *,
    name: str,
    skill_type: str,
    description: str = "",
    summary: str = "",
    config: dict | None = None,
    source: str = "manual",
) -> models.AuthorSkill:
    if skill_type not in SKILL_TYPES:
        raise ApiError("invalid_skill_type", "技能类型只能是 writing、persona 或 knowledge", 422)
    skill = models.AuthorSkill(
        owner_user_id=user_id,
        name=_check_name(db, user_id, name),
        skill_type=skill_type,
        description=_text(description, "说明"),
        summary=_text(summary, "定位", _SUMMARY_MAX),
        config=normalize_config(skill_type, config),
        source=source if source in SOURCE_LABELS else "manual",
        status="ready",
        version=1,
    )
    db.add(skill)
    db.flush()
    _revision(db, skill, "manual", "创建技能")
    db.commit()
    db.refresh(skill)
    return skill


def update_skill(
    db: Session,
    user_id: str,
    skill_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    summary: str | None = None,
    config: dict | None = None,
    note: str = "",
) -> models.AuthorSkill:
    skill = get_skill(db, user_id, skill_id)
    changed = False
    if name is not None and name.strip() != skill.name:
        skill.name = _check_name(db, user_id, name, exclude_id=skill.id)
        changed = True
    if description is not None:
        skill.description = _text(description, "说明")
        changed = True
    if summary is not None:
        skill.summary = _text(summary, "定位", _SUMMARY_MAX)
        changed = True
    if config is not None:
        skill.config = normalize_config(skill.skill_type, config)
        changed = True
    if changed:
        skill.version += 1
        _revision(db, skill, "manual", note or "编辑技能")
    db.commit()
    db.refresh(skill)
    return skill


def delete_skill(db: Session, user_id: str, skill_id: str) -> None:
    skill = get_skill(db, user_id, skill_id)
    db.delete(skill)
    db.commit()


def duplicate_skill(db: Session, user_id: str, skill_id: str) -> models.AuthorSkill:
    skill = get_skill(db, user_id, skill_id)
    base = f"{skill.name} 副本"
    name = base
    index = 2
    while db.scalar(
        select(models.AuthorSkill.id).where(
            models.AuthorSkill.owner_user_id == user_id, models.AuthorSkill.name == name
        )
    ):
        name = f"{base} {index}"
        index += 1
    copy = models.AuthorSkill(
        owner_user_id=user_id,
        name=name,
        skill_type=skill.skill_type,
        description=skill.description,
        summary=skill.summary,
        config=skill.config,
        source=skill.source,
        status="ready",
        version=1,
    )
    db.add(copy)
    db.flush()
    _revision(db, copy, "manual", f"复制自 {skill.name}")
    db.commit()
    db.refresh(copy)
    return copy


# ---------------- 导入导出 ----------------


def export_skill(skill: models.AuthorSkill) -> dict:
    return {
        "format": EXPORT_FORMAT,
        "name": skill.name,
        "skillType": skill.skill_type,
        "description": skill.description,
        "summary": skill.summary,
        "config": skill.config,
        "version": skill.version,
        "source": skill.source,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
    }


def export_package(db: Session, user_id: str) -> dict:
    skills = list_skills(db, user_id)
    return {
        "format": EXPORT_FORMAT,
        "kind": "bundle",
        "count": len(skills),
        "skills": [export_skill(skill) for skill in skills],
        "exportedAt": datetime.now(timezone.utc).isoformat(),
    }


def _unique_name(db: Session, user_id: str, name: str) -> str:
    base = name
    candidate = base
    index = 2
    while db.scalar(
        select(models.AuthorSkill.id).where(
            models.AuthorSkill.owner_user_id == user_id, models.AuthorSkill.name == candidate
        )
    ):
        candidate = f"{base}（导入 {index}）"
        index += 1
    return candidate


def import_skill(db: Session, user_id: str, payload: dict) -> list[models.AuthorSkill]:
    """导入单张或整包技能；格式不符明确拒绝，同名自动改名，不覆盖已有技能。"""
    if not isinstance(payload, dict):
        raise ApiError("invalid_import", "导入内容必须是 JSON 对象", 422)
    if payload.get("format") != EXPORT_FORMAT:
        raise ApiError("invalid_import", f"不支持的技能包格式，需要 {EXPORT_FORMAT}", 422)

    if payload.get("kind") == "bundle" or isinstance(payload.get("skills"), list):
        entries = payload.get("skills")
        if not isinstance(entries, list) or not entries:
            raise ApiError("invalid_import", "技能包为空", 422)
    else:
        entries = [payload]

    if len(entries) > 50:
        raise ApiError("invalid_import", "一次最多导入 50 个技能", 422)

    created: list[models.AuthorSkill] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ApiError("invalid_import", "技能条目必须是对象", 422)
        skill_type = entry.get("skillType")
        if skill_type not in SKILL_TYPES:
            raise ApiError("invalid_import", "技能类型无效", 422)
        name = _text(entry.get("name"), "技能名称", _NAME_MAX, required=True)
        config = normalize_config(skill_type, entry.get("config"))
        if not any(value for value in config.values()):
            raise ApiError("invalid_import", f"技能「{name}」没有可用内容", 422)
        skill = models.AuthorSkill(
            owner_user_id=user_id,
            name=_unique_name(db, user_id, name),
            skill_type=skill_type,
            description=_text(entry.get("description"), "说明"),
            summary=_text(entry.get("summary"), "定位", _SUMMARY_MAX),
            config=config,
            source="imported",
            status="ready",
            version=1,
        )
        db.add(skill)
        db.flush()
        _revision(db, skill, "imported", "导入技能")
        created.append(skill)
    db.commit()
    for skill in created:
        db.refresh(skill)
    return created


# ---------------- 资料蒸馏 ----------------


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


async def fetch_source_text(url: str) -> str:
    """抓取公开网页正文。SSRF 逐跳校验 + 类型/体积限制，空正文视为失败。"""
    if not isinstance(url, str) or not url.strip():
        raise ApiError("invalid_url", "链接不能为空", 422)
    try:
        response = await asyncio.to_thread(
            safe_request,
            "GET",
            url.strip(),
            headers={"User-Agent": "HanyueSkillCenter/1.0", "Accept": "text/html,text/plain"},
            timeout=12,
            max_redirects=3,
        )
        response.raise_for_status()
    except (UnsafeUrl, httpx.HTTPError, ValueError) as exc:
        raise ApiError("source_fetch_failed", f"无法安全获取来源网页：{url}", 422) from exc
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
        raise ApiError("unsupported_source_type", "来源仅支持 HTML 或纯文本网页", 422)
    if len(response.content) > 1_000_000:
        raise ApiError("source_too_large", "来源网页超过 1 MB 限制", 422)
    raw = response.text
    if content_type in {"text/html", "application/xhtml+xml"} or "<html" in raw[:1000].lower():
        parser = _TextExtractor()
        parser.feed(raw)
        raw = "\n".join(parser.parts)
    raw = raw.strip()
    if len(raw) < 100:
        raise ApiError("insufficient_material", f"来源网页没有足够的可读正文：{url}", 422)
    return raw[:20000]


async def _collect_materials(payload: dict) -> tuple[str, dict]:
    materials = payload.get("materials")
    urls = payload.get("urls")
    chunks: list[dict] = []

    if materials not in (None, []):
        if not isinstance(materials, list):
            raise ApiError("invalid_materials", "materials 必须是数组", 422)
        for item in materials:
            if isinstance(item, str):
                title, content = "", item
            elif isinstance(item, dict):
                title = str(item.get("title") or "")
                content = item.get("content")
            else:
                raise ApiError("invalid_materials", "材料必须是文本或 {title, content}", 422)
            if not isinstance(content, str):
                raise ApiError("invalid_materials", "材料 content 必须是文本", 422)
            content = content.strip()
            if content:
                chunks.append({"title": title.strip()[:120], "content": content[:20000]})
    if urls not in (None, []):
        if not isinstance(urls, list):
            raise ApiError("invalid_urls", "urls 必须是数组", 422)
        if len(urls) > 20:
            raise ApiError("too_many_urls", "一次最多抓取 20 个网页", 422)
        for url in urls:
            target = url.get("url") if isinstance(url, dict) else url
            text = await fetch_source_text(target)
            chunks.append({"title": str(target)[:120], "content": text, "url": target})

    total = sum(len(chunk["content"]) for chunk in chunks)
    if total < MIN_MATERIAL_CHARS:
        raise ApiError(
            "insufficient_material",
            f"资料不足：至少需要 {MIN_MATERIAL_CHARS} 字可读材料，当前 {total} 字",
            422,
        )
    joined = "\n\n---\n\n".join(
        f"[{chunk.get('title') or '材料'}]\n{chunk['content']}" for chunk in chunks
    )[:60000]
    stats = {
        "materialCount": len(chunks),
        "charCount": total,
        "urlCount": sum(1 for chunk in chunks if chunk.get("url")),
        "titles": [chunk["title"] for chunk in chunks if chunk.get("title")][:10],
    }
    return joined, stats


_PROMPTS = {
    "writing": (
        "你是写作风格分析师。只依据给定语料，提炼可复用的写作特征，不照抄长句，不虚构作者经历。\n"
        "输出 JSON 对象，字段："
        "tone（整体语气，<=100字）；"
        "styleRules（3-12条可执行规则，每条<=120字）；"
        "openings（开头习惯，0-5条）；"
        "structures（结构习惯，0-5条）；"
        "vocabulary（偏好用词，0-12个）；"
        "taboos（该语料明显避免的表达，0-8条）；"
        "materialPolicy（素材/数据使用习惯，<=150字）；"
        "expression（对象：sentenceLength 句长偏好，punctuation 标点习惯）。"
        "证据不足的字段留空字符串或空数组，不要编造。"
    ),
    "persona": (
        "你是人物思维分析师。只依据给定材料，提取该人物的思考与表达方式；"
        "不推断私生活，不确定的内容写入 boundaries。\n"
        "输出 JSON 对象，字段："
        "mentalModels（数组，3-6项，每项 {name, description, application}）；"
        "heuristics（决策启发，3-8条）；"
        "values（价值取向，2-6条）；"
        "boundaries（证据不足或不该断言之处，2-6条）；"
        "expression（对象：style 表达风格，tone 语气，habits 表达习惯数组）。"
    ),
    "knowledge": (
        "你是知识结构化分析师。只依据给定材料，提炼可迁移的方法论；"
        "每条结论标注依据，材料没有的不要补造。\n"
        "输出 JSON 对象，字段："
        "overview（知识概览，<=300字）；"
        "capabilities（能力卡数组，3-8项，每项 {name, cognition 认知要点, "
        "examples 材料中的实例数组, application 如何应用}）；"
        "glossary（术语数组，每项 {term, definition}）；"
        "cases（案例数组，每项 {title, summary}）；"
        "counterexamples（反例，0-8条）。"
    ),
}


def _parse_json(raw: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ApiError("invalid_model_output", "模型返回的蒸馏结果不是有效 JSON，请重试", 502) from exc
    if not isinstance(value, dict):
        raise ApiError("invalid_model_output", "模型返回的蒸馏结果必须是 JSON 对象", 502)
    return value


def _has_content(skill_type: str, config: dict) -> bool:
    if skill_type == "writing":
        return bool(config.get("styleRules") or config.get("tone") or config.get("openings"))
    if skill_type == "persona":
        return bool(config.get("mentalModels") or config.get("heuristics"))
    return bool(config.get("capabilities") or config.get("overview"))


async def distill(db: Session, user_id: str, skill_id: str, payload: dict) -> models.AuthorSkillDistillRun:
    """用用户投喂的材料重写技能内容，成功写入新版本，失败保留失败记录且不动原技能。"""
    skill = get_skill(db, user_id, skill_id)
    if not isinstance(payload, dict):
        raise ApiError("invalid_payload", "请求体必须是对象", 422)
    if payload.get("tier") not in (None, "quick", "standard", "deep"):
        raise ApiError("invalid_tier", "蒸馏深度只能是 quick、standard 或 deep", 422)

    run = models.AuthorSkillDistillRun(
        skill_id=skill.id,
        owner_user_id=user_id,
        skill_type=skill.skill_type,
        status="running",
        progress=10,
        request={
            "materialCount": len(payload.get("materials") or []),
            "urlCount": len(payload.get("urls") or []),
            "personName": str(payload.get("person_name") or "")[:80],
        },
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        source, stats = await _collect_materials(payload)
        run.material_stats = stats
        run.progress = 40
        db.commit()

        generated = _parse_json(
            await generate_text(
                db,
                user_id,
                f"{_PROMPTS[skill.skill_type]}\n\n只返回 JSON，不要代码块或解释。\n\n资料：\n{source}",
                "你是严谨的技能蒸馏器：区分事实、归纳与未知，不虚构来源与数据。",
            )
        )
        config = normalize_config(skill.skill_type, generated)
        if not _has_content(skill.skill_type, config):
            raise ApiError("insufficient_distillation", "材料中未能提炼出可用技能内容", 422)

        summary = generated.get("summary")
        skill.summary = _text(summary, "定位", _SUMMARY_MAX) if summary else skill.summary
        skill.config = config
        skill.source = "distilled"
        skill.version += 1
        skill.status = "ready"
        _revision(db, skill, "distilled", f"资料蒸馏 v{skill.version}")

        run.result = config
        run.status = "completed"
        run.progress = 100
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(run)
        return run
    except Exception as exc:
        db.rollback()
        failed = db.get(models.AuthorSkillDistillRun, run.id)
        if failed is None:
            failed = models.AuthorSkillDistillRun(
                id=run.id,
                skill_id=skill.id,
                owner_user_id=user_id,
                skill_type=skill.skill_type,
                request=run.request or {},
                started_at=run.started_at,
            )
            db.add(failed)
        failed.status = "failed"
        failed.progress = 0
        failed.error = str(exc)[:2000]
        failed.material_stats = run.material_stats or {}
        failed.completed_at = datetime.now(timezone.utc)
        db.commit()
        raise
