"""小说改写的纯逻辑：提示词、抽取解析、草稿版本与接受/回退。

不入队、不 commit。模型正文由调用方注入；没有可用连接时不写占位文本。
"""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy import select

from .. import models
from . import ai_gateway
from .job_runner import JobFailure

# 小说链路既有正文改写，也有必须输出 JSON 的抽取/事实任务。
# ai_gateway 默认的 SYSTEM_RULES 是「中文自媒体创作助手，只输出正文」，
# 与 JSON 任务冲突，因此这里单独指定系统提示词。
NOVEL_SYSTEM_RULES = (
    "你是中文小说文本处理助手。严格遵循用户的输出格式要求：要求正文时只输出正文，"
    "要求 JSON 时只输出一个 JSON 对象，不要标题、不要解释、不要 Markdown 代码块。"
)

logger = logging.getLogger(__name__)

FACT_KEYS = ("characterChanges", "itemHoldings", "events", "openHooks")
ALIAS_KINDS = {"person", "place", "org"}
CHARACTER_STATES = {"present", "dead", "absent"}
_JSON_DECODER = json.JSONDecoder()


def resolve_novel_selection(
    db,
    user_id: str,
    *,
    model_id: str | None = None,
    apply_default: bool = True,
    explicit: bool = False,
) -> dict:
    """小说链路的模型解析（与创作空间共用 ai_gateway.resolve_selection）。

    - 显式选择 / 快照失效：explicit=True 时抛 InvalidModelSelection，由调用方转成明确失败；
      默认模型快照失效时忽略并回退到原候选链（不清除偏好），同时回报
      defaultModelIgnored，符合「失效忽略 + 明确提示」约定。
    - apply_default=False：队列任务执行时严格按入队快照，不读当前默认。
    """
    try:
        return ai_gateway.resolve_selection(
            db, user_id, model_id, apply_default=apply_default
        )
    except ai_gateway.InvalidModelSelection as exc:
        if explicit:
            raise
        fallback = ai_gateway.resolve_selection(db, user_id, apply_default=False)
        ignored = {
            "reason": "snapshot_invalid",
            "message": exc.message,
            "modelId": model_id,
        }
        logger.warning(
            "[novel] 入队模型快照 %s 已失效，按原候选链生成：%s", model_id, exc.message
        )
        return {
            **fallback,
            "defaultModelIgnored": fallback["defaultModelIgnored"] or ignored,
        }


def get_text_candidates(
    db,
    user_id: str,
    *,
    model_id: str | None = None,
    apply_default: bool = True,
    explicit: bool = False,
) -> list[tuple]:
    """与创作空间一致的文本模型候选链，剔除 `unconfigured` 占位。

    网关已过滤只有图像/视频能力的模型；这里再去掉「连接存在但未登记模型」
    的占位，避免把字面量 "unconfigured" 当模型代号发给供应商（必然 400）。
    """
    selection = resolve_novel_selection(
        db, user_id, model_id=model_id, apply_default=apply_default, explicit=explicit
    )
    return [
        (connection, model_code)
        for connection, model_code in selection["candidates"]
        if connection is not None and model_code and model_code != "unconfigured"
    ]


def _rule(rules: dict | None, key: str) -> str:
    if not isinstance(rules, dict):
        return ""
    value = rules.get(key)
    return str(value).strip() if value is not None else ""


def _alias_rows(aliases) -> list[tuple[str, str, str]]:
    rows = []
    for alias in aliases or []:
        if isinstance(alias, dict):
            kind = str(alias.get("kind") or "").strip()
            original = str(alias.get("original_name") or alias.get("originalName") or "").strip()
            new_name = str(alias.get("new_name") or alias.get("newName") or "").strip()
        else:
            kind = str(getattr(alias, "kind", "") or "").strip()
            original = str(getattr(alias, "original_name", "") or "").strip()
            new_name = str(getattr(alias, "new_name", "") or "").strip()
        if not original:
            continue
        rows.append((kind, original, new_name))
    return rows


def _character_rows(characters) -> list[tuple[str, str, str, str, str]]:
    rows = []
    for character in characters or []:
        if isinstance(character, dict):
            original = str(character.get("original_name") or character.get("originalName") or "").strip()
            new_name = str(character.get("new_name") or character.get("newName") or "").strip()
            relations = str(character.get("relations") or "").strip()
            speech = str(character.get("speech_style") or character.get("speechStyle") or "").strip()
            state = str(character.get("state") or "present").strip()
        else:
            original = str(getattr(character, "original_name", "") or "").strip()
            new_name = str(getattr(character, "new_name", "") or "").strip()
            relations = str(getattr(character, "relations", "") or "").strip()
            speech = str(getattr(character, "speech_style", "") or "").strip()
            state = str(getattr(character, "state", "") or "present").strip()
        if not original:
            continue
        if state not in CHARACTER_STATES:
            state = "present"
        rows.append((original, new_name, relations, speech, state))
    return rows


def build_rewrite_prompt(chapter, aliases, characters, rules) -> str:
    """改写提示词：保住情节骨架，只改距离、句式、密度和措辞。"""
    source = str(getattr(chapter, "source_text", "") or "")
    title = str(getattr(chapter, "title", "") or "").strip()
    person = _rule(rules, "person")
    style = _rule(rules, "style")
    banned = _rule(rules, "banned")

    lines = [
        "你在改写一章小说。只输出改写正文，不要标题，不要解释。",
        "必须保住：事件顺序、因果、转折、结局、人物关系。",
        "允许改：人称距离、句式文风、环境描写密度、对话措辞。信息量不变，不增删情节。",
        f"人称：{person}" if person else "人称跟随原文",
        f"文风：{style}" if style else "文风：不额外指定",
        f"禁用：{banned}" if banned else "禁用：不额外指定",
    ]
    if title:
        lines.append(f"原章节标题（不要写进正文）：{title}")

    alias_lines = []
    for kind, original, new_name in _alias_rows(aliases):
        if new_name:
            alias_lines.append(f"- {original} → {new_name}（{kind}）")
        else:
            alias_lines.append(f"- {original}：该名称保持原名（{kind}）")
    lines.append("人名、地名、组织名必须按下表替换：")
    lines.append("\n".join(alias_lines) if alias_lines else "- （无别名表）")

    character_lines = []
    for original, new_name, relations, speech, state in _character_rows(characters):
        shown = new_name or original
        extra = "、".join(part for part in (relations, speech, state) if part)
        character_lines.append(f"- {original}（文中用 {shown}）" + (f"：{extra}" if extra else ""))
    if character_lines:
        lines.append("人物：")
        lines.append("\n".join(character_lines))

    lines.append("原文：")
    lines.append(source)
    return "\n".join(lines)


def build_extract_prompt(source_text) -> str:
    return "\n".join([
        "从下面的小说原文抽取人物和专名。只输出一个 JSON 对象，不要解释，不要起新名字。",
        "新名字由用户填写，你不要生成 newName。",
        '结构：{"characters":[{"originalName":"","relations":"","speechStyle":"","state":"present"}],'
        '"aliases":[{"kind":"person","originalName":""}]}',
        "kind 只许 person、place、org。state 只许 present、dead、absent，不确定就写 present。",
        "characters 只放人物。aliases 放需要替换的人名、地名、组织名，人物名也应出现在 aliases 里，kind 为 person。",
        "原文：",
        str(source_text or ""),
    ])


def build_facts_prompt(accepted_text, characters) -> str:
    lines = [
        "根据已接受的正文抽取事实。只输出一个 JSON 对象，不要解释，不要写文风评论。",
        '{"characterChanges":[],"itemHoldings":[],"events":[],"openHooks":[]}',
        "四个键都必须在。每项是短字符串：人物状态变化、物品持有、发生过的事、尚未收回的伏笔。",
        "没有就留空数组。不要编造正文里没有的事。",
    ]
    character_lines = []
    for original, new_name, relations, speech, state in _character_rows(characters):
        shown = new_name or original
        character_lines.append(f"- {shown}（原名 {original}，状态 {state}）")
    if character_lines:
        lines.append("已知人物：")
        lines.append("\n".join(character_lines))
    lines.append("正文：")
    lines.append(str(accepted_text or ""))
    return "\n".join(lines)


def _first_json_object(raw: str) -> dict:
    text = str(raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("no object")
    data, _ = _JSON_DECODER.raw_decode(text[start:])
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def parse_extract(raw: str) -> tuple[list[dict], list[dict]]:
    try:
        data = _first_json_object(raw)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise JobFailure("extract_parse_failed", "抽取结果不是可解析的 JSON") from exc

    characters: list[dict] = []
    seen_names: set[str] = set()
    for item in data.get("characters") or []:
        if not isinstance(item, dict):
            continue
        original = str(item.get("originalName") or item.get("original_name") or "").strip()
        if not original or original in seen_names:
            continue
        seen_names.add(original)
        state = str(item.get("state") or "present").strip()
        if state not in CHARACTER_STATES:
            state = "present"
        characters.append({
            "originalName": original,
            "relations": str(item.get("relations") or "").strip(),
            "speechStyle": str(item.get("speechStyle") or item.get("speech_style") or "").strip(),
            "state": state,
        })

    aliases: list[dict] = []
    seen_aliases: set[tuple[str, str]] = set()
    for item in data.get("aliases") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        original = str(item.get("originalName") or item.get("original_name") or "").strip()
        if kind not in ALIAS_KINDS or not original:
            continue
        key = (kind, original)
        if key in seen_aliases:
            continue
        seen_aliases.add(key)
        aliases.append({"kind": kind, "originalName": original})
    return characters, aliases


def _normalize_facts(facts) -> dict:
    source = facts if isinstance(facts, dict) else {}
    cleaned = {}
    for key in FACT_KEYS:
        value = source.get(key, [])
        cleaned[key] = list(value) if isinstance(value, list) else []
    return cleaned


def parse_facts(raw: str) -> dict:
    try:
        data = _first_json_object(raw)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise JobFailure("facts_parse_failed", "事实结果不是可解析的 JSON") from exc
    return _normalize_facts(data)


def apply_extract(db, project, characters, aliases) -> None:
    """覆盖项目的人物和别名。用户已经填过的 new_name 按原名保留。"""
    project_id = project.id
    kept_alias_names = {
        (row.kind, row.original_name): row.new_name
        for row in db.scalars(
            select(models.NovelAlias).where(models.NovelAlias.project_id == project_id)
        ).all()
        if (row.new_name or "").strip()
    }
    kept_character_names = {
        row.original_name: row.new_name
        for row in db.scalars(
            select(models.NovelCharacter).where(models.NovelCharacter.project_id == project_id)
        ).all()
        if (row.new_name or "").strip()
    }
    for row in db.scalars(select(models.NovelAlias).where(models.NovelAlias.project_id == project_id)).all():
        db.delete(row)
    for row in db.scalars(
        select(models.NovelCharacter).where(models.NovelCharacter.project_id == project_id)
    ).all():
        db.delete(row)
    db.flush()

    seen_aliases: set[tuple[str, str]] = set()
    for item in aliases or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        original = str(item.get("originalName") or item.get("original_name") or "").strip()
        if kind not in ALIAS_KINDS or not original:
            continue
        key = (kind, original)
        if key in seen_aliases:
            continue
        seen_aliases.add(key)
        db.add(models.NovelAlias(
            project_id=project_id,
            kind=kind,
            original_name=original[:120],
            new_name=(kept_alias_names.get(key) or "")[:120],
        ))

    seen_names: set[str] = set()
    for item in characters or []:
        if not isinstance(item, dict):
            continue
        original = str(item.get("originalName") or item.get("original_name") or "").strip()
        if not original or original in seen_names:
            continue
        seen_names.add(original)
        state = str(item.get("state") or "present").strip()
        if state not in CHARACTER_STATES:
            state = "present"
        relations = str(item.get("relations") or "")
        speech = str(item.get("speechStyle") or item.get("speech_style") or "")
        db.add(models.NovelCharacter(
            project_id=project_id,
            original_name=original[:120],
            new_name=(kept_character_names.get(original) or "")[:120],
            relations=relations,
            speech_style=speech,
            state=state,
        ))
    db.flush()


def apply_rewrite_result(db, chapter, text: str, expected_version: int) -> str:
    """版本一致则写入草稿；不一致则只暂存候选，返回 conflict。"""
    if chapter.draft_version != expected_version:
        chapter.pending_candidate = text
        db.flush()
        return "conflict"
    chapter.draft_text = text
    chapter.draft_version = int(chapter.draft_version or 0) + 1
    chapter.status = "drafted"
    chapter.pending_candidate = None
    db.flush()
    return "applied"


def accept_chapter(db, chapter, facts: dict) -> None:
    draft = str(chapter.draft_text or "")
    if not draft.strip():
        raise JobFailure("empty_draft", "没有可接受的草稿")
    chapter.accepted_text = chapter.draft_text
    chapter.status = "accepted"
    chapter.facts = _normalize_facts(facts)
    db.flush()


def revert_chapter(db, chapter, later_chapters: list) -> None:
    if chapter.status != "accepted":
        raise JobFailure("not_accepted", "只有已接受的章节可以回退")
    chapter.status = "drafted"
    for later in later_chapters or []:
        later.stale = True
    db.flush()
