"""写作工作流检查工具：审稿、AI 味、事实抽取、配图规划、稿件检索。

原则：全部是纯函数 / 只读查询，默认不调用模型，也不写库。
结果是给调用方（Agent 运行器或前端）看的检查报告，不修改正文。
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import prompt_rules

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


def _rule_active(rule: dict) -> bool:
    """规则是否可执行：与 _rule_violation 的入参校验一致，无效配置会被跳过。"""
    kind = rule.get("kind")
    value = rule.get("value")
    if kind in ("contains", "not_contains"):
        return isinstance(value, str) and bool(value)
    if kind in ("min_chars", "max_chars"):
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return False


def review_rule_reports(draft_dict: dict, rules) -> list[dict]:
    """逐条给出审稿规则的通过情况（含内置图片占位检查），供「流程」面板展示。

    与 review_article 共用判定，区别是这里通过的规则也列出来；
    ok=None 表示规则配置无效被跳过，前端显示为「未生效」。
    """
    draft = draft_dict if isinstance(draft_dict, dict) else {}
    reports: list[dict] = []
    for rule in rules if isinstance(rules, list) else []:
        if not isinstance(rule, dict) or not isinstance(rule.get("kind"), str):
            continue
        kind = rule["kind"]
        field = rule.get("field", "markdown")
        if field not in ("markdown", "title", "summary"):
            field = "markdown"
        active = _rule_active(rule)
        try:
            violation = _rule_violation(draft, rule) if active else None
        except Exception:  # 与 review_article 同口径：单条异常不影响其它规则
            violation = None
        reports.append(
            {
                "id": str(rule.get("id") or f"{kind}:{field}"),
                "kind": kind,
                "value": rule.get("value"),
                "field": field,
                "ok": None if not active else violation is None,
                "message": violation["message"] if violation else None,
            }
        )
    placeholder = _placeholder_violation(extract_placeholders(_field_text(draft, "markdown")))
    reports.append(
        {
            "id": "image-placeholders",
            "kind": "builtin",
            "value": None,
            "field": "markdown",
            "ok": placeholder is None,
            "message": placeholder["message"] if placeholder else None,
        }
    )
    return reports


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

# 模式句式：整段正则匹配（AI 高频开头套路）
_PATTERN_RULES = (
    ("pattern-era", re.compile(r"在这个[^，。！？]{2,20}的时代")),
    ("pattern-with", re.compile(r"随着[^，。！？]{2,20}的(?:发展|普及|进步|到来)")),
)

# 段首判定窗口：段内前 12 个非空白字符
_LEAD_WINDOW = 12

# 内置默认词表：tone_lexicon.* 各 key 的出厂数据。
# 运行期规则表缺失某 key = 用这里的默认；显式空列表 = 该规则关闭（见 check_ai_tone）。
DEFAULT_LEXICON: dict = {
    "sentence_openers": {"window": _LEAD_WINDOW, "phrases": list(_SENTENCE_START_PHRASES)},
    "buzzwords": {"rules": [{"id": rid, "word": word} for rid, word in _WORD_RULES]},
    "patterns": {"rules": [{"id": rid, "pattern": pattern.pattern} for rid, pattern in _PATTERN_RULES]},
    "first_then": {"words": ["首先", "其次"], "ruleId": "first-then"},
}

_TONE_LEXICON_KEYS = tuple(f"tone_lexicon.{name}" for name in DEFAULT_LEXICON)


def load_lexicon(db: Session | None) -> dict:
    """运行期词表：tone_lexicon.* 各 key 的 payload（经 prompt_rules 短缓存）。

    缺失 / 停用 / 空 payload 的 key 不出现在结果里，check_ai_tone 会回退到内置默认。
    """
    lexicon: dict = {}
    if db is None:
        return lexicon
    for key in _TONE_LEXICON_KEYS:
        payload = prompt_rules.get_prompt_payload(db, key)
        if payload:
            lexicon[key.split(".", 1)[1]] = payload
    return lexicon


def lexicon_overview(lexicon: dict | None) -> dict:
    """AI 味词表各节的生效条数与来源，供「流程」面板说明检查依据。

    custom=True 表示该节被规则表覆盖（显式空列表 = 该节规则已关闭）；
    custom=False 表示沿用出厂默认词表。
    """
    lex = {**DEFAULT_LEXICON, **(lexicon or {})}
    custom = set(lexicon or {})

    def _count(section: str, key: str, field: str | None = None) -> int:
        items = (lex.get(section) or {}).get(key) or []
        if field:
            return sum(1 for x in items if isinstance(x, dict) and str(x.get(field) or "").strip())
        return sum(1 for x in items if str(x).strip())

    return {
        "sentenceOpeners": {
            "custom": "sentence_openers" in custom,
            "count": _count("sentence_openers", "phrases"),
        },
        "buzzwords": {"custom": "buzzwords" in custom, "count": _count("buzzwords", "rules", "word")},
        "patterns": {"custom": "patterns" in custom, "count": _count("patterns", "rules", "pattern")},
        "firstThen": {"custom": "first_then" in custom, "count": _count("first_then", "words")},
    }


def _paragraphs(text: str) -> list[tuple[int, str]]:
    """按空行切段，返回 (段起始下标, 段文本)。"""
    paragraphs: list[tuple[int, str]] = []
    pos = 0
    for match in _PARAGRAPH_SPLIT_RE.finditer(text):
        paragraphs.append((pos, text[pos : match.start()]))
        pos = match.end()
    paragraphs.append((pos, text[pos:]))
    return [(start, para) for start, para in paragraphs if para.strip()]


def _lead_end(paragraph: str, window: int = _LEAD_WINDOW) -> int:
    """段首窗口的结束下标：段内前 window 个非空白字符。"""
    seen = 0
    for i, ch in enumerate(paragraph):
        if ch.isspace():
            continue
        seen += 1
        if seen >= window:
            return i + 1
    return len(paragraph)


def check_ai_tone(markdown: str, lexicon: dict | None = None) -> dict:
    """规则式 AI 腔调检查，只报命中位置，不打分、不调用模型。

    lexicon：tone_lexicon.* 各 key 的 payload 合并（见 load_lexicon），按节覆盖默认：
    某节缺失 = 用内置默认（纯函数单测不依赖 DB）；显式空列表 = 该规则关闭。
    """
    text = markdown if isinstance(markdown, str) else ""
    lex = {**DEFAULT_LEXICON, **(lexicon or {})}
    hits: list[dict] = []

    # 段首套话（sentence_openers）：phrases 为空 = 显式关闭；window 可配置
    openers = lex.get("sentence_openers") or {}
    phrases = [str(p) for p in (openers.get("phrases") or []) if str(p).strip()]
    window = openers.get("window")
    lead_window = (
        window
        if isinstance(window, int) and not isinstance(window, bool) and window > 0
        else _LEAD_WINDOW
    )

    # 首先其次（first_then）：words 不足两个 = 关闭
    first_then = lex.get("first_then") or {}
    ft_words = [str(w) for w in (first_then.get("words") or []) if str(w).strip()]
    ft_rule_id = str(first_then.get("ruleId") or "first-then")

    for start, para in _paragraphs(text):
        for phrase in phrases:
            idx = para[: _lead_end(para, lead_window)].find(phrase)
            if idx >= 0:
                hits.append(
                    {
                        "ruleId": "sentence-opener",
                        "excerpt": phrase,
                        "offset": start + idx,
                    }
                )

        if len(ft_words) >= 2:
            first, second = para.find(ft_words[0]), para.find(ft_words[1])
            if first >= 0 and second >= 0:
                excerpt, idx = (ft_words[0], first) if first <= second else (ft_words[1], second)
                hits.append({"ruleId": ft_rule_id, "excerpt": excerpt, "offset": start + idx})

    # 商业黑话（buzzwords）：单词匹配
    for rule in lex.get("buzzwords", {}).get("rules") or []:
        if not isinstance(rule, dict) or not str(rule.get("word") or "").strip():
            continue
        word = str(rule["word"])
        rule_id = str(rule.get("id") or "buzzword")
        for match in re.finditer(re.escape(word), text):
            hits.append({"ruleId": rule_id, "excerpt": word, "offset": match.start()})

    # 模式句式（patterns）：正则匹配；非法正则整条跳过，不让管理端一个 typo 打挂生成
    for rule in lex.get("patterns", {}).get("rules") or []:
        if not isinstance(rule, dict) or not str(rule.get("pattern") or "").strip():
            continue
        try:
            compiled = re.compile(str(rule["pattern"]))
        except re.error:
            continue
        rule_id = str(rule.get("id") or "pattern")
        for match in compiled.finditer(text):
            hits.append({"ruleId": rule_id, "excerpt": match.group(0), "offset": match.start()})

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
