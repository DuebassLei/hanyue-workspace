"""本轮「安全 / 正确性 / 队列可靠性」改动的回归网。

约定：所有 app 模块都在测试函数内延迟导入，避免 pytest 收集阶段就绑定
core.db 的 engine 到默认开发库（README 开发说明里有这条约束）。
"""

from __future__ import annotations

import json
import socket
from datetime import date, datetime, timedelta, timezone

TODAY = date.today().isoformat()

import pytest


def _headers(client, subject: str) -> dict:
    res = client.post("/api/v1/auth/login", json={"subject": subject, "displayName": subject})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def test_production_gate_rejects_weak_secret_and_dev_login():
    from app.core.config import Settings, assert_safe_configuration

    pg = "postgresql+psycopg://u:p@db:5432/hanyue"
    good = Settings(
        environment="production", secret_key="x" * 40,
        allow_dev_login=False, database_url=pg,
    )
    assert_safe_configuration(good)  # 不抛异常

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        assert_safe_configuration(
            Settings(environment="production", secret_key="dev-only-change-me",
                     allow_dev_login=False, database_url=pg)
        )
    with pytest.raises(RuntimeError, match="ALLOW_DEV_LOGIN"):
        assert_safe_configuration(
            Settings(environment="production", secret_key="x" * 40,
                     allow_dev_login=True, database_url=pg)
        )
    # 开发态不受门禁影响
    assert_safe_configuration(Settings(environment="development", secret_key="dev-only-change-me"))


def test_dev_login_can_be_disabled(client, monkeypatch):
    from app.modules import auth

    monkeypatch.setattr(auth.settings, "allow_dev_login", False)
    res = client.post("/api/v1/auth/login", json={"subject": "blocked"})
    assert res.status_code == 403
    assert res.json()["code"] == "dev_login_disabled"


def test_logout_revokes_the_current_token(client):
    headers = _headers(client, "hardening-logout")
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    res = client.post("/api/v1/auth/logout", headers=headers)
    assert res.status_code == 200 and res.json()["revoked"] is True
    denied = client.get("/api/v1/auth/me", headers=headers)
    assert denied.status_code == 401
    assert denied.json()["code"] == "unauthorized"


def test_secret_encryption_uses_random_nonce():
    from app.core.security import decrypt_secret, encrypt_secret

    first = encrypt_secret("sk-repeat")
    second = encrypt_secret("sk-repeat")
    assert first != second, "相同明文不能产生相同密文"
    assert decrypt_secret(first) == "sk-repeat"
    assert decrypt_secret(second) == "sk-repeat"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/v1",
        "https://localhost/v1",
        "https://[::1]/v1",
        "https://2130706433/v1",
        "https://0x7f000001/v1",
        "https://127.1/v1",
        "https://0177.0.0.1/v1",
        "https://user:pass@example.com/v1",
        "https://example.com:8080/v1",
        "https://example.com/v1#frag",
    ],
)
def test_ssrf_rejects_obfuscated_internal_addresses(url):
    from app.core import ssrf

    with pytest.raises(ssrf.UnsafeUrl):
        ssrf.validate_base_url(url)
    assert ssrf.validate_base_url("https://api.example.com/v1") == "https://api.example.com/v1"


def test_pin_url_fixes_the_validated_ip(monkeypatch):
    from app.core import ssrf

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    # 隔离代理环境：本机若配置了 HTTP(S)_PROXY，pin_url 会主动跳过 IP 固定
    monkeypatch.setattr(ssrf, "proxy_in_effect", lambda: False)
    pinned, headers, extensions = ssrf.pin_url("https://api.example.com/v1/chat")
    assert "93.184.216.34" in pinned
    assert headers["Host"] == "api.example.com"
    assert extensions["sni_hostname"] == "api.example.com"


def test_pin_url_skips_pinning_under_proxy(monkeypatch):
    """代理隧道下 sni_hostname 不生效，固定 IP 会让 TLS 按 IP 校验证书而失败。

    因此配置了代理时必须把原域名交给代理，但仍要完成内网地址拦截。
    """
    from app.core import ssrf

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    monkeypatch.setattr(ssrf, "proxy_in_effect", lambda: True)
    target, headers, extensions = ssrf.pin_url("https://api.example.com/v1/chat")
    assert target == "https://api.example.com/v1/chat", "代理下不应替换成 IP"
    assert headers == {}
    assert extensions == {}

    # 内网地址在代理模式下同样被拦截（校验环节不能削弱）
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))],
    )
    with pytest.raises(ssrf.UnsafeUrl):
        ssrf.pin_url("https://internal.example.com/v1")


def test_proxy_in_effect_reads_env(monkeypatch):
    from app.core import ssrf

    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    assert ssrf.proxy_in_effect() is False
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    assert ssrf.proxy_in_effect() is True
    monkeypatch.setenv("HTTPS_PROXY", "   ")
    assert ssrf.proxy_in_effect() is False, "空白值视为未配置"


def test_generation_without_provider_errors_and_does_not_save_version(client):
    headers = _headers(client, "hardening-no-model")
    item = client.post("/api/v1/content", json={"title": "无模型稿件"}, headers=headers).json()
    with client.stream(
        "POST",
        "/api/v1/ai/text-generations",
        json={"taskType": "article", "prompt": "写点东西", "contentItemId": item["id"]},
        headers=headers,
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    assert "provider_unavailable" in body
    assert "generation.saved" not in body
    detail = client.get(f"/api/v1/content/{item['id']}", headers=headers).json()
    assert len(detail["versions"]) == 1, "报错时不得写入 AI 稿件版本"


def test_run_job_claims_atomically_only_once(client, monkeypatch):
    from app import models
    from app.core.db import SessionLocal
    from app.services import job_runner

    db = SessionLocal()
    job = models.BackgroundJob(
        owner_user_id="hardening-owner", queue="unknown", kind="test",
        payload={}, status="queued",
    )
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    calls: list[str] = []
    monkeypatch.setattr(job_runner, "_execute", lambda db, job: calls.append(job.id) or {"ok": True})
    job_runner.run_job(job_id)
    job_runner.run_job(job_id)  # 重复投递 / 并发消费者不得二次执行
    assert calls == [job_id]

    db = SessionLocal()
    row = db.get(models.BackgroundJob, job_id)
    assert row.status == "succeeded"
    db.close()


def test_stale_running_job_is_recovered(client):
    from app import models
    from app.core import queue
    from app.core.db import SessionLocal

    db = SessionLocal()
    job = models.BackgroundJob(
        owner_user_id="hardening-owner", queue="unknown", kind="test",
        payload={}, status="running", attempt_count=1,
        started_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    assert queue.recover_stale_running(lease_seconds=1) >= 1
    db = SessionLocal()
    assert db.get(models.BackgroundJob, job_id).status == "queued"
    db.close()


def test_evidence_requires_real_dates_url_and_scopes(monkeypatch, tmp_path):
    from app.connectors import evidence

    def load(verified_at, evidence_url, scopes, evidence_date=TODAY):
        path = tmp_path / "evidence.json"
        path.write_text(
            json.dumps({
                "verifiedAt": verified_at,
                "platforms": {
                    "wechat": {
                        "confirmed": True,
                        "evidenceDate": evidence_date,
                        "evidenceUrl": evidence_url,
                        "scopes": scopes,
                        "publish": True,
                    }
                },
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("PLATFORM_EVIDENCE_FILE", str(path))
        return evidence.load_evidence()

    good = load(TODAY, "https://open.weixin.qq.com/doc", ["publish"])
    assert good["platforms"]["wechat"]["confirmed"] is True
    assert load("x", "https://open.weixin.qq.com/doc", ["publish"])["platforms"]["wechat"]["confirmed"] is False
    assert load(TODAY, "x", ["publish"])["platforms"]["wechat"]["confirmed"] is False
    assert load(TODAY, "https://open.weixin.qq.com/doc", [])["platforms"]["wechat"]["confirmed"] is False
    assert load(TODAY, "https://open.weixin.qq.com/doc", ["publish"], "2020-01-01")["platforms"]["wechat"]["confirmed"] is False


def test_upload_session_respects_max_bytes_alias(client):
    headers = _headers(client, "hardening-session")
    res = client.post("/api/v1/assets/upload-sessions?maxBytes=1000", headers=headers)
    assert res.status_code == 200
    assert res.json()["maxBytes"] == 1000, "camelCase 查询参数必须真正绑定到后端变量"


def test_upload_enforces_global_size_limit(client, monkeypatch):
    from app.modules import assets

    monkeypatch.setattr(assets.settings, "max_upload_bytes", 8)
    headers = _headers(client, "hardening-upload")
    res = client.post(
        "/api/v1/assets",
        files={"file": ("tiny.png", b"0123456789", "image/png")},
        headers=headers,
    )
    assert res.status_code == 400
    assert res.json()["code"] == "payload_too_large"
