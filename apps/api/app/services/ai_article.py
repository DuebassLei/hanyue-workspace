"""AI 成文：生成公众号文章 + 配图提示词。

写作风格由 Skill 的 manifest 驱动（promptTemplate / tone / styleRules），
用户可创建自定义 skill 覆盖语气与规则，不再硬编码。

输出契约（与所有模型路径共享，避免格式漂移）：
- Markdown 正文：公众号排版语法（# 标题、## 小标题、> 引用、- 列表、--- 分割线、
  :::card 强调卡、:::follow 关注引导、:::sign 署名、可选 :::lead / :::stat /
  :::steps / :::compare / :::layers / :::checklist / :::quote-card、{{image1}} 图片占位）
- 公众号排版 HTML：调用 wechat_layout.render_document 生成
- 封面图提示词：1 条（英文，适合生图模型）
- 正文插图提示词：若干条

不伪造：LLM 未配置或生成失败时返回空结果 + errorCode，调用方据此标失败。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from sqlalchemy import select

from .. import models
from . import ai_gateway, prompt_rules, skill_registry, wechat_layout, writing_rules

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
    "下面这些区块按内容选用，不要每篇都堆齐："
    ":::lead 导语；:::stat 每行「数字 | 说明」；:::steps 每行「名称 | 说明」，不要自己写「1.」；"
    ":::compare 左 / 右 再写「左 | 右」；:::layers 每行「标签 | 正文」；"
    ":::checklist 每行「done、todo 或 warn | 事项」；"
    ":::quote-card 出处 用来放金句，不要写成 > 引用。"
    "封面图提示词用英文，描述画面主体、风格、色调。"
    "正文中的 {{imageN}} 数量必须与 inlinePrompts 数量保持一致。"
)

DEFAULT_THEME_ID = wechat_layout.DEFAULT_THEME_ID

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
    """读取当前账号可见且可信的 Skill，个人定制副本优先。

    可见性/信任判定统一由 skill_registry 负责，避免写作路径绕过启停与信任策略。
    """
    return skill_registry.resolve_visible(db, user_id, skill_key)


def resolve_skill_style(skill: models.SkillRegistryEntry | None) -> tuple[str, str, list[str], str]:
    """从 skill.manifest 解析写作风格配置，返回 (prompt_template, tone, style_rules, theme_id)。

    skill 为 None 或 manifest 缺字段时，回退到内置默认风格。
    运行期请改用 build_system_prompt(..., db=...)：它会先经规则表合成 manifest 再解析。
    """
    manifest = (skill.manifest or {}) if skill else {}
    if not isinstance(manifest, dict):
        manifest = {}
    return _style_from_manifest(manifest, skill)


def _style_from_manifest(
    manifest: dict, skill: models.SkillRegistryEntry | None = None
) -> tuple[str, str, list[str], str]:
    """manifest（已合成）→ 风格四元组；缺字段回退到代码兜底常量（R5）。"""
    prompt_template = str(manifest.get("promptTemplate") or skill_registry.DEFAULT_ARTICLE_PROMPT)
    tone = str(manifest.get("tone") or skill_registry.DEFAULT_TONE)
    rules = manifest.get("styleRules")
    if not isinstance(rules, list):
        rules = skill_registry.DEFAULT_STYLE_RULES
    style_rules = [str(r) for r in rules if str(r).strip()]
    theme_id = str(manifest.get("defaultTheme") or DEFAULT_THEME_ID)
    return prompt_template, tone, style_rules, theme_id


def build_system_prompt(
    skill: models.SkillRegistryEntry | None,
    *,
    db=None,
    owner_user_id: str | None = None,
) -> str:
    """构建受保护的文章输出契约，并附加 Skill 写作风格与流程约束。

    db 传入时（生产路径）：manifest 经规则表合成（个人副本按类型覆盖全局），
    输出契约读 prompt_rules 的 `article.output_contract`（缺失回退代码常量）；
    db 为 None 时（纯单测）：沿用条目 manifest 与常量，行为与旧版完全一致。

    factPolicy / imagePolicy 只在这份 manifest 自己写了时才追加。
    没有 workflow 的文风 Skill 不注入这两段。
    """
    manifest = writing_rules.effective_manifest(db, skill, owner_user_id)
    prompt_template, tone, style_rules, _ = _style_from_manifest(manifest, skill)
    contract = (
        prompt_rules.get_prompt_rule(db, "article.output_contract", default=ARTICLE_OUTPUT_CONTRACT)
        if db is not None
        else ARTICLE_OUTPUT_CONTRACT
    )
    style_prompt = prompt_template.replace("{tone}", tone).strip()
    parts = [contract]
    style_requirements = f"语气：{tone}"
    if style_prompt:
        style_requirements += f"\n{style_prompt}"
    if style_requirements:
        parts.append(f"写作风格要求（不得改变以上输出契约）：\n{style_requirements}")
    if style_rules:
        rules_text = "\n".join(f"- {r}" for r in style_rules)
        parts.append(f"必须遵守以下风格规则（不得改变以上输出契约）：\n{rules_text}")
    fact_text = _fact_policy_text(manifest.get("factPolicy"))
    if fact_text:
        parts.append(fact_text)
    image_text = _image_policy_text(manifest.get("imagePolicy"))
    if image_text:
        parts.append(image_text)
    return "\n\n".join(parts)


def _fact_policy_text(policy) -> str:
    """把 factPolicy 收成一段提示词。缺字段或类型不对则不注入。"""
    if not isinstance(policy, dict):
        return ""
    lines: list[str] = []
    if policy.get("forbidUnverifiedQuotes") is True:
        lines.append("不要编造引语；没有出处的原话不要写成引语。")
    if policy.get("requireSources") is True:
        lines.append("每个关键事实都要标明可核对的来源。")
    placeholder = policy.get("placeholder")
    if isinstance(placeholder, str) and placeholder.strip():
        lines.append(f"还不能确认的事实，用「{placeholder.strip()}」标出，不要写成已核实。")
    extras = policy.get("extraInstructions")
    if isinstance(extras, list):
        for item in extras:
            if isinstance(item, str) and item.strip():
                lines.append(item.strip())
    if not lines:
        return ""
    body = "\n".join(f"- {line}" for line in lines)
    return f"事实约束（不得改变以上输出契约）：\n{body}"


def _image_policy_text(policy) -> str:
    """把 imagePolicy 收成一段提示词。缺字段则不注入；不在这里拦截生图工具。"""
    if not isinstance(policy, dict):
        return ""
    lines: list[str] = []
    max_inline = policy.get("maxInlineImages")
    if isinstance(max_inline, int) and not isinstance(max_inline, bool) and max_inline >= 0:
        lines.append(
            f"正文插图最多 {max_inline} 张：{{{{image1}}}} 到 {{{{image{max_inline}}}}} 连续编号，"
            "条数必须与 inlinePrompts 一致。"
        )
    cover_ratio = policy.get("coverAspectRatio")
    if isinstance(cover_ratio, str) and cover_ratio.strip():
        lines.append(f"封面图比例 {cover_ratio.strip()}。")
    inline_ratio = policy.get("inlineAspectRatio")
    if isinstance(inline_ratio, str) and inline_ratio.strip():
        lines.append(f"正文插图比例 {inline_ratio.strip()}。")
    if policy.get("allowAiImages") is False:
        lines.append("不要为封面或插图编写 AI 生图提示词；coverPrompt 留空，inlinePrompts 为空数组。")
    suffix = policy.get("promptStyleSuffix")
    if isinstance(suffix, str) and suffix.strip():
        lines.append(f"生图提示词末尾附加：{suffix.strip()}")
    if not lines:
        return ""
    body = "\n".join(f"- {line}" for line in lines)
    return f"配图约束（不得改变以上输出契约）：\n{body}"


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


_IMAGE_PLACEHOLDER_RE = re.compile(r"\{\{\s*image\s*(\d+)\s*(?:\|[^{}]*)?\}\}", re.IGNORECASE)


def review_contract(draft: ArticleDraft) -> dict:
    """纯函数契约检查：正文图片占位编号必须 1..n 连续，且与 inlinePrompts 数量一致。"""
    numbers = [int(n) for n in _IMAGE_PLACEHOLDER_RE.findall(draft.markdown or "")]
    inline_count = len(draft.inline_prompts or [])
    if not numbers:
        return {"ok": True, "issues": []} if inline_count == 0 else {
            "ok": False,
            "issues": ["image_count_mismatch"],
        }
    unique = sorted(set(numbers))
    contiguous = unique == list(range(1, len(numbers) + 1))
    if contiguous and len(numbers) == inline_count:
        return {"ok": True, "issues": []}
    return {"ok": False, "issues": ["image_count_mismatch"]}


def _resolve_theme(theme_id: str) -> dict:
    """解析主题 tokens；预设主题直接取，不查库。"""
    preset = wechat_layout.theme_by_id(theme_id)
    return dict(preset["tokens"])


def article_to_variant_body(draft: ArticleDraft) -> str:
    """把草稿转成平台版本的 body（Markdown，推送时再渲染 HTML）。"""
    return draft.markdown


def deposit_article_prompts(
    db,
    *,
    cover_prompt: str = "",
    inline_prompts: list[str] | None = None,
    title_prefix: str = "",
) -> list[models.PromptTemplate]:
    """手动把 AI 成文的封面/插图提示词沉淀进统一提示词库（source=ai）。

    默认不自动调用；由 StudioView 的「存入提示词库」按钮经接口显式触发，避免污染库。
    幂等：同一标题的 AI 提示词已存在时跳过，重复点击不会产生副本。
    """
    prefix = (title_prefix or "").strip()[:40] or "AI 成文"
    specs: list[tuple[str, str, str, str]] = []
    cover = (cover_prompt or "").strip()
    if cover:
        specs.append((f"{prefix}·封面", cover, "cover", "封面图"))
    for index, raw in enumerate(inline_prompts or []):
        text = (raw or "").strip() if isinstance(raw, str) else ""
        if text:
            specs.append((f"{prefix}·配图{index + 1}", text, "illustration", "文章配图"))
    if not specs:
        return []

    existing = set(
        db.scalars(
            select(models.PromptTemplate.title).where(models.PromptTemplate.source == "ai")
        )
    )
    created: list[models.PromptTemplate] = []
    for title, content, prompt_type, label in specs:
        if title in existing:
            continue
        row = models.PromptTemplate(
            title=title[:120],
            category="AI 成文",
            content=content,
            tags=["ai", prompt_type],
            prompt_type=prompt_type,
            source="ai",
            summary=f"AI 成文生成的{label}提示词",
        )
        db.add(row)
        existing.add(title)
        created.append(row)
    if created:
        db.commit()
    return created


def deposit_draft_prompts(
    db, draft: ArticleDraft, title_prefix: str = ""
) -> list[models.PromptTemplate]:
    """便捷重载：直接接收 ArticleDraft（供服务内部/未来自动沉淀开关使用）。"""
    return deposit_article_prompts(
        db,
        cover_prompt=draft.cover_prompt,
        inline_prompts=draft.inline_prompts,
        title_prefix=title_prefix or draft.title,
    )
