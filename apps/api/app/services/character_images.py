"""角色参考图的提示词模板（Q9-C：模板为主，用户可整体覆写）。

刻意不走 LLM 翻译：便宜、可控、离线可用；不满意由前端「编辑最终提示词」兜底。
锚定连续性走 /images/edits 的参考图通道，这里只负责文字部分。
"""

from __future__ import annotations

from .. import schemas

STYLE_WORDS: dict[str, str] = {
    "manga": "日系漫画风格，干净线条，赛璐璐上色",
    "realistic": "写实插画风格，细腻光影",
    "guoman": "国漫风格，精致线稿与厚涂上色",
    "watercolor": "水彩手绘风格，柔和晕染",
}

ANGLE_WORDS: dict[str, str] = {
    "front": "正面半身像，双眼直视镜头",
    "side": "侧面全身像，展示侧脸轮廓与身形",
    "full": "全身立绘，从头到脚完整入画",
    "close": "面部特写，突出五官细节",
    "back": "背面全身像，展示发型与服装背部",
    "any": "角色设定图，构图自然",
}

_APPEARANCE_LABELS: dict[str, str] = {
    "hair": "发型", "hairColor": "发色", "eyes": "眼睛", "face": "面部",
    "outfit": "服装", "body": "体型", "age": "年龄", "extra": "特征",
}


def build_prompt(name: str, appearance: dict | None, angle: str, style: str) -> str:
    """appearance → 绘图提示词。风格/角度词都从白名单取，未知值按默认而不是原样拼入。"""
    style_word = STYLE_WORDS.get(style or "", STYLE_WORDS["manga"])
    angle_word = ANGLE_WORDS.get(angle or "", ANGLE_WORDS["any"])
    traits = "，".join(
        f"{_APPEARANCE_LABELS[key]}：{(appearance or {}).get(key)}"
        for key in schemas.APPEARANCE_KEYS
        if (appearance or {}).get(key)
    )
    subject = f"角色「{name.strip() or '未命名'}」"
    body = f"，{traits}" if traits else ""
    return (
        f"{subject}{body}。{angle_word}。{style_word}。"
        "单一角色居中，中性纯色背景，无文字，无水印。"
    )
