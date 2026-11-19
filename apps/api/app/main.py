from __future__ import annotations

import shutil
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from . import models
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
    image_styles,
    jobs,
    logs,
    models_module,
    platform_auth,
    preferences,
    prompts,
    publishing,
    rbac,
    skills,
    novels,
    schedules,
    stickers,
    storyboards,
    topics,
    videos,
    wechat_mp,
    workspaces,
)
from .services import handdraw as handdraw_import
from .services import rbac_service, scheduled_tasks, scheduler, skill_registry

settings = get_settings()
configure_logging(settings)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as session:
        # RBAC 播种：补权限点/内置角色/菜单，并保证存量用户至少有一个角色
        rbac_service.seed(session)
        handdraw_import.import_library(session)
        # 内置 Skill 在启动时同步；读接口不再承担写库/迁移职责
        skill_registry.sync_builtin_skills(session)
        # 出厂配图风格：固化封面模板，Studio 的封面模板下拉开箱即用
        image_styles.seed_factory_styles(session)
    publishing.sweep_legacy_platform_accounts()
    # 线程池后端没有独立 Worker：API 重启后自己回收崩溃遗留的 running 任务，
    # 并重投启动前创建、尚未开始的任务。Redis 后端由 Worker 负责，避免重复投递。
    if queue.backend_name() == "threadpool":
        stale = queue.recover_stale_running()
        recovered = queue.recover_pending()
        if stale or recovered:
            logger.info("API 启动恢复：崩溃遗留 %s 个、未开始 %s 个", stale, recovered)
    _sweep_video_tmp()
    _start_scheduler()
    yield
    scheduler.stop()


def _start_scheduler() -> None:
    """定时任务：先对账，再装配调度器（docs/scheduled-tasks.md §2.2 / §2.6）。

    调度器起不来不能拖垮整个 API——接口与表还能用，用户至少能看到「为什么没跑」。
    system 类型任务主要由登录路径播种（modules/auth.py），这里补一次：
    从旧库升级上来、库里已有用户但没有 system 任务的情况。
    """
    try:
        with SessionLocal() as session:
            first_user = session.scalar(select(models.User).order_by(models.User.created_at))
            if first_user is not None:
                scheduled_tasks.ensure_system_tasks(session, first_user.id, first_user.timezone)
            report = scheduled_tasks.reconcile_runs(session)
            if any(report.values()):
                logger.info("定时任务启动对账：%s", report)
            scheduler.start(session)
    except Exception as exc:
        logger.exception("定时调度器启动失败，定时任务不会触发（接口与表仍可用）：%s", exc)


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
    image_styles,
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
    schedules,
    rbac,
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
