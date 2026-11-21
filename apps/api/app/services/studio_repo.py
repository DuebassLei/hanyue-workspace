"""成稿台的仓储层：文章头 + 段落 + 快照，在 ORM 与纯 dict 之间搬运。

服务层只处理 dict（见 `article_blocks`），落库与读库集中在这里，
避免段落操作函数沾上 Session 生命周期，单测可以直接跑纯函数。
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from sqlalchemy import delete, select

from .. import models
from . import article_blocks

HISTORY_MAX = 20

# 需要与 dict 同步的标量字段（段落之外的都算）
SCALARS = (
    "topic",
    "material",
    "front",
    "title",
    "hook",
    "titles",
    "summary",
    "banner_style",
    "banners",
    "layout_options",
    "follow_card",
    "zhuque",
    "chat",
    "notice",
    "cover",
    "inline_diagrams",
    "scheduled_for",
    "published_at",
    "publish_marked_at",
    "archived",
    "archived_at",
    "last_view",
)


def row_to_dict(row: models.StudioArticle) -> dict:
    data = {"id": row.id, "content_item_id": row.content_item_id}
    for key in SCALARS:
        value = getattr(row, key)
        if key in ("titles", "chat", "inline_diagrams") and value is None:
            value = []
        if key in ("banners", "layout_options", "zhuque", "cover") and value is None:
            value = {}
        data[key] = copy.deepcopy(value) if isinstance(value, (dict, list)) else value
    data["blocks"] = [
        {
            "id": block.id,
            "text": block.text,
            "kind": block.kind,
            "check": copy.deepcopy(block.check),
            "prev_text": block.prev_text,
            "inserted_diagram_id": block.inserted_diagram_id,
        }
        for block in sorted(row.blocks, key=lambda item: item.ordinal)
    ]
    data["created_at"] = row.created_at.isoformat() if row.created_at else None
    data["updated_at"] = row.updated_at.isoformat() if row.updated_at else None
    return data


_DATETIME_FIELDS = ("scheduled_for", "published_at", "publish_marked_at", "archived_at")


def _parse_dt(value: str):
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def save(db, row: models.StudioArticle, data: dict) -> None:
    """把 dict 写回数据库。段落整表替换（id 保持不变）。"""
    for key in SCALARS:
        if key in data:
            value = data[key]
            if key in _DATETIME_FIELDS and isinstance(value, str):
                value = _parse_dt(value)
            setattr(row, key, copy.deepcopy(value) if isinstance(value, (dict, list)) else value)
    if data.get("updated_at") and isinstance(data["updated_at"], str):
        try:
            row.updated_at = datetime.fromisoformat(data["updated_at"])
        except ValueError:
            row.updated_at = datetime.now(timezone.utc)
    _replace_blocks(db, row, data.get("blocks", []))


def _replace_blocks(db, row: models.StudioArticle, blocks: list[dict]) -> None:
    db.execute(delete(models.ArticleBlock).where(models.ArticleBlock.article_id == row.id))
    for ordinal, block in enumerate(blocks):
        db.add(
            models.ArticleBlock(
                id=block["id"],
                article_id=row.id,
                ordinal=ordinal,
                text=block.get("text", ""),
                kind=block.get("kind") or article_blocks.block_kind(block.get("text", "")),
                check=copy.deepcopy(block.get("check")),
                prev_text=block.get("prev_text"),
                inserted_diagram_id=block.get("inserted_diagram_id"),
            )
        )
    db.flush()
    db.expire(row, ["blocks"])


def create(
    db,
    *,
    owner_user_id: str,
    workspace_id: str | None = None,
    topic: str = "",
    material: str = "",
    markdown: str = "",
) -> models.StudioArticle:
    data = article_blocks.build_article(topic=topic, material=material, markdown=markdown)
    row = models.StudioArticle(
        owner_user_id=owner_user_id,
        workspace_id=workspace_id,
        topic=data["topic"],
        material=data["material"],
        front=data["front"],
        title=data["title"],
        titles=[],
        chat=[],
        zhuque={"round": 0, "lastAt": None},
        banners={},
        layout_options={},
        cover={},
        inline_diagrams=[],
        banner_style="poster",
        last_view="paper",
    )
    db.add(row)
    db.flush()
    _replace_blocks(db, row, data["blocks"])
    return row


def snapshot(db, row: models.StudioArticle, data: dict, label: str) -> models.ArticleRevision:
    """改动前存一份快照。超出上限时丢最旧的那条。"""
    revision = models.ArticleRevision(
        article_id=row.id,
        label=label[:120],
        payload={
            **article_blocks.snapshot_payload(data),
            "label": label[:120],
            "at": article_blocks.now_iso(),
        },
    )
    db.add(revision)
    db.flush()
    rows = db.scalars(
        select(models.ArticleRevision)
        .where(models.ArticleRevision.article_id == row.id)
        .order_by(models.ArticleRevision.created_at.asc())
    ).all()
    if len(rows) > HISTORY_MAX:
        for old in rows[: len(rows) - HISTORY_MAX]:
            db.delete(old)
        db.flush()
    return revision


def drop_last_snapshot(db, article_id: str) -> None:
    """一次改动最终什么都没变时，把刚存的快照删掉，别在历史里留空条目。"""
    last = db.scalar(
        select(models.ArticleRevision)
        .where(models.ArticleRevision.article_id == article_id)
        .order_by(models.ArticleRevision.created_at.desc())
        .limit(1)
    )
    if last:
        db.delete(last)
        db.flush()


def history(db, article_id: str) -> list[dict]:
    rows = db.scalars(
        select(models.ArticleRevision)
        .where(models.ArticleRevision.article_id == article_id)
        .order_by(models.ArticleRevision.created_at.asc())
    )
    return [{"label": row.label, "at": row.payload.get("at") or row.created_at.isoformat(), "payload": row.payload} for row in rows]


def pop_last(db, article_id: str) -> dict | None:
    last = db.scalar(
        select(models.ArticleRevision)
        .where(models.ArticleRevision.article_id == article_id)
        .order_by(models.ArticleRevision.created_at.desc())
        .limit(1)
    )
    if not last:
        return None
    payload = {"label": last.label, "payload": last.payload}
    db.delete(last)
    db.flush()
    return payload


def get_article(db, article_id: str, *, owner_user_id: str | None = None) -> models.StudioArticle | None:
    row = db.get(models.StudioArticle, article_id)
    if not row:
        return None
    if owner_user_id and row.owner_user_id != owner_user_id:
        return None
    return row
