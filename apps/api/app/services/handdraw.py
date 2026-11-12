"""手绘风格库：内置资源导入、编号归一、模型激活策略与提示词合成。

规则对齐 handdraw-style-prompter 的 resolve_reference.py 与 prompt_style.py（纯图模式）。
运行时不读取本机 Skill 目录，只使用 app/resources/handdraw 下的资源快照。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import models

LIBRARY_KEY = "handdraw-style"
LIBRARY_DIR = Path(__file__).resolve().parents[1] / "resources" / "handdraw"
RESOURCE_JSON = (
    "styles.json",
    "colors.json",
    "model_capabilities.json",
    "style_alias_map.json",
    "version.json",
)
DEFAULT_MODEL = "gpt-image-2"
MAX_REFERENCES = 4
KEEP_RAW_TRAITS = {"FE-048", "FE-049", "FE-051", "FH-050", "FD-042"}
KEEP_RAW_LEGACY = {"240", "242", "257", "259", "260"}

REFERENCE_ISOLATION_ZH = (
    "所附图片仅用于参考画风。只提取参考图的风格特征，例如线条、笔触、媒介、材质、色彩倾向和整体视觉语言；"
    "不要使用、复制或延续参考图中的任何主体、人物、动物、服装、道具、动作、姿态、场景、背景、构图、布局、文字或故事。"
    "最终画面内容完全以用户提供的主题为准。"
)
REFERENCE_ISOLATION_EN = (
    "Use the attached image only as a style reference. Extract only its stylistic qualities, such as linework, brushwork, medium, "
    "material texture, color tendencies, and overall visual language. Do not use, copy, or carry over any subject, person, animal, "
    "clothing, prop, action, pose, setting, background, composition, layout, text, or story from the reference image. "
    "The user's written theme is the sole source for the image content."
)

_CN_RE = re.compile(r"[\u4e00-\u9fa5]")
_STYLE_NUMBER_RE = re.compile(r"^([A-Za-z]{2})[-_]?(\d+)$")
_NEGATIVE_WORDS = ("避免", "不要", "不准", "禁止")
_NEGATIVE_TRAIT_RE = re.compile(r"无(?:写实纹理|精细材质|真实纹理)")


def _read_json(name: str) -> object:
    return json.loads((LIBRARY_DIR / name).read_text(encoding="utf-8"))


def resource_hash() -> str:
    """对参与导入的文件内容做稳定哈希，用于判断是否需要重建内置表。"""
    digest = hashlib.sha256()
    for name in RESOURCE_JSON:
        path = LIBRARY_DIR / name
        if path.exists():
            digest.update(name.encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def reference_rel_path(number: str) -> str:
    """参考图相对路径：优先 {number}_grid.webp，回退单图。"""
    group = number.split("-")[0]
    grid = LIBRARY_DIR / "refs" / group / f"{number}_grid.webp"
    if grid.exists():
        return f"refs/{group}/{number}_grid.webp"
    return f"refs/{group}/{number}.webp"


def resolve_reference_file(style: models.HanddrawStyle) -> Path | None:
    if not style.reference_file:
        return None
    path = LIBRARY_DIR / style.reference_file
    return path if path.exists() else None


# ---------------- 导入 ----------------


def import_library(db: Session, force: bool = False) -> models.HanddrawLibraryMeta:
    """把内置资源写入数据库；哈希未变且数据完整时直接返回现有元信息。"""
    current_hash = resource_hash()
    styles_data = _read_json("styles.json")
    colors_data = _read_json("colors.json")
    alias_data = _read_json("style_alias_map.json")
    version_data = _read_json("version.json")
    new_to_legacy = alias_data.get("new_to_legacy", {}) if isinstance(alias_data, dict) else {}
    version = ""
    if isinstance(version_data, dict):
        version = str(version_data.get("version") or "")

    meta = db.scalar(
        select(models.HanddrawLibraryMeta).where(
            models.HanddrawLibraryMeta.library_key == LIBRARY_KEY
        )
    )
    style_total = db.scalar(select(func.count()).select_from(models.HanddrawStyle)) or 0
    color_total = db.scalar(select(func.count()).select_from(models.HanddrawColor)) or 0
    if (
        not force
        and meta is not None
        and meta.source_hash == current_hash
        and style_total == len(styles_data)
        and color_total == len(colors_data)
    ):
        return meta

    db.execute(delete(models.HanddrawStyle))
    db.execute(delete(models.HanddrawColor))
    missing: list[str] = []
    reference_count = 0
    for index, item in enumerate(styles_data):
        number = str(item.get("number") or "")
        legacy = str(new_to_legacy.get(number) or "")
        rel = reference_rel_path(number)
        available = (LIBRARY_DIR / rel).exists()
        if available:
            reference_count += 1
        else:
            missing.append(number)
        group_label = str(item.get("group") or "")
        db.add(
            models.HanddrawStyle(
                style_number=number,
                legacy_number=legacy,
                group_key=number.split("-")[0],
                group_name=group_label,
                reference_author=str(item.get("reference") or ""),
                generation_name=str(item.get("generation_name") or ""),
                traits_raw=str(item.get("traits") or ""),
                reference_file=rel if available else None,
                reference_kind="grid" if rel.endswith("_grid.webp") else "single",
                reference_available=available,
                sort_order=index,
            )
        )
    for index, item in enumerate(colors_data):
        color_id = str(item.get("id") or "")
        swatch = f"swatches/{color_id}.webp"
        db.add(
            models.HanddrawColor(
                color_id=color_id,
                category=str(item.get("category") or ""),
                category_zh=str(item.get("category_zh") or ""),
                category_en=str(item.get("category_en") or ""),
                name_zh=str(item.get("name_zh") or ""),
                name_en=str(item.get("name_en") or ""),
                quote_zh=str(item.get("quote_zh") or ""),
                quote_en=str(item.get("quote_en") or ""),
                prompt_zh=str(item.get("prompt_zh") or ""),
                prompt_en=str(item.get("prompt_en") or ""),
                swatch_file=swatch if (LIBRARY_DIR / swatch).exists() else None,
                sort_order=index,
            )
        )
    if meta is None:
        meta = models.HanddrawLibraryMeta(library_key=LIBRARY_KEY)
        db.add(meta)
    meta.version = version
    meta.source_hash = current_hash
    meta.imported_at = models.utcnow()
    meta.style_count = len(styles_data)
    meta.color_count = len(colors_data)
    meta.reference_count = reference_count
    meta.missing_references = missing
    db.commit()
    db.refresh(meta)
    return meta


def ensure_library(db: Session) -> None:
    """启动时调用：内置表为空或资源变化时执行导入。"""
    import_library(db, force=False)


# ---------------- 编号归一 ----------------


def normalize_style_number(style_input: str | None, alias_map: dict | None = None) -> str:
    """把 018 / fa_1 / FA-001 统一成规范编号；无法识别时抛 ValueError。"""
    text = str(style_input or "").strip()
    if not text:
        raise ValueError("风格编号为空")
    if alias_map is None:
        raw = _read_json("style_alias_map.json")
        alias_map = raw if isinstance(raw, dict) else {}
    legacy_to_new: dict = alias_map.get("legacy_to_new", {})
    new_to_legacy: dict = alias_map.get("new_to_legacy", {})
    if text.isdigit():
        num = f"{int(text):03}"
        canonical = legacy_to_new.get(num, num)
    else:
        matched = _STYLE_NUMBER_RE.match(text)
        if matched:
            canonical = f"{matched.group(1).upper()}-{int(matched.group(2)):03}"
        else:
            canonical = text.upper()
    if canonical in legacy_to_new:
        canonical = legacy_to_new[canonical]
    if canonical not in new_to_legacy:
        raise ValueError(f"风格编号不存在：{style_input}")
    return canonical


# ---------------- 激活策略 ----------------


def normalize_model_code(model: str | None) -> str:
    """模型代号归一：小写、去空格、去供应商前缀。未指定时用标定基线模型。"""
    text = str(model or "").strip().lower()
    if not text:
        return DEFAULT_MODEL
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text.replace(" ", "")


def positive_traits(traits: str) -> str:
    """只保留正向可见特征，剔除否定句与「无材质」类描述。"""
    if not traits:
        return ""
    kept: list[str] = []
    for part in re.split(r"[；;。\n]+", traits):
        part = part.strip(" ，,、：:。；;\t")
        if not part:
            continue
        if any(word in part for word in _NEGATIVE_WORDS):
            continue
        if _NEGATIVE_TRAIT_RE.search(part):
            continue
        kept.append(part)
    return "；".join(kept)


@dataclass
class Activation:
    model_code: str
    activation_source: str
    include_traits: bool
    need_reference: bool
    traits: str


def resolve_activation(style: models.HanddrawStyle, model: str | None) -> Activation:
    policy = _read_json("model_capabilities.json")
    if not isinstance(policy, dict):
        policy = {}
    model_code = normalize_model_code(model)
    default = dict(policy.get("default") or {})
    profile = (policy.get("models") or {}).get(model_code)
    entry = dict(default)
    if isinstance(profile, dict):
        entry.update({key: value for key, value in profile.items() if key != "styles"})
        styles_map = profile.get("styles") or {}
        style_entry = styles_map.get(style.style_number) or styles_map.get(style.legacy_number)
        if isinstance(style_entry, dict):
            entry.update(style_entry)
    name_activation = str(entry.get("name_activation") or "unknown")
    traits_activation = str(entry.get("traits_activation") or "unknown")

    keep_raw = (
        style.style_number in KEEP_RAW_TRAITS or style.legacy_number in KEEP_RAW_LEGACY
    )
    traits = style.traits_raw if keep_raw else positive_traits(style.traits_raw)

    if name_activation == "strong":
        return Activation(model_code, "name+style", False, False, "")
    if traits_activation == "strong" and traits:
        return Activation(model_code, "name+style+traits", True, False, traits)
    return Activation(
        model_code,
        "name+style+traits+reference-image" if traits else "name+style+reference-image",
        bool(traits),
        True,
        traits,
    )


# ---------------- 主题色 ----------------


def resolve_color_sentence(
    color: models.HanddrawColor | None, accent: models.HanddrawColor | None
) -> tuple[str, str]:
    if color is None:
        return "", ""
    if accent is not None and accent.color_id != color.color_id:
        zh = (
            f"主题色：主色为{color.name_zh}（{color.name_en}），"
            f"点缀色为{accent.name_zh}（{accent.name_en}）。"
        )
        en = (
            f"Theme color: {color.name_en} as primary tone, "
            f"accented with {accent.name_en}."
        )
        return zh, en
    return color.prompt_zh or f"主题色：{color.name_zh}。", color.prompt_en or ""


# ---------------- 提示词合成 ----------------


def detect_language(theme: str) -> str:
    return "zh" if _CN_RE.search(theme or "") else "en"


def compose_prompt(
    style: models.HanddrawStyle,
    activation: Activation,
    theme: str,
    color: models.HanddrawColor | None = None,
    accent: models.HanddrawColor | None = None,
) -> dict:
    theme_text = (theme or "").strip()
    color_zh, color_en = resolve_color_sentence(color, accent)
    zh_parts = [f"风格名称：{style.generation_name}。"]
    if color_zh:
        zh_parts.append(color_zh)
    zh_parts.append(f"主题：{theme_text}。")
    zh_parts.append(f"参考作者/风格名称：{style.reference_author}。")
    if activation.include_traits and activation.traits:
        zh_parts.append(f"核心风格特征：{activation.traits}。")
    if activation.need_reference:
        zh_parts.append(f"参考图：随请求自动附加本风格参考图。{REFERENCE_ISOLATION_ZH}")

    en_parts = [f"Style name: {style.generation_name}."]
    if color_en:
        en_parts.append(color_en)
    en_parts.append(f"Theme: {theme_text}.")
    en_parts.append(f"Reference author/style name: {style.reference_author}.")
    if activation.include_traits and activation.traits:
        en_parts.append(f"Core style traits: {activation.traits}.")
    if activation.need_reference:
        en_parts.append(f"Reference image: attached automatically with this style. {REFERENCE_ISOLATION_EN}")

    return {
        "promptZh": "".join(zh_parts),
        "promptEn": " ".join(en_parts),
        "language": detect_language(theme_text),
    }
