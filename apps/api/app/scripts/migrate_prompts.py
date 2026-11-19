"""幂等迁移：image_styles + 历史私有提示词 → 统一 prompt_templates。

用法（在 apps/api 目录，应用启动不会自动执行）：
    .venv/Scripts/python.exe -m app.scripts.migrate_prompts

约定：
- image_styles.purpose → prompt_type：base→base、cover→cover、inline→illustration。
- prompt 中「画面避免出现：…」切分到 negative_content（出厂封面模板同样处理）。
- 历史 prompt_templates 的 owner_user_id 原值保留仅作追溯；新记录恒为 null。
- 按 (title, owner_user_id) 判重，重复执行不产生新行。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 兼容直接 `python app/scripts/migrate_prompts.py` 运行。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import inspect, select

from app import models
from app.core.db import SessionLocal
from app.modules.image_styles import FACTORY_STYLES

PURPOSE_TO_TYPE = {"base": "base", "cover": "cover", "inline": "illustration"}
NEGATIVE_MARKER = "画面避免出现："
_FACTORY_NAMES = {spec["name"] for spec in FACTORY_STYLES}


def split_negative(prompt: str) -> tuple[str, str]:
    """切分正向/负向提示词，与 modules.image_styles.split_negative 同语义。"""
    text = prompt or ""
    index = text.find(NEGATIVE_MARKER)
    if index < 0:
        return text.strip(), ""
    return text[:index].strip(), text[index + len(NEGATIVE_MARKER):].strip()


def migrate(db) -> dict:
    """执行迁移并返回统计信息。可重复调用（幂等）。"""
    stats = {
        "legacy_prompts_kept": 0,
        "styles_inserted": 0,
        "styles_skipped": 0,
    }

    # 历史私有提示词本就在 prompt_templates：新列由地基 server_default 兜底，
    # owner 值仅作来源追溯，不再影响可见性，这里只统计不改写。
    stats["legacy_prompts_kept"] = len(
        db.scalars(
            select(models.PromptTemplate.id).where(
                models.PromptTemplate.owner_user_id.is_not(None)
            )
        ).all()
    )

    # 老库可能没有 image_styles 表（全新库），直接跳过。
    if "image_styles" not in inspect(db.bind).get_table_names():
        return stats

    rows = db.scalars(
        select(models.ImageStyle).order_by(
            models.ImageStyle.sort_order.asc(), models.ImageStyle.id.asc()
        )
    ).all()

    for row in rows:
        prompt_type = PURPOSE_TO_TYPE.get(row.purpose, "base")
        positive, negative = split_negative(row.prompt)
        if not positive:
            positive = (row.prompt or "").strip()

        duplicate = db.scalar(
            select(models.PromptTemplate.id).where(
                models.PromptTemplate.title == row.name,
                models.PromptTemplate.owner_user_id.is_(None),
            )
        )
        if duplicate is not None:
            stats["styles_skipped"] += 1
            continue

        db.add(
            models.PromptTemplate(
                owner_user_id=None,
                title=row.name,
                category="封面" if prompt_type == "cover" else "风格",
                content=positive,
                tags=["style", row.purpose],
                prompt_type=prompt_type,
                negative_content=negative,
                summary=(row.summary or "").strip(),
                sort_order=row.sort_order or 0,
                enabled=bool(row.enabled),
                source="factory" if row.name in _FACTORY_NAMES else "manual",
            )
        )
        stats["styles_inserted"] += 1

    if stats["styles_inserted"]:
        db.commit()
    return stats


def main() -> None:
    with SessionLocal() as db:
        stats = migrate(db)
    print(
        "迁移完成：历史提示词保留 {legacy_prompts_kept} 条，"
        "风格迁入 {styles_inserted} 条，判重跳过 {styles_skipped} 条。".format(**stats)
    )


if __name__ == "__main__":
    main()
