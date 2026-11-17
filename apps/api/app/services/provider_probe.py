"""模型连接真实探测（修复 README「测试」名不副实的问题）。

绝不因为「密钥非空」就返回 connected：必须真的向供应商发一次请求，
按 HTTP 状态码给出结论，并把失败原因原样回传。

两个层次的探测，配置弹窗和列表页共用同一套口径：
- fetch_models / probe_connection：拉 GET /models，验证地址与凭据（不花钱）；
- probe_model：在连接可用的前提下，再验证「这个模型代号到底能不能用」。
  文本模型发一次 max_tokens=1 的对话请求；生图模型不发起付费出图，只核对是否出现在
  供应商的模型列表里——花钱的动作必须由用户自己点「生成」触发。
"""

from __future__ import annotations

import time

import httpx

from ..core import ssrf

# 已实现协议适配器。前端出现的其他选项在此显式登记为未实现，不允许静默回退。
SUPPORTED_ADAPTERS = {"openai_compatible": "OpenAI 兼容协议"}
UNIMPLEMENTED_ADAPTERS = {"vendor_native": "供应商原生协议适配器尚未接入"}

# 供应商列表可能很长（聚合网关动辄上千条），返回前截断，避免一次把前端撑爆
MAX_MODELS = 300


def adapter_status(provider_type: str) -> tuple[bool, str]:
    if provider_type in SUPPORTED_ADAPTERS:
        return True, SUPPORTED_ADAPTERS[provider_type]
    if provider_type in UNIMPLEMENTED_ADAPTERS:
        return False, UNIMPLEMENTED_ADAPTERS[provider_type]
    return False, f"未知的供应商协议：{provider_type}"


def probe_connection(base_url: str, api_key: str, provider_type: str) -> dict:
    """返回 {status, message, httpStatus, latencyMs, modelCount}。

    status 取值：connected | unauthorized | adapter_unavailable | invalid_base_url |
    missing_credential | provider_unavailable | provider_unreachable | endpoint_not_supported
    """
    fetched = fetch_models(base_url, api_key, provider_type)
    return {
        key: fetched[key]
        for key in ("status", "message", "httpStatus", "latencyMs", "modelCount")
    }


def fetch_models(base_url: str, api_key: str, provider_type: str) -> dict:
    """拉取供应商模型列表，同时给出连通性结论。

    返回 {status, message, httpStatus, latencyMs, modelCount, models, truncated}，
    models 为 [{id, ownedBy}]，按 id 排序；失败时为空列表，不编造内容。
    """
    if not api_key:
        return _result("missing_credential", "缺少 API Key，无法调用供应商")
    ok, label = adapter_status(provider_type)
    if not ok:
        return _result("adapter_unavailable", label)
    try:
        root = ssrf.assert_safe_url(base_url)
    except ssrf.UnsafeUrl as exc:
        return _result("invalid_base_url", str(exc))

    started = time.perf_counter()
    try:
        response = ssrf.safe_request(
            "GET",
            f"{root}/models",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=8.0,
        )
    except ssrf.UnsafeUrl as exc:
        return _result("invalid_base_url", str(exc))
    except httpx.HTTPError as exc:
        return _result("provider_unreachable", _connect_error_message(exc))
    latency = int((time.perf_counter() - started) * 1000)
    code = response.status_code
    if 200 <= code < 300:
        models, total = _parse_models(response)
        return _result(
            "connected",
            f"供应商已响应（{label}），连通性验证通过",
            http_status=code,
            latency_ms=latency,
            models=models,
            model_count=total,
            truncated=total > len(models),
        )
    if code in (401, 403):
        return _result("unauthorized", "凭据被供应商拒绝，请检查 API Key", code, latency)
    if code == 404:
        return _result(
            "endpoint_not_supported",
            "供应商未提供 /models 接口，无法验证凭据，请手动确认或改用 OpenAI 兼容地址",
            code,
            latency,
        )
    if code == 429:
        return _result("provider_unavailable", "供应商限流（429），请稍后重试", code, latency)
    return _result("provider_unavailable", f"供应商返回 HTTP {code}", code, latency)


def probe_model(
    base_url: str,
    api_key: str,
    provider_type: str,
    model_code: str,
    kind: str = "text",
) -> dict:
    """验证「这个模型代号能不能用」，返回 {status, message, verified, modelVerified, ...}。

    - 连接本身不可用：直接返回连接层结论，modelVerified 为 False；
    - 没填模型代号：连接可用但没做模型级测试，modelVerified 为 False，message 说清楚；
    - 文本模型：真的发一次 1 token 的对话请求；
    - 生图模型：只核对是否出现在 /models 列表，不替用户花钱出图。
    """
    fetched = fetch_models(base_url, api_key, provider_type)
    base = {
        key: fetched[key]
        for key in ("status", "message", "httpStatus", "latencyMs", "modelCount")
    }
    base.update({"verified": False, "modelVerified": False})
    if fetched["status"] != "connected":
        return base

    code = (model_code or "").strip()
    if not code:
        return {
            **base,
            "verified": True,
            "message": "连接可用；未填写模型代号，未做模型级测试",
        }
    listed = {item["id"] for item in fetched.get("models", [])}
    if kind == "image":
        present = code in listed
        return {
            **base,
            "verified": present,
            "modelVerified": present,
            "status": "connected" if present else "model_not_found",
            "message": (
                f"生图模型 {code} 已出现在供应商模型列表中；未发起付费出图请求"
                if present
                else f"供应商模型列表里没有 {code}，请核对代号"
            ),
        }
    return _probe_text_model(
        base_url=base_url,
        api_key=api_key,
        model_code=code,
        base=base,
        listed=listed,
    )


def _probe_text_model(
    *,
    base_url: str,
    api_key: str,
    model_code: str,
    base: dict,
    listed: set[str],
) -> dict:
    """发一次最小对话请求：只关心供应商是否真的认识这个模型、是否真的能出字。"""
    try:
        root = ssrf.assert_safe_url(base_url)
    except ssrf.UnsafeUrl as exc:
        return {**base, "status": "invalid_base_url", "httpStatus": None, "message": str(exc)}
    started = time.perf_counter()
    try:
        response = ssrf.safe_request(
            "POST",
            f"{root}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            json_body={
                "model": model_code,
                "stream": False,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            },
            timeout=15.0,
        )
    except ssrf.UnsafeUrl as exc:
        return {**base, "status": "invalid_base_url", "httpStatus": None, "message": str(exc)}
    except httpx.HTTPError as exc:
        return {
            **base,
            "status": "provider_unreachable",
            "httpStatus": None,
            "message": _connect_error_message(exc),
        }
    latency = int((time.perf_counter() - started) * 1000)
    code = response.status_code
    detail = _error_detail(response)
    # 之后所有结论都以「这次对话请求」的 HTTP 状态与耗时为准，不沿用 /models 那次的
    common = {**base, "httpStatus": code, "latencyMs": latency}
    if 200 <= code < 300:
        return {
            **common,
            "status": "connected",
            "verified": True,
            "modelVerified": True,
            "message": f"模型 {model_code} 已真实响应（{latency}ms，消耗 1 token）",
        }
    if code in (401, 403):
        return {
            **common,
            "status": "unauthorized",
            "message": "凭据被供应商拒绝，请检查 API Key",
        }
    if code == 404:
        # 404 有两种含义：模型不存在，或根本没有 /chat/completions
        if "model" in detail.lower() or model_code in detail:
            return {
                **common,
                "status": "model_not_found",
                "message": f"供应商不认识模型代号 {model_code}：{detail or 'model not found'}",
            }
        return {
            **common,
            "status": "endpoint_not_supported",
            "message": "该供应商未提供 /chat/completions，无法做模型级测试；连接本身可用",
        }
    if code == 429:
        return {
            **common,
            "status": "provider_unavailable",
            "message": "供应商限流（429），请稍后重试",
        }
    hint = f"：{detail}" if detail else ""
    # 400 且列表里查不到该代号时，最可能的原因就是模型名写错了
    if code == 400 and listed and model_code not in listed:
        hint += f"（供应商的模型列表里没有 {model_code}）"
    return {
        **common,
        "status": "model_rejected" if code == 400 else "provider_unavailable",
        "message": f"测试请求被供应商拒绝（HTTP {code}）{hint}",
    }


def _parse_models(response: httpx.Response) -> tuple[list[dict], int]:
    """兼容 OpenAI 的 {data:[{id, owned_by}]} 和纯数组 / 纯字符串数组三种返回。

    返回 (截断后的列表, 去重后的总数)；总数用于如实展示「供应商有多少个模型」。
    """
    try:
        payload = response.json()
    except ValueError:
        return [], 0
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return [], 0
    parsed: list[dict] = []
    for item in items:
        if isinstance(item, str):
            name = item.strip()
            if name:
                parsed.append({"id": name, "ownedBy": ""})
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("id") or item.get("name") or item.get("model") or "").strip()
        if name:
            parsed.append({"id": name, "ownedBy": str(item.get("owned_by") or "")})
    parsed.sort(key=lambda row: row["id"])
    return parsed[:MAX_MODELS], len(parsed)


def _error_detail(response: httpx.Response) -> str:
    """把供应商的错误说明抠出来：体面的报错比 HTTP 状态码有用得多。"""
    try:
        payload = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return text[:160]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("code") or ""
            return str(message)[:200]
        if isinstance(error, str):
            return error[:200]
        message = payload.get("message")
        if message:
            return str(message)[:200]
    return ""


def _connect_error_message(exc: httpx.HTTPError) -> str:
    """把底层异常翻译成用户能据此行动的原因，而不是只甩一个类名。"""
    text = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in text or "certificate" in text.lower():
        return (
            "TLS 证书校验失败：当前出站请求可能把域名固定成了 IP，"
            "或代理/中间人对该域名做了证书替换。请检查代理设置。"
        )
    if "SSLV3_ALERT_HANDSHAKE_FAILURE" in text or "handshake" in text.lower():
        return "TLS 握手被对端拒绝：该地址可能不支持当前 TLS 版本，或代理转发异常。"
    if "Name or service not known" in text or "getaddrinfo" in text:
        return "域名解析失败：请确认 Base URL 拼写正确、网络可访问外网 DNS。"
    if isinstance(exc, httpx.ConnectTimeout):
        return "连接超时：目标地址在 8 秒内未响应，请检查网络或代理是否可用。"
    if isinstance(exc, httpx.ProxyError):
        return "代理连接失败：环境变量里的代理不可用，请检查代理进程是否在运行。"
    if isinstance(exc, httpx.ConnectError):
        return f"无法建立连接：{text[:120]}"
    return f"请求失败：{type(exc).__name__} {text[:100]}"


def _result(
    status: str,
    message: str,
    http_status: int | None = None,
    latency_ms: int | None = None,
    models: list[dict] | None = None,
    model_count: int | None = None,
    truncated: bool = False,
) -> dict:
    rows = models or []
    return {
        "status": status,
        "message": message,
        "httpStatus": http_status,
        "latencyMs": latency_ms,
        "modelCount": model_count if model_count is not None else len(rows),
        "models": rows,
        "truncated": truncated,
    }
