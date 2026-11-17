"""统一日志查看（运维）：直读磁盘日志文件，不入库。

- `GET /logs`：tail 或按字节偏移增量拉取，支持服务/级别/关键词/requestId 过滤。
- `GET /logs/summary`：各服务最近日志的级别计数，供前端 Tab 角标提示。
权限：登录用户可看（复用统一 token 鉴权），不做角色区分。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..core import log_reader
from ..core.deps import ApiError, CurrentUser

from ..core.rbac import require_permission

router = APIRouter(tags=["logs"], dependencies=[require_permission("logs.view")])

_VALID_SERVICES = ("api", "worker")


@router.get("/logs")
def read_logs(
    user: CurrentUser,
    service: str = Query(default="api"),
    offset: int | None = Query(default=None, ge=0),
    # 上限放宽到 20000：前端「导出日志文本」一次拉最近 1 万行（带过滤条件）
    limit: int = Query(default=200, le=20000),
    min_level: str | None = Query(default=None, alias="level"),
    keyword: str | None = Query(default=None, max_length=200),
    request_id: str | None = Query(default=None, alias="requestId", max_length=64),
) -> dict:
    if service not in _VALID_SERVICES:
        raise ApiError("invalid_service", f"不支持的日志服务：{service}")
    min_level = min_level.upper() if min_level else None
    if min_level and min_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ApiError("invalid_level", f"不支持的日志级别：{min_level}")
    page = log_reader.read_logs(
        service,
        offset=offset,
        limit=limit,
        min_level=min_level,
        keyword=keyword,
        request_id=request_id,
    )
    return {
        "service": service,
        "entries": [
            {
                "ts": entry.ts,
                "level": entry.level,
                "logger": entry.logger,
                "requestId": entry.request_id,
                "message": entry.message,
                "raw": entry.raw,
            }
            for entry in page.entries
        ],
        "counts": page.counts,
        "truncated": page.truncated,
        "hasMore": page.has_more,
        "fileSize": page.file_size,
        "modifiedAt": page.modified_at,
        "nextOffset": page.next_offset,
        "services": log_reader.available_services(),
    }


@router.get("/logs/summary")
def logs_summary(user: CurrentUser) -> dict:
    data = log_reader.summary()
    services = {
        name: {
            "errorCount": counts["counts"].get("ERROR", 0) + counts["counts"].get("CRITICAL", 0),
            "warnCount": counts["counts"].get("WARNING", 0),
            "fileSize": counts["fileSize"],
            "modifiedAt": counts["modifiedAt"],
        }
        for name, counts in data["services"].items()
    }
    return {"services": services}
