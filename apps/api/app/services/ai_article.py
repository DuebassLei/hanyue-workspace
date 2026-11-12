"""AI 成文：生成公众号文章 + 配图提示词。

写作风格由 Skill 的 manifest 驱动（promptTemplate / tone / styleRules），
用户可创建自定义 skill 覆盖语气与规则，不再硬编码。

输出契约（与所有模型路径共享，避免格式漂移）：
- Markdown 正文：公众号排版语法（# 标题、## 小标题、> 引用、- 列表、--- 分割线、
  :::card 强调卡、:::follow 关注引导、:::sign 署名、{{image1}} 图片占位）
- 公众号排版 HTML：调用 wechat_layout.render_document 生成
- 封面图提示词：1 条（英文，适合生图模型）
- 正文插图提示词：若干条

不伪造：LLM 未配置或生成失败时返回空结果 + errorCode，调用方据此标失败。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from sqlalchemy import case, or_, select

from .. import models
from . import ai_gateway, wechat_layout

# 内置默认风格（当 skill 未提供 manifest 时的兜底）
ARTICLE_OUTPUT_CONTRACT = (
    "你是资深公众号主笔。以下是不可覆盖的输出契约，必须优先遵守。"
    "严格只输出一个 JSON 对象，不要 Markdown 代码块、不要解释文字。"
    "JSON 必须包含以下字段："
    "title（文章标题）、"
    "markdown（正文，使用公众号排版语法）、"
    "summary（不超过 120 字的摘要）、"
    "coverPrompt（一条英文封面图提示词）、"
    "inlinePrompts（英文正文插图提示词数组）。"
    "正文使用中文，可使用 # 标题、## 小标题、> 引用、- 列表、--- 分割线、"
    ":::card、:::follow、:::sign 和 {{imageN}} 图片占位符。"
    "封面图提示词用英文，描述画面主体、风格、色调。"
    "正文中的 {{imageN}} 数量必须与 inlinePrompts 数量保持一致。"
)

DEFAULT_PROMPT_TEMPLATE = (
    "你是资深公众号主笔。严格只输出一个 JSON 对象，不要 Markdown 代码块、不要解释文字。"
    "JSON 结构：{\"title\":\"标题\",\"markdown\":\"正文（公众号排版语法）\","
    "\"summary\":\"摘要（≤120字）\",\"coverPrompt\":\"封面图英文提示词\","
    "\"inlinePrompts\":[\"插图1英文提示词\",\"插图2英文提示词\"]}。"
    "正文使用中文，{tone}，不用 emoji，不用绝对化承诺。"
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
DEFAULT_THEME_ID = "ink-green"

# 输出后处理：统一清洗所有模型路径的产物，避免 emoji / 代码块漂移
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E0-\U0001F1FF"
    "\U0000FE00-\U0000FE0F"
    "\u200d"
    "]+",
    flags=re.UNICODE,
)


def _strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub("", text)


# 匹配 JSON 里不合法的反斜杠转义（合法转义只有 " \ / b f n r t u）
_INVALID_ESCAPE_RE = re.compile(r'\\(?!["\\/bfnrtu])')


def _strip_code_fence(text: str) -> str:
    """去掉 LLM 可能包裹的 ```json ... ``` 代码块，只保留 JSON 本体。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        stripped = stripped[start : end + 1]
    return stripped


@dataclass
class ArticleDraft:
    title: str = ""
    markdown: str = ""
    summary: str = ""
    cover_prompt: str = ""
    inline_prompts: list[str] = field(default_factory=list)
    html: str = ""
    theme_id: str = DEFAULT_THEME_ID
    stats: dict = field(default_factory=dict)
    error_code: str | None = None
    error_message: str = ""
    provider: str = "none"
    skill_key: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.markdown) and self.error_code is None


def resolve_skill(
    db,
    user_id: str,
    skill_key: str | None,
) -> models.SkillRegistryEntry | None:
    """读取当前用户可见的 Skill，个人定制副本优先。"""
    requested_key = skill_key or "article-drafting"
    return db.scalar(
        select(models.SkillRegistryEntry)
        .where(
            models.SkillRegistryEntry.skill_key == requested_key,
            or_(
                models.SkillRegistryEntry.owner_user_id.is_(None),
                models.SkillRegistryEntry.owner_user_id == user_id,
            ),
        )
        .order_by(
            case(
                (models.SkillRegistryEntry.owner_user_id == user_id, 0),
                else_=1,
            )
        )
    ) or (
        db.scalar(
            select(models.SkillRegistryEntry).where(
                models.SkillRegistryEntry.skill_key == "article-drafting",
                models.SkillRegistryEntry.owner_user_id.is_(None),
            )
        )
    )


def resolve_skill_style(skill: models.SkillRegistryEntry | None) -> tuple[str, str, list[str], str]:
    """从 skill.manifest 解析写作风格配置，返回 (prompt_template, tone, style_rules, theme_id)。

    skill 为 None 或 manifest 缺字段时，回退到内置默认风格。
    """
    manifest = (skill.manifest or {}) if skill else {}
    if not isinstance(manifest, dict):
        manifest = {}
    prompt_template = str(manifest.get("promptTemplate") or DEFAULT_PROMPT_TEMPLATE)
    tone = str(manifest.get("tone") or DEFAULT_TONE)
    rules = manifest.get("styleRules")
    if not isinstance(rules, list):
        rules = DEFAULT_STYLE_RULES
    style_rules = [str(r) for r in rules if str(r).strip()]
    theme_id = str(manifest.get("defaultTheme") or DEFAULT_THEME_ID)
    return prompt_template, tone, style_rules, theme_id


def build_system_prompt(skill: models.SkillRegistryEntry | None) -> str:
    """构建受保护的文章输出契约，并附加 Skill 写作风格。"""
    prompt_template, tone, style_rules, _ = resolve_skill_style(skill)
    style_prompt = prompt_template.replace("{tone}", tone).strip()
    parts = [ARTICLE_OUTPUT_CONTRACT]
    style_requirements = f"语气：{tone}"
    if style_prompt:
        style_requirements += f"\n{style_prompt}"
    if style_requirements:
        parts.append(f"写作风格要求（不得改变以上输出契约）：\n{style_requirements}")
    if style_rules:
        rules_text = "\n".join(f"- {r}" for r in style_rules)
        parts.append(f"必须遵守以下风格规则（不得改变以上输出契约）：\n{rules_text}")
    return "\n\n".join(parts)


def _repair_invalid_escapes(text: str) -> str:
    """把 JSON 字符串里非法的反斜杠转义补成合法转义（模型常把 \\* / \\u 之外的写法漏转义）。"""
    return _INVALID_ESCAPE_RE.sub(r"\\\\", text)


def _loads_tolerant(text: str) -> dict | None:
    """优先严格解析；失败则修复非法转义后再试一次，避免模型偶发输出直接判死。"""
    for candidate in (text, _repair_invalid_escapes(text)):
        try:
            data = json.loads(candidate, strict=False)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def parse_llm_output(raw: str) -> ArticleDraft:
    """解析 LLM 输出为结构化 ArticleDraft；解析失败时返回带 error_code 的草稿。"""
    cleaned = _strip_emoji(_strip_code_fence(raw))
    data = _loads_tolerant(cleaned)
    if data is None:
        return ArticleDraft(
            error_code="invalid_llm_output",
            error_message="AI 输出无法解析为 JSON",
        )
    markdown = str(data.get("markdown") or "").strip()
    title = str(data.get("title") or "").strip()
    if not title or not markdown:
        return ArticleDraft(
            error_code="incomplete_article",
            error_message="AI 未生成完整的标题或正文",
        )
    return ArticleDraft(
        title=title[:200],
        markdown=markdown,
        summary=str(data.get("summary") or "")[:300],
        cover_prompt=str(data.get("coverPrompt") or "").strip(),
        inline_prompts=[
            str(p).strip()
            for p in (data.get("inlinePrompts") or [])
            if isinstance(p, str) and p.strip()
        ],
    )


def article_from_raw_output(raw: str) -> tuple[str, str]:
    """把模型原始输出规整成 (标题, 正文)。

    文章类任务按 JSON 契约输出，直接当正文落库会把 title/summary 等字段一起写进正文；
    能解析出 markdown 时只取正文和标题，解析失败（未按契约输出）时按原文保留。
    """
    draft = parse_llm_output(raw)
    if draft.ok:
        return draft.title, draft.markdown
    return "", raw


def _resolve_theme(theme_id: str) -> dict:
    """解析主题 tokens；预设主题直接取，不查库。"""
    preset = wechat_layout.theme_by_id(theme_id)
    return dict(preset["tokens"])


def article_to_variant_body(draft: ArticleDraft) -> str:
    """把草稿转成平台版本的 body（Markdown，推送时再渲染 HTML）。"""
    return draft.markdown
