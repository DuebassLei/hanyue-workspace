"""三类采集器模板：JsonAdapter / TextPatternAdapter / ScriptAdapter。

模板只做「取数 → 调解析函数 → 产出 CollectResult」这件事。单源失败一律返回
ok=false + error_code，不向外抛异常；编排器负责汇总 notes 与判定全源失败。

新增平台见 catalog.yaml 顶部维护规则：
- 结构常见的 JSON 源：catalog 里配 parser=direct + 字段路径，0 行代码；
- Markdown/HTML 源：选 parser=aihot_agent / wechat_search / openrank；
- 完全自定义的源：写 app/hot_sources/scripts/ 下的脚本函数，kind=script + adapter=模块:函数。
"""

from __future__ import annotations

import importlib
import os
from typing import Any
from urllib.parse import quote

from . import parsers
from .contracts import CollectContext, CollectResult, HotRawItem, HotSourcesError


def _failed(platform: str, code: str, message: str, fetched_at: str) -> CollectResult:
    return CollectResult(
        platform=platform,
        ok=False,
        notes=[f"{platform}：失败（{code}）{message}"],
        error_code=code,
        fetched_at=fetched_at,
    )


def _rows_to_result(
    platform: str, rows: list[dict], fetched_at: str, source: dict[str, Any]
) -> CollectResult:
    """原始行 → 契约对象；解析到 0 行时显式标注 selector_may_changed。"""
    items = [
        HotRawItem(
            platform=row.get("platform") or platform,
            title=str(row.get("title") or "").strip(),
            captured_at=fetched_at,
            rank=row.get("rank"),
            hot=row.get("hot"),
            url=str(row.get("url") or ""),
            extra=row.get("extra") or {},
        )
        for row in rows
        if str(row.get("title") or "").strip()
    ]
    notes = [f"{platform}：{len(items)} 条"]
    if not items and not source.get("allow_empty"):
        notes.append(f"{platform}：selector_may_changed（解析到 0 条，页面结构可能改版）")
    return CollectResult(platform=platform, ok=True, items=items, notes=notes, fetched_at=fetched_at)


class JsonAdapter:
    """直取 JSON / 聚合 JSON / AIHOT JSON / UAPI 共用模板。"""

    kind = "json"

    def collect(self, ctx: CollectContext) -> CollectResult:
        source = ctx.config
        platform = source["name"]
        fetched_at = ctx.now()
        try:
            response = ctx.safe_get(source["url"], headers=source.get("headers"))
        except HotSourcesError as exc:
            return _failed(platform, exc.code, exc.message, fetched_at)

        try:
            payload = response.json()
        except ValueError:
            return _failed(platform, "invalid_response", "响应不是 JSON", fetched_at)

        parser = source.get("parser")
        options = source.get("options") or {}
        try:
            if parser == "direct":
                rows = parsers._rows_direct(
                    platform,
                    payload,
                    tuple(str(x) for x in options.get("items_path", [])),
                    tuple(str(x) for x in options.get("title_keys", [])),
                    tuple(str(x) for x in options.get("hot_keys", [])),
                    tuple(str(x) for x in options.get("url_keys", [])),
                )
            elif parser == "agg":
                rows = parsers._rows_agg(platform, payload)
            elif parser == "aihot":
                rows = parsers._rows_aihot(platform, payload, options.get("mode", "hot"))
            elif parser == "wechat_10w":
                rows = parsers._rows_wechat_10w(payload)
            elif parser == "uapi":
                rows = parsers._rows_uapi(platform, payload)
            else:
                return _failed(platform, "unknown_parser", f"未注册的解析器 {parser}", fetched_at)
        except Exception as exc:  # noqa: BLE001 - 解析异常属于单源降级，不拖垮整页
            return _failed(platform, "adapter_error", f"{type(exc).__name__}", fetched_at)
        return _rows_to_result(platform, rows, fetched_at, source)


class TextPatternAdapter:
    """Markdown / HTML 正则模板。解析 0 行按 selector_may_changed 标注。"""

    kind = "markdown"

    def collect(self, ctx: CollectContext) -> CollectResult:
        source = ctx.config
        platform = source["name"]
        fetched_at = ctx.now()
        options = source.get("options") or {}
        parser = source.get("parser")

        # 微信文章搜索：关键词来自环境变量，没配就跳过（不算失败，也不替用户搜）
        keyword_env = source.get("keyword_env")
        if parser == "wechat_search" and keyword_env:
            keyword = os.getenv(keyword_env, "").strip()
            if not keyword:
                return CollectResult(
                    platform=platform,
                    ok=True,
                    notes=[f"{platform}：未配置关键词（{keyword_env}），已跳过"],
                    fetched_at=fetched_at,
                )
            url = str(source["url"]).replace("{keyword}", quote(keyword))
        else:
            url = source["url"]

        try:
            response = ctx.safe_get(url, headers=source.get("headers"))
        except HotSourcesError as exc:
            return _failed(platform, exc.code, exc.message, fetched_at)
        text = response.text

        try:
            if parser == "aihot_agent":
                rows = parsers._rows_aihot_agent(platform, text, options.get("mode", "hot"))
            elif parser == "wechat_search":
                rows = parsers._rows_wechat_search(text)
            elif parser == "openrank":
                rows = parsers._rows_openrank(platform, text)
            else:
                return _failed(platform, "unknown_parser", f"未注册的解析器 {parser}", fetched_at)
        except Exception as exc:  # noqa: BLE001
            return _failed(platform, "adapter_error", f"{type(exc).__name__}", fetched_at)
        return _rows_to_result(platform, rows, fetched_at, source)


class ScriptAdapter:
    """受控的进程内扩展点：加载 catalog.adapter 指向的函数，产出 CollectResult。

    与 manifest 的哲学一致——写脚本模块 + 一行注册、随代码部署、走代码评审，
    不是在线执行用户提交的任意代码。脚本只能通过 ctx 拿数据。
    """

    kind = "script"

    def collect(self, ctx: CollectContext) -> CollectResult:
        source = ctx.config
        platform = source["name"]
        fetched_at = ctx.now()
        target = str(source.get("adapter") or "")
        module_path, _, func_name = target.partition(":")
        if not module_path or not func_name:
            return _failed(platform, "config_missing", "adapter 未配置为 模块:函数", fetched_at)
        try:
            module = importlib.import_module(module_path)
            collect_fn = getattr(module, func_name)
            result = collect_fn(ctx)
        except Exception as exc:  # noqa: BLE001
            return _failed(platform, "adapter_error", f"{type(exc).__name__}", fetched_at)
        if not isinstance(result, CollectResult):
            return _failed(platform, "invalid_result", "脚本未返回 CollectResult", fetched_at)
        return result


_ADAPTERS = {
    "json": JsonAdapter,
    "markdown": TextPatternAdapter,
    "html": TextPatternAdapter,
    "script": ScriptAdapter,
}


def adapter_for(source: dict[str, Any]):
    """按 catalog.kind 选模板；未知类型返回 None，由编排器记 notes 跳过。"""
    cls = _ADAPTERS.get(source.get("kind"))
    return cls() if cls else None
