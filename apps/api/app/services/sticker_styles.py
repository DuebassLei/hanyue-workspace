"""贴图创作：内置风格库、赛道默认风格与叙事线。

风格句（prompt_suffix）只在任务执行器里拼到 prompt 末尾，模块层不重复拼接，
避免同一份 prompt 在两处漂移。写实摄影类必须带 photorealistic / documentary 等
约束词，否则模型容易画成插画。info_card 需要额外的版式提示（need_layout_hint）。
"""

from __future__ import annotations

STYLES: dict[str, dict] = {
    "humanist_photo": {
        "id": "humanist_photo",
        "name": "人文纪实",
        "desc": "适合社会民生、人物故事，用真实光影传递情绪与现场感。",
        "tags": ["写实", "纪实", "人文", "光影"],
        "prompt_suffix": (
            "photorealistic documentary photograph, natural available light, candid real-life "
            "moment, shallow depth of field, authentic human emotion, film grain, high detail, "
            "no illustration, no cartoon"
        ),
        "need_layout_hint": False,
    },
    "minimal_tech": {
        "id": "minimal_tech",
        "name": "极简科技",
        "desc": "适合 AI 与科技、财经商业，冷静克制、留白多、信息聚焦。",
        "tags": ["极简", "科技", "扁平", "冷色"],
        "prompt_suffix": (
            "minimalist flat vector illustration, clean geometric shapes, generous negative space, "
            "cool muted palette with one accent color, subtle gradients, modern tech aesthetic, "
            "crisp edges, no photographic elements, no clutter"
        ),
        "need_layout_hint": False,
    },
    "flat_illustration": {
        "id": "flat_illustration",
        "name": "扁平插画",
        "desc": "通用叙事款，色彩明快、主体清晰，适配大多数知识科普与生活选题。",
        "tags": ["插画", "扁平", "明快", "通用"],
        "prompt_suffix": (
            "modern flat illustration, bold simple shapes, bright harmonious colors, friendly and "
            "approachable, clean composition, soft shadows, consistent style, no photorealism, "
            "no text artifacts"
        ),
        "need_layout_hint": False,
    },
    "handdraw_note": {
        "id": "handdraw_note",
        "name": "手绘笔记",
        "desc": "适合教育与职场、文化与生活，亲切随性、像手账一样有温度。",
        "tags": ["手绘", "笔记", "手账", "温暖"],
        "prompt_suffix": (
            "hand-drawn sketch style, ink line art with watercolor washes, notebook doodle "
            "aesthetic, imperfect organic lines, warm paper texture, cozy handwritten feel, "
            "charming and personal, no photorealism"
        ),
        "need_layout_hint": False,
    },
    "info_card": {
        "id": "info_card",
        "name": "信息卡片",
        "desc": "适合数据结论、要点清单，图文分区、层次分明，强调可读性。",
        "tags": ["信息图", "卡片", "版式", "数据"],
        "prompt_suffix": (
            "clean infographic card layout, clear visual hierarchy, structured blocks and zones, "
            "balanced whitespace, legible typographic grid, restrained corporate palette, flat "
            "design, high readability"
        ),
        "need_layout_hint": True,
    },
    "retro_nostalgia": {
        "id": "retro_nostalgia",
        "name": "复古怀旧",
        "desc": "适合娱乐情感、时代记忆，胶片质感与年代色调唤起共鸣。",
        "tags": ["复古", "怀旧", "胶片", "年代"],
        "prompt_suffix": (
            "vintage retro illustration, faded nostalgic color grading, analog film look, subtle "
            "grain and light leaks, 70s-90s print aesthetic, warm sepia tones, textured paper, "
            "nostalgic mood"
        ),
        "need_layout_hint": False,
    },
}

# 8 大赛道 → 默认风格。前端从热点预填时按赛道取默认值，用户仍可改。
TRACK_DEFAULTS: dict[str, str] = {
    "社会民生": "humanist_photo",
    "教育与职场": "handdraw_note",
    "娱乐与情感": "retro_nostalgia",
    "国际与时事": "humanist_photo",
    "财经与商业": "minimal_tech",
    "AI与科技": "minimal_tech",
    "体育与健康": "flat_illustration",
    "文化与生活": "handdraw_note",
}

# 5 种叙事线：一组贴图讲一件事的骨架。
NARRATIVES: dict[str, dict] = {
    "scene": {
        "key": "scene",
        "name": "场景叙事",
        "desc": "按时间或空间顺序推进画面，像连环画一样把一件事讲完整。",
    },
    "knowledge": {
        "key": "knowledge",
        "name": "知识科普",
        "desc": "每张承载一个知识点或步骤，组合起来是一份可收藏的干货。",
    },
    "parallel": {
        "key": "parallel",
        "name": "并列罗列",
        "desc": "多张同权重的画面并列呈现要点、清单或多面观点，结构最稳。",
    },
    "contrast": {
        "key": "contrast",
        "name": "对比反差",
        "desc": "前后/对错/今昔成对出现，用张力制造记忆点与传播欲。",
    },
    "suspense": {
        "key": "suspense",
        "name": "悬念递进",
        "desc": "逐张抛出线索、最后一张揭晓答案，引导读者翻到底。",
    },
}


def style_ids() -> set[str]:
    return set(STYLES.keys())
