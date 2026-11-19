"""源注册表的读取层：catalog.yaml 是唯一真相源。

enable/过滤、界面清单、编排器遍历都走这里，消除「文档写 7 路、代码 22 平台」的漂移。
不缓存解析结果：env 覆盖需要在测试与运行时即时生效，而 22 条 YAML 的解析成本
相对网络采集可以忽略。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

CATALOG_PATH = Path(__file__).with_name("catalog.yaml")
_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(text: str) -> str:
    """把 url 里的 ${VAR} 展开；未配置的环境变量展开为空串。"""
    return _ENV_REF.sub(lambda m: os.getenv(m.group(1), ""), text or "")


def load_catalog() -> dict[str, Any]:
    """读取并归一化 catalog.yaml。每次调用都重新读，保证 env 覆盖即时生效。"""
    with CATALOG_PATH.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    sources: list[dict] = []
    for entry in raw.get("sources", []):
        source = dict(entry)
        source["url"] = _expand_env(str(source.get("url", "")))
        # url_env 显式配置时优先级最高：快照地址失效可运维替换，不改代码
        url_env = source.get("url_env")
        if url_env and os.getenv(url_env, "").strip():
            source["url"] = os.getenv(url_env, "").strip()
        source.setdefault("enabled", True)
        source.setdefault("retry", 1)
        source.setdefault("timeout_seconds", None)
        source.setdefault("tags", [])
        source.setdefault("options", {})
        source.setdefault("headers", {})
        source.setdefault("requires_ssrf_safe", True)
        sources.append(source)
    return {"version": raw.get("version", 1), "sources": sources}


def all_platforms() -> list[str]:
    return [source["name"] for source in load_catalog()["sources"]]


def enabled_platforms() -> list[str]:
    """启用的平台名。`HOT_TOPICS_PLATFORMS=微博,知乎` 可只跑一部分（调试/降噪）。"""
    raw = os.getenv("HOT_TOPICS_PLATFORMS", "").strip()
    wanted = {part.strip() for part in raw.split(",") if part.strip()} if raw else None
    return [
        source["name"]
        for source in load_catalog()["sources"]
        if source["enabled"] and (wanted is None or source["name"] in wanted)
    ]


def enabled_sources(platforms: list[str] | None = None) -> list[dict]:
    """编排器遍历用：按平台名过滤后的启用源声明（保持 catalog 顺序）。"""
    names = enabled_platforms() if platforms is None else platforms
    wanted = set(names or [])
    return [
        source
        for source in load_catalog()["sources"]
        if source["enabled"] and source["name"] in wanted
    ]


def source_catalog() -> list[dict]:
    """给界面用的源清单（哪些平台会被采集及其类型）。"""
    return [
        {"platform": source["name"], "kind": source["kind"]}
        for source in load_catalog()["sources"]
    ]
