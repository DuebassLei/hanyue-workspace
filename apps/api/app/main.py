from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .core import pagination, queue
from .core.config import get_settings
from .core.db import init_db
from .core.deps import ApiError
from .core.logging import RequestLoggingMiddleware, configure_logging, get_logger
from .modules import (
    agent,
    ai_content,
    ai_images,
    assets,
    auth,
    cards,
    content,
    dashboard,
    jobs,
    models_module,
    platform_auth,
    preferences,
    publishing,
    topics,
    wechat_mp,
    workspaces,
)

settings = get_settings()
configure_logging(settings)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    publishing.sweep_legacy_platform_accounts()
    # 线程池后端没有独立 Worker：API 重启后自己回收崩溃遗留的 running 任务，
    # 并重投启动前创建、尚未开始的任务。Redis 后端由 Worker 负责，避免重复投递。
    if queue.backend_name() == "threadpool":
        stale = queue.recover_stale_running()
        recovered = queue.recover_pending()
        if stale or recovered:
            logger.info("API 启动恢复：崩溃遗留 %s 个、未开始 %s 个", stale, recovered)
    yield


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[pagination.CURSOR_HEADER, "X-Request-ID"],
)

# 放在 CORS 之后添加，使其位于中间件链最外层：CORS 预检请求也会被记录。
app.add_middleware(RequestLoggingMiddleware)

for module in (
    auth,
    preferences,
    topics,          # 必须在 workspaces 之前：/workspaces/current 不能被 {workspace_id} 抢匹配
    workspaces,
    content,
    models_module,
    assets,
    ai_images,
    ai_content,
    publishing,
    platform_auth,
    wechat_mp,
    agent,
    dashboard,
    jobs,
    cards,
):
    app.include_router(module.router, prefix=settings.api_prefix)


@app.exception_handler(ApiError)
async def api_error_handler(_request, exc: ApiError):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=exc.status_code, content=exc.detail)


@app.exception_handler(pagination.InvalidCursor)
async def invalid_cursor_handler(_request, exc: pagination.InvalidCursor):
    """游标伪造/损坏时明确报 400，不静默回退到第一页。"""
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=400,
        content=ApiError("invalid_cursor", str(exc)).detail,
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": settings.app_name}
