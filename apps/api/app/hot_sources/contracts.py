"""热榜采集统一数据契约（规范 §2）。

采集器只负责「抓取 + 结构化」：产出 HotRawItem / CollectResult，
不做去重、不跨源合并、不评分、不归一化标题——那些是 hot_topics._ingest
与 hot_ranking 的职责，避免两套逻辑各自漂移。

所有出站请求都必须经 CollectContext.safe_get → core.ssrf.safe_request，
采集器不得自行 new httpx client 直连外部域名。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from ..core import ssrf


def utc_now() -> str:
    """带时区的 ISO-8601 采集时刻；同一批用同一个值，保证评分可复现。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class HotSourcesError(RuntimeError):
    """采集失败。`code` 是机器可读错误码，`notes` 会汇总进 /hot-topics/degraded。

    单源失败时由 Adapter 包成 CollectResult(ok=false) 返回，不向外抛；
    只有编排器判定「全源失败」时才抛出本异常，由上层转成 502。
    """

    def __init__(
        self, message: str, notes: list[str] | None = None, code: str = "fetch_failed"
    ) -> None:
        super().__init__(message)
        self.message = message
        self.notes = notes or []
        self.code = code


@dataclass
class HotRawItem:
    """标准化榜单行。采集器不做归一化，title 原样交给下游。"""

    platform: str
    title: str
    captured_at: str
    rank: int | None = None
    hot: int | None = None
    url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict:
        """对齐 services/hot_topics._ingest 期望的行结构。

        captured_at / extra 不进主表：前者由 refresh 统一注入评分基准，
        后者是可选扩展，下游忽略。
        """
        return {
            "platform": self.platform,
            "rank": self.rank,
            "title": self.title,
            "hot": self.hot,
            "url": self.url,
        }


@dataclass
class CollectResult:
    """一次采集的批次结果。单源失败不抛异常，用 ok=false + error_code 表达。"""

    platform: str
    ok: bool
    items: list[HotRawItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error_code: str | None = None
    fetched_at: str = ""


@dataclass
class CollectContext:
    """编排器注入给采集器的受控能力集合。

    `source` 是 catalog 中该源的声明（含 url / headers / options / retry 等）。
    采集器只通过下面三个入口拿数据，拿不到就返回 ok=false，不自己造请求。
    """

    source: dict[str, Any]
    now_fn: Callable[[], str] = utc_now
    timeout: float = 12.0
    retries: int = 1
    # None 表示运行时取 ssrf.safe_request；这样测试/运维对 ssrf 模块的 monkeypatch 能生效
    request_fn: Callable[..., httpx.Response] | None = None

    @property
    def config(self) -> dict[str, Any]:
        """该源在 catalog 里的声明；采集器用它读 url / options / 环境变量。"""
        return self.source

    def now(self) -> str:
        return self.now_fn()

    def safe_get(
        self, url: str, headers: dict[str, str] | None = None, *, retries: int | None = None
    ) -> httpx.Response:
        """走 ssrf.safe_request 的 GET：HTTPS/端口白名单、逐跳校验、不手搓 client。

        统一超时与重试；3xx 按失败处理（避免把重定向页当 JSON 解析成空榜单）。
        失败时抛 HotSourcesError，由 Adapter 翻成 CollectResult(ok=false)。
        """
        last: HotSourcesError | None = None
        attempts = max(0, self.retries if retries is None else retries) + 1
        for _ in range(attempts):
            try:
                request_fn = self.request_fn or ssrf.safe_request
                response = request_fn(
                    "GET",
                    url,
                    headers={"Accept": "application/json,text/plain,*/*", **(headers or {})},
                    timeout=self.timeout,
                )
            except ssrf.UnsafeUrl as exc:
                # SSRF 拦截是确定性失败，重试也不会变安全，直接记码
                raise HotSourcesError(f"SSRF 拦截：{exc}", code="ssrf_blocked") from exc
            except httpx.HTTPError as exc:
                last = HotSourcesError(f"请求失败：{type(exc).__name__}", code="fetch_failed")
                continue
            if response.status_code >= 300:
                last = HotSourcesError(f"HTTP {response.status_code}", code="http_status")
                continue
            return response
        raise last or HotSourcesError("请求失败", code="fetch_failed")
