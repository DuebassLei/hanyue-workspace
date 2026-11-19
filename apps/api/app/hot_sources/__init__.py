"""热榜采集通用平台（需求 01）。

对外只暴露两件事：
- 契约：HotRawItem / CollectResult / CollectContext；
- 编排：collect(platforms) -> (rows, notes)，签名与旧 services/hot_sources.collect 一致。

新增平台 = catalog.yaml 加一条声明（必要时加一个 ScriptAdapter 脚本），
本文件与 adapters.py 的采集主流程零改动。
"""

from __future__ import annotations

import os

from . import catalog
from .adapters import adapter_for
from .contracts import CollectContext, CollectResult, HotRawItem, HotSourcesError

__all__ = [
    "CollectContext",
    "CollectResult",
    "HotRawItem",
    "HotSourcesError",
    "adapter_for",
    "all_platforms",
    "collect",
    "enabled_platforms",
    "enabled_sources",
    "load_catalog",
    "source_catalog",
]


def _global_timeout() -> float:
    try:
        return max(3.0, float(os.getenv("HOT_TOPICS_TIMEOUT", "12")))
    except ValueError:
        return 12.0


def _source_timeout(source: dict) -> float:
    value = source.get("timeout_seconds")
    if value is None:
        return _global_timeout()
    try:
        return max(3.0, float(value))
    except (TypeError, ValueError):
        return _global_timeout()


def _source_retry(source: dict) -> int:
    try:
        return max(0, int(source.get("retry", 1) or 0))
    except (TypeError, ValueError):
        return 1


def collect(platforms: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """按 catalog 遍历启用源，统一超时/重试，汇总 rows 与 notes。

    单源失败降级为 notes，不影响其它源；全部源都不可用时抛 HotSourcesError，
    由 services/hot_topics.refresh 转成 502（不返回「0 个事件」的假成功）。
    """
    wanted = platforms if platforms is not None else catalog.enabled_platforms()
    rows: list[dict] = []
    notes: list[str] = []
    ok = 0

    for source in catalog.enabled_sources(wanted):
        name = source["name"]
        adapter = adapter_for(source)
        if adapter is None:
            notes.append(f"{name}：未知采集类型（{source.get('kind')}）")
            continue
        ctx = CollectContext(
            source=source,
            timeout=_source_timeout(source),
            retries=_source_retry(source),
        )
        try:
            result = adapter.collect(ctx)
        except Exception as exc:  # noqa: BLE001 - 兜底：任何意外都只降级该源
            result = CollectResult(
                platform=name,
                ok=False,
                notes=[f"{name}：失败（{type(exc).__name__}）"],
                error_code="adapter_error",
                fetched_at=ctx.now(),
            )
        if result.ok:
            ok += 1
            rows.extend(item.to_row() for item in result.items)
        notes.extend(result.notes)

    if ok == 0:
        raise HotSourcesError("所有热点源都不可用：" + "；".join(notes), notes)
    return rows, notes


# 兼容层常用的派生清单，避免调用方各自拼装
all_platforms = catalog.all_platforms
enabled_platforms = catalog.enabled_platforms
enabled_sources = catalog.enabled_sources
load_catalog = catalog.load_catalog
source_catalog = catalog.source_catalog
