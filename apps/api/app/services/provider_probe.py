"""模型连接真实探测（修复 README「测试」名不副实的问题）。

绝不因为「密钥非空」就返回 connected：必须真的向供应商发一次请求，
按 HTTP 状态码给出结论，并把失败原因原样回传。
"""

from __future__ import annotations

import time

import httpx

from ..core import ssrf

# 已实现协议适配器。前端出现的其他选项在此显式登记为未实现，不允许静默回退。
SUPPORTED_ADAPTERS = {"openai_compatible": "OpenAI 兼容协议"}
UNIMPLEMENTED_ADAPTERS = {"vendor_native": "供应商原生协议适配器尚未接入"}


def adapter_status(provider_type: str) -> tuple[bool, str]:
    if provider_type in SUPPORTED_ADAPTERS:
        return True, SUPPORTED_ADAPTERS[provider_type]
    if provider_type in UNIMPLEMENTED_ADAPTERS:
        return False, UNIMPLEMENTED_ADAPTERS[provider_type]
    return False, f"未知的供应商协议：{provider_type}"


def probe_connection(base_url: str, api_key: str, provider_type: str) -> dict:
    """返回 {status, message, httpStatus, latencyMs, modelCount, checkedAt}。

    status 取值：connected | unauthorized | adapter_unavailable | invalid_base_url |
    missing_credential | provider_unavailable | provider_unreachable | endpoint_not_supported
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
        count = 0
        try:
            data = response.json().get("data")
            count = len(data) if isinstance(data, list) else 0
        except ValueError:
            pass
        return _result(
            "connected",
            f"供应商已响应（{label}），连通性验证通过",
            http_status=code,
            latency_ms=latency,
            model_count=count,
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
    model_count: int = 0,
) -> dict:
    return {
        "status": status,
        "message": message,
        "httpStatus": http_status,
        "latencyMs": latency_ms,
        "modelCount": model_count,
    }
