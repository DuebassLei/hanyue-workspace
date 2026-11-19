"""全局提示词规则（`prompt_rules`）的读取层，带进程内短缓存。

提示词与词表在每次生成时都会被读，直接查库会把一个请求放大成十几次 SELECT；
一颗改动过的词条要等到下次请求才生效也可以接受，但不能接受「管理端改了、生成还在用旧值」，
所以管理端写接口在提交后调用 `invalidate_cache()`，下一次读取立即回源。

缓存按 `(id(db), key)` 记账：不同会话、不同测试库互不串值；同一个会话里换了库不会发生。
TTL 只是兜底——防止绕过写接口直接改库（脚本 / 迁移）后旧值滞留过久。
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select

from .. import models

_DEFAULT_CACHE_TTL = 60  # 秒
_CACHE: dict[tuple[int, str], tuple[float, Any]] = {}


def _now() -> float:
    return time.monotonic()


def invalidate_cache(db=None) -> None:
    """清空缓存。传 db 时只清该会话的条目，不传则全清（管理端写入后的默认用法）。"""
    if db is None:
        _CACHE.clear()
        return
    prefix = id(db)
    for cache_key in [key for key in _CACHE if key[0] == prefix]:
        _CACHE.pop(cache_key, None)


def _load(db, key: str) -> dict | None:
    cache_key = (id(db), key)
    hit = _CACHE.get(cache_key)
    now = _now()
    if hit is not None and now - hit[0] < _DEFAULT_CACHE_TTL:
        return hit[1]
    # 读路径不触发自动 flush：调用方手里的未提交改动不该被一次提示词读取顺带落库
    with db.no_autoflush:
        row = db.scalar(select(models.PromptRule).where(models.PromptRule.key == key))
    value = None
    if row is not None and row.enabled:
        value = {"content": row.content or "", "payload": dict(row.payload or {})}
    _CACHE[cache_key] = (now, value)
    return value


def get_prompt_rule(db, key: str, default: str = "") -> str:
    """取提示词正文；缺失或已停用返回 default（调用方据此回退到代码常量）。"""
    value = _load(db, key)
    if value is None:
        return default
    return value["content"] or default


def get_prompt_payload(db, key: str) -> dict:
    """取结构化部分（词表 / 句式等）；缺失或已停用返回空 dict。"""
    value = _load(db, key)
    if value is None:
        return {}
    return dict(value["payload"])
