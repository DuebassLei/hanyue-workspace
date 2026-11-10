"""运行日志基础设施：统一格式、请求访问日志、异常堆栈与文件轮转。

- 控制台与文件共用同一格式，文件默认写入 `storage/logs/<service>.log`。
- 每个 HTTP 请求生成/透传 `X-Request-ID`，并注入该请求内所有日志记录。
- 未捕获异常记录完整堆栈后原样抛出，交给上层错误处理，不吞异常。
"""

from __future__ import annotations

import logging
import sys
import time
import uuid
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import Settings, get_settings

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_MAX_LEN = 64

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_configured = False


class RequestIdFilter(logging.Filter):
    """把当前请求 ID 注入日志记录，兼容无请求上下文的后台线程。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def _build_formatter() -> logging.Formatter:
    return logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] [%(request_id)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def configure_logging(settings: Settings | None = None, *, service: str = "api") -> None:
    """初始化根日志器；重复调用直接返回，避免 reload 后重复挂 handler。"""
    global _configured
    if _configured:
        return

    settings = settings or get_settings()
    level = getattr(logging, settings.log_level.strip().upper(), logging.INFO)
    formatter = _build_formatter()
    request_filter = RequestIdFilter()

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.addFilter(request_filter)
    root.addHandler(console)

    log_dir = Path(settings.log_dir) if settings.log_dir else settings.storage_dir / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / f"{service}.log",
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(request_filter)
        root.addHandler(file_handler)
    except OSError as exc:
        root.warning("日志目录不可写，仅输出到控制台：%s（%s）", log_dir, exc)

    # uvicorn 自己的 handler 会绕过统一格式；改为冒泡到根日志器。
    # 访问日志由 RequestLoggingMiddleware 记录（含耗时与 requestId），避免重复。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True

    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def _read_request_id(scope: Scope) -> str:
    """优先沿用调用方传入的 ID，限制长度与字符集，防止日志注入。"""
    for key, value in scope.get("headers") or []:
        if key.lower() != REQUEST_ID_HEADER.lower().encode():
            continue
        candidate = value.decode("latin-1", errors="ignore").strip()
        cleaned = "".join(ch for ch in candidate if ch.isalnum() or ch in "-_")[:REQUEST_ID_MAX_LEN]
        if cleaned:
            return cleaned
    return uuid.uuid4().hex[:12]


class RequestLoggingMiddleware:
    """纯 ASGI 中间件：不包装响应体，因此不会影响 SSE 流式输出。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.logger = logging.getLogger("app.access")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _read_request_id(scope)
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500
        error: BaseException | None = None

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # 记录堆栈后原样抛出，保持框架原有错误语义
            error = exc
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            method = scope.get("method", "-")
            path = scope.get("path", "-")
            client = scope.get("client") or ("-", 0)
            if error is not None:
                self.logger.error(
                    "%s %s -> 500 %.1fms client=%s",
                    method,
                    path,
                    duration_ms,
                    client[0],
                    exc_info=(type(error), error, error.__traceback__),
                )
            else:
                self.logger.info(
                    "%s %s -> %s %.1fms client=%s",
                    method,
                    path,
                    status_code,
                    duration_ms,
                    client[0],
                )
            request_id_var.reset(token)
