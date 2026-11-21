"""质检端点 SSRF 回归：禁止内网，出站固定经过校验的 IP，不发送真实请求。"""

import asyncio
import json
import socket

import httpx
import pytest

from app.services import article_quality as AQ


@pytest.mark.parametrize("host", [
    "127.0.0.1", "169.254.169.254", "10.0.0.1", "172.16.0.1",
    "192.168.1.1", "2130706433", "127.1", "0x7f000001",
    "0177.0.0.1", "localhost", "[::1]", "[::ffff:127.0.0.1]",
])
def test_private_endpoint_rejected_without_request(host, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("拒绝的端点不得创建网络客户端")

    monkeypatch.setattr(AQ.httpx, "AsyncClient", forbidden)
    with pytest.raises(AQ.ApiError) as validation:
        AQ.check_endpoint(f"https://{host}/classify")
    assert validation.value.status_code == 400
    with pytest.raises(AQ.ApiError) as caught:
        asyncio.run(AQ.detect("待检测正文", key="secret", endpoint=f"https://{host}/classify"))
    assert caught.value.status_code == 400
    assert caught.value.detail["code"] == "unsafe_endpoint"
    assert "质检接口地址不安全" in caught.value.detail["message"]


def test_dns_private_endpoint_rejected_before_request(monkeypatch):
    monkeypatch.delenv("SSRF_TRUSTED_HOSTS", raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
    ])
    monkeypatch.setattr(AQ.httpx, "AsyncClient", lambda **kw: pytest.fail("不得发起请求"))
    with pytest.raises(AQ.ApiError) as caught:
        asyncio.run(AQ.detect("正文", key="secret", endpoint="https://quality.example/classify"))
    assert caught.value.status_code == 400
    assert "域名解析到禁止访问的地址" in caught.value.detail["message"]


def test_public_request_pins_ip_and_preserves_payload(monkeypatch):
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    calls = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
    ])

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://8.8.8.8/v1/classify"
        assert request.headers["host"] == "quality.example"
        assert request.headers["authorization"] == "Bearer secret"
        assert request.extensions["sni_hostname"] == "quality.example"
        assert json.loads(request.content) == {"text": "正文", "is_merge": False}
        return httpx.Response(200, json={"status": "success", "usage": {"total_tokens": 2}})

    real_client = httpx.AsyncClient
    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(AQ.httpx, "AsyncClient", client)
    result = asyncio.run(AQ.detect("正文", key="secret", endpoint="https://quality.example/v1/classify"))
    assert result == {"overall": None, "segments": [], "tokens": 2}
    assert len(calls) == 1
