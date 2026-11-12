from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from .. import models, schemas
from ..core import pagination, ssrf
from ..services import ai_article, ai_gateway, provider_probe
from ..core.deps import ApiError, CurrentUser, DbSession, default_workspace, not_found
from ..core.security import decrypt_secret, encrypt_secret, mask_secret
from ..services import ai_gateway

router = APIRouter(tags=["models"])


def _model_out(entry: models.ModelCatalogEntry) -> schemas.CatalogOut:
    return schemas.CatalogOut(
        id=entry.id,
        model_code=entry.model_code,
        display_name=entry.display_name or entry.model_code,
        capabilities=entry.capabilities or [],
        enabled=entry.enabled,
        supports_streaming=entry.supports_streaming,
    )


def _connection_out(conn: models.ModelConnection, db: DbSession) -> schemas.ConnectionOut:
    models_rows = db.scalars(
        select(models.ModelCatalogEntry).where(models.ModelCatalogEntry.connection_id == conn.id)
    ).all()
    return schemas.ConnectionOut(
        id=conn.id,
        display_name=conn.display_name,
        provider_type=conn.provider_type,
        base_url=conn.base_url,
        key_masked=mask_secret(conn.credential_ciphertext),
        status=conn.status,
        models=[_model_out(m) for m in models_rows],
    )


@router.get("/models/connections", response_model=list[schemas.ConnectionOut])
def list_connections(
    user: CurrentUser,
    db: DbSession,
    response: Response,
    limit: int = Query(default=100, le=200),
    cursor: str | None = Query(default=None),
) -> list[schemas.ConnectionOut]:
    stmt = select(models.ModelConnection).where(
        models.ModelConnection.owner_user_id == user.id
    )
    rows, next_cursor = pagination.paginate(
        db, stmt, models.ModelConnection,
        sort_column=models.ModelConnection.created_at,
        cursor=cursor, limit=limit, descending=False,
    )
    pagination.set_cursor_header(response, next_cursor)
    return [_connection_out(row, db) for row in rows]


@router.post("/models/connections", response_model=schemas.ConnectionOut, status_code=201)
def create_connection(
    payload: schemas.ConnectionIn, user: CurrentUser, db: DbSession
) -> schemas.ConnectionOut:
    try:
        base_url = ssrf.validate_base_url(payload.base_url)
    except ssrf.UnsafeUrl as exc:
        raise ApiError("invalid_base_url", str(exc)) from exc
    conn = models.ModelConnection(
        owner_user_id=user.id,
        display_name=payload.display_name,
        provider_type=payload.provider_type,
        base_url=base_url,
        credential_ciphertext=encrypt_secret(payload.api_key),
        status="untested" if payload.api_key else "missing_credential",
    )
    db.add(conn)
    db.commit()
    return _connection_out(conn, db)


@router.patch("/models/connections/{connection_id}", response_model=schemas.ConnectionOut)
def update_connection(
    connection_id: str, payload: schemas.ConnectionPatch, user: CurrentUser, db: DbSession
) -> schemas.ConnectionOut:
    conn = db.get(models.ModelConnection, connection_id)
    if not conn or conn.owner_user_id != user.id:
        raise not_found("模型连接")
    data = payload.model_dump(exclude_unset=True)
    if "api_key" in data:
        new_key = (data.pop("api_key") or "").strip()
        # 传空字符串表示「清空凭据」；不传该字段表示保持原密钥不变
        conn.credential_ciphertext = encrypt_secret(new_key) if new_key else ""
        conn.status = "untested" if new_key else "missing_credential"
    if "base_url" in data:
        try:
            data["base_url"] = ssrf.validate_base_url(data["base_url"] or "")
        except ssrf.UnsafeUrl as exc:
            raise ApiError("invalid_base_url", str(exc)) from exc
    if not str(data.get("display_name", "x") or "").strip():
        raise ApiError("invalid_display_name", "连接名称不能为空")
    for key, value in data.items():
        setattr(conn, key, value)
    db.commit()
    return _connection_out(conn, db)


@router.delete("/models/connections/{connection_id}", status_code=204)
def delete_connection(connection_id: str, user: CurrentUser, db: DbSession) -> None:
    conn = db.get(models.ModelConnection, connection_id)
    if not conn or conn.owner_user_id != user.id:
        raise not_found("模型连接")
    db.query(models.ModelCatalogEntry).filter(
        models.ModelCatalogEntry.connection_id == conn.id
    ).delete()
    db.delete(conn)
    db.commit()


@router.post("/models/connections/{connection_id}/test")
def test_connection(connection_id: str, user: CurrentUser, db: DbSession) -> dict:
    conn = db.get(models.ModelConnection, connection_id)
    if not conn or conn.owner_user_id != user.id:
        raise not_found("模型连接")
    # 真实连通性探测：只有供应商返回 2xx 才允许标记为 connected。
    result = provider_probe.probe_connection(
        base_url=conn.base_url,
        api_key=decrypt_secret(conn.credential_ciphertext),
        provider_type=conn.provider_type,
    )
    conn.status = result["status"]
    db.commit()
    return {
        "status": conn.status,
        "message": result["message"],
        "verified": conn.status == "connected",
        "httpStatus": result["httpStatus"],
        "latencyMs": result["latencyMs"],
        "modelCount": result["modelCount"],
    }


@router.get("/models/catalog", response_model=list[schemas.CatalogOut])
def list_catalog(user: CurrentUser, db: DbSession) -> list[schemas.CatalogOut]:
    connection_ids = [
        row.id
        for row in db.scalars(
            select(models.ModelConnection).where(models.ModelConnection.owner_user_id == user.id)
        ).all()
    ]
    if not connection_ids:
        return []
    rows = db.scalars(
        select(models.ModelCatalogEntry).where(
            models.ModelCatalogEntry.connection_id.in_(connection_ids)
        )
    ).all()
    return [_model_out(row) for row in rows]


@router.post("/models/catalog", response_model=schemas.CatalogOut, status_code=201)
def add_catalog(
    payload: schemas.CatalogIn, user: CurrentUser, db: DbSession
) -> schemas.CatalogOut:
    conn = db.get(models.ModelConnection, payload.connection_id)
    if not conn or conn.owner_user_id != user.id:
        raise not_found("模型连接")
    entry = models.ModelCatalogEntry(
        connection_id=conn.id,
        model_code=payload.model_code,
        display_name=payload.display_name or payload.model_code,
        capabilities=payload.capabilities,
        supports_streaming=payload.supports_streaming,
    )
    db.add(entry)
    db.commit()
    return _model_out(entry)


@router.patch("/models/catalog/{model_id}", response_model=schemas.CatalogOut)
def update_catalog(
    model_id: str, payload: schemas.CatalogPatch, user: CurrentUser, db: DbSession
) -> schemas.CatalogOut:
    entry = db.get(models.ModelCatalogEntry, model_id)
    if not entry:
        raise not_found("模型")
    conn = db.get(models.ModelConnection, entry.connection_id)
    if not conn or conn.owner_user_id != user.id:
        raise not_found("模型")
    data = payload.model_dump(exclude_unset=True)
    if "model_code" in data and not str(data["model_code"] or "").strip():
        raise ApiError("invalid_model_code", "模型代号不能为空")
    for key, value in data.items():
        setattr(entry, key, value)
    db.commit()
    return _model_out(entry)


@router.delete("/models/catalog/{model_id}", status_code=204)
def delete_catalog(model_id: str, user: CurrentUser, db: DbSession) -> None:
    entry = db.get(models.ModelCatalogEntry, model_id)
    if not entry:
        raise not_found("模型")
    conn = db.get(models.ModelConnection, entry.connection_id)
    if not conn or conn.owner_user_id != user.id:
        raise not_found("模型")
    db.delete(entry)
    db.commit()


@router.post("/ai/text-generations")
async def text_generation(
    payload: schemas.TextGenerationIn, user: CurrentUser, db: DbSession
) -> StreamingResponse:
    """SSE 流式生成；同时持久化生成记录，断线可用 generationId 恢复。"""
    candidates: list[tuple[models.ModelConnection | None, str]] = []
    if payload.model_id:
        entry = db.get(models.ModelCatalogEntry, payload.model_id)
        if not entry:
            raise not_found("模型")
        chosen = db.get(models.ModelConnection, entry.connection_id)
        if not chosen or chosen.owner_user_id != user.id:
            raise not_found("模型")
        candidates = [(chosen, entry.model_code)]
    else:
        # 前端没有模型选择器时不传 modelId；此时按创建顺序列出所有可用文本模型。
        # 只取第一个会出现「配了两个连接、第一个订阅过期就整体不可用」，
        # 所以保留完整候选链，首个失败且未产出任何文本时自动换下一个。
        candidates = ai_gateway.resolve_connections(db, user.id)
    if not candidates:
        candidates = [(None, "unconfigured")]
    conn, model_code = candidates[0]
    style_tasks = {"article", "polish", "rewrite"}
    selected_skill = (
        ai_article.resolve_skill(db, user.id, payload.skill_key)
        if payload.task_type in style_tasks
        else None
    )
    system_prompt = (
        ai_article.build_system_prompt(selected_skill)
        if payload.task_type in style_tasks
        else None
    )

    generation = models.AiGeneration(
        owner_user_id=user.id,
        task_type=payload.task_type,
        model_id=payload.model_id,
        status="running",
        prompt_version=ai_gateway.PROMPT_TEMPLATE_VERSION,
        request_summary={
            "prompt": payload.prompt[:200],
            "constraints": payload.constraints,
            "promptTemplateVersion": ai_gateway.PROMPT_TEMPLATE_VERSION,
        },
    )
    db.add(generation)
    db.commit()

    async def event_stream() -> AsyncIterator[str]:
        collected: list[str] = []
        error_code: str | None = None
        completed = False
        saved_version_id: str | None = None
        try:
            yield _sse(
                "generation.started",
                {
                    "generationId": generation.id,
                    "modelCode": model_code,
                    "provider": conn.provider_type if conn else "none",
                },
            )
            for idx, (cand_conn, cand_model) in enumerate(candidates):
                emitted = False
                pending_error: dict | None = None
                async for event in ai_gateway.stream_text(
                    db,
                    connection=cand_conn,
                    model_code=cand_model,
                    task_type=payload.task_type,
                    prompt=payload.prompt,
                    constraints=payload.constraints,
                    system_prompt=system_prompt,
                ):
                    kind = event["event"]
                    if kind == "generation.delta":
                        collected.append(event["data"]["text"])
                        emitted = True
                        yield _sse(kind, {**event["data"], "generationId": generation.id})
                    elif kind == "generation.error":
                        # 先攒着：还没吐字就失败时可以静默降级，不打扰用户
                        pending_error = event["data"]
                    elif kind == "generation.done":
                        completed = True
                        yield _sse(kind, {**event["data"], "generationId": generation.id})
                    else:
                        yield _sse(kind, {**event["data"], "generationId": generation.id})
                if pending_error is None:
                    break
                error_code = pending_error.get("code")
                if not emitted and idx + 1 < len(candidates):
                    # 一个字都没吐出去，换下一个候选重试，客户端无感
                    error_code = None
                    continue
                yield _sse(
                    "generation.error",
                    {**pending_error, "generationId": generation.id},
                )
                break
            # 只有真实供应商完整返回才写入稿件版本；错误/中断/占位一律不落库
            if payload.content_item_id and collected and completed and not error_code:
                saved_version_id = _persist_version(
                    db, user, payload.content_item_id, "".join(collected), payload
                )
                if saved_version_id:
                    yield _sse("generation.saved", {"versionId": saved_version_id})
        finally:
            # 客户端中断也保留已生成部分与状态，避免丢失内容；但状态必须如实区分
            text = "".join(collected)
            generation.result_text = text
            if error_code:
                generation.status = "failed"
            elif completed:
                generation.status = "succeeded"
            else:
                generation.status = "partial" if text else "cancelled"
            generation.error_code = error_code
            db.commit()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _persist_version(
    db, user: models.User, content_item_id: str, text: str, payload: schemas.TextGenerationIn
) -> str | None:
    from sqlalchemy import func

    item = db.get(models.ContentItem, content_item_id)
    if not item or item.owner_user_id != user.id:
        return None
    max_no = db.scalar(
        select(func.max(models.ContentVersion.version_no)).where(
            models.ContentVersion.content_item_id == item.id
        )
    )
    # 文章类任务是 JSON 契约输出，直接落库会把整个 JSON 写进正文
    ai_title, ai_body = ai_article.article_from_raw_output(text)
    version = models.ContentVersion(
        content_item_id=item.id,
        version_no=(max_no or 0) + 1,
        parent_version_id=item.current_version_id,
        source_type="ai",
        model_id=payload.model_id,
        title=ai_title or item.title,
        body=ai_body,
        summary=f"{payload.task_type} 生成",
    )
    db.add(version)
    db.flush()
    return version.id


@router.get("/ai/generations", response_model=list[schemas.GenerationOut])
def list_generations(user: CurrentUser, db: DbSession) -> list[schemas.GenerationOut]:
    rows = db.scalars(
        select(models.AiGeneration)
        .where(models.AiGeneration.owner_user_id == user.id)
        .order_by(models.AiGeneration.created_at.desc())
        .limit(30)
    ).all()
    return [
        schemas.GenerationOut(
            id=row.id,
            task_type=row.task_type,
            status=row.status,
            result_text=row.result_text or "",
            error_code=row.error_code,
            duration_ms=row.duration_ms,
        )
        for row in rows
    ]


@router.get("/ai/generations/{generation_id}", response_model=schemas.GenerationOut)
def get_generation(
    generation_id: str, user: CurrentUser, db: DbSession
) -> schemas.GenerationOut:
    row = db.get(models.AiGeneration, generation_id)
    if not row or row.owner_user_id != user.id:
        raise not_found("生成记录")
    return schemas.GenerationOut(
        id=row.id,
        task_type=row.task_type,
        status=row.status,
        result_text=row.result_text or "",
        error_code=row.error_code,
        duration_ms=row.duration_ms,
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
