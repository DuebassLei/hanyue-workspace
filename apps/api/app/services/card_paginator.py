"""Markdown → 固定画幅卡片的分页器。

逻辑逐条对齐前端 `src/features/cards/paginator.js`（同一套估算公式与分页规则），
保证「纯前端模式」与「后端 API 协同模式」产出的卡片 AST 完全一致。
"""

from __future__ import annotations

import math
import re
from typing import Any

_INLINE_MARKS = re.compile(r"(\*\*|\*|==|`)")
_IMAGE_LINE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)$")
_HEADING = re.compile(r"^(#{1,3})\s+(.+)$")
_UL_ITEM = re.compile(r"^[-*+]\s+")
_OL_ITEM = re.compile(r"^\d+\.\s+")
_TASK_ITEM = re.compile(r"^\[([ xX])\]\s+(.+)$")
_TABLE_SEPARATOR = re.compile(r"[\s|:\-]")
_HR_SPLIT = re.compile(r"\n\s*---+\s*\n")

PLATFORM_PRESETS: list[dict[str, Any]] = [
    {
        "id": "xhs-3-4",
        "name": "小红书标准竖图",
        "platformLabel": "小红书 RedNote",
        "ratioLabel": "3 : 4",
        "width": 360,
        "height": 480,
        "exportScale": 3,
        "description": "1080 × 1440 px · 小红书流量池最优选竖版画幅",
    },
    {
        "id": "douyin-9-16",
        "name": "抖音图文全屏",
        "platformLabel": "抖音 Douyin",
        "ratioLabel": "9 : 16",
        "width": 360,
        "height": 640,
        "exportScale": 3,
        "description": "1080 × 1920 px · 沉浸式全屏滑动图文画幅",
    },
    {
        "id": "square-1-1",
        "name": "极简方形卡片",
        "platformLabel": "通用方图 1:1",
        "ratioLabel": "1 : 1",
        "width": 380,
        "height": 380,
        "exportScale": 3,
        "description": "1080 × 1080 px · 适合知识金句与多宫格连发",
    },
    {
        "id": "landscape-4-3",
        "name": "横版干货图谱",
        "platformLabel": "横屏笔记 4:3",
        "ratioLabel": "4 : 3",
        "width": 480,
        "height": 360,
        "exportScale": 3,
        "description": "1440 × 1080 px · 适合宽幅对比表格与思维框架",
    },
]

TEMPLATE_DEFAULTS: dict[str, Any] = {
    "id": "editorial-paper",
    "name": "宣纸杂志",
    "subtitle": "",
    "bgStyle": "linear-gradient(165deg, #FAF7F0 0%, #F3EDE0 100%)",
    "bgColorHex": "#FAF7F0",
    "patternType": "paper-grain",
    "patternColor": "#8C7A6B",
    "patternOpacity": 0.12,
    "borderFrameType": "double-editorial",
    "borderColor": "#A69580",
    "borderInset": 14,
    "fontMode": "editorial-serif",
    "textColor": "#231F1C",
    "mutedColor": "#786E65",
    "accentColor": "#C83E2B",
    "accentBgSoft": "rgba(200, 62, 43, 0.10)",
    "cardSurfaceBg": "rgba(255, 255, 255, 0.65)",
    "tableBorderColor": "#D6CBC0",
    "paddingX": 30,
    "paddingY": 32,
    "borderRadius": 0,
}

BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    {
        **TEMPLATE_DEFAULTS,
        "id": "editorial-paper",
        "name": "宣纸杂志",
        "subtitle": "温润米白 · 双线画框 · 宋刻标题",
    },
    {
        "id": "rednote-studio",
        "name": "小红书纯白实验室",
        "subtitle": "高透纯白 · 细密方格 · 朱砂重点",
        "bgStyle": "#FFFFFF",
        "bgColorHex": "#FFFFFF",
        "patternType": "grid",
        "patternColor": "#18181B",
        "patternOpacity": 0.05,
        "borderFrameType": "viewfinder-corners",
        "borderColor": "#E11D48",
        "borderInset": 14,
        "fontMode": "modern-sans",
        "textColor": "#18181B",
        "mutedColor": "#71717A",
        "accentColor": "#E11D48",
        "accentBgSoft": "rgba(225, 29, 72, 0.09)",
        "cardSurfaceBg": "#F8FAFC",
        "tableBorderColor": "#E4E4E7",
        "paddingX": 28,
        "paddingY": 30,
        "borderRadius": 0,
    },
    {
        "id": "obsidian-gold",
        "name": "暗夜黑金刊物",
        "subtitle": "深邃曜石 · 典藏花角 · 鎏金高光",
        "bgStyle": "linear-gradient(155deg, #131316 0%, #09090B 100%)",
        "bgColorHex": "#0D0D10",
        "patternType": "crosshairs",
        "patternColor": "#D4AF37",
        "patternOpacity": 0.14,
        "borderFrameType": "gallery-filigree",
        "borderColor": "#D4AF37",
        "borderInset": 14,
        "fontMode": "editorial-serif",
        "textColor": "#F5F3EF",
        "mutedColor": "#A19E95",
        "accentColor": "#EAB308",
        "accentBgSoft": "rgba(234, 179, 8, 0.15)",
        "cardSurfaceBg": "rgba(255, 255, 255, 0.05)",
        "tableBorderColor": "#2E2D2A",
        "paddingX": 30,
        "paddingY": 32,
        "borderRadius": 0,
    },
    {
        "id": "kyoto-matcha",
        "name": "京都抹茶手账",
        "subtitle": "森系苔绿 · 横线信纸 · 邮票齿孔框",
        "bgStyle": "linear-gradient(180deg, #F3F6F1 0%, #E7EFE4 100%)",
        "bgColorHex": "#F1F5EE",
        "patternType": "ruled-lines",
        "patternColor": "#3F624D",
        "patternOpacity": 0.09,
        "borderFrameType": "stamp-perforated",
        "borderColor": "#4D725B",
        "borderInset": 12,
        "fontMode": "editorial-serif",
        "textColor": "#1B2E22",
        "mutedColor": "#5B7564",
        "accentColor": "#2F6F4E",
        "accentBgSoft": "rgba(47, 111, 78, 0.12)",
        "cardSurfaceBg": "rgba(255, 255, 255, 0.7)",
        "tableBorderColor": "#C5D6C9",
        "paddingX": 30,
        "paddingY": 32,
        "borderRadius": 0,
    },
    {
        "id": "film-35mm",
        "name": "复古胶片暗房",
        "subtitle": "银盐暖灰 · 点阵底纹 · 35mm齿孔边框",
        "bgStyle": "linear-gradient(160deg, #F4F1EA 0%, #E8E2D5 100%)",
        "bgColorHex": "#EFECE4",
        "patternType": "dots",
        "patternColor": "#27272A",
        "patternOpacity": 0.10,
        "borderFrameType": "film-sprocket",
        "borderColor": "#27272A",
        "borderInset": 10,
        "fontMode": "modern-sans",
        "textColor": "#18181B",
        "mutedColor": "#52525B",
        "accentColor": "#D97706",
        "accentBgSoft": "rgba(217, 119, 6, 0.13)",
        "cardSurfaceBg": "rgba(255, 255, 255, 0.6)",
        "tableBorderColor": "#D4CFC4",
        "paddingX": 34,
        "paddingY": 28,
        "borderRadius": 0,
    },
    {
        "id": "swiss-grid",
        "name": "瑞士国际主义网格",
        "subtitle": "建筑秩序 · 十字基准 · 结构外框",
        "bgStyle": "#F4F4F0",
        "bgColorHex": "#F4F4F0",
        "patternType": "crosshairs",
        "patternColor": "#18181B",
        "patternOpacity": 0.12,
        "borderFrameType": "swiss-grid",
        "borderColor": "#18181B",
        "borderInset": 14,
        "fontMode": "modern-sans",
        "textColor": "#111111",
        "mutedColor": "#666660",
        "accentColor": "#2563EB",
        "accentBgSoft": "rgba(37, 99, 235, 0.10)",
        "cardSurfaceBg": "#FFFFFF",
        "tableBorderColor": "#18181B",
        "paddingX": 28,
        "paddingY": 30,
        "borderRadius": 0,
    },
    {
        "id": "sunset-mesh",
        "name": "落日弥散微光",
        "subtitle": "柔光渐变 · 斜纹肌理 · 双层细框",
        "bgStyle": "linear-gradient(135deg, #FFF5F5 0%, #FFFBEB 50%, #F0FDF4 100%)",
        "bgColorHex": "#FFF8F6",
        "patternType": "diagonal",
        "patternColor": "#F43F5E",
        "patternOpacity": 0.05,
        "borderFrameType": "double-editorial",
        "borderColor": "#FDA4AF",
        "borderInset": 14,
        "fontMode": "modern-sans",
        "textColor": "#27171C",
        "mutedColor": "#886973",
        "accentColor": "#E11D48",
        "accentBgSoft": "rgba(225, 29, 72, 0.10)",
        "cardSurfaceBg": "rgba(255, 255, 255, 0.80)",
        "tableBorderColor": "#FECDD3",
        "paddingX": 28,
        "paddingY": 30,
        "borderRadius": 0,
    },
    {
        "id": "blueprint-tech",
        "name": "深海工程蓝图",
        "subtitle": "精密制图 · 坐标方格 · 极客等宽",
        "bgStyle": "linear-gradient(165deg, #0B192C 0%, #07111E 100%)",
        "bgColorHex": "#0B192C",
        "patternType": "grid",
        "patternColor": "#38BDF8",
        "patternOpacity": 0.12,
        "borderFrameType": "viewfinder-corners",
        "borderColor": "#38BDF8",
        "borderInset": 14,
        "fontMode": "technical-mono",
        "textColor": "#F0F9FF",
        "mutedColor": "#7DD3FC",
        "accentColor": "#38BDF8",
        "accentBgSoft": "rgba(56, 189, 248, 0.15)",
        "cardSurfaceBg": "rgba(14, 116, 144, 0.18)",
        "tableBorderColor": "#1E3A5F",
        "paddingX": 28,
        "paddingY": 30,
        "borderRadius": 0,
    },
]

META_DEFAULTS: dict[str, Any] = {
    "showHeader": True,
    "headerLeft": "小红书 · 视觉笔记",
    "headerRight": "2026 EDITION",
    "showFooter": True,
    "authorHandle": "@林间漫步",
    "footerNote": "滑动查看下一张",
    "showPageNumber": True,
    "showSafeZoneGuide": False,
    "fontSizeScale": 1,
}


def find_preset(preset_id: str) -> dict[str, Any]:
    for preset in PLATFORM_PRESETS:
        if preset["id"] == preset_id:
            return dict(preset)
    return dict(PLATFORM_PRESETS[0])


def resolve_template(template_id: str | None, custom: dict[str, Any] | None) -> dict[str, Any]:
    """自定义模板优先；否则按 id 取内置模板；再兜底第一套。"""
    if custom:
        return {**TEMPLATE_DEFAULTS, **custom}
    for template in BUILTIN_TEMPLATES:
        if template["id"] == template_id:
            return dict(template)
    return dict(BUILTIN_TEMPLATES[0])


def resolve_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    if not meta:
        return dict(META_DEFAULTS)
    resolved = dict(META_DEFAULTS)
    resolved.update({key: value for key, value in meta.items() if value is not None})
    return resolved


def _plain_length(text: str) -> int:
    return len(_INLINE_MARKS.sub("", text))


def _estimate_block_height(
    block: dict[str, Any], content_width: int, font_scale: float
) -> int:
    chars_per_line = max(12, math.floor(content_width / (14.5 * font_scale)))
    block_type = block["type"]
    content = block.get("content", "")

    if block_type == "h1":
        lines = max(1, math.ceil(len(content) / max(1, math.floor(chars_per_line * 0.62))))
        return round((lines * 34 + 22) * font_scale)
    if block_type == "h2":
        lines = max(1, math.ceil(len(content) / max(1, math.floor(chars_per_line * 0.75))))
        return round((lines * 26 + 18) * font_scale)
    if block_type == "h3":
        lines = max(1, math.ceil(len(content) / max(1, math.floor(chars_per_line * 0.85))))
        return round((lines * 22 + 14) * font_scale)
    if block_type == "paragraph":
        lines = max(1, math.ceil(_plain_length(content) / chars_per_line))
        return round((lines * 22 + 12) * font_scale)
    if block_type == "blockquote":
        per_line = max(10, chars_per_line - 3)
        lines = max(1, math.ceil(_plain_length(content) / per_line))
        return round((lines * 21 + 26) * font_scale)
    if block_type == "image":
        return round(148 + (22 if block.get("imageAlt") else 6))
    if block_type == "table":
        table = block.get("table")
        if not table:
            return 80
        row_count = len(table.get("rows", [])) + 1
        return round(row_count * 31 + 20)
    if block_type in ("ul", "ol"):
        items = block.get("items") or []
        per_line = max(10, chars_per_line - 2)
        total_lines = 0
        for item in items:
            total_lines += max(1, math.ceil(_plain_length(item.get("text", "")) / per_line))
        return round((total_lines * 21 + len(items) * 6 + 12) * font_scale)
    if block_type == "code":
        code_lines = len(content.split("\n"))
        return round(code_lines * 18 + 28)
    return 40


def _parse_row(row: str) -> list[str]:
    return [cell.strip() for cell in row[1:-1].split("|")]


def _parse_segment_blocks(
    raw_segment: str, content_width: int, font_scale: float, segment_idx: int
) -> list[dict[str, Any]]:
    lines = raw_segment.replace("\r\n", "\n").split("\n")
    blocks: list[dict[str, Any]] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        value = f"blk-{segment_idx}-{counter}"
        counter += 1
        return value

    def push(block: dict[str, Any]) -> None:
        block["estimatedHeight"] = _estimate_block_height(block, content_width, font_scale)
        blocks.append(block)

    i = 0
    while i < len(lines):
        trimmed = lines[i].strip()

        if not trimmed:
            i += 1
            continue

        # 1. 代码块 ```
        if trimmed.startswith("```"):
            language = trimmed[3:].strip() or "text"
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            push(
                {
                    "id": next_id(),
                    "type": "code",
                    "content": "\n".join(code_lines),
                    "language": language,
                }
            )
            continue

        # 2. 标题 # / ## / ###
        heading = _HEADING.match(trimmed)
        if heading:
            level = len(heading.group(1))
            push(
                {
                    "id": next_id(),
                    "type": "h1" if level == 1 else "h2" if level == 2 else "h3",
                    "content": heading.group(2).strip(),
                }
            )
            i += 1
            continue

        # 3. 独立图片 ![alt](url)
        image = _IMAGE_LINE.match(trimmed)
        if image:
            push(
                {
                    "id": next_id(),
                    "type": "image",
                    "content": image.group(1),
                    "imageAlt": image.group(1),
                    "imageUrl": image.group(2).strip(),
                }
            )
            i += 1
            continue

        # 4. 表格 | ... |
        if trimmed.startswith("|") and trimmed.endswith("|"):
            table_lines: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            if len(table_lines) >= 2:
                headers = _parse_row(table_lines[0])
                is_separator = _TABLE_SEPARATOR.sub("", table_lines[1]) == ""
                data_start = 2 if is_separator else 1
                rows = [_parse_row(row) for row in table_lines[data_start:]]
                push(
                    {
                        "id": next_id(),
                        "type": "table",
                        "content": "",
                        "table": {"headers": headers, "rows": rows},
                    }
                )
                continue

        # 5. 引用 > ...
        if trimmed.startswith(">"):
            quote_lines: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(re.sub(r"^>\s?", "", lines[i].strip()))
                i += 1
            push(
                {
                    "id": next_id(),
                    "type": "blockquote",
                    "content": " ".join(quote_lines),
                }
            )
            continue

        # 6. 无序列表 - / * / +
        if _UL_ITEM.match(trimmed):
            items: list[dict[str, Any]] = []
            while i < len(lines) and _UL_ITEM.match(lines[i].strip()):
                raw_item = _UL_ITEM.sub("", lines[i].strip(), count=1)
                task = _TASK_ITEM.match(raw_item)
                if task:
                    items.append(
                        {"checked": task.group(1).lower() == "x", "text": task.group(2).strip()}
                    )
                else:
                    items.append({"text": raw_item})
                i += 1
            push({"id": next_id(), "type": "ul", "content": "", "items": items})
            continue

        # 7. 有序列表 1. ...
        if _OL_ITEM.match(trimmed):
            items = []
            while i < len(lines) and _OL_ITEM.match(lines[i].strip()):
                items.append({"text": _OL_ITEM.sub("", lines[i].strip(), count=1)})
                i += 1
            push({"id": next_id(), "type": "ol", "content": "", "items": items})
            continue

        # 8. 普通段落（连续纯文本行合并）
        para_lines: list[str] = []
        while i < len(lines):
            current = lines[i].strip()
            if not current:
                break
            if (
                current.startswith("#")
                or current.startswith("```")
                or current.startswith(">")
                or current.startswith("|")
                or _IMAGE_LINE.match(current)
                or _UL_ITEM.match(current)
                or _OL_ITEM.match(current)
            ):
                break
            para_lines.append(current)
            i += 1
        content = " ".join(para_lines)
        if content:
            push({"id": next_id(), "type": "paragraph", "content": content})

    return blocks


def paginate_markdown_to_cards(
    markdown: str,
    preset: dict[str, Any],
    template: dict[str, Any],
    meta: dict[str, Any],
    mode: str,
) -> list[dict[str, Any]]:
    header_reserve = 32 if meta.get("showHeader") else 0
    footer_reserve = 34 if (meta.get("showFooter") or meta.get("showPageNumber")) else 0
    font_scale = float(meta.get("fontSizeScale") or 1)
    padding_x = int(template.get("paddingX") or 0)
    padding_y = int(template.get("paddingY") or 0)

    max_available_height = max(
        160, int(preset["height"]) - padding_y * 2 - header_reserve - footer_reserve
    )
    content_width = max(180, int(preset["width"]) - padding_x * 2)

    raw_segments = [
        segment.strip()
        for segment in _HR_SPLIT.split(markdown.replace("\r\n", "\n"))
        if segment.strip()
    ]
    segments = [
        _parse_segment_blocks(segment, content_width, font_scale, idx)
        for idx, segment in enumerate(raw_segments)
    ]

    raw_pages: list[dict[str, Any]] = []

    if mode == "manual-hr":
        for blocks in segments:
            if not blocks:
                continue
            used = sum(block["estimatedHeight"] for block in blocks)
            raw_pages.append({"blocks": blocks, "usedHeight": used})

    elif mode == "by-h2":
        for blocks in segments:
            current_blocks: list[dict[str, Any]] = []
            current_height = 0
            for block in blocks:
                if block["type"] in ("h1", "h2") and current_blocks:
                    raw_pages.append({"blocks": current_blocks, "usedHeight": current_height})
                    current_blocks = [block]
                    current_height = block["estimatedHeight"]
                else:
                    current_blocks.append(block)
                    current_height += block["estimatedHeight"]
            if current_blocks:
                raw_pages.append({"blocks": current_blocks, "usedHeight": current_height})

    else:  # smart-auto
        for blocks in segments:
            current_blocks = []
            current_height = 0
            for idx, block in enumerate(blocks):
                next_block = blocks[idx + 1] if idx + 1 < len(blocks) else None
                keep_with_next = (
                    min(60, next_block["estimatedHeight"] * 0.5)
                    if block["type"] in ("h2", "h3") and next_block
                    else 0
                )
                if (
                    current_blocks
                    and current_height + block["estimatedHeight"] + keep_with_next
                    > max_available_height
                ):
                    raw_pages.append({"blocks": current_blocks, "usedHeight": current_height})
                    current_blocks = [block]
                    current_height = block["estimatedHeight"]
                else:
                    current_blocks.append(block)
                    current_height += block["estimatedHeight"]
            if current_blocks:
                raw_pages.append({"blocks": current_blocks, "usedHeight": current_height})

    if not raw_pages:
        raw_pages.append(
            {
                "blocks": [
                    {
                        "id": "empty-placeholder",
                        "type": "paragraph",
                        "content": "在左侧输入 Markdown 内容，这里将实时生成符合平台规范的固定尺寸卡片...",
                        "estimatedHeight": 48,
                    }
                ],
                "usedHeight": 48,
            }
        )

    total = len(raw_pages)
    return [
        {
            "index": idx + 1,
            "total": total,
            "isCover": idx == 0
            and any(block["type"] == "h1" for block in page["blocks"]),
            "blocks": page["blocks"],
            "estimatedUsedHeight": page["usedHeight"],
            "maxAvailableHeight": max_available_height,
            "isOverflowing": page["usedHeight"] > max_available_height + 16,
        }
        for idx, page in enumerate(raw_pages)
    ]
