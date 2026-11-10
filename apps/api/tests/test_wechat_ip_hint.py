"""微信 40164（IP 白名单）的错误信息必须带上具体 IP，否则用户无从配置。"""

from __future__ import annotations


def test_describe_errcode_extracts_ipv4():
    from app.connectors.wechat_mp import describe_errcode

    code, message, retryable = describe_errcode(
        40164, "invalid ip 183.6.44.12, not in whitelist rid: 6512abc-123"
    )
    assert code == "ip_not_whitelisted"
    assert "183.6.44.12" in message, "错误信息必须包含微信看到的来源 IP"
    assert "白名单" in message
    assert retryable is False


def test_describe_errcode_handles_ipv6_mapped_form():
    """微信常返回 `invalid ip 1.2.3.4 ipv6 ::ffff:1.2.3.4`，不应把 IPv4 重复列出。"""
    from app.connectors.wechat_mp import describe_errcode

    _, message, _ = describe_errcode(
        40164, "invalid ip 1.2.3.4 ipv6 ::ffff:1.2.3.4, not in whitelist"
    )
    assert "1.2.3.4" in message
    assert message.count("1.2.3.4") == 1, "IPv4 不应出现两次"


def test_describe_errcode_falls_back_when_no_ip():
    from app.connectors.wechat_mp import describe_errcode

    _, message, _ = describe_errcode(40164, "something unexpected")
    assert "白名单" in message
    assert "检测出口 IP" in message, "拿不到 IP 时应引导用户去主动检测"


def test_describe_errcode_leaves_other_codes_untouched():
    from app.connectors.wechat_mp import describe_errcode

    code, message, retryable = describe_errcode(40001, "invalid credential")
    assert code == "invalid_token"
    assert "access_token" in message
    assert retryable is True


def test_extract_ips_ignores_private_and_garbage():
    from app.connectors.wechat_mp import _extract_ips

    assert _extract_ips("") == []
    assert _extract_ips("no ip here") == []
    # 非法网段（999.1.1.1）不应被当成 IP
    assert _extract_ips("invalid ip 999.1.1.1") == []


def test_egress_ip_endpoint_returns_shape(client, monkeypatch):
    """接口必须返回统一结构；探测失败也要给出 hint 而不是编造 IP。"""
    from app.services import egress_ip

    monkeypatch.setattr(
        egress_ip,
        "detect_egress_ip",
        lambda force=False: {
            "ip": "203.0.113.9",
            "source": "test",
            "checkedAt": 0,
            "cached": False,
            "attempts": [],
        },
    )
    headers = _headers(client, "egress-user")
    res = client.get("/api/v1/wechat-mp/egress-ip", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["ip"] == "203.0.113.9"
    assert "203.0.113.9" in body["hint"]


def test_egress_ip_endpoint_failure_has_no_fake_ip(client, monkeypatch):
    from app.services import egress_ip

    monkeypatch.setattr(
        egress_ip,
        "detect_egress_ip",
        lambda force=False: {
            "ip": "",
            "source": "",
            "checkedAt": 0,
            "cached": False,
            "attempts": [{"url": "https://x", "ok": False, "error": "boom"}],
        },
    )
    headers = _headers(client, "egress-user-2")
    body = client.get("/api/v1/wechat-mp/egress-ip", headers=headers).json()
    assert body["ip"] == ""
    assert "手动获取" in body["hint"]


def _headers(client, subject: str) -> dict:
    res = client.post(
        "/api/v1/auth/login", json={"subject": subject, "displayName": subject}
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}
