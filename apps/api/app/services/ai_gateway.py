"""AI Gateway：统一适配器 + SSE 事件（设计文档第 6 章）。

原则：没有可用的模型连接就明确报 `provider_unavailable`，**不生成本地占位文本冒充 AI 产出**。
所有生成事件都带 provider 标识；调用方不得把错误事件当成成功落库。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator

import httpx

from sqlalchemy import select

from .. import models
from ..core import ssrf
from ..core.security import decrypt_secret

TEXT_CAPABILITIES = {"text.generate", "text.stream"}

# 长文生成首 token 可能很慢（推理型模型尤甚），读超时按 3 分钟设置；
# 60 秒会在正常生成中被误判成 ReadTimeout 直接失败。
TEXT_REQUEST_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=60.0, pool=10.0)

TASK_LABELS = {
    "idea_expand": "选题扩展",
    "outline": "大纲",
    "article": "初稿",
    "rewrite": "改写",
    "polish": "润色",
    "summarize": "摘要",
    "platform_variant": "平台版本",
    "image_prompt": "配图提示词",
    "novel": "小说文本处理",
}

SYSTEM_RULES = (
    "你是中文自媒体创作助手。只输出可直接使用的正文，不解释过程，"
    "不编造具体数据来源，不使用绝对化承诺。"
)
# 提示词模板版本（设计 §6.5）：每次修改 SYSTEM_RULES / build_prompt 必须同步升级，
# 生成记录会带上版本号，便于复盘时区分不同模板下的产出。
PROMPT_TEMPLATE_VERSION = "2026-11-06.v1"

logger = logging.getLogger(__name__)

# 默认模型在「配置层」失效的原因。运行时 400（订阅过期等）由候选链降级覆盖，
# 这里只处理模型/连接被删、停用、越权、能力变更、密钥清空。
DEFAULT_IGNORE_MESSAGES = {
    "not_found": "默认模型已被删除",
    "foreign": "默认模型不属于当前用户",
    "connection_missing": "默认模型所属连接已被删除",
    "disabled": "默认模型已被停用",
    "not_text": "默认模型已不具备文本能力",
    "no_credential": "默认模型连接缺少 API Key",
}


class InvalidModelSelection(Exception):
    """显式传入的 modelId 非法。与「默认模型失效静默忽略」刻意不同：必须 400 拒绝。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ProviderHTTPError(httpx.HTTPStatusError):
    """供应商返回错误状态码：携带响应体，便于把可操作原因透传到任务错误里。"""

    def __init__(self, message: str, *, request, response, detail: str, retryable: bool) -> None:
        super().__init__(message, request=request, response=response)
        self.detail = detail
        self.retryable = retryable


def build_prompt(task_type: str, prompt: str, constraints: dict | None = None) -> str:
    constraints = constraints or {}
    parts = [f"任务：{TASK_LABELS.get(task_type, task_type)}", f"需求：{prompt}"]
    if constraints.get("targetLength"):
        parts.append(f"目标字数：约 {constraints['targetLength']} 字")
    if constraints.get("tone"):
        parts.append(f"语气：{constraints['tone']}")
    if constraints.get("audience"):
        parts.append(f"受众：{constraints['audience']}")
    if constraints.get("platform"):
        parts.append(f"目标平台：{constraints['platform']}")
    return "\n".join(parts)


async def stream_text(
    db,
    *,
    connection: models.ModelConnection | None,
    model_code: str,
    task_type: str,
    prompt: str,
    constraints: dict | None = None,
    system_prompt: str | None = None,
) -> AsyncIterator[dict]:
    """产出统一事件：delta / usage / done / error（started 由调用方带 generationId 发送）。

    system_prompt 不为空时覆盖内置 SYSTEM_RULES，用于 Skill 自定义写作风格。
    """
    started = time.perf_counter()
    full_prompt = build_prompt(task_type, prompt, constraints)
    api_key = decrypt_secret(connection.credential_ciphertext) if connection else ""
    provider = connection.provider_type if connection else "none"

    if not (connection and connection.base_url and api_key):
        yield {
            "event": "generation.error",
            "data": {
                "code": "provider_unavailable",
                "message": "尚未配置可用的模型连接与 API Key；请在「设置 → 模型连接」登记并测试通过后重试",
                "retryable": False,
                "provider": "none",
                "isPlaceholder": False,
            },
        }
        return

    try:
        async for chunk in _stream_openai_compatible(
            base_url=connection.base_url,
            api_key=api_key,
            model=model_code,
            prompt=full_prompt,
            system_prompt=system_prompt,
        ):
            yield {
                "event": "generation.delta",
                "data": {"text": chunk, "provider": provider, "isPlaceholder": False},
            }
    except ProviderHTTPError as exc:
        detail = (exc.detail or "").strip()[:300]
        message = f"供应商返回 HTTP {exc.response.status_code}"
        if detail:
            message += f"：{detail}"
        yield {
            "event": "generation.error",
            "data": {
                "code": "provider_unavailable",
                "message": message,
                "retryable": exc.retryable,
                "provider": provider,
                "isPlaceholder": False,
            },
        }
        return
    except (httpx.HTTPError, ssrf.UnsafeUrl) as exc:
        detail = str(exc).strip()
        message = f"供应商请求失败：{type(exc).__name__}"
        if detail:
            message += f"：{detail}"
        yield {
            "event": "generation.error",
            "data": {
                "code": "provider_unavailable",
                "message": message,
                "retryable": True,
                "provider": provider,
                "isPlaceholder": False,
            },
        }
        return

    duration_ms = int((time.perf_counter() - started) * 1000)
    yield {
        "event": "generation.usage",
        "data": {"durationMs": duration_ms, "promptChars": len(full_prompt), "provider": provider},
    }
    yield {
        "event": "generation.done",
        "data": {"durationMs": duration_ms, "provider": provider, "isPlaceholder": False},
    }


async def _stream_openai_compatible(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    system_prompt: str | None = None,
) -> AsyncIterator[str]:
    url = base_url.rstrip("/") + "/chat/completions"
    # system_prompt 不为空时覆盖内置规则（Skill 自定义风格），否则回退 SYSTEM_RULES
    system_content = system_prompt.strip() if system_prompt else SYSTEM_RULES
    payload = {
        "model": model,
        "stream": True,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt},
        ],
    }
    # 走代理时不能固定 IP：httpcore 的代理隧道不读 sni_hostname，
    # 固定 IP 会让 TLS 按 IP 校验证书而失败（表现为 ConnectError，误判成供应商不可达）。
    via_proxy = ssrf.proxy_in_effect()
    try:
        async for chunk in _stream_once(
            payload=payload,
            url=url,
            api_key=api_key,
            trust_env=True,
            use_pin=not via_proxy,
        ):
            yield chunk
    except (httpx.ConnectError, httpx.ProxyError, httpx.ConnectTimeout):
        if not via_proxy:
            raise
        # 代理配了但连不上，回退直连（此时必须恢复 IP 固定）
        async for chunk in _stream_once(
            payload=payload,
            url=url,
            api_key=api_key,
            trust_env=False,
            use_pin=True,
        ):
            yield chunk


async def _stream_once(
    *,
    payload: dict,
    url: str,
    api_key: str,
    trust_env: bool,
    use_pin: bool,
) -> AsyncIterator[str]:
    if use_pin:
        target, pinned_headers, extensions = await asyncio.to_thread(ssrf.pin_url, url)
    else:
        # 仍要过一遍校验（解析并拦截内网地址），只是不做 IP 固定
        ssrf.assert_safe_url(url)
        target, pinned_headers, extensions = url, {}, {}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        **pinned_headers,
    }
    RETRYABLE = {429, 500, 502, 503, 504}
    async with httpx.AsyncClient(
        timeout=TEXT_REQUEST_TIMEOUT, follow_redirects=False, trust_env=trust_env
    ) as client:
        for attempt in range(3):
            request = client.build_request("POST", target, json=payload, headers=headers)
            request.extensions.update(extensions)
            response = await client.send(request, stream=True)
            try:
                code = response.status_code
                if code in RETRYABLE and attempt < 2:
                    await response.aread()
                    await asyncio.sleep(0.4 * (2**attempt))  # 指数退避
                    continue
                if 300 <= code < 400:
                    raise httpx.HTTPStatusError(
                        "供应商返回重定向，已拒绝跟随以防 SSRF",
                        request=response.request,
                        response=response,
                    )
                if code >= 400:
                    # 把供应商的错误体带出来（如「该模型是 image 模型」「订阅已过期」），
                    # 否则上层只能看到无意义的 HTTPStatusError，用户无从修复。
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise ProviderHTTPError(
                        f"供应商返回 HTTP {code}",
                        request=response.request,
                        response=response,
                        detail=body,
                        retryable=code in RETRYABLE,
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        parsed = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = (parsed.get("choices") or [{}])[0].get("delta") or {}
                    text = delta.get("content")
                    if text:
                        yield text
                return
            finally:
                await response.aclose()


def _default_model_id(db, owner_user_id: str) -> str | None:
    """读用户级默认文本模型偏好；无记录或未设置返回 None。"""
    value = db.scalar(
        select(models.UserPreference.default_text_model_id).where(
            models.UserPreference.owner_user_id == owner_user_id
        )
    )
    return str(value or "").strip() or None


def _validate_entry(db, owner_user_id: str, model_id: str):
    """校验一个 catalog 条目能不能用于文本生成。

    返回 (connection, entry, reason)；reason 为 None 表示有效。
    不解密密钥：只看有没有登记凭据，避免把解密异常带进候选链解析。
    """
    entry = db.get(models.ModelCatalogEntry, model_id)
    if entry is None:
        return None, None, "not_found"
    connection = db.get(models.ModelConnection, entry.connection_id)
    if connection is None:
        return None, entry, "connection_missing"
    if connection.owner_user_id != owner_user_id:
        return None, entry, "foreign"
    if not entry.enabled:
        return connection, entry, "disabled"
    if not _is_text_model(entry):
        return connection, entry, "not_text"
    if not str(connection.credential_ciphertext or "").strip():
        return connection, entry, "no_credential"
    return connection, entry, None


def _ignored(reason: str, model_id: str) -> dict:
    return {
        "reason": reason,
        "message": DEFAULT_IGNORE_MESSAGES.get(reason, "默认模型当前不可用"),
        "modelId": model_id,
    }


def describe_default_model(db, owner_user_id: str) -> dict:
    """给设置页 / 下拉「自动」项用的默认模型展示信息。

    失效时只返回 ignored 原因，**不清除偏好**：订阅续上、模型重新启用后自动恢复。
    """
    model_id = _default_model_id(db, owner_user_id)
    if not model_id:
        return {"id": None, "label": None, "ignored": None}
    connection, entry, reason = _validate_entry(db, owner_user_id, model_id)
    label = None
    if entry is not None:
        name = (connection.display_name if connection else None) or "已删除的连接"
        label = f"{name} · {entry.model_code}"
    if not reason:
        return {"id": model_id, "label": label, "ignored": None}
    detail = _ignored(reason, model_id)
    logger.warning("[model] 默认模型 %s 配置失效，本次忽略：%s", model_id, reason)
    return {"id": model_id, "label": label, "ignored": detail}


def _text_candidates(db, owner_user_id: str) -> list[tuple]:
    """按创建顺序收集全部文本候选 (connection, modelCode, catalogId)。

    同一个连接下登记的多个 enabled 模型各自成为一个候选；只挑文本能力模型，
    图像/视频模型混进文本降级链会被供应商直接 400 拒绝。
    这里刻意先收全再截断：默认模型可能排在后面，提前 limit 会把它丢掉。
    """
    rows = db.scalars(
        select(models.ModelConnection)
        .where(models.ModelConnection.owner_user_id == owner_user_id)
        .order_by(models.ModelConnection.created_at)
    ).all()
    candidates: list[tuple] = []
    for connection in rows:
        entries = db.scalars(
            select(models.ModelCatalogEntry)
            .where(
                models.ModelCatalogEntry.connection_id == connection.id,
                models.ModelCatalogEntry.enabled.is_(True),
            )
            .order_by(models.ModelCatalogEntry.created_at)
        ).all()
        for entry in entries:
            if not _is_text_model(entry):
                continue
            candidates.append((connection, entry.model_code, entry.id))
        if not entries:
            # 连接尚未登记任何模型：保留为候选，供状态页提示补齐模型/密钥
            candidates.append((connection, "unconfigured", None))
    return candidates


def resolve_selection(
    db,
    owner_user_id: str,
    model_id: str | None = None,
    *,
    apply_default: bool = True,
    limit: int = 3,
) -> dict:
    """统一模型解析：显式选中钉死，不选择时把默认模型排候选链首位仍可降级。

    返回 {candidates, entryIds, pinned, defaultModelId, defaultModelIgnored,
    requestedModelId}；entryIds 与 candidates 下标对齐，供生成记录落「实际模型」。

    apply_default=False 用于小说队列任务：执行时严格按入队快照，不受之后
    修改默认模型的影响。
    """
    requested = str(model_id or "").strip() or None
    if requested:
        connection, entry, reason = _validate_entry(db, owner_user_id, requested)
        if reason:
            raise InvalidModelSelection(
                "model_not_found",
                f"指定的模型不可用（{DEFAULT_IGNORE_MESSAGES.get(reason, reason)}）；"
                "请重新选择或清空 modelId 自动使用默认文本模型",
            )
        return {
            "candidates": [(connection, entry.model_code)],
            "entryIds": [entry.id],
            "pinned": True,
            "defaultModelId": None,
            "defaultModelIgnored": None,
            "requestedModelId": requested,
        }

    base = _text_candidates(db, owner_user_id)
    default_id = _default_model_id(db, owner_user_id) if apply_default else None
    ignored = None
    if default_id:
        connection, entry, reason = _validate_entry(db, owner_user_id, default_id)
        if reason:
            ignored = _ignored(reason, default_id)
            logger.warning(
                "[model] 默认模型 %s 配置失效，本次按原候选链生成：%s", default_id, reason
            )
        else:
            head = (connection, entry.model_code, entry.id)
            base = [head, *[row for row in base if row[2] != entry.id]]
    selected = base[: max(limit, 1)]
    return {
        "candidates": [(conn, code) for conn, code, _ in selected],
        "entryIds": [entry_id for _, _, entry_id in selected],
        "pinned": False,
        "defaultModelId": default_id,
        "defaultModelIgnored": ignored,
        "requestedModelId": None,
    }


def resolve_connection(db, owner_user_id: str):
    """取该用户第一个可用连接与模型；无连接时返回 (None, "unconfigured")。"""
    candidates = resolve_connections(db, owner_user_id, limit=1)
    return candidates[0] if candidates else (None, "unconfigured")


def resolve_connections(
    db, owner_user_id: str, limit: int = 3, *, apply_default: bool = True
) -> list[tuple]:
    """按候选顺序返回 (connection, modelCode)，供备用模型降级使用（设计 §6.4）。

    不传 modelId 的所有文本入口都会经过这里，因此用户级默认模型自动生效，
    无需逐个页面加 UI。
    """
    return resolve_selection(
        db, owner_user_id, apply_default=apply_default, limit=limit
    )["candidates"]


def snapshot_model_id(db, owner_user_id: str, explicit: str | None = None) -> str | None:
    """入队时把「显式选择，或当时有效的默认」快照成 catalog id。

    失效的默认不写进快照（执行时按原候选链降级），避免任务因为一条失效偏好卡死。
    """
    explicit = str(explicit or "").strip() or None
    if explicit:
        _, _, reason = _validate_entry(db, owner_user_id, explicit)
        if reason:
            raise InvalidModelSelection(
                "model_not_found",
                f"指定的模型不可用（{DEFAULT_IGNORE_MESSAGES.get(reason, reason)}）",
            )
        return explicit
    described = describe_default_model(db, owner_user_id)
    return described["id"] if not described["ignored"] else None


def _is_text_model(entry: models.ModelCatalogEntry) -> bool:
    """能力未登记的历史模型按文本模型处理，已登记的必须声明文本能力。"""
    capabilities = entry.capabilities or []
    if not capabilities:
        return True
    return any(str(cap) in TEXT_CAPABILITIES for cap in capabilities)


def is_text_model(entry: models.ModelCatalogEntry) -> bool:
    """公开给模型目录/选择器判断文本能力，口径与候选链过滤完全一致。"""
    return _is_text_model(entry)


async def _collect(
    db,
    *,
    connection,
    model_code: str,
    task_type: str,
    prompt: str,
    constraints: dict | None,
    system_prompt: str | None = None,
) -> dict:
    collected: list[str] = []
    error_code: str | None = None
    error_message: str = ""
    async for event in stream_text(
        db,
        connection=connection,
        model_code=model_code,
        task_type=task_type,
        prompt=prompt,
        constraints=constraints,
        system_prompt=system_prompt,
    ):
        if event["event"] == "generation.delta":
            collected.append(event["data"]["text"])
        elif event["event"] == "generation.error":
            error_code = event["data"].get("code")
            error_message = event["data"].get("message") or ""
    text = "".join(collected)
    return {
        "text": text,
        "modelCode": model_code,
        "provider": "none" if connection is None else connection.provider_type,
        "errorCode": error_code if not text else None,
        "errorMessage": error_message if not text else "",
    }


async def generate_text(
    db,
    *,
    owner_user_id: str,
    task_type: str,
    prompt: str,
    constraints: dict | None = None,
    system_prompt: str | None = None,
) -> dict:
    """非流式生成，供工作流节点等后台链路使用。

    system_prompt 不为空时覆盖内置系统规则（Skill 自定义风格）。
    逐个尝试所有候选模型（设计 §6.4），全部失败则 text 为空并带 errorCode，
    调用方必须据此把节点标为失败，不得用占位文本伪装成功。
    """
    candidates = resolve_connections(db, owner_user_id)
    if not candidates:
        candidates = [(None, "unconfigured")]
    primary_error: str | None = None
    last_result: dict = {}
    for index, (connection, model_code) in enumerate(candidates):
        last_result = await _collect(
            db,
            connection=connection,
            model_code=model_code,
            task_type=task_type,
            prompt=prompt,
            constraints=constraints,
            system_prompt=system_prompt,
        )
        if last_result["text"] and not last_result["errorCode"]:
            return {
                **last_result,
                "fallbackUsed": index > 0,
                "primaryErrorCode": primary_error,
                "attempts": index + 1,
            }
        if index == 0:
            primary_error = last_result["errorCode"]
    return {
        **last_result,
        "text": "",
        "fallbackUsed": len(candidates) > 1,
        "primaryErrorCode": primary_error,
        "attempts": len(candidates),
        "errorCode": last_result.get("errorCode") or "provider_unavailable",
    }
