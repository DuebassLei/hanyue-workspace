"""微信公众号排版：主题 tokens、Markdown 超集解析、内联样式渲染、兼容性检查。

为什么不用通用 Markdown 渲染器：
- 微信正文只保留**内联样式**，`<style>`/class 会被剥离，必须逐元素写 style；
- 微信会重置 `h1~h6`、剥离 `div`，所以标题用 `section + span/strong` 模拟；
- 正文图片只能是微信域名，链接指向外部域会被降级；
- 深色模式下微信会反色，纯白/纯黑背景会出现色块。

边界：预览与推送**共用同一个渲染函数**（`render_document`），保证所见即所得。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------- 主题 ----------

#: 字号/行高/字距等数值型 token 的允许范围，越界直接拒绝（防注入与离谱值）。
NUMERIC_RANGES: dict[str, tuple[float, float]] = {
    "bodySize": (13, 18),
    "bodyLineHeight": (1.4, 2.2),
    "letterSpacing": (0, 2),
    "paragraphGap": (0.8, 3),
    "indent": (0, 2),
    "imageRadius": (0, 16),
}

ENUMS: dict[str, set[str]] = {
    "align": {"left", "justify", "center"},
    "h1Style": {"bar", "block", "number", "center", "underline", "number-divider"},
    "h2Style": {"tag", "underline", "plain", "bar", "strong"},
    "listMarker": {"dot", "square", "number"},
}

_COLOR_KEYS = (
    "textColor",
    "mutedColor",
    "primaryColor",
    "primarySoft",
    "quoteBg",
    "dividerColor",
    "strongColor",
)
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGBA = re.compile(r"^rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(?:,\s*(?:0|1|0?\.\d+)\s*)?\)$")

FONT_STACK = (
    "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Hiragino Sans GB', "
    "'Microsoft YaHei', sans-serif"
)


def _base_tokens(**overrides) -> dict:
    tokens = {
        "fontFamily": FONT_STACK,
        "bodySize": 16,
        "bodyLineHeight": 1.8,
        "letterSpacing": 0.5,
        "paragraphGap": 1.5,
        "textColor": "#333333",
        "mutedColor": "#888888",
        "primaryColor": "#0F6E56",
        "primarySoft": "#E1F5EE",
        "quoteBg": "#F7F7F7",
        "dividerColor": "#E5E5E5",
        "strongColor": "#333333",
        "align": "justify",
        "indent": 0,
        "imageRadius": 6,
        "h1Style": "bar",
        "h2Style": "tag",
        "listMarker": "dot",
    }
    tokens.update(overrides)
    return tokens


#: 内置预设。id 稳定，历史任务的 layout_snapshot 里记录的是 tokens 快照而非 id。
PRESET_THEMES: dict[str, dict] = {
    "ink-green": {
        "id": "ink-green",
        "name": "墨绿简约",
        "description": "左侧竖线标题，适合知识、职场、干货",
        "tokens": _base_tokens(),
    },
    "deep-blue": {
        "id": "deep-blue",
        "name": "深海蓝",
        "description": "色块标签标题，适合科技、财经、分析",
        "tokens": _base_tokens(
            primaryColor="#185FA5",
            primarySoft="#E6F1FB",
            textColor="#2C2C2A",
            quoteBg="#F4F7FA",
        ),
    },
    "warm-orange": {
        "id": "warm-orange",
        "name": "暖橙生活",
        "description": "居中大字标题，适合生活、情感、美食",
        "tokens": _base_tokens(
            primaryColor="#BA7517",
            primarySoft="#FAEEDA",
            textColor="#3A2E22",
            quoteBg="#FCF7EF",
            h1Style="center",
            h2Style="underline",
            align="left",
            bodyLineHeight=1.9,
        ),
    },
    "mono": {
        "id": "mono",
        "name": "极简黑白",
        "description": "序号标题 + 细线，适合深度长文与访谈",
        "tokens": _base_tokens(
            primaryColor="#2C2C2A",
            primarySoft="#F1EFE8",
            textColor="#2C2C2A",
            quoteBg="#F7F7F7",
            h1Style="number",
            h2Style="plain",
            letterSpacing=0.3,
        ),
    },
    "terracotta-warm": {
        "id": "terracotta-warm",
        "name": "暖陶土",
        "description": "陶土主色 + 下划线主标题 + 竖线副标题，适合工具推荐、产品测评",
        "tokens": _base_tokens(
            bodySize=15,
            bodyLineHeight=2.0,
            letterSpacing=0,
            paragraphGap=1.3,
            textColor="#3F3F3F",
            mutedColor="#999999",
            primaryColor="#D97757",
            primarySoft="#F7ECE5",
            quoteBg="#FAF9F5",
            dividerColor="#E4B1A0",
            strongColor="#D97757",
            align="left",
            indent=0,
            imageRadius=8,
            h1Style="underline",
            h2Style="bar",
            listMarker="dot",
        ),
    },
    "blue-business": {
        "id": "blue-business",
        "name": "商务蓝",
        "description": "深蓝编号标题 + 深海军蓝正文强调，适合职场干货、产品教程、商业分析",
        "tokens": _base_tokens(
            bodySize=15,
            bodyLineHeight=1.9,
            letterSpacing=0.3,
            paragraphGap=1.4,
            textColor="#4B5563",
            mutedColor="#758095",
            primaryColor="#003BB3",
            primarySoft="#F6F8FB",
            quoteBg="#F6F8FB",
            dividerColor="#DCE3EE",
            strongColor="#263344",
            align="justify",
            indent=0,
            imageRadius=0,
            h1Style="number-divider",
            h2Style="strong",
            listMarker="dot",
        ),
    },
}

DEFAULT_THEME_ID = "ink-green"


class ThemeError(ValueError):
    """主题 tokens 不合法。"""


def _check_color(key: str, value: str) -> str:
    raw = (value or "").strip()
    if not (_HEX.match(raw) or _RGBA.match(raw)):
        raise ThemeError(f"{key} 必须是 #rrggbb 或 rgba() 颜色，收到：{value!r}")
    lowered = raw.lower()
    if "#ffffff" in lowered or "#fff" == lowered or "#000" == lowered or "#000000" in lowered:
        # 不是硬错误：给出可用的同时提示深色模式风险（由 lint 呈现）
        pass
    return raw


def known_token_keys() -> set[str]:
    return {"fontFamily", *NUMERIC_RANGES, *ENUMS, *_COLOR_KEYS}


def normalize_tokens(raw: dict | None) -> dict:
    """校验并归一化主题 tokens；未知键报错（不能静默丢弃，否则用户以为生效了）。"""
    tokens = _base_tokens()
    if not raw:
        return tokens
    unknown = [key for key in raw if key not in known_token_keys()]
    if unknown:
        raise ThemeError(
            f"不认识的排版参数：{'、'.join(sorted(unknown))}；"
            f"可用参数：{'、'.join(sorted(known_token_keys()))}"
        )
    for key, value in raw.items():
        if key == "fontFamily":
            # 只允许系统字体栈，禁止外链字体（微信也不支持 web font）
            if not isinstance(value, str) or "url(" in value.lower() or "@" in value:
                raise ThemeError("fontFamily 只允许系统字体栈，不能引用外部字体")
            tokens[key] = value
            continue
        if key in NUMERIC_RANGES:
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ThemeError(f"{key} 必须是数字") from exc
            low, high = NUMERIC_RANGES[key]
            if not (low <= number <= high):
                raise ThemeError(f"{key} 必须在 {low}~{high} 之间，收到 {number}")
            tokens[key] = number
            continue
        if key in ENUMS:
            if value not in ENUMS[key]:
                raise ThemeError(f"{key} 只能是 {'/'.join(sorted(ENUMS[key]))}")
            tokens[key] = value
            continue
        if key in _COLOR_KEYS:
            tokens[key] = _check_color(key, str(value))
            continue
    # 未显式指定加粗色时，回退到正文色，保持现有主题的视觉不变
    if not raw or "strongColor" not in raw:
        tokens["strongColor"] = tokens["textColor"]
    return tokens


def theme_by_id(theme_id: str | None) -> dict:
    """返回预设主题；未知 id 回退默认预设（调用方负责告知用户）。"""
    return PRESET_THEMES.get(theme_id or DEFAULT_THEME_ID) or PRESET_THEMES[DEFAULT_THEME_ID]


# ---------- 行内标记 ----------

_INLINE_RULES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\*\*(.+?)\*\*"), "strong"),
    (re.compile(r"`([^`]+)`"), "code"),
    (re.compile(r"==(.+?)=="), "mark"),
    (re.compile(r"~~(.+?)~~"), "del"),
    (re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)"), "em"),
)
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_IMAGE_PLACEHOLDER = re.compile(r"\{\{\s*image\s*(\d+)\s*(?:\|\s*([^}]*?)\s*)?\}\}", re.IGNORECASE)


def escape_text(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


#: 数值型 token 写进 CSS 时必须补 px 的属性（否则 `font-size:16` 是无效声明）
_LENGTH_KEYS = {
    "font-size",
    "letter-spacing",
    "border-radius",
    "width",
    "height",
    "margin-right",
    "padding-top",
    "padding-bottom",
    "text-indent",
}


def _style(pairs: dict) -> str:
    items: list[str] = []
    for key, value in pairs.items():
        if value is None or value == "":
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool) and key in _LENGTH_KEYS:
            value = f"{value}px"
        items.append(f"{key}:{value}")
    return ";".join(items)


def render_inline(text: str, tokens: dict, warnings: list[dict], *, where: str = "") -> str:
    """行内标记 → 内联样式 HTML。所有文本先转义，标记替换只作用于纯文本片段。"""
    escaped = escape_text(text)
    # 程序化标签占位，避免后续规则误伤已生成的标签
    slots: list[str] = []

    def stash(html: str) -> str:
        slots.append(html)
        return f"\x00{len(slots) - 1}\x00"

    size = tokens["bodySize"]
    color = tokens["textColor"]
    primary = tokens["primaryColor"]
    strong_color = tokens.get("strongColor", color)

    for pattern, kind in _INLINE_RULES:
        def repl(match: re.Match, kind=kind) -> str:
            content = match.group(1)
            if kind == "strong":
                return stash(f'<strong style="{_style({"font-weight": 600, "color": strong_color})}">{content}</strong>')
            if kind == "em":
                return stash(f'<em style="{_style({"font-style": "italic"})}">{content}</em>')
            if kind == "mark":
                return stash(
                    f'<span style="{_style({"background": tokens["primarySoft"], "padding": "0 3px", "border-radius": 3})}">{content}</span>'
                )
            if kind == "del":
                return stash(f'<span style="{_style({"text-decoration": "line-through", "color": tokens["mutedColor"]})}">{content}</span>')
            return stash(
                f'<span style="{_style({"font-family": "Menlo, Consolas, monospace", "font-size": size - 1, "background": tokens["quoteBg"], "padding": "1px 4px", "border-radius": 3, "color": primary})}">{content}</span>'
            )

        escaped = pattern.sub(repl, escaped)

    # 外链在公众号正文里不可点击，降级为「文本（链接）」并给出告警
    def link_repl(match: re.Match) -> str:
        label, url = match.group(1), match.group(2)
        warnings.append(
            {
                "field": where or "body",
                "level": "warning",
                "message": f"外部链接在公众号正文中不可点击，已降级为文本：{url}",
            }
        )
        return stash(f'{label}<span style="{_style({"color": tokens["mutedColor"], "font-size": size - 2})}">（{url}）</span>')

    escaped = _LINK.sub(link_repl, escaped)

    restored = escaped
    for index, html in enumerate(slots):
        restored = restored.replace(f"\x00{index}\x00", html)
    return restored


# ---------- 区块解析 ----------

@dataclass
class Block:
    kind: str
    text: str = ""
    level: int = 0
    items: list[str] = field(default_factory=list)
    image_index: int = 0
    caption: str = ""
    meta: str = ""


def parse_blocks(body: str, warnings: list[dict]) -> list[Block]:
    """把正文解析成区块。规则保守：不认识的结构保留为段落，不静默丢内容。"""
    text = (body or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    lines = text.split("\n")
    blocks: list[Block] = []
    buffer: list[str] = []
    index = 0

    def flush() -> None:
        if buffer:
            blocks.append(Block(kind="paragraph", text="\n".join(buffer).strip()))
            buffer.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped.startswith("```"):
            flush()
            fence: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                fence.append(lines[index])
                index += 1
            if index >= len(lines):
                warnings.append(
                    {"field": "body", "level": "warning", "message": "代码块未闭合，已按原样输出"}
                )
            blocks.append(Block(kind="code", text="\n".join(fence)))
            index += 1
            continue

        if stripped.startswith(":::"):
            flush()
            header = stripped[3:].strip()
            name, _, meta = header.partition(" ")
            if name not in {"card", "follow", "sign"}:
                warnings.append(
                    {
                        "field": "body",
                        "level": "warning",
                        "message": f"未知区块 :::{name}，已按普通段落输出",
                    }
                )
                buffer.append(line)
                index += 1
                continue
            collected: list[str] = []
            index += 1
            closed = False
            while index < len(lines):
                if lines[index].strip() == ":::":
                    closed = True
                    index += 1
                    break
                collected.append(lines[index])
                index += 1
            if not closed:
                warnings.append(
                    {
                        "field": "body",
                        "level": "warning",
                        "message": f"区块 :::{name} 未闭合，内容仍会输出但样式按普通区块处理",
                    }
                )
            blocks.append(Block(kind=name, text="\n".join(collected).strip(), meta=meta.strip()))
            continue

        if stripped in {"---", "***", "___"}:
            flush()
            blocks.append(Block(kind="divider"))
            index += 1
            continue

        match = _IMAGE_PLACEHOLDER.fullmatch(stripped)
        if match:
            flush()
            blocks.append(
                Block(
                    kind="image",
                    image_index=int(match.group(1)),
                    caption=(match.group(2) or "").strip(),
                )
            )
            index += 1
            continue

        for marker, level in (("### ", 3), ("## ", 2), ("# ", 1)):
            if stripped.startswith(marker):
                flush()
                blocks.append(Block(kind="heading", text=stripped[len(marker):].strip(), level=level))
                index += 1
                break
        else:
            if stripped.startswith("> "):
                flush()
                quote: list[str] = []
                while index < len(lines) and lines[index].strip().startswith("> "):
                    quote.append(lines[index].strip()[2:])
                    index += 1
                blocks.append(Block(kind="quote", text="\n".join(quote)))
                continue

            if re.match(r"^([-*]|\d+\.)\s+", stripped):
                flush()
                items: list[str] = []
                ordered = bool(re.match(r"^\d+\.\s+", stripped))
                while index < len(lines):
                    candidate = lines[index].strip()
                    item = re.match(r"^([-*]|\d+\.)\s+(.*)$", candidate)
                    if not item:
                        break
                    items.append(item.group(2))
                    index += 1
                blocks.append(Block(kind="ordered" if ordered else "list", items=items))
                continue

            if not stripped:
                flush()
                index += 1
                continue

            buffer.append(line)
            index += 1

    flush()
    return blocks


# ---------- 区块渲染 ----------


def _section(pairs: dict, inner: str) -> str:
    return f'<section style="{_style(pairs)}">{inner}</section>'


def _render_heading(block: Block, tokens: dict, number: int) -> str:
    size = tokens["bodySize"] + (3 if block.level == 1 else 1)
    color = tokens["textColor"]
    primary = tokens["primaryColor"]
    strong_color = tokens.get("strongColor", color)
    base = {"margin": "2em 0 0.9em", "line-height": 1.5}

    if block.level == 1:
        style = tokens["h1Style"]
        if style == "block":
            return _section(
                {**base},
                f'<span style="{_style({"display": "inline-block", "padding": "4px 12px", "background": primary, "color": "#FFFFFF", "font-size": size, "font-weight": 600, "border-radius": 4})}">{escape_text(block.text)}</span>',
            )
        if style == "number":
            return _section(
                {**base},
                f'<span style="{_style({"color": primary, "font-size": size, "font-weight": 600, "margin-right": 8})}">{number:02d}</span>'
                f'<strong style="{_style({"color": color, "font-size": size, "font-weight": 600})}">{escape_text(block.text)}</strong>',
            )
        if style == "center":
            return _section(
                {**base, "text-align": "center"},
                f'<strong style="{_style({"color": color, "font-size": size, "font-weight": 600, "letter-spacing": 1})}">{escape_text(block.text)}</strong>'
                f'<span style="{_style({"display": "block", "width": 32, "height": 2, "background": primary, "margin": "10px auto 0"})}"></span>',
            )
        if style == "underline":
            return _section(
                {**base, "border-bottom": f"2px solid {tokens['dividerColor']}", "padding-bottom": 6},
                f'<strong style="{_style({"color": primary, "font-size": size, "font-weight": 600, "letter-spacing": 0.5})}">{escape_text(block.text)}</strong>',
            )
        if style == "number-divider":
            # 大号主色编号独占一行，下方紧跟深标题文字，再用分割线收束
            return _section(
                {**base, "border-bottom": f"1px solid {tokens['dividerColor']}", "padding-bottom": 14},
                f'<span style="{_style({"display": "block", "color": primary, "font-size": size + 6, "font-weight": 700, "margin-bottom": 4})}">{number:02d}</span>'
                f'<strong style="{_style({"color": strong_color, "font-size": size, "font-weight": 600, "line-height": 1.6})}">{escape_text(block.text)}</strong>',
            )
        return _section(
            {**base},
            f'<span style="{_style({"display": "inline-block", "width": 4, "height": "1em", "background": primary, "vertical-align": "-1px", "margin-right": 8})}"></span>'
            f'<strong style="{_style({"color": color, "font-size": size, "font-weight": 600})}">{escape_text(block.text)}</strong>',
        )

    style = tokens["h2Style"]
    if style == "tag":
        return _section(
            {**base, "margin": "1.6em 0 0.7em"},
            f'<span style="{_style({"display": "inline-block", "padding": "3px 10px", "background": tokens["primarySoft"], "color": primary, "font-size": size, "font-weight": 600, "border-radius": 4})}">{escape_text(block.text)}</span>',
        )
    if style == "underline":
        return _section(
            {**base, "margin": "1.6em 0 0.7em", "border-bottom": f"1px solid {tokens['dividerColor']}", "padding-bottom": 6},
            f'<strong style="{_style({"color": color, "font-size": size, "font-weight": 600})}">{escape_text(block.text)}</strong>',
        )
    if style == "bar":
        return _section(
            {**base, "margin": "1.6em 0 0.7em"},
            f'<span style="{_style({"display": "inline-block", "width": 4, "height": "1em", "background": primary, "vertical-align": "-1px", "margin-right": 8, "border-radius": 2})}"></span>'
            f'<strong style="{_style({"color": color, "font-size": size, "font-weight": 600})}">{escape_text(block.text)}</strong>',
        )
    if style == "strong":
        return _section(
            {**base, "margin": "1.6em 0 0.7em"},
            f'<strong style="{_style({"color": strong_color, "font-size": size, "font-weight": 600, "line-height": 1.6})}">{escape_text(block.text)}</strong>',
        )
    return _section(
        {**base, "margin": "1.6em 0 0.7em"},
        f'<strong style="{_style({"color": primary, "font-size": size, "font-weight": 600})}">{escape_text(block.text)}</strong>',
    )


def _render_paragraph(block: Block, tokens: dict, warnings: list[dict]) -> str:
    inner = render_inline(block.text, tokens, warnings).replace("\n", "<br/>")
    return (
        f'<p style="{_style({"margin": f"0 0 {tokens["paragraphGap"]}em", "font-size": tokens["bodySize"], "line-height": tokens["bodyLineHeight"], "letter-spacing": tokens["letterSpacing"], "color": tokens["textColor"], "text-align": tokens["align"], "text-indent": f"{tokens["indent"]}em" if tokens["indent"] else 0})}">'
        f"{inner}</p>"
    )


def _render_list(block: Block, tokens: dict, warnings: list[dict], ordered: bool) -> str:
    marker = tokens["listMarker"]
    if ordered or marker == "number":
        tag = "ol"
        list_style = "decimal"
    else:
        tag = "ul"
        list_style = "square" if marker == "square" else "disc"
    items = "".join(
        f'<li style="{_style({"margin": "0 0 0.35em", "line-height": tokens["bodyLineHeight"]})}">{render_inline(item, tokens, warnings)}</li>'
        for item in block.items
    )
    return (
        f'<{tag} style="{_style({"margin": f"0 0 {tokens["paragraphGap"]}em", "padding-left": "1.4em", "list-style-type": list_style, "font-size": tokens["bodySize"], "letter-spacing": tokens["letterSpacing"], "color": tokens["textColor"]})}">'
        f"{items}</{tag}>"
    )


def _render_quote(block: Block, tokens: dict, warnings: list[dict]) -> str:
    inner = render_inline(block.text, tokens, warnings).replace("\n", "<br/>")
    return (
        f'<blockquote style="{_style({"margin": f"0 0 {tokens["paragraphGap"]}em", "padding": "10px 14px", "border-left": f"3px solid {tokens["primaryColor"]}", "background": tokens["quoteBg"], "color": tokens["mutedColor"], "font-size": tokens["bodySize"] - 1, "line-height": tokens["bodyLineHeight"], "letter-spacing": tokens["letterSpacing"]})}">'
        f"{inner}</blockquote>"
    )


def _render_image(block: Block, tokens: dict, image_urls: list[str], used: set[int]) -> str:
    if block.image_index < 1 or block.image_index > len(image_urls):
        return ""
    used.add(block.image_index)
    url = escape_text(image_urls[block.image_index - 1])
    caption = (
        f'<span style="{_style({"display": "block", "margin": "6px 0 0", "font-size": 12, "color": tokens["mutedColor"], "text-align": "center"})}">{escape_text(block.caption)}</span>'
        if block.caption
        else ""
    )
    return (
        f'<section style="{_style({"margin": f"0 0 {tokens["paragraphGap"]}em", "text-align": "center"})}">'
        f'<img src="{url}" style="{_style({"max-width": "100%", "display": "block", "margin": "0 auto", "border-radius": tokens["imageRadius"]})}"/>'
        f"{caption}</section>"
    )


def _render_blockquote_like(kind: str, block: Block, tokens: dict, warnings: list[dict]) -> str:
    inner = render_inline(block.text, tokens, warnings).replace("\n", "<br/>")
    if kind == "card":
        head = (
            f'<strong style="{_style({"display": "block", "margin": "0 0 6px", "color": tokens["primaryColor"], "font-size": tokens["bodySize"] - 1, "font-weight": 600})}">{escape_text(block.meta)}</strong>'
            if block.meta
            else ""
        )
        return _section(
            {
                "margin": f"0 0 {tokens['paragraphGap']}em",
                "padding": "14px 16px",
                "background": tokens["primarySoft"],
                "border-radius": tokens["imageRadius"],
                "font-size": tokens["bodySize"] - 1,
                "line-height": tokens["bodyLineHeight"],
                "color": tokens["textColor"],
            },
            head + inner,
        )
    if kind == "follow":
        return _section(
            {
                "margin": "2em 0 1em",
                "padding": "16px",
                "background": tokens["quoteBg"],
                "border-radius": tokens["imageRadius"],
                "text-align": "center",
                "font-size": tokens["bodySize"] - 1,
                "line-height": tokens["bodyLineHeight"],
                "color": tokens["mutedColor"],
            },
            inner,
        )
    # sign：文末署名，上面一条细线
    return _section(
        {
            "margin": "2em 0 0",
            "padding-top": 12,
            "border-top": f"1px solid {tokens['dividerColor']}",
            "text-align": "center",
            "font-size": 12,
            "line-height": 1.8,
            "color": tokens["mutedColor"],
        },
        inner,
    )


def _render_code(block: Block, tokens: dict) -> str:
    code = escape_text(block.text)
    return _section(
        {
            "margin": f"0 0 {tokens['paragraphGap']}em",
            "padding": "12px 14px",
            "background": tokens["quoteBg"],
            "border-radius": tokens["imageRadius"],
            "font-family": "Menlo, Consolas, monospace",
            "font-size": 13,
            "line-height": 1.6,
            "color": tokens["textColor"],
            "white-space": "pre-wrap",
            "word-break": "break-all",
        },
        f"<span>{code}</span>",
    )


def _render_divider(tokens: dict) -> str:
    return (
        f'<section style="{_style({"margin": "2em 0", "text-align": "center", "color": tokens["dividerColor"], "font-size": 12, "letter-spacing": 4})}">'
        "— — —</section>"
    )


# ---------- 对外入口 ----------


def render_document(
    body: str,
    tokens: dict | None = None,
    image_urls: list[str] | None = None,
) -> dict:
    """渲染正文：返回 html / warnings / stats。预览与推送必须都调用这里。"""
    theme = normalize_tokens(tokens)
    urls = [str(url) for url in (image_urls or [])]
    warnings: list[dict] = []
    blocks = parse_blocks(body, warnings)
    used: set[int] = set()
    heading_no = 0
    parts: list[str] = []

    for block in blocks:
        if block.kind == "heading":
            if block.level == 1:
                heading_no += 1
            parts.append(_render_heading(block, theme, heading_no))
        elif block.kind == "paragraph":
            parts.append(_render_paragraph(block, theme, warnings))
        elif block.kind in {"list", "ordered"}:
            parts.append(_render_list(block, theme, warnings, block.kind == "ordered"))
        elif block.kind == "quote":
            parts.append(_render_quote(block, theme, warnings))
        elif block.kind == "image":
            rendered = _render_image(block, theme, urls, used)
            if not rendered:
                warnings.append(
                    {
                        "field": "body",
                        "level": "warning",
                        "message": f"图片占位 image{block.image_index} 超出已选正文图数量，已忽略",
                    }
                )
            parts.append(rendered)
        elif block.kind in {"card", "follow", "sign"}:
            parts.append(_render_blockquote_like(block.kind, block, theme, warnings))
        elif block.kind == "code":
            parts.append(_render_code(block, theme))
        elif block.kind == "divider":
            parts.append(_render_divider(theme))

    # 没有用占位符的图片按顺序追加到文末，避免静默丢图
    for index in range(1, len(urls) + 1):
        if index not in used:
            parts.append(_render_image(Block(kind="image", image_index=index), theme, urls, used))
    html = "".join(part for part in parts if part)

    plain = re.sub(r"<[^>]+>", "", html)
    chars = len(plain.strip())
    return {
        "html": html,
        "warnings": warnings,
        "stats": {
            "sourceChars": len((body or "").strip()),
            "renderedChars": chars,
            "htmlChars": len(html),
            "images": len(urls),
            "readingMinutes": max(1, round(chars / 400)) if chars else 0,
        },
    }


def lint_html(html: str, tokens: dict | None = None) -> list[dict]:
    """渲染后兼容性检查：非白名单标签/属性一旦出现说明渲染器有 bug，必须报出来。"""
    theme = normalize_tokens(tokens)
    issues: list[dict] = []
    for tag in ("<style", "<script", "<div", "<h1", "<h2", "<h3", "<iframe", "<table"):
        if tag in html:
            issues.append(
                {
                    "field": "body",
                    "level": "error",
                    "message": f"渲染结果包含公众号不支持的标签 {tag}>，请反馈此问题",
                }
            )
    if "class=" in html or " id=" in html:
        issues.append(
            {"field": "body", "level": "error", "message": "渲染结果包含 class/id，微信会剥离，请反馈此问题"}
        )
    if "background-image" in html:
        issues.append(
            {"field": "body", "level": "warning", "message": "公众号不渲染 background-image，已改用纯色底"}
        )
    for key in ("textColor", "quoteBg"):
        value = str(theme[key]).lower()
        if value in {"#fff", "#ffffff"}:
            issues.append(
                {
                    "field": "theme",
                    "level": "warning",
                    "message": f"主题 {key} 接近纯白，公众号深色模式下可能显示异常",
                }
            )
    return issues
