"""把故事正文拆成分镜草稿。没有可用模型时明确失败，不写占位镜头。"""

from __future__ import annotations

import json
import re

from .. import models
from . import ai_article, ai_gateway

SHOT_SIZES = {"wide", "full", "medium", "close", "extreme_close"}
_FIELD_ALIASES = {
    "frameDescription": ("frameDescription", "frame_description", "description", "frame", "visual"),
    "action": ("action", "movement"),
    "dialogue": ("dialogue", "dialog", "line"),
    "shotSize": ("shotSize", "shot_size", "size"),
}
_SHOT_SIZE_ALIASES = {
    "远景": "wide", "全景": "full", "中景": "medium", "近景": "close", "特写": "extreme_close",
    "大特写": "extreme_close", "wide shot": "wide", "full shot": "full",
    "medium shot": "medium", "close-up": "close", "close up": "close",
    "extreme close-up": "extreme_close", "extreme close up": "extreme_close",
}
BREAKDOWN_SYSTEM = (
    "你是漫画分镜师。只输出一个 JSON 对象，不要 Markdown，不要解释，不要在 JSON 前后写任何文字。"
    "结构必须是 {\"scenes\":[{\"title\":\"场景名\",\"summary\":\"这一场发生什么\",\"chapter\":\"第一章\","
    "\"shots\":[{\"frameDescription\":\"画面里看见什么\",\"action\":\"角色做什么\",\"dialogue\":\"对白，没有就留空\","
    "\"shotSize\":\"medium\"}]}]}。"
    "shotSize 只能是 wide、full、medium、close、extreme_close 之一。"
    "字段名必须用上面的英文 camelCase，不要改成中文键名或下划线。"
    "按正文自然分段，最多 12 个场景，每场 1 到 6 个镜头。"
    "画面、动作、对白用中文。不要生成绘画提示词，不要提及参考图。"
)


class BreakdownError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _field(item: dict, name: str) -> str:
    for key in _FIELD_ALIASES[name]:
        value = item.get(key)
        if value:
            return str(value).strip()
    return ""


def _shot_size(value: str) -> str:
    raw = (value or "").strip()
    lowered = raw.lower().replace("-", " ").replace("_", " ")
    compact = lowered.replace(" ", "")
    if compact in SHOT_SIZES or raw in SHOT_SIZES:
        return compact if compact in SHOT_SIZES else raw
    return _SHOT_SIZE_ALIASES.get(raw) or _SHOT_SIZE_ALIASES.get(lowered) or "medium"


def _loads_breakdown(text: str):
    cleaned = ai_article._strip_code_fence(text)
    decoder = json.JSONDecoder(strict=False)
    for candidate in (cleaned, ai_article._repair_invalid_escapes(cleaned)):
        try:
            data = json.loads(candidate, strict=False)
        except (json.JSONDecodeError, ValueError):
            data = None
        if isinstance(data, (dict, list)):
            return data
        for match in re.finditer(r"[\{\[]", candidate):
            try:
                data, _ = decoder.raw_decode(candidate[match.start():])
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(data, (dict, list)):
                return data
    return None


def _scene_list(raw) -> list:
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict):
        return []
    for key in ("scenes", "storyboard", "data", "result"):
        value = raw.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = value.get("scenes")
            if isinstance(nested, list):
                return nested
    return []


def _shot_list(scene: dict) -> list:
    for key in ("shots", "panels", "frames"):
        value = scene.get(key)
        if isinstance(value, list):
            return value
    return []


def _normalize(raw) -> list[dict]:
    scenes = _scene_list(raw)
    if not scenes:
        raise BreakdownError("invalid_breakdown", "模型没有返回可用的场景")
    cleaned = []
    for scene in scenes[:12]:
        if not isinstance(scene, dict):
            continue
        shots = []
        for shot in _shot_list(scene)[:6]:
            if not isinstance(shot, dict):
                continue
            frame = _field(shot, "frameDescription")
            if not frame:
                continue
            shots.append({
                "frame_description": frame[:2000],
                "action": _field(shot, "action")[:1000],
                "dialogue": _field(shot, "dialogue")[:1000],
                "shot_size": _shot_size(_field(shot, "shotSize")),
            })
        title = str(scene.get("title") or scene.get("name") or "").strip()
        if not title or not shots:
            continue
        cleaned.append({
            "title": title[:200],
            "summary": str(scene.get("summary") or "").strip()[:1000],
            "chapter": str(scene.get("chapter") or "第一章").strip()[:200] or "第一章",
            "shots": shots,
        })
    if not cleaned:
        raise BreakdownError("invalid_breakdown", "模型返回的场景里没有可用镜头")
    return cleaned


async def break_down(db, user_id: str, source_text: str, model_id: str | None) -> list[dict]:
    """拆分正文为镜头草稿。

    模型解析走统一网关：显式 modelId 钉死单候选（失败明确报错），
    不传时用「默认模型优先 + 自动降级」的完整候选链。
    """
    try:
        selection = ai_gateway.resolve_selection(db, user_id, model_id)
    except ai_gateway.InvalidModelSelection as exc:
        raise BreakdownError(exc.code, exc.message) from exc
    candidates = selection["candidates"]
    if not candidates:
        raise BreakdownError("provider_unavailable", "尚未配置可用的文本模型")
    collected: list[str] = []
    error = ""
    for conn, model_code in candidates:
        parts: list[str] = []
        pending_error = ""
        async for event in ai_gateway.stream_text(
            db, connection=conn, model_code=model_code, task_type="outline",
            prompt=source_text.strip(), system_prompt=BREAKDOWN_SYSTEM,
        ):
            if event.get("event") == "generation.delta":
                parts.append((event.get("data") or {}).get("text") or "")
            elif event.get("event") == "generation.error":
                pending_error = (event.get("data") or {}).get("message") or "模型拆分失败"
        # 出错的候选整段作废，避免把两个模型的半截 JSON 拼在一起
        if parts and not pending_error:
            collected = parts
            break
        if pending_error:
            error = pending_error
    if not collected:
        raise BreakdownError("provider_unavailable", error or "模型没有返回拆分结果")
    raw = _loads_breakdown("".join(collected))
    if raw is None:
        raise BreakdownError("invalid_breakdown", "模型返回的不是可解析的分镜")
    return _normalize(raw)
