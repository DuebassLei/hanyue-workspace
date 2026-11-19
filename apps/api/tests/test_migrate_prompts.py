"""提示词合并迁移脚本：拆分负向句、类型映射、幂等判重。"""

from __future__ import annotations

from sqlalchemy import select

from app import models
from app.core.db import SessionLocal
from app.scripts.migrate_prompts import migrate


def test_migrate_styles_splits_negative_and_is_idempotent():
    with SessionLocal() as db:
        db.add(
            models.ImageStyle(
                name="迁移测试封面",
                purpose="cover",
                prompt="正向画面描述画面避免出现：小字、杂乱背景",
                summary="迁移摘要",
                sort_order=2,
                enabled=True,
            )
        )
        db.add(
            models.ImageStyle(
                name="迁移测试配图",
                purpose="inline",
                prompt="插画描述",
                sort_order=1,
                enabled=True,
            )
        )
        db.commit()

        first = migrate(db)
        assert first["styles_inserted"] >= 2
        assert first["styles_skipped"] >= 1  # 出厂风格已在启动播种，判重跳过

        cover = db.scalar(
            select(models.PromptTemplate).where(models.PromptTemplate.title == "迁移测试封面")
        )
        assert cover is not None
        assert cover.prompt_type == "cover"
        assert cover.content == "正向画面描述"
        assert cover.negative_content == "小字、杂乱背景"
        assert cover.summary == "迁移摘要"
        assert cover.sort_order == 2
        assert cover.owner_user_id is None
        assert cover.source == "manual"

        inline = db.scalar(
            select(models.PromptTemplate).where(models.PromptTemplate.title == "迁移测试配图")
        )
        assert inline is not None
        assert inline.prompt_type == "illustration"
        assert inline.negative_content == ""

        second = migrate(db)
        assert second["styles_inserted"] == 0
        duplicates = len(
            db.scalars(
                select(models.PromptTemplate.id).where(
                    models.PromptTemplate.title.in_(["迁移测试封面", "迁移测试配图"])
                )
            ).all()
        )
        assert duplicates == 2


def test_factory_negative_is_split_in_seeded_template():
    """启动播种的出厂封面模板已拆出负向字段，正向不再含标记。"""
    with SessionLocal() as db:
        factory = db.scalar(
            select(models.PromptTemplate).where(
                models.PromptTemplate.title == "公众号爆款封面·Q版女仆桶"
            )
        )
        assert factory is not None
        assert factory.prompt_type == "cover"
        assert factory.source == "factory"
        assert "画面避免出现" not in factory.content
        assert "小字、细节过多的背景" in factory.negative_content
