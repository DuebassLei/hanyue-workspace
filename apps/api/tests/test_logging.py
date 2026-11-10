"""运行日志：请求 ID 透传与访问日志记录。"""

import logging


def test_health_returns_request_id(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.headers.get("X-Request-ID")


def test_request_id_is_propagated(client):
    res = client.get("/health", headers={"X-Request-ID": "trace-abc_123"})
    assert res.headers["X-Request-ID"] == "trace-abc_123"


def test_request_id_strips_unsafe_characters(client):
    res = client.get("/health", headers={"X-Request-ID": "bad<script>"})
    request_id = res.headers["X-Request-ID"]
    assert "<" not in request_id and ">" not in request_id
    assert request_id == "badscript"


def test_access_log_contains_status_and_request_id(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.access"):
        res = client.get("/health", headers={"X-Request-ID": "req-42"})
    assert res.status_code == 200

    record = next(
        r
        for r in caplog.records
        if r.name == "app.access" and "/health" in r.getMessage()
    )
    assert "200" in record.getMessage()
    assert record.request_id == "req-42"
