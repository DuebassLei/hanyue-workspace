"""续写提示词、写回后的警告扫描、接受后的只读影响检查。

不调用模型，也不改写已接受章节的正文。
"""

from __future__ import annotations

import re
import hashlib
import json

from sqlalchemy import select
from .. import models


def lock_project(db, project_id):
    """Serialize cooperating novel writers on PostgreSQL until transaction ends."""
    return db.scalars(select(models.NovelProject).where(models.NovelProject.id == project_id).with_for_update().execution_options(populate_existing=True)).first()


def context_snapshot(db, project_id, target_order):
    db.expire_all()
    project = db.get(models.NovelProject, project_id)
    chapters = db.scalars(select(models.NovelChapter).where(models.NovelChapter.project_id == project_id, models.NovelChapter.sort_order < target_order).order_by(models.NovelChapter.sort_order, models.NovelChapter.id)).all()
    characters = db.scalars(select(models.NovelCharacter).where(models.NovelCharacter.project_id == project_id).order_by(models.NovelCharacter.id)).all()
    aliases = db.scalars(select(models.NovelAlias).where(models.NovelAlias.project_id == project_id).order_by(models.NovelAlias.id)).all()
    value = [project.rules, [(c.id, c.sort_order, c.title, c.status, c.stale, c.accepted_text, c.facts) for c in chapters], [(c.id, c.original_name, c.new_name, c.relations, c.speech_style, c.state) for c in characters], [(a.id, a.kind, a.original_name, a.new_name) for a in aliases]]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()

_MIN_NAME = 2
_DEATH_MARKERS = ("死", "离场")


def _rule(rules: dict | None, key: str) -> str:
    if not isinstance(rules, dict):
        return ""
    value = rules.get(key)
    return str(value).strip() if value is not None else ""


def _facts(raw) -> dict:
    data = raw if isinstance(raw, dict) else {}
    result = {}
    for key in ('characterChanges', 'itemHoldings', 'events', 'openHooks'):
        value = data.get(key)
        result[key] = value if isinstance(value, list) else ([] if value is None else [value])
    return result


def _as_text(item) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("text") or item.get("name") or json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)).strip()
    return str(item or "").strip()


def _shown_name(character) -> str:
    if isinstance(character, dict):
        original = str(character.get("original_name") or character.get("originalName") or "").strip()
        new_name = str(character.get("new_name") or character.get("newName") or "").strip()
    else:
        original = str(getattr(character, "original_name", "") or "").strip()
        new_name = str(getattr(character, "new_name", "") or "").strip()
    return new_name or original


def build_continue_prompt(accepted_chapters, previous_text: str, characters, aliases, rules) -> str:
    """只收已接受章的 facts、上一章正文、人物、别名和写作规则。"""
    person = _rule(rules, "person")[:500]
    style = _rule(rules, "style")[:500]
    banned = _rule(rules, "banned")[:500]
    lines = [
        "你在续写下一章小说。只输出正文，不要标题，不要解释。",
        "只能依据下面已经接受的事实和上一章正文往下写。",
        "不要使用未接受的草稿、候选稿或改写稿。",
        f"人称：{person}" if person else "人称：跟随默认",
        f"文风：{style}" if style else "文风：跟随默认",
        f"禁用：{banned}" if banned else "禁用：跟随默认",
    ]

    fact_lines = []
    ordered = sorted((row for row in accepted_chapters or [] if getattr(row, 'status', 'accepted') == 'accepted' and not getattr(row, 'stale', False)), key=lambda row: int(getattr(row, "sort_order", 0) or 0))
    for chapter in ordered:
        facts = _facts(getattr(chapter, "facts", None))
        title = str(getattr(chapter, "title", "") or "").strip() or f"第 {getattr(chapter, 'sort_order', 0)} 章"
        bits = []
        for key, label in (
            ("characterChanges", "人物变化"),
            ("itemHoldings", "物品"),
            ("events", "事件"),
            ("openHooks", "伏笔"),
        ):
            values = [_as_text(item) for item in facts[key]]
            values = [item for item in values if item]
            if values:
                bits.append(f"{label}：" + "；".join(values))
        if not bits:
            bits.append('正文片段：' + str(getattr(chapter, 'accepted_text', '') or '')[:200])
        fact_lines.append(f"- {title} [章节 {getattr(chapter, 'id', getattr(chapter, 'sort_order', 0))}]：" + " ".join(bits)[:600])
    budget = 6000
    retained = []
    for line in reversed(fact_lines):
        if budget <= 0:
            break
        retained.append(line[:budget])
        budget -= len(retained[-1]) + 1
    fact_lines = list(reversed(retained))
    lines.append("已接受章节的事实：")
    lines.append("\n".join(fact_lines) if fact_lines else "- （无）")

    character_lines = []
    for character in characters or []:
        if isinstance(character, dict):
            original = str(character.get("original_name") or character.get("originalName") or "").strip()
            new_name = str(character.get("new_name") or character.get("newName") or "").strip()
            state = str(character.get("state") or "present").strip()
            relations = str(character.get("relations") or "").strip()
        else:
            original = str(getattr(character, "original_name", "") or "").strip()
            new_name = str(getattr(character, "new_name", "") or "").strip()
            state = str(getattr(character, "state", "") or "present").strip()
            relations = str(getattr(character, "relations", "") or "").strip()
        if not original and not new_name:
            continue
        shown = new_name or original
        speech = str(character.get('speech_style') or character.get('speechStyle') or '') if isinstance(character, dict) else str(getattr(character, 'speech_style', '') or '')
        extra = "、".join(part for part in (relations, state, speech) if part)
        character_lines.append(f"- {original or shown}（文中用 {shown}）" + (f"：{extra}" if extra else ""))
    lines.append("人物表：")
    lines.append(("\n".join(character_lines)[:2000]) if character_lines else "- （无）")

    alias_lines = []
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
        if new_name:
            alias_lines.append(f"- {original} → {new_name}（{kind}）")
        else:
            alias_lines.append(f"- {original}：该名称保持原名（{kind}）")
    lines.append("别名表：")
    lines.append(("\n".join(alias_lines)[:1000]) if alias_lines else "- （无）")

    lines.append("上一章已接受正文：")
    lines.append(str(previous_text or "")[-4000:])
    return "\n".join(lines)


def _name_in(text: str, name: str) -> bool:
    cleaned = str(name or "").strip()
    if len(cleaned) < _MIN_NAME:
        return False
    return cleaned in (text or "")


def scan_warnings(draft_text: str, characters, aliases) -> list[dict]:
    """死亡或离场人物、以及泄漏的原名。短于 2 字的名字不报。"""
    text = str(draft_text or "")
    warnings = []
    seen = set()
    for character in characters or []:
        if isinstance(character, dict):
            original = str(character.get("original_name") or character.get("originalName") or "").strip()
            new_name = str(character.get("new_name") or character.get("newName") or "").strip()
            state = str(character.get("state") or "").strip()
        else:
            original = str(getattr(character, "original_name", "") or "").strip()
            new_name = str(getattr(character, "new_name", "") or "").strip()
            state = str(getattr(character, "state", "") or "").strip()
        if state not in ("dead", "absent"):
            continue
        shown = new_name or original
        if not _name_in(text, shown):
            continue
        key = ("character_present", shown)
        if key in seen:
            continue
        seen.add(key)
        warnings.append({"code": "character_present", "name": shown})

    for alias in aliases or []:
        if isinstance(alias, dict):
            original = str(alias.get("original_name") or alias.get("originalName") or "").strip()
            new_name = str(alias.get("new_name") or alias.get("newName") or "").strip()
        else:
            original = str(getattr(alias, "original_name", "") or "").strip()
            new_name = str(getattr(alias, "new_name", "") or "").strip()
        if not original or not new_name:
            continue
        if not _name_in(text, original):
            continue
        key = ("original_name_leaked", original)
        if key in seen:
            continue
        seen.add(key)
        warnings.append({"code": "original_name_leaked", "name": original})
    return warnings


def scan_impact(accepted_chapter, later_accepted_chapters, characters) -> dict:
    """只读：被接受章的「死 / 离场」是否点到后续已接受章，以及后续章是否已标 stale。"""
    facts = _facts(getattr(accepted_chapter, "facts", None))
    changes = [_as_text(item) for item in facts["characterChanges"]]
    death_notes = [item for item in changes if item and any(mark in item for mark in _DEATH_MARKERS)]
    names = []
    seen_names = set()

    def _add(name: str) -> None:
        cleaned = str(name or "").strip()
        if len(cleaned) < _MIN_NAME or cleaned in seen_names:
            return
        if not any(cleaned in note for note in death_notes):
            return
        seen_names.add(cleaned)
        names.append(cleaned)

    if death_notes:
        for character in characters or []:
            if isinstance(character, dict):
                candidates = (
                    str(character.get("new_name") or character.get("newName") or "").strip(),
                    str(character.get("original_name") or character.get("originalName") or "").strip(),
                )
            else:
                candidates = (
                    str(getattr(character, "new_name", "") or "").strip(),
                    str(getattr(character, "original_name", "") or "").strip(),
                )
            for name in candidates:
                _add(name)
        for note in death_notes:
            if any(name in note for name in names):
                continue
            for piece in re.findall(r"[一-鿿A-Za-z0-9]{2,}", note):
                if piece in _DEATH_MARKERS:
                    continue
                _add(piece)

    chapters = []
    for chapter in later_accepted_chapters or []:
        if str(getattr(chapter, "status", "") or "") != "accepted":
            continue
        reasons = []
        text = str(getattr(chapter, "accepted_text", "") or "")
        for name in names:
            if name in text:
                reasons.append({"code": "stale_character", "name": name})
        if bool(getattr(chapter, "stale", False)):
            reasons.append({"reason": "marked_stale"})
        if reasons:
            chapters.append({"chapterId": chapter.id, "reasons": reasons})
    return {"chapters": chapters}
