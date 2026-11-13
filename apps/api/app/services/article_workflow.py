"""写作工作流检查工具：审稿、AI 味、事实抽取、配图规划、稿件检索。

原则：全部是纯函数 / 只读查询，默认不调用模型，也不写库。
结果是给调用方（Agent 运行器或前端）看的检查报告，不修改正文。
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models

# ---------------------------------------------------------------- 占位提取

# {{image3}} 或 {{image3|一段图注}}；不要求相邻，正文里允许夹空行
_PLACEHOLDER_RE = re.compile(r"\{\{\s*image(\d+)\s*(?:\|[^{}]*?)?\}\}")


def extract_placeholders(markdown: str) -> list[int]:
    """按出现顺序返回 {{imageN}} 的 N 列表（可能重复）。"""
    if not isinstance(markdown, str) or not markdown:
        return []
    return [int(m.group(1)) for m in _PLACEHOLDER_RE.finditer(markdown)]


def _placeholder_violation(indices: list[int]) -> dict | None:
    if not indices:
        return None
    problems: list[str] = []
    if len(set(indices)) != len(indices):
        problems.append("占位编号重复")
    if sorted(set(indices)) != list(range(1, len(set(indices)) + 1)):
        problems.append("占位编号未从 1 连续编号")
    if not problems:
        return None
    return {
        "id": "image-placeholders",
        "message": f"图片占位应为 {{image1}}..{{image{len(set(indices))}}} 连续且不重复，实际为："
        + ", ".join(f"{{{{image{i}}}}}" for i in indices),
        "field": "markdown",
    }


# ---------------------------------------------------------------- 审稿


def _field_text(draft: dict, field: str) -> str:
    value = draft.get(field)
    return value if isinstance(value, str) else ""


def _rule_violation(draft: dict, rule) -> dict | None:
    """单条规则判定；规则缺失或类型不对返回 None（跳过，不抛）。"""
    if not isinstance(rule, dict):
        return None
    kind = rule.get("kind")
    value = rule.get("value")
    if not isinstance(kind, str):
        return None
    field = rule.get("field", "markdown")
    if field not in ("markdown", "title", "summary"):
        field = "markdown"
    text = _field_text(draft, field)
    rule_id = str(rule.get("id") or f"{kind}:{field}")

    if kind in ("contains", "not_contains"):
        if not isinstance(value, str) or not value:
            return None
        if kind == "contains" and value not in text:
            default_msg = f"正文缺少必备内容：{value}"
        elif kind == "not_contains" and value in text:
            default_msg = f"正文出现了禁用内容：{value}"
        else:
            return None
    elif kind in ("min_chars", "max_chars"):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        size = len(text)
        if kind == "min_chars" and size < value:
            default_msg = f"{field} 仅 {size} 字，少于要求的 {value} 字"
        elif kind == "max_chars" and size > value:
            default_msg = f"{field} 共 {size} 字，超过上限 {value} 字"
        else:
            return None
    else:
        return None

    message = rule.get("message")
    return {
        "id": rule_id,
        "message": message if isinstance(message, str) and message else default_msg,
        "field": field,
    }


def review_article(draft_dict: dict, rules) -> dict:
    """按 Skill 的 reviewRules 检查草稿，并始终检查图片占位连续性。

    draft_dict：含 title / markdown / summary 的映射（Agent 运行器直接传 version 字段）。
    """
    draft = draft_dict if isinstance(draft_dict, dict) else {}
    violations: list[dict] = []
    for rule in rules if isinstance(rules, list) else []:
        try:
            violation = _rule_violation(draft, rule)
        except Exception:  # 单条规则异常不影响其它规则
            violation = None
        if violation:
            violations.append(violation)

    placeholder = _placeholder_violation(extract_placeholders(_field_text(draft, "markdown")))
    if placeholder:
        violations.append(placeholder)
    return {"ok": not violations, "violations": violations}


# ---------------------------------------------------------------- AI 味

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")

_SENTENCE_START_PHRASES = (
    "此外",
    "值得注意的是",
    "综上所述",
    "总而言之",
    "不难发现",
    "接下来我们看",
)

_WORD_RULES = (
    ("buzzword-enable", "赋能"),
    ("buzzword-leverage", "抓手"),
    ("buzzword-loop", "闭环"),
)

# 段首判定窗口：段内前 12 个非空白字符
_LEAD_WINDOW = 12


def _paragraphs(text: str) -> list[tuple[int, str]]:
    """按空行切段，返回 (段起始下标, 段文本)。"""
    paragraphs: list[tuple[int, str]] = []
    pos = 0
    for match in _PARAGRAPH_SPLIT_RE.finditer(text):
        paragraphs.append((pos, text[pos : match.start()]))
        pos = match.end()
    paragraphs.append((pos, text[pos:]))
    return [(start, para) for start, para in paragraphs if para.strip()]


def _lead_end(paragraph: str) -> int:
    """段首窗口的结束下标：段内前 _LEAD_WINDOW 个非空白字符。"""
    seen = 0
    for i, ch in enumerate(paragraph):
        if ch.isspace():
            continue
        seen += 1
        if seen >= _LEAD_WINDOW:
            return i + 1
    return len(paragraph)


def check_ai_tone(markdown: str) -> dict:
    """规则式 AI 腔调检查，只报命中位置，不打分、不调用模型。"""
    text = markdown if isinstance(markdown, str) else ""
    hits: list[dict] = []

    for start, para in _paragraphs(text):
        head = para[: _lead_end(para)]
        for phrase in _SENTENCE_START_PHRASES:
            idx = head.find(phrase)
            if idx >= 0:
                hits.append(
                    {
                        "ruleId": "sentence-opener",
                        "excerpt": phrase,
                        "offset": start + idx,
                    }
                )

        first, second = para.find("首先"), para.find("其次")
        if first >= 0 and second >= 0:
            excerpt, idx = ("首先", first) if first <= second else ("其次", second)
            hits.append({"ruleId": "first-then", "excerpt": excerpt, "offset": start + idx})

    for rule_id, word in _WORD_RULES:
        for match in re.finditer(re.escape(word), text):
            hits.append({"ruleId": rule_id, "excerpt": word, "offset": match.start()})

    hits.sort(key=lambda h: h["offset"])
    return {"hits": hits}


# ---------------------------------------------------------------- 事实抽取

_NUMBER_RE = re.compile(
    r"\d+(?:[,，]\d{3})*(?:\.\d+)?"
    r"\s?"
    r"(?:%|％|个百分点|百万|千万|公里|美元|万元|亿元|小时|分钟|"
    r"倍|万|亿|千|元|人|名|家|个|项|次|篇|天|年|月|日|岁|台|辆|吨|米)"
)

_QUOTE_RE = re.compile(r"「([^「」]{4,40})」")

_MAX_NUMBERS = 30
_MAX_QUOTES = 15


def extract_facts(markdown: str) -> dict:
    """正则抽取数字与「」引语，仅做候选清单，不判断真伪、不补来源。"""
    text = markdown if isinstance(markdown, str) else ""
    numbers: list[str] = []
    for match in _NUMBER_RE.finditer(text):
        value = match.group(0)
        if value not in numbers:
            numbers.append(value)
        if len(numbers) >= _MAX_NUMBERS:
            break
    quotes: list[str] = []
    for match in _QUOTE_RE.finditer(text):
        value = match.group(1)
        if value not in quotes:
            quotes.append(value)
        if len(quotes) >= _MAX_QUOTES:
            break
    return {"numbers": numbers, "quotes": quotes}


# ---------------------------------------------------------------- 配图规划


def _policy_int(policy: dict, key: str, default: int) -> int:
    value = policy.get(key, default) if isinstance(policy, dict) else default
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _policy_str(policy: dict, key: str, default: str) -> str:
    value = policy.get(key, default) if isinstance(policy, dict) else default
    return value if isinstance(value, str) and value else default


def _policy_bool(policy: dict, key: str, default: bool) -> bool:
    value = policy.get(key, default) if isinstance(policy, dict) else default
    return value if isinstance(value, bool) else default


def plan_images(
    markdown: str,
    inline_prompts,
    cover_prompt,
    policy=None,
) -> dict:
    """按稿件占位与 imagePolicy 给出封面/正文配图计划；不触发生图。"""
    policy_map = policy if isinstance(policy, dict) else {}
    max_inline = _policy_int(policy_map, "maxInlineImages", 3)
    cover_ratio = _policy_str(policy_map, "coverAspectRatio", "3:4")
    inline_ratio = _policy_str(policy_map, "inlineAspectRatio", "1:1")
    allow_ai = _policy_bool(policy_map, "allowAiImages", True)

    prompts = [p.strip() for p in (inline_prompts or []) if isinstance(p, str) and p.strip()]
    indices = sorted(set(extract_placeholders(markdown)))
    # 占位和提示词条数对不上才是问题；只是被 maxInlineImages 截断不算
    issue = "placeholder_mismatch" if len(indices) != len(prompts) else None
    count = min(len(indices), max_inline if max_inline >= 0 else 0, len(prompts))
    inline = [
        {"index": n, "prompt": prompts[i], "ratio": inline_ratio}
        for i, n in enumerate(indices[:count])
    ]

    cover = None
    if isinstance(cover_prompt, str) and cover_prompt.strip():
        cover = {"prompt": cover_prompt.strip(), "ratio": cover_ratio}
    return {
        "allowAiImages": bool(allow_ai),
        "cover": cover,
        "inline": inline,
        "issue": issue,
    }


# ---------------------------------------------------------------- 稿件检索

_MAX_RETRIEVE_LIMIT = 10


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def retrieve_content(
    db: Session, workspace_id: str, query: str | None, limit: int = 5
) -> dict:
    """只读检索工作区内的稿件标题，返回 id/title，不返回正文。"""
    try:
        limit_value = int(limit)
    except (TypeError, ValueError):
        limit_value = 5
    limit_value = max(1, min(limit_value, _MAX_RETRIEVE_LIMIT))

    stmt = select(models.ContentItem).where(models.ContentItem.workspace_id == workspace_id)
    keyword = (query or "").strip() if isinstance(query, str) else ""
    if keyword:
        stmt = stmt.where(
            models.ContentItem.title.like(f"%{_escape_like(keyword)}%", escape="\\")
        )
    stmt = stmt.order_by(models.ContentItem.updated_at.desc()).limit(limit_value)
    rows = db.scalars(stmt).all()
    items = [{"id": r.id, "title": r.title} for r in rows]
    return {"count": len(items), "items": items}
