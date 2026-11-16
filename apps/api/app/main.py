from __future__ import annotations

import shutil
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .core import pagination, queue
from .core.config import get_settings
from .core.db import SessionLocal, init_db
from .core.deps import ApiError
from .core.logging import RequestLoggingMiddleware, configure_logging, get_logger
from .modules import (
    agent,
    ai_content,
    ai_images,
    assets,
    auth,
    cards,
    characters,
    content,
    dashboard,
    handdraw,
    hot_topics,
    jobs,
    logs,
    models_module,
    platform_auth,
    preferences,
    prompts,
    publishing,
    skills,
    novels,
    stickers,
    storyboards,
    topics,
    videos,
    wechat_mp,
    workspaces,
)
from .services import handdraw as handdraw_import
from .services import skill_registry

settings = get_settings()
configure_logging(settings)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as session:
        handdraw_import.import_library(session)
        # 内置 Skill 在启动时同步；读接口不再承担写库/迁移职责
        skill_registry.sync_builtin_skills(session)
    publishing.sweep_legacy_platform_accounts()
    # 线程池后端没有独立 Worker：API 重启后自己回收崩溃遗留的 running 任务，
    # 并重投启动前创建、尚未开始的任务。Redis 后端由 Worker 负责，避免重复投递。
    if queue.backend_name() == "threadpool":
        stale = queue.recover_stale_running()
        recovered = queue.recover_pending()
        if stale or recovered:
            logger.info("API 启动恢复：崩溃遗留 %s 个、未开始 %s 个", stale, recovered)
    _sweep_video_tmp()
    yield


def _sweep_video_tmp() -> None:
    """清理图文成片中间产物残留：失败/取消/进程被杀时 handler 的 rmtree 不会执行，
    单项目峰值 ~80MB，必须兜底。判据用目录 mtime——运行中任务每个阶段都写文件，
    mtime 必然新鲜，24h 阈值绝不误伤活跃渲染；失败现场也保留满一天供取证。"""
    root = get_settings().storage_dir / "video_tmp"
    if not root.is_dir():
        return
    cutoff = time.time() - 24 * 3600
    swept = 0
    for child in root.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                swept += 1
        except OSError:
            continue
    if swept:
        logger.info("video_tmp 启动清理：回收过期目录 %s 个", swept)


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
    prompts,
    handdraw,
    assets,
    ai_images,
    ai_content,
    hot_topics,
    publishing,
    platform_auth,
    wechat_mp,
    agent,
    skills,
    dashboard,
    jobs,
    logs,
    cards,
    characters,
    novels,
    stickers,
    storyboards,
    videos,
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
