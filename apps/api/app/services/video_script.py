"""把文案拆成视频解说分镜草稿。没有可用模型时明确失败，不写占位镜头。

输出结构（供 modules/videos.py 落库）：
    {"title": str, "shots": [{"narration", "imagePrompt", "motion"}]}
"""

from __future__ import annotations

import asyncio
import json
import re

from . import ai_gateway

MOTIONS = {"zoom_in", "zoom_out", "pan_left", "pan_right"}
_MOTION_ALIASES = {
    "推": "zoom_in", "推进": "zoom_in", "zoomin": "zoom_in", "push_in": "zoom_in",
    "拉": "zoom_out", "拉远": "zoom_out", "zoomout": "zoom_out", "pull_out": "zoom_out",
    "左移": "pan_left", "panleft": "pan_left", "左摇": "pan_left",
    "右移": "pan_right", "panright": "pan_right", "右摇": "pan_right",
}
SCRIPT_SYSTEM = (
    "你是短视频编导，为「图文成片」类视频写解说分镜。只输出一个 JSON 对象，"
    "不要 Markdown，不要解释，不要在 JSON 前后写任何文字。结构必须是 "
    "{\"title\":\"视频标题\",\"shots\":[{\"narration\":\"这一镜头的旁白口播文案\","
    "\"imagePrompt\":\"配图的中文画面描述，具体、有主体和场景\",\"motion\":\"zoom_in\"}]}。"
    "motion 只能是 zoom_in、zoom_out、pan_left、pan_right 之一。"
    "字段名必须用上面的英文 camelCase。每个镜头旁白 15~60 个汉字，一句话到两句话；"
    "按内容逻辑切镜头，8~15 个镜头，首镜头抓人、尾镜头收束。"
    "旁白口语化、连贯成篇，合起来就是完整解说稿；不要编号、不要小标题。"
)


class ScriptError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _motion(value: str) -> str:
    raw = (value or "").strip()
    lowered = raw.lower().replace("-", "_").replace(" ", "")
    if lowered in MOTIONS or raw in MOTIONS:
        return lowered if lowered in MOTIONS else raw
    return _MOTION_ALIASES.get(raw) or _MOTION_ALIASES.get(lowered) or "zoom_in"


def _loads_script(text: str) -> dict:
    cleaned = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ScriptError("invalid_model_output", "模型没有返回 JSON 分镜")
    try:
        data = json.loads(cleaned[start : end + 1])
    except ValueError as exc:
        raise ScriptError("invalid_model_output", f"分镜 JSON 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise ScriptError("invalid_model_output", "分镜结果不是 JSON 对象")
    return data


def parse_script(raw: str) -> dict:
    """模型原文 → 规范化 shots 列表；空旁白/超量镜头在这里拦下。"""
    data = _loads_script(raw)
    title = str(data.get("title") or "").strip()[:200]
    items = data.get("shots")
    if not isinstance(items, list) or not items:
        raise ScriptError("invalid_model_output", "模型未返回任何镜头")
    shots: list[dict] = []
    for item in items[:24]:
        if not isinstance(item, dict):
            continue
        narration = str(item.get("narration") or "").strip()
        if not narration:
            continue
        image_prompt = str(item.get("imagePrompt") or item.get("image_prompt") or "").strip()
        shots.append(
            {
                "narration": narration[:1000],
                "imagePrompt": image_prompt[:1000],
                "motion": _motion(str(item.get("motion") or "")),
            }
        )
    if not shots:
        raise ScriptError("invalid_model_output", "所有镜头旁白都是空的")
    return {"title": title, "shots": shots}


def build_prompt(source_text: str) -> str:
    body = (source_text or "").strip()
    if len(body) > 12_000:
        body = body[:12_000] + "\n……（内容过长，已截断）"
    return f"请把下面的文案改编成短视频解说分镜：\n\n{body}"


async def _collect_text(db, user_id: str, prompt: str, model_id: str | None) -> tuple[str, dict]:
    meta: dict = {}
    try:
        selection = ai_gateway.resolve_selection(db, user_id, model_id)
    except ai_gateway.InvalidModelSelection as exc:
        raise ScriptError(exc.code, exc.message) from exc
    failures: list[str] = []
    for index, ((connection, model_code), _entry_id) in enumerate(
        zip(selection["candidates"], selection["entryIds"])
    ):
        if connection is None or not model_code or model_code == "unconfigured":
            continue
        parts: list[str] = []
        error: dict | None = None
        async for event in ai_gateway.stream_text(
            db,
            connection=connection,
            model_code=model_code,
            task_type="video",
            prompt=prompt,
            system_prompt=SCRIPT_SYSTEM,
        ):
            name = event.get("event")
            data = event.get("data") or {}
            if name == "generation.delta":
                parts.append(data.get("text") or "")
            elif name == "generation.error":
                error = data
        text = "".join(parts)
        if error is None and text.strip():
            meta.update({"modelCode": model_code, "fallbackUsed": index > 0})
            return text, meta
        failures.append(f"{model_code}：{(error or {}).get('message') or '模型没有返回内容'}")
        if selection["pinned"]:
            break
    if failures:
        raise ScriptError("provider_unavailable", "所有文本模型均不可用 → " + "；".join(failures)[:500])
    raise ScriptError(
        "provider_unavailable",
        "尚未配置可用的文本模型连接；请在「系统 → 模型管理」登记并测试通过后重试",
    )


def breakdown(source_text: str, *, db=None, user_id: str = "", model_id: str | None = None) -> dict:
    """同步入口（worker/API 线程池可用）。无模型时抛 ScriptError，绝不产占位镜头。"""
    if not (source_text or "").strip():
        raise ScriptError("empty_input", "文案为空，无法拆分镜")
    text, meta = asyncio.run(_collect_text(db, user_id, build_prompt(source_text), model_id))
    result = parse_script(text)
    result["meta"] = meta
    return result
