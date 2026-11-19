"""统一风格注册表（地基契约 §7）。

提示词库（需求 02）与分镜统一风格（需求 03）共用这一份只读注册表，
避免各维护一套画风枚举。元数据是代码级内置常量，不落库、不提供写接口：
维护方式由后续 Agent / Lead 决定；本模块只负责读取。

风格词来源：
- 角色 4 档：services/character_images.py 的 STYLE_WORDS（中文风格词）。
- 贴图 6 档：services/sticker_styles.py 的 STYLES[*].prompt_suffix。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StylePreset:
    id: str
    name: str
    prompt_suffix: str
    negative_prompt: str = ""
    reference_asset_id: str | None = None
    applicable_to: tuple[str, ...] = ()  # cover / illustration / comic_panel / base


# 角色 4 档的中文词与 character_images.STYLE_WORDS 保持一致。
_MANGA = "日系漫画风格，干净线条，赛璐璐上色"
_REALISTIC = "写实插画风格，细腻光影"
_GUOMAN = "国漫风格，精致线稿与厚涂上色"
_WATERCOLOR = "水彩手绘风格，柔和晕染"

# 贴图 6 档的 prompt_suffix 与 sticker_styles.STYLES 保持一致。
_HUMANIST_PHOTO = (
    "photorealistic documentary photograph, natural available light, candid real-life "
    "moment, shallow depth of field, authentic human emotion, film grain, high detail, "
    "no illustration, no cartoon"
)
_MINIMAL_TECH = (
    "minimalist flat vector illustration, clean geometric shapes, generous negative space, "
    "cool muted palette with one accent color, subtle gradients, modern tech aesthetic, "
    "crisp edges, no photographic elements, no clutter"
)
_FLAT_ILLUSTRATION = (
    "modern flat illustration, bold simple shapes, bright harmonious colors, friendly and "
    "approachable, clean composition, soft shadows, consistent style, no photorealism, "
    "no text artifacts"
)
_HANDDRAW_NOTE = (
    "hand-drawn sketch style, ink line art with watercolor washes, notebook doodle "
    "aesthetic, imperfect organic lines, warm paper texture, cozy handwritten feel, "
    "charming and personal, no photorealism"
)
_INFO_CARD = (
    "clean infographic card layout, clear visual hierarchy, structured blocks and zones, "
    "balanced whitespace, legible typographic grid, restrained corporate palette, flat "
    "design, high readability"
)
_RETRO_NOSTALGIA = (
    "vintage retro illustration, faded nostalgic color grading, analog film look, subtle "
    "grain and light leaks, 70s-90s print aesthetic, warm sepia tones, textured paper, "
    "nostalgic mood"
)

_ALL_SCENES = ("cover", "illustration", "comic_panel")
_STICKER_SCENES = ("cover", "illustration")


STYLE_PRESETS: dict[str, StylePreset] = {
    # 角色 / 漫画 4 档：分镜 comic_panel 是主场景，同时可用于封面与配图。
    "manga": StylePreset("manga", "日系漫画", _MANGA, applicable_to=_ALL_SCENES),
    "realistic": StylePreset("realistic", "写实插画", _REALISTIC, applicable_to=_ALL_SCENES),
    "guoman": StylePreset("guoman", "国漫", _GUOMAN, applicable_to=_ALL_SCENES),
    "watercolor": StylePreset("watercolor", "水彩手绘", _WATERCOLOR, applicable_to=_ALL_SCENES),
    # 贴图 6 档：主要用于封面 / 文章配图。
    "humanist_photo": StylePreset(
        "humanist_photo", "人文纪实", _HUMANIST_PHOTO, applicable_to=_STICKER_SCENES
    ),
    "minimal_tech": StylePreset(
        "minimal_tech", "极简科技", _MINIMAL_TECH, applicable_to=_STICKER_SCENES
    ),
    "flat_illustration": StylePreset(
        "flat_illustration", "扁平插画", _FLAT_ILLUSTRATION, applicable_to=_STICKER_SCENES
    ),
    "handdraw_note": StylePreset(
        "handdraw_note", "手绘笔记", _HANDDRAW_NOTE, applicable_to=_STICKER_SCENES
    ),
    "info_card": StylePreset(
        "info_card", "信息卡片", _INFO_CARD, applicable_to=_STICKER_SCENES
    ),
    "retro_nostalgia": StylePreset(
        "retro_nostalgia", "复古怀旧", _RETRO_NOSTALGIA, applicable_to=_STICKER_SCENES
    ),
}


def list_presets(applicable_to: str | None = None) -> list[dict]:
    """按适用场景过滤并返回可 JSON 序列化的字典列表（保持注册顺序）。

    applicable_to 为空时返回全部；传入未知场景返回空列表而不是抛错，
    便于前端按 comic_panel 等场景动态拉选项。
    """
    rows: list[dict] = []
    for preset in STYLE_PRESETS.values():
        if applicable_to and applicable_to not in preset.applicable_to:
            continue
        rows.append(
            {
                "id": preset.id,
                "name": preset.name,
                "prompt_suffix": preset.prompt_suffix,
                "negative_prompt": preset.negative_prompt,
                "reference_asset_id": preset.reference_asset_id,
                "applicable_to": list(preset.applicable_to),
            }
        )
    return rows


def get_preset(style_id: str) -> StylePreset | None:
    """按 id 取单个预设；未知 id 返回 None（调用方自行决定报错还是忽略）。"""
    return STYLE_PRESETS.get(style_id)
