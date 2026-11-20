"""文章原创度检测 API 路由。

POST /api/v1/content/analyze-originality              - 执行分析
GET  /api/v1/content/{content_version_id}/originality - 获取最新结果
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from .. import models, schemas
from ..core.deps import ApiError, CurrentUser, DbSession, default_workspace, not_found
from ..services import originality_service

router = APIRouter(tags=["originality"])


def json_items(payload: dict | None) -> list[dict]:
    """JSONB 列可能是 NULL 或有历史结构，统一兜底成 list。"""
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    return items if isinstance(items, list) else []


def load_version(
    db: DbSession, user: models.User, content_version_id: str
) -> tuple[models.Workspace, models.ContentVersion]:
    """取出当前用户工作区下的稿件版本，不存在即 404。"""
    workspace = default_workspace(db, user)
    content_version = db.scalar(
        select(models.ContentVersion)
        .join(
            models.ContentItem,
            models.ContentVersion.content_item_id == models.ContentItem.id,
        )
        .where(
            models.ContentVersion.id == content_version_id,
            models.ContentItem.workspace_id == workspace.id,
            models.ContentItem.owner_user_id == user.id,
        )
    )
    if not content_version:
        raise not_found("稿件版本")
    return workspace, content_version


def snippet_out(seg: dict) -> dict:
    """片段是裸 dict（不走 pydantic 模型），这里手动转 camelCase 保持接口风格一致。"""
    return {
        "position": seg.get("position", ""),
        "similarity": seg.get("similarity", 0),
        "targetText": seg.get("target_text", ""),
        "sourceText": seg.get("source_text", ""),
        "matchedTitle": seg.get("matched_title", ""),
    }


def to_out(
    analysis: models.OriginalityAnalysis,
    *,
    processing_time_ms: int = 0,
    cached: bool = False,
) -> schemas.OriginalityAnalysisOut:
    """把 ORM 行转成响应体。历史行可能缺新字段（NULL），一律取 0 / 空。"""
    similar_articles = [
        schemas.SimilarArticleOut(
            id=item.get("id", ""),
            title=item.get("title", ""),
            similarity=item.get("similarity", 0.0),
            matching_snippets=[snippet_out(seg) for seg in (item.get("matching_snippets") or [])],
        )
        for item in json_items(analysis.similar_articles)
    ]
    key_points = [
        schemas.KeyPointOut(
            point=item.get("point", ""),
            novelty=item.get("novelty", 0),
            sources=item.get("sources", 0),
        )
        for item in json_items(analysis.key_points)
    ]
    risk_flags = [
        schemas.RiskFlagOut(
            type=item.get("type", ""),
            position=item.get("position", ""),
            severity=item.get("severity", "warning"),
            reason=item.get("reason", ""),
        )
        for item in json_items(analysis.risk_flags)
    ]

    return schemas.OriginalityAnalysisOut(
        analysis_id=analysis.id,
        content_version_id=analysis.content_version_id,
        analyzed_at=analysis.analyzed_at,
        total_score=analysis.total_score,
        level=analysis.level,
        scoreboard=schemas.ScoreboardOut(
            similarity=analysis.similarity_score,
            originality=analysis.originality_score,
            structure=analysis.structure_score,
            data_accuracy=analysis.data_accuracy_score,
            style_uniqueness=analysis.style_uniqueness_score,
        ),
        details=schemas.AnalysisDetailsOut(
            similar_articles=similar_articles,
            key_points=key_points,
            risk_flags=risk_flags,
        ),
        recommendations=[
            line.strip()
            for line in (analysis.recommendations or "").split("\n")
            if line.strip()
        ],
        model_version=analysis.model_version or "",
        processing_time_ms=processing_time_ms,
        summary=analysis.summary or "",
        corpus_size=analysis.corpus_size or 0,
        self_version_count=analysis.self_version_count or 0,
        cached=cached,
    )


@router.post(
    "/analyze-originality",
    response_model=schemas.OriginalityAnalysisOut,
    summary="执行文章原创度检测",
)
async def analyze_originality(
    req: schemas.OriginalityAnalysisRequest,
    user: CurrentUser,
    db: DbSession,
) -> schemas.OriginalityAnalysisOut:
    """执行文章原创度检测分析。

    返回五维评分、相似文章与相似片段、关键观点、风险标记与改进建议。
    同版本正文未改动且缓存未过期时直接复用上次结果（响应里 `cached=true`）。
    """
    workspace, content_version = load_version(db, user, req.content_version_id)

    # 空正文没有可分析的东西：旧实现会照常落一条「0 分 / 偏低」的记录，
    # 用户看到的是「你这稿很糟」，实际原因是根本没存正文。
    if len((content_version.body or "").strip()) < 20:
        raise ApiError(
            "empty_content",
            "该版本正文为空或不足 20 字，请先保存正文再检测",
            status_code=422,
        )

    result = originality_service.analyze_originality(
        db=db,
        content_version_id=req.content_version_id,
        content_version=content_version,
        workspace_id=workspace.id,
        owner_user_id=user.id,
        compare_scope=req.compare_scope,
        use_cache=req.use_cache,
    )
    return to_out(
        result.analysis,
        processing_time_ms=result.elapsed_ms,
        cached=result.cached,
    )


@router.get(
    "/{content_version_id}/originality",
    response_model=schemas.OriginalityAnalysisOut | None,
    summary="获取稿件最新的原创度检测结果",
)
async def get_originality_result(
    content_version_id: str,
    user: CurrentUser,
    db: DbSession,
) -> schemas.OriginalityAnalysisOut | None:
    """获取稿件版本的最新检测结果。从未检测过返回 null。"""
    load_version(db, user, content_version_id)
    analysis = originality_service.get_latest_analysis(db, content_version_id)
    if not analysis:
        return None
    return to_out(analysis)
