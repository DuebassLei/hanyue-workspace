"""真 keyset 分页（设计 §10 / §11）。

旧实现是「先取 limit+1 条，再在内存里找 cursor 的位置切片」，翻到第 2 页
就读不到全表第 limit+2 条之后的数据，`nextCursor` 也基本恒为 None。

这里改为把游标编码成 (排序字段值, id)，在 SQL 里加 seek 谓词：

    WHERE (sort_col, id) < (:value, :id)   -- 降序
    ORDER BY sort_col DESC, id DESC
    LIMIT limit + 1

复合第二排序键 id 保证同时间戳下顺序稳定；游标失效/损坏时明确报 400，
不静默退化成第一页。列表响应仍保持原有形状（裸数组或既有对象），
下一页令牌通过 `X-Next-Cursor` 响应头返回，不破坏任何现有前端与测试。
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from fastapi import Response
from sqlalchemy import Select, and_, or_
from sqlalchemy.orm import Session

CURSOR_HEADER = "X-Next-Cursor"


class InvalidCursor(ValueError):
    """游标无法解析：明确报错，不回退到第一页。"""


def encode_cursor(row: Any, sort_column: Any) -> str:
    value = getattr(row, sort_column.key)
    if isinstance(value, datetime):
        value = value.isoformat()
    else:
        value = str(value)
    raw = json.dumps({"v": value, "i": row.id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(token: str) -> tuple[str, str] | None:
    try:
        padded = token + "=" * (-len(token) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode())
        value, row_id = data["v"], data["i"]
        if not isinstance(value, str) or not isinstance(row_id, str) or not row_id:
            return None
        return value, row_id
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return None


def _typed_value(raw: str, sort_column: Any) -> Any:
    """把游标里的字符串还原成与排序列同类型的值（用列类型而非实例判断）。"""
    try:
        python_type = sort_column.type.python_type
    except (AttributeError, NotImplementedError):
        python_type = str
    if isinstance(python_type, type) and issubclass(python_type, datetime):
        try:
            return datetime.fromisoformat(raw)
        except ValueError as exc:
            raise InvalidCursor("分页游标的时间戳无效") from exc
    return raw


def paginate(
    db: Session,
    stmt: Select,
    model: Any,
    *,
    sort_column: Any,
    cursor: str | None,
    limit: int,
    descending: bool = True,
) -> tuple[list, str | None]:
    """执行 keyset 查询，返回 (当前页行, 下一页游标)。

    调用方不要在 stmt 上再写 order_by / limit；已有 where 条件会被保留。
    """
    direction = sort_column.desc() if descending else sort_column.asc()
    id_direction = model.id.desc() if descending else model.id.asc()

    if cursor:
        point = decode_cursor(cursor)
        if point is None:
            raise InvalidCursor("分页游标无效或已损坏")
        raw_value, row_id = point
        # 用列类型推断，避免把时间戳当字符串比较（PostgreSQL 下会类型不符）
        value = _typed_value(raw_value, sort_column)
        if descending:
            predicate = or_(
                sort_column < value,
                and_(sort_column == value, model.id < row_id),
            )
        else:
            predicate = or_(
                sort_column > value,
                and_(sort_column == value, model.id > row_id),
            )
        stmt = stmt.where(predicate)

    stmt = stmt.order_by(direction, id_direction).limit(limit + 1)
    rows = list(db.scalars(stmt).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(rows[-1], sort_column) if has_more and rows else None
    return rows, next_cursor


def set_cursor_header(response: Response | None, token: str | None) -> None:
    if response is not None and token:
        response.headers[CURSOR_HEADER] = token
