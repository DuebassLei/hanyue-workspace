"""清理历史遗留的“无归属自定义 Skill”。

旧版创建路径（workspaces.register_skill）没有写入 owner_user_id，
这些行对所有账号可见，却没有任何账号能编辑、启用或删除。
新版本已经不再展示和解析它们，但数据库里仍然占位。

本脚本默认只打印清单，加 --delete 才会删除（连同工作区授权），避免误删。

用法（在 apps/api 下执行）：
    python scripts/repair_skills.py            # 只报告
    python scripts/repair_skills.py --delete   # 删除
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal, init_db  # noqa: E402
from app.models import SkillRegistryEntry, WorkspaceSkill  # noqa: E402
from sqlalchemy import select  # noqa: E402


def _orphans(db):
    return list(
        db.scalars(
            select(SkillRegistryEntry).where(
                SkillRegistryEntry.source == "custom",
                SkillRegistryEntry.owner_user_id.is_(None),
            )
        ).all()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="清理无归属自定义 Skill")
    parser.add_argument(
        "--delete", action="store_true", help="真正删除（默认只打印清单）"
    )
    args = parser.parse_args()

    init_db()
    with SessionLocal() as db:
        rows = _orphans(db)
        if not rows:
            print("没有发现无归属自定义 Skill。")
            return
        print(f"发现 {len(rows)} 条无归属自定义 Skill：")
        for row in rows:
            grants = db.scalars(
                select(WorkspaceSkill).where(
                    WorkspaceSkill.skill_entry_id == row.id
                )
            ).all()
            print(
                f"  - {row.skill_key}（{row.display_name or '未命名'}）"
                f" · checksum={row.checksum[:12]} · 授权 {len(grants)} 条"
            )
        if not args.delete:
            print("\n未删除。确认无误后加 --delete 重新执行。")
            return
        for row in rows:
            for grant in db.scalars(
                select(WorkspaceSkill).where(
                    WorkspaceSkill.skill_entry_id == row.id
                )
            ).all():
                db.delete(grant)
            db.delete(row)
        db.commit()
        print(f"\n已删除 {len(rows)} 条无归属 Skill 及其授权。")


if __name__ == "__main__":
    main()
