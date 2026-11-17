"""统一日志查看接口：tail / 增量偏移 / 过滤 / 概览 / 鉴权。"""

from __future__ import annotations

from app.core import log_reader
from app.core.config import get_settings

from .conftest import login


def _write_log(tmp_path, service: str, lines: list[str]):
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{service}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_dir


def _line(ts: str, level: str, name: str, rid: str, msg: str) -> str:
    return f"{ts} {level:<7} [{name}] [{rid}] {msg}"


class _FakeSettings:
    def __init__(self, root):
        from pathlib import Path

        self.storage_dir = Path(root)
        self.log_dir = None
        self.log_backup_count = 5


def test_read_tail_and_counts(tmp_path):
    _write_log(
        tmp_path,
        "api",
        [
            _line("2026-11-15 06:00:01", "INFO", "app.access", "r1", "GET /api/v1/jobs -> 200 1.2ms"),
            _line("2026-11-15 06:00:02", "WARNING", "app.services.ai", "r2", "provider slow"),
            _line("2026-11-15 06:00:03", "ERROR", "app.services.ai", "r3", "provider_unavailable"),
        ],
    )
    page = log_reader.read_logs("api", settings=_FakeSettings(tmp_path))
    assert len(page.entries) == 3
    assert page.counts["ERROR"] == 1 and page.counts["INFO"] == 1
    assert page.entries[-1].level == "ERROR"
    assert page.entries[-1].request_id == "r3"


def test_filter_level_keyword_and_request_id(tmp_path):
    _write_log(
        tmp_path,
        "api",
        [
            _line("2026-11-15 06:00:01", "INFO", "app.access", "r1", "GET /health -> 200"),
            _line("2026-11-15 06:00:02", "ERROR", "app.worker", "r2", "publish failed"),
            _line("2026-11-15 06:00:03", "ERROR", "app.worker", "r3", "publish failed"),
        ],
    )
    settings = _FakeSettings(tmp_path)
    errors = log_reader.read_logs("api", settings=settings, min_level="ERROR")
    assert [entry.request_id for entry in errors.entries] == ["r2", "r3"]
    by_rid = log_reader.read_logs("api", settings=settings, request_id="r3")
    assert len(by_rid.entries) == 1
    by_kw = log_reader.read_logs("api", settings=settings, keyword="health")
    assert len(by_kw.entries) == 1


def test_forward_offset_incremental(tmp_path):
    log_dir = _write_log(tmp_path, "worker", [_line("2026-11-15 06:00:01", "INFO", "w", "a1", "first")])
    settings = _FakeSettings(tmp_path)
    tail = log_reader.read_logs("worker", settings=settings)
    assert len(tail.entries) == 1
    offset = len((log_dir / "worker.log").read_bytes())
    with (log_dir / "worker.log").open("a", encoding="utf-8") as fh:
        fh.write(_line("2026-11-15 06:00:05", "ERROR", "w", "a2", "second") + "\n")
    forward = log_reader.read_logs("worker", settings=settings, offset=offset)
    assert len(forward.entries) == 1
    assert forward.entries[0].message == "second"
    assert forward.next_offset > offset


def test_offset_reset_on_rotation(tmp_path):
    log_dir = _write_log(tmp_path, "api", [_line("2026-11-15 06:00:01", "INFO", "a", "x", "old")] * 10)
    # 模拟轮转后文件被清空重写
    (log_dir / "api.log").write_text("", encoding="utf-8")
    page = log_reader.read_logs("api", settings=_FakeSettings(tmp_path), offset=99999)
    assert page.next_offset == 0
    assert page.entries == []


def test_tail_large_limit_not_capped_by_scan_window(tmp_path):
    # 导出场景：limit=10000 时扫描预算应随 limit 放大，不能只拿到默认 4MB 窗口内的尾部
    lines = [
        _line("2026-11-15 06:00:00", "INFO", "app.bulk", f"b{i}", "x" * 200)
        for i in range(30000)
    ]
    _write_log(tmp_path, "api", lines)
    page = log_reader.read_logs("api", settings=_FakeSettings(tmp_path), limit=10000)
    assert len(page.entries) == 10000
    assert page.entries[0].request_id == "b20000"
    # 文件比扫描窗口更大时 has_more=True 是预期语义（还有更早的日志未展示）
    assert page.has_more


def test_summary_counts(tmp_path):
    _write_log(
        tmp_path,
        "api",
        [
            _line("2026-11-15 06:00:01", "INFO", "a", "x", "ok"),
            _line("2026-11-15 06:00:02", "ERROR", "a", "y", "boom"),
        ],
    )
    data = log_reader.summary(settings=_FakeSettings(tmp_path))
    assert data["services"]["api"]["counts"]["ERROR"] == 1


def test_logs_endpoint_requires_auth(client):
    assert client.get("/api/v1/logs").status_code == 401


def test_logs_endpoint_tail_and_bad_service(client, tmp_path, monkeypatch):
    headers = login(client)
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "api.log").write_text(
        _line("2026-11-15 06:00:01", "ERROR", "app.test", "z1", "endpoint boom") + "\n", encoding="utf-8"
    )
    fake = _FakeSettings(tmp_path)  # noqa: F841  # 预留：如需按 settings 注入可切换此路径
    monkeypatch.setattr(log_reader, "log_dir", lambda settings=None: log_dir)
    # read_logs/summary 内部经 log_dir(None) 取目录，lambda 忽略入参即可全局生效
    res = client.get("/api/v1/logs", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["entries"][0]["level"] == "ERROR"
    assert body["entries"][0]["requestId"] == "z1"
    assert "api" in body["services"]

    bad = client.get("/api/v1/logs", params={"service": "nginx"}, headers=headers)
    assert bad.status_code == 400
    assert bad.json()["code"] == "invalid_service"

    summary = client.get("/api/v1/logs/summary", headers=headers)
    assert summary.status_code == 200
    assert summary.json()["services"]["api"]["errorCount"] == 1
