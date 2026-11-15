"""从正文抽取角色卡片草稿。草稿不落库，由前端人工确认后再创建。

刻意与分镜拆解解耦：拆解的 system prompt 明令「不要提及参考图」，而角色卡
需要的正是外貌描述；同时抽取失败只应回落到手工登记，不能连带毁掉分镜生成。
没有可用模型或返回不可解析时明确报错，绝不生成占位角色。
"""

from __future__ import annotations

import json
import re

from . import ai_article, ai_gateway
from .. import schemas

EXTRACT_SYSTEM = (
    "你是漫画角色设定师。只输出一个 JSON 对象，不要 Markdown，不要解释，不要在 JSON 前后写任何文字。"
    "结构必须是 {\"characters\":[{\"name\":\"角色名\",\"aliases\":[\"别名\"],"
    "\"appearance\":{\"hair\":\"发型\",\"hairColor\":\"发色\",\"eyes\":\"眼睛\",\"face\":\"面部特征\","
    "\"outfit\":\"服装\",\"body\":\"体型\",\"age\":\"年龄段\",\"extra\":\"其他显著特征\"}}]}。"
    "只收录正文中真正出现的有名字的角色，不要收录地点、组织、道具或无名的路人。"
    "appearance 里只写正文明确提到或能合理直读的外貌特征，没有把握的字段留空字符串，"
    "不要编造身高、品牌、具体年龄，不要写绘画提示词，不要提及参考图。"
    "aliases 收录正文中指代同一人的其他称呼，没有就给空数组。最多 12 个角色。"
    "字段名必须用上面的英文 camelCase，值用中文。"
)


class ExtractionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _loads(text: str):
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


def _clean_appearance(value) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    cleaned: dict[str, str] = {}
    for key in schemas.APPEARANCE_KEYS:
        text = str(raw.get(key) or "").strip()
        if text:
            cleaned[key] = text[:200]
    return cleaned


def _normalize(raw) -> list[dict]:
    """纯函数：把模型输出收敛成角色草稿，脏数据直接丢，不猜、不补占位。"""
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict):
        rows = raw.get("characters") or raw.get("roles") or raw.get("people") or []
    else:
        rows = []
    if not isinstance(rows, list):
        raise ExtractionError("invalid_extraction", "模型没有返回可用的角色列表")
    drafts: list[dict] = []
    seen: set[str] = set()
    for row in rows[:12]:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("角色名") or "").strip()[:120]
        if not name or name in seen:
            continue
        aliases: list[str] = []
        for item in row.get("aliases") or []:
            value = str(item or "").strip()[:120]
            if value and value != name and value not in aliases:
                aliases.append(value)
        seen.add(name)
        drafts.append({
            "name": name,
            "aliases": aliases[:12],
            "appearance": _clean_appearance(row.get("appearance")),
        })
    if not drafts:
        raise ExtractionError("invalid_extraction", "正文里没有抽到有名字的角色")
    return drafts


COMPLETE_SYSTEM = (
    "你是漫画角色设定师。根据给出的角色名、设定与（可选的）正文片段，补全外貌字段。"
    "只输出一个 JSON 对象，不要 Markdown，不要解释，不要在 JSON 前后写任何文字。"
    "结构必须是 {\"appearance\":{\"hair\":\"发型\",\"hairColor\":\"发色\",\"eyes\":\"眼睛\","
    "\"face\":\"面部特征\",\"outfit\":\"服装\",\"body\":\"体型\",\"age\":\"年龄段\","
    "\"extra\":\"其他显著特征\"}}。"
    "有正文片段时以正文明确提到或能合理直读的特征为准，没有把握的字段留空字符串；"
    "没有正文片段时才允许依据设定合理脑补，但必须是一套自洽、可直接作画的外貌。"
    "不要编造品牌与具体年龄数字，不要写绘画提示词。"
    "字段名必须用上面的英文 camelCase，值用中文。"
)


async def _stream_json(db, user_id: str, model_id: str | None, prompt: str, system: str) -> tuple[object, str, bool]:
    """跑一次候选链，返回 (解析后的 JSON, 实际使用的模型, 是否降级)。失败抛 ExtractionError。"""
    try:
        selection = ai_gateway.resolve_selection(db, user_id, model_id)
    except ai_gateway.InvalidModelSelection as exc:
        raise ExtractionError(exc.code, exc.message) from exc
    candidates = selection["candidates"]
    if not candidates:
        raise ExtractionError("provider_unavailable", "尚未配置可用的文本模型，请手工登记角色")
    collected: list[str] = []
    error = ""
    used_model = ""
    fallback = False
    for index, (conn, model_code) in enumerate(candidates):
        parts: list[str] = []
        pending_error = ""
        async for event in ai_gateway.stream_text(
            db, connection=conn, model_code=model_code, task_type="outline",
            prompt=prompt, system_prompt=system,
        ):
            if event.get("event") == "generation.delta":
                parts.append((event.get("data") or {}).get("text") or "")
            elif event.get("event") == "generation.error":
                pending_error = (event.get("data") or {}).get("message") or "模型抽取失败"
        if parts and not pending_error:
            collected = parts
            used_model = model_code
            fallback = index > 0
            break
        if pending_error:
            error = pending_error
    if not collected:
        raise ExtractionError("provider_unavailable", error or "模型没有返回抽取结果")
    raw = _loads("".join(collected))
    if raw is None:
        raise ExtractionError("invalid_extraction", "模型返回的内容无法解析为 JSON")
    return raw, used_model, fallback


async def extract(db, user_id: str, source_text: str, model_id: str | None) -> dict:
    """返回 {drafts, modelCode, fallbackUsed}；候选链与分镜拆解一致。"""
    raw, used_model, fallback = await _stream_json(
        db, user_id, model_id, source_text.strip(), EXTRACT_SYSTEM
    )
    return {"drafts": _normalize(raw), "model_code": used_model, "fallback_used": fallback}


def build_complete_prompt(name: str, hint: str, source_text: str) -> str:
    parts = [f"角色名：{name.strip() or '（未命名）'}"]
    if hint.strip():
        parts.append(f"设定：{hint.strip()}")
    if source_text.strip():
        parts.append(f"正文片段：\n{source_text.strip()}")
    else:
        parts.append("没有提供正文片段，请依据设定合理补全。")
    return "\n\n".join(parts)


async def complete_appearance(db, user_id: str, name: str, hint: str, source_text: str, model_id: str | None) -> dict:
    """只返回建议的外貌字段，不落库；用户勾选后才写入角色卡。"""
    raw, used_model, fallback = await _stream_json(
        db, user_id, model_id, build_complete_prompt(name, hint, source_text), COMPLETE_SYSTEM
    )
    row = raw.get("appearance") if isinstance(raw, dict) else raw
    if not isinstance(row, dict):
        raise ExtractionError("invalid_extraction", "模型没有返回可解析的外貌字段")
    appearance = _clean_appearance(row)
    if not appearance:
        raise ExtractionError("invalid_extraction", "模型没有补出任何有把握的外貌字段")
    return {"appearance": appearance, "model_code": used_model, "fallback_used": fallback}
