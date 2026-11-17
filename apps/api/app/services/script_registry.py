"""定时任务类型加载器：manifest 是任务类型的唯一真相来源。

职责：
- 读 app/resources/scripts/manifest.yaml，把每条声明变成一个 TaskType；
- 用声明式 fields 动态生成 Pydantic 参数模型，复用 scheduled_tasks 的校验/字段导出；
- 按 runner 路径 import 脚本函数并校验可调用，配置写错在加载期就抛错，绝不留到运行期；
- 暴露 runner 查询供 job_runner 的通用 script 队列调用。

「类型从代码写死」改成「声明驱动」后，新增一种定时任务 = 一个脚本模块 + 一条 manifest，
前端类型下拉和参数表单自动出现。脚本运行在服务端进程内，manifest 只能由部署方维护。
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Callable, Optional

import yaml
from pydantic import ConfigDict, Field, create_model

from ..core.config import BASE_DIR
from ..core.logging import get_logger
from . import scheduled_tasks as st

logger = get_logger("app.script_registry")

MANIFEST_PATH = BASE_DIR / "app" / "resources" / "scripts" / "manifest.yaml"

# manifest 字段 type → Python 类型。新增一种控件类型时前后端要同时支持，这里先拒绝未知的。
_FIELD_TYPES: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list[str],
}

# options_from：从服务端词表动态取候选项，避免把会变的词表硬编码进 manifest。
def _hot_topics_tracks() -> list[str]:
    from . import hot_ranking

    return list(hot_ranking.ALL_TRACKS)


_OPTION_SOURCES: dict[str, Callable[[], list[str]]] = {
    "hot_topics_tracks": _hot_topics_tracks,
}


class ManifestError(RuntimeError):
    """manifest 配置错误：启动期抛出，明确告诉维护者哪一条写错了。"""


def _build_params_model(key: str, fields: list[dict]):
    """把声明式 fields 编译成 Pydantic 模型。

    字段结构与前端 SchedulesView 的控件一一对应；生成的 JSON Schema 再经
    scheduled_tasks.params_fields 归一化成前端能直接渲染的一层结构。
    枚举取值的合法性由 validate_params 统一按字段 options 校验（见 scheduled_tasks）。
    """
    definitions: dict[str, tuple[Any, Any]] = {}
    for field in fields:
        if not isinstance(field, dict) or not field.get("name"):
            raise ManifestError(f"{key}: 每个 field 都必须有 name")
        name = field["name"]
        ftype = field.get("type", "string")
        if ftype not in _FIELD_TYPES:
            raise ManifestError(f"{key}.{name}: 未知字段类型 {ftype!r}，可选 {list(_FIELD_TYPES)}")

        # 类型严格按声明走：array 字段即使配了 options 也要保持 array，
        # 前端才会渲染成多选下拉；string + options 则是单选下拉。
        py_type = _FIELD_TYPES[ftype]

        nullable = field.get("nullable", False) or field.get("default", ...) is None
        annotation = Optional[py_type] if nullable else py_type

        default = ... if field.get("required") else field.get("default")
        kwargs: dict[str, Any] = {}
        if field.get("title"):
            kwargs["title"] = field["title"]
        if field.get("description"):
            kwargs["description"] = field["description"]
        if "min" in field and ftype in ("integer", "number"):
            kwargs["ge"] = field["min"]
        if "max" in field and ftype in ("integer", "number"):
            kwargs["le"] = field["max"]

        options = field.get("options")
        if field.get("options_from"):
            source = _OPTION_SOURCES.get(field["options_from"])
            if source is None:
                raise ManifestError(
                    f"{key}.{name}: 未知 options_from {field['options_from']!r}，"
                    f"可选 {list(_OPTION_SOURCES)}"
                )
            options = source()
        if options:
            options = [str(item) for item in options]
            kwargs["json_schema_extra"] = {
                "options": options,
                "item_label": field.get("item_label", "取值"),
            }

        definitions[name] = (annotation, Field(default, **kwargs))

    # extra=forbid：拼错参数名要当场报错，不能静默忽略后「看着成功其实没生效」
    return create_model(
        f"{key.replace('.', '_')}_params",
        __config__=ConfigDict(extra="forbid"),
        **definitions,
    )


def _resolve_runner(key: str, runner: str) -> Callable[[dict, Any], dict]:
    """按「模块路径:函数名」import 并校验，配置错误在加载期就炸。"""
    if not runner or ":" not in runner:
        raise ManifestError(f"{key}: runner 必须写成「模块路径:函数名」，实际是 {runner!r}")
    module_path, func_name = runner.split(":", 1)
    try:
        module = importlib.import_module(module_path)
    except Exception as exc:
        raise ManifestError(f"{key}: 无法导入脚本模块 {module_path!r}：{exc}") from exc
    func = getattr(module, func_name, None)
    if func is None or not callable(func):
        raise ManifestError(f"{key}: 模块 {module_path!r} 里没有可调用的函数 {func_name!r}")
    return func


def _build_payload_for(key: str):
    """生成一个闭包，把校验后的参数模型转成 job payload 的参数部分。"""
    def build(params) -> dict:
        data = params.model_dump(exclude_none=True)
        return data

    build.__name__ = f"build_payload_{key.replace('.', '_')}"
    return build


def load_all() -> int:
    """读取 manifest 并把所有声明注册成 TaskType。可重复调用（幂等）。

    幂等做法：先摘掉本加载器关心的 key，再注册。register 仍保持「重复即报错」的
    严格语义（有测试专门验证），这里不放松它，而是在调用前清场。
    """
    if not MANIFEST_PATH.is_file():
        raise ManifestError(f"定时任务 manifest 不存在：{MANIFEST_PATH}")

    raw = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8")) or {}
    entries = raw.get("scripts") or []
    if not isinstance(entries, list):
        raise ManifestError("manifest.scripts 必须是列表")

    seen: set[str] = set()
    built: list[st.TaskType] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ManifestError("manifest.scripts 的每一项都必须是映射")
        key = (entry.get("key") or "").strip()
        if not key:
            raise ManifestError("有一条脚本声明缺少 key")
        if key in seen:
            raise ManifestError(f"manifest 里 key 重复：{key}")
        seen.add(key)

        scope = (entry.get("scope") or "user").strip()
        if scope not in ("system", "user"):
            raise ManifestError(f"{key}: scope 只能是 system/user，实际 {scope!r}")

        fields = entry.get("fields") or []
        if not isinstance(fields, list):
            raise ManifestError(f"{key}: fields 必须是列表")

        runner = _resolve_runner(key, (entry.get("runner") or "").strip())
        params_model = _build_params_model(key, fields)
        built.append(
            st.TaskType(
                key=key,
                title=(entry.get("title") or key).strip(),
                scope=scope,
                queue=(entry.get("queue") or ("script" if scope == "user" else key)).strip(),
                kind=(entry.get("kind") or key).strip(),
                params_model=params_model,
                build_payload=_build_payload_for(key),
                description=(entry.get("description") or "").strip(),
                default_cron=(entry.get("default_cron") or "0 8 * * *").strip(),
            )
        )
        # runner 存在 _RUNNERS，供 script 队列统一执行；非 script 队列的脚本
        # （热点两条）仍走各自的专用 handler，runner 留着备用不冲突。
        _RUNNERS[key] = runner

    for spec in built:
        st.TASK_TYPES.pop(spec.key, None)
    for spec in built:
        st.register(spec)
    logger.info("定时任务类型已从 manifest 装载 %s 个", len(built))
    return len(built)


# key → 脚本函数。由 load_all 填充；job_runner 的 script handler 按 kind 查。
_RUNNERS: dict[str, Callable[[dict, Any], dict]] = {}


def runner_for_kind(kind: str) -> Callable[[dict, Any], dict] | None:
    """按 job.kind 找脚本函数；非脚本类型返回 None。"""
    return _RUNNERS.get((kind or "").strip())


def runner_for_key(key: str) -> Callable[[dict, Any], dict] | None:
    return _RUNNERS.get((key or "").strip())
