from __future__ import annotations

from contextlib import asynccontextmanager
from threading import Thread

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
    image_styles,
    jobs,
    logs,
    models_module,
    originality,
    originality_reports,
    pet_chat,
    platform_auth,
    preferences,
    prompts,
    publishing,
    rbac,
    script_schedules,
    skill_center,
    skills,
    novels,
    schedules,
    stickers,
    storyboards,
    studio,
    studio_layout,
    studio_media,
    studio_publish,
    style_presets,
    topics,
    wechat_mp,
    workspaces,
)
from .services import handdraw as handdraw_import
from .services import originality_service
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
    from .services import draft_dispatcher
    draft_dispatcher.start()
    _start_scheduler()
    _warmup_originality()
    yield
    draft_dispatcher.stop()
    scheduler.stop()


def _warmup_originality() -> None:
    """后台预热原创度分析器。

    jieba 首次调用要加载约 1s 的词典，落点如果正好是用户第一次点「检测」，
    会表现成莫名卡顿。丢到后台线程里，API 一起来就开始加载，不拖慢启动。
    """
    def run() -> None:
        try:
            originality_service.warmup()
        except Exception:  # 预热失败不影响功能，首次检测时再加载一次即可
            logger.debug("原创度分析器预热失败", exc_info=True)

    Thread(target=run, name="originality-warmup", daemon=True).start()


def _start_scheduler() -> None:
    """定时任务：先对账，再装配调度器（docs/scheduled-tasks.md §2.2 / §2.6）。

    调度器起不来不能拖垮整个 API——接口与表还能用，用户至少能看到「为什么没跑」。
    system 类型任务主要由登录路径播种（modules/auth.py），这里补一次：
    从旧库升级上来、库里已有用户但没有 system 任务的情况。
    """
    try:
        with SessionLocal() as session:
            report = scheduled_tasks.reconcile_runs(session)
            if any(report.values()):
                logger.info("定时任务启动对账：%s", report)
            scheduler.start(session)
    except Exception as exc:
        logger.exception("定时调度器启动失败，定时任务不会触发（接口与表仍可用）：%s", exc)


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
    studio,
    studio_layout,
    studio_media,
    studio_publish,
    style_presets,
    script_schedules,
    schedules,
    rbac,
    skill_center,
    pet_chat,
):
    app.include_router(module.router, prefix=settings.api_prefix)

# 原创度检测（嵌套在 content 路由下）
app.include_router(originality.router, prefix=f"{settings.api_prefix}/content")
# 原创度检测的历史记录与报告导出（/api/v1/originality/...）
app.include_router(originality_reports.router, prefix=settings.api_prefix)


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
