from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from .. import models, schemas
from ..core import pagination, ssrf
from ..services import ai_article, ai_gateway, prompt_rules, provider_probe
from ..core.deps import ApiError, CurrentUser, DbSession, default_workspace, not_found
from ..core.security import decrypt_secret, encrypt_secret, mask_secret
from ..services import ai_gateway

from ..core.rbac import require_permission

# 这个模块混了三类东西，**不能整模块挂管理权限**：
#   - 连接写操作 / 探针 / catalog 写操作 → models.manage（会碰密钥，误删代价极高）
#   - GET /models/connections → models.view（看得见配置，改不了）
#   - GET /models/catalog、/models/status → 所有创作者（模型下拉、状态栏）
#   - /ai/text-generations、/ai/generations → 创作主通道，全员可用
_ADMIN_ONLY = [require_permission("models.manage")]
# 「能管必能看」：只读角色（models.view）可以看连接与模型清单；
# 老的自定义角色可能只被授过 models.manage，这里一并放行，避免升级后突然进不去。
_MODEL_READ = [require_permission("models.view", "models.manage", mode="any")]
router = APIRouter(tags=["models"])


def _model_out(
    entry: models.ModelCatalogEntry,
    db: DbSession | None = None,
    default_id: str | None = None,
) -> schemas.CatalogOut:
    connection = db.get(models.ModelConnection, entry.connection_id) if db else None
    return schemas.CatalogOut(
        id=entry.id,
        model_code=entry.model_code,
        display_name=entry.display_name or entry.model_code,
        capabilities=entry.capabilities or [],
        enabled=entry.enabled,
        supports_streaming=entry.supports_streaming,
        connection_id=entry.connection_id,
        connection_name=connection.display_name if connection else None,
        connection_status=connection.status if connection else None,
        is_default=bool(default_id and default_id == entry.id),
        is_text_model=ai_gateway.is_text_model(entry),
    )


def _connection_out(
    conn: models.ModelConnection, db: DbSession, default_id: str | None = None
) -> schemas.ConnectionOut:
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
        models=[_model_out(m, db, default_id) for m in models_rows],
    )


@router.get("/models/connections", response_model=list[schemas.ConnectionOut], dependencies=_MODEL_READ)
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
    default_id = ai_gateway.describe_default_model(db, user.id)["id"]
    return [_connection_out(row, db, default_id) for row in rows]


@router.post("/models/connections", response_model=schemas.ConnectionOut, status_code=201, dependencies=_ADMIN_ONLY)
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


@router.patch("/models/connections/{connection_id}", response_model=schemas.ConnectionOut, dependencies=_ADMIN_ONLY)
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


@router.delete("/models/connections/{connection_id}", status_code=204, dependencies=_ADMIN_ONLY)
def delete_connection(connection_id: str, user: CurrentUser, db: DbSession) -> None:
    conn = db.get(models.ModelConnection, connection_id)
    if not conn or conn.owner_user_id != user.id:
        raise not_found("模型连接")
    db.query(models.ModelCatalogEntry).filter(
        models.ModelCatalogEntry.connection_id == conn.id
    ).delete()
    db.delete(conn)
    db.commit()


@router.post("/models/connections/{connection_id}/test", dependencies=_ADMIN_ONLY)
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


def _probe_credentials(
    payload: schemas.ModelProbeIn, user: CurrentUser, db: DbSession
) -> tuple[str, str]:
    """取「这次探测用哪个地址、哪把钥匙」。

    弹窗里可能还没保存连接：优先用表单里的值；编辑已有连接且密钥留空时，
    回落该连接已存的密文，避免为了测试逼用户重新粘贴一次 Key。
    """
    base_url = (payload.base_url or "").strip()
    api_key = (payload.api_key or "").strip()
    if payload.connection_id:
        conn = db.get(models.ModelConnection, payload.connection_id)
        if not conn or conn.owner_user_id != user.id:
            raise not_found("模型连接")
        base_url = base_url or conn.base_url
        api_key = api_key or decrypt_secret(conn.credential_ciphertext)
    try:
        validated = ssrf.validate_base_url(base_url)
    except ssrf.UnsafeUrl as exc:
        raise ApiError("invalid_base_url", str(exc)) from exc
    if not validated:
        raise ApiError("missing_base_url", "请先填写 Base URL")
    return validated, api_key


@router.post("/models/probe/models", dependencies=_ADMIN_ONLY)
def probe_models(
    payload: schemas.ModelProbeIn, user: CurrentUser, db: DbSession
) -> dict:
    """按当前填写的地址与密钥拉一次供应商模型列表，供「模型代号」下拉选择。

    只读：不落库、不改连接状态，拉不到就是拉不到，不返回占位列表。
    """
    base_url, api_key = _probe_credentials(payload, user, db)
    result = provider_probe.fetch_models(base_url, api_key, payload.provider_type)
    return {
        "status": result["status"],
        "message": result["message"],
        "httpStatus": result["httpStatus"],
        "latencyMs": result["latencyMs"],
        "modelCount": result["modelCount"],
        "truncated": result["truncated"],
        "models": result["models"],
    }


@router.post("/models/probe/test", dependencies=_ADMIN_ONLY)
def probe_model(
    payload: schemas.ModelProbeIn, user: CurrentUser, db: DbSession
) -> dict:
    """测试当前表单这套配置：连接是否通、这个模型代号到底能不能用。

    文本模型会真发一次 1 token 的请求；生图模型只核对模型列表，不替用户花钱出图。
    同样只读：结果不写入连接状态，保存后的状态以列表页「测试」为准。
    """
    base_url, api_key = _probe_credentials(payload, user, db)
    return provider_probe.probe_model(
        base_url=base_url,
        api_key=api_key,
        provider_type=payload.provider_type,
        model_code=payload.model_code,
        kind=payload.kind,
    )


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
    default_id = ai_gateway.describe_default_model(db, user.id)["id"]
    return [_model_out(row, db, default_id) for row in rows]


@router.post("/models/catalog", response_model=schemas.CatalogOut, status_code=201, dependencies=_ADMIN_ONLY)
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
    return _model_out(entry, db, ai_gateway.describe_default_model(db, user.id)["id"])


@router.patch("/models/catalog/{model_id}", response_model=schemas.CatalogOut, dependencies=_ADMIN_ONLY)
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
    return _model_out(entry, db, ai_gateway.describe_default_model(db, user.id)["id"])


@router.delete("/models/catalog/{model_id}", status_code=204, dependencies=_ADMIN_ONLY)
def delete_catalog(model_id: str, user: CurrentUser, db: DbSession) -> None:
    entry = db.get(models.ModelCatalogEntry, model_id)
    if not entry:
        raise not_found("模型")
    conn = db.get(models.ModelConnection, entry.connection_id)
    if not conn or conn.owner_user_id != user.id:
        raise not_found("模型")
    db.delete(entry)
    db.commit()


# 连接已测过且失败的几种结论。连接一旦测过，之后再改密钥 / 地址都会被重置回
# untested，所以这里都是最近一次真实探测的结论，而不是陈年旧账。
FAILED_CONNECTION_STATUS = {
    "unauthorized": "凭据被供应商拒绝",
    "adapter_unavailable": "供应商协议适配器未接入",
    "invalid_base_url": "Base URL 不合法",
    "provider_unavailable": "供应商不可用",
    "provider_unreachable": "供应商连接失败",
    "endpoint_not_supported": "供应商未提供 /models 接口",
}


def _status_item(
    kind: str,
    *,
    state: str,
    message: str,
    entry: Any = None,
    conn: Any = None,
) -> schemas.ModelStatusOut:
    return schemas.ModelStatusOut(
        kind=kind,
        state=state,
        message=message,
        model_code=entry.model_code if entry else None,
        display_name=(entry.display_name or entry.model_code) if entry else None,
        connection_name=conn.display_name if conn else None,
        connection_status=conn.status if conn else None,
    )


def _current_text_model(db, user_id: str) -> schemas.ModelStatusOut:
    """与 ai_gateway 候选链同口径：默认模型优先，否则取第一个可用文本模型。"""
    described = ai_gateway.describe_default_model(db, user_id)
    default_id = described["id"]
    is_default = bool(default_id) and ai_gateway._validate_entry(db, user_id, default_id)[2] is None
    entry = db.get(models.ModelCatalogEntry, default_id) if is_default else None
    if entry is None:
        # 默认模型不可用时的真实降级目标：候选链里第一个校验通过的文本模型
        for _conn, _code, entry_id in ai_gateway._text_candidates(db, user_id):
            if entry_id and ai_gateway._validate_entry(db, user_id, entry_id)[2] is None:
                entry = db.get(models.ModelCatalogEntry, entry_id)
                break

    if entry is None:
        return _status_item(
            "text",
            state="blocked",
            message="没有可用的文本模型：生成请求会直接报错，请到设置里检查连接与 API Key",
        )
    conn = db.get(models.ModelConnection, entry.connection_id)
    failure = FAILED_CONNECTION_STATUS.get(conn.status) if conn else "连接已被删除"
    if not is_default:
        # 没设默认、或默认失效后降级到了这个模型：生成能成功，但不在用户「以为」的那个模型上。
        # 连接已测过失败仍然算红；其余是配置示意的偏差，算灰色并说清降级目标。
        if failure:
            return _status_item(
                "text",
                state="blocked",
                message=f"最近一次连接测试失败：{failure}",
                entry=entry,
                conn=conn,
            )
        if described["ignored"]:
            message = (
                f"默认模型不可用（{described['ignored']['message']}），"
                "未手动指定时会自动使用本模型"
            )
        else:
            message = "尚未设置默认模型，未手动指定时会自动使用本模型"
        return _status_item(
            "text", state="unchecked", message=message, entry=entry, conn=conn
        )
    if failure:
        return _status_item(
            "text",
            state="blocked",
            message=f"最近一次连接测试失败：{failure}",
            entry=entry,
            conn=conn,
        )
    if conn.status != "connected":
        return _status_item(
            "text",
            state="unchecked",
            message="连接尚未测试，到设置里测试后才能确认可用",
            entry=entry,
            conn=conn,
        )
    return _status_item(
        "text", state="ok", message="默认文本模型，生成时会优先使用", entry=entry, conn=conn
    )


def _current_image_model(db, user_id: str) -> schemas.ModelStatusOut:
    """与 image_provider.resolve_image_provider 同口径：环境变量供应商优先。"""
    env_model = os.getenv("IMAGE_PROVIDER_MODEL", "").strip()
    if env_model:
        ready = bool(
            os.getenv("IMAGE_PROVIDER_BASE_URL", "").strip()
            and os.getenv("IMAGE_PROVIDER_API_KEY", "").strip()
        )
        return _status_item(
            "image",
            state="ok" if ready else "blocked",
            message="环境变量供应商" if ready else "环境变量供应商配置不完整（缺少地址或密钥）",
            # 环境变量供应商没有 catalog 行，只借用展示字段
            entry=SimpleNamespace(model_code=env_model, display_name=env_model),
            conn=SimpleNamespace(display_name="环境变量", status=None),
        )

    entries = db.scalars(
        select(models.ModelCatalogEntry)
        .where(models.ModelCatalogEntry.enabled.is_(True))
        .order_by(models.ModelCatalogEntry.created_at)
    ).all()
    candidate = None
    for entry in entries:
        if "image.generate" not in (entry.capabilities or []):
            continue
        conn = db.get(models.ModelConnection, entry.connection_id)
        if not conn or conn.owner_user_id != user_id:
            continue
        candidate = candidate or (entry, conn)
        # 与解析顺序一致：第一个「地址 + 密钥」齐全的就是实际会用的那个
        if conn.base_url and conn.credential_ciphertext:
            failure = FAILED_CONNECTION_STATUS.get(conn.status)
            if failure:
                return _status_item(
                    "image",
                    state="blocked",
                    message=f"最近一次连接测试失败：{failure}",
                    entry=entry,
                    conn=conn,
                )
            state = "ok" if conn.status == "connected" else "unchecked"
            message = (
                "生图会使用该模型"
                if state == "ok"
                else "连接尚未测试，到设置里测试后才能确认可用"
            )
            return _status_item("image", state=state, message=message, entry=entry, conn=conn)
    if candidate:
        entry, conn = candidate
        return _status_item(
            "image",
            state="blocked",
            message="图像模型所属连接缺少地址或 API Key",
            entry=entry,
            conn=conn,
        )
    return _status_item(
        "image",
        state="none",
        message="未接入图像生成供应商，配图需指定已上传素材；可在设置里添加带生图能力的模型",
    )


@router.get("/models/status", response_model=list[schemas.ModelStatusOut])
def model_status(user: CurrentUser, db: DbSession) -> list[schemas.ModelStatusOut]:
    return [_current_text_model(db, user.id), _current_image_model(db, user.id)]


@router.post("/ai/text-generations")
async def text_generation(
    payload: schemas.TextGenerationIn, user: CurrentUser, db: DbSession
) -> StreamingResponse:
    """SSE 流式生成；同时持久化生成记录，断线可用 generationId 恢复。

    模型解析统一走 ai_gateway.resolve_selection：
    - 显式 modelId → 钉死单候选，失败直接报错（含供应商原因），绝不偷偷换模型；
    - 不传 modelId → 用户级默认模型排候选链首位，后面仍保留自动降级；
    - 默认模型配置失效 → 忽略并回报 defaultModelIgnored，不清除偏好。
    """
    try:
        selection = ai_gateway.resolve_selection(db, user.id, payload.model_id)
    except ai_gateway.InvalidModelSelection as exc:
        raise ApiError(exc.code, exc.message, status_code=400) from exc
    candidates: list[tuple[models.ModelConnection | None, str]] = selection["candidates"]
    entry_ids: list[str | None] = selection["entryIds"]
    pinned = selection["pinned"]
    if not candidates:
        candidates = [(None, "unconfigured")]
        entry_ids = [None]
    style_tasks = {"article", "polish", "rewrite"}
    selected_skill = (
        ai_article.resolve_skill(db, user.id, payload.skill_key)
        if payload.task_type in style_tasks
        else None
    )
    system_prompt = (
        ai_article.build_system_prompt(selected_skill, db=db, owner_user_id=user.id)
        if payload.task_type in style_tasks
        else None
    )

    skill_key = getattr(selected_skill, "skill_key", None)
    skill_version = getattr(selected_skill, "version", None)

    # 复盘用：这次生成实际读了哪些提示词规则行（key + 行版本 + 更新时间）
    summary_rule_keys = ["gateway.system_rules"] + (
        ["article.output_contract"] if payload.task_type in style_tasks else []
    )

    generation = models.AiGeneration(
        owner_user_id=user.id,
        task_type=payload.task_type,
        # 先记首选；finally 里会覆盖为「实际使用的模型」，降级后不假装用的是首选
        model_id=entry_ids[0],
        status="running",
        prompt_version=ai_gateway.PROMPT_TEMPLATE_VERSION,
        request_summary={
            "prompt": payload.prompt[:200],
            "constraints": payload.constraints,
            "promptTemplateVersion": ai_gateway.PROMPT_TEMPLATE_VERSION,
            "promptRules": prompt_rules.describe_rules(db, summary_rule_keys),
            "skillKey": skill_key,
            "skillVersion": skill_version,
            "requestedModelId": selection["requestedModelId"],
            "defaultModelId": selection["defaultModelId"],
            "defaultModelIgnored": selection["defaultModelIgnored"],
            "pinned": pinned,
        },
    )
    db.add(generation)
    db.commit()

    async def event_stream() -> AsyncIterator[str]:
        collected: list[str] = []
        error_code: str | None = None
        completed = False
        saved_version_id: str | None = None
        used_index: int | None = None

        def model_meta(index: int) -> dict:
            cand_conn = candidates[index][0]
            return {
                "modelCode": candidates[index][1],
                "modelId": entry_ids[index] if index < len(entry_ids) else None,
                "provider": cand_conn.provider_type if cand_conn else "none",
                "fallbackUsed": index > 0,
                "pinned": pinned,
                "defaultModelId": selection["defaultModelId"],
                "defaultModelIgnored": selection["defaultModelIgnored"],
                "requestedModelId": selection["requestedModelId"],
            }

        try:
            yield _sse(
                "generation.started",
                {
                    "generationId": generation.id,
                    **model_meta(0),
                    "candidates": [code for _, code in candidates],
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
                        if used_index is None:
                            # 真正开始产出的候选才算「实际使用的模型」，降级后不谎报首选
                            used_index = idx
                            yield _sse(
                                "generation.model",
                                {"generationId": generation.id, **model_meta(idx)},
                            )
                        collected.append(event["data"]["text"])
                        emitted = True
                        yield _sse(kind, {**event["data"], "generationId": generation.id})
                    elif kind == "generation.error":
                        # 先攒着：还没吐字就失败时可以静默降级，不打扰用户
                        pending_error = event["data"]
                    elif kind == "generation.done":
                        completed = True
                        if used_index is None:
                            used_index = idx
                        done_data = {
                            **event["data"],
                            "generationId": generation.id,
                            "skillKey": skill_key,
                            "skillVersion": skill_version,
                            **model_meta(idx),
                        }
                        if payload.task_type == "article":
                            # 契约检查失败只告知，不阻断落库
                            draft = ai_article.parse_llm_output("".join(collected))
                            if draft.ok:
                                done_data["review"] = ai_article.review_contract(draft)
                        yield _sse(kind, done_data)
                    else:
                        yield _sse(kind, {**event["data"], "generationId": generation.id})
                if pending_error is None:
                    break
                error_code = pending_error.get("code")
                # 显式选中的模型钉死：失败直接把供应商原因透传，绝不偷偷换模型
                if not emitted and not pinned and idx + 1 < len(candidates):
                    # 一个字都没吐出去，换下一个候选重试，客户端无感
                    error_code = None
                    continue
                yield _sse(
                    "generation.error",
                    {**pending_error, "generationId": generation.id, **model_meta(idx)},
                )
                break
            # 只有真实供应商完整返回才写入稿件版本；错误/中断/占位一律不落库
            if payload.content_item_id and collected and completed and not error_code:
                actual = used_index if used_index is not None else 0
                saved_version_id = _persist_version(
                    db,
                    user,
                    payload.content_item_id,
                    "".join(collected),
                    payload,
                    model_id=entry_ids[actual] if actual < len(entry_ids) else None,
                    skill_ref=(
                        f"{skill_key}@{skill_version}"
                        if skill_key and skill_version
                        else None
                    ),
                )
                if saved_version_id:
                    yield _sse("generation.saved", {"versionId": saved_version_id})
        finally:
            # 客户端中断也保留已生成部分与状态，避免丢失内容；但状态必须如实区分
            text = "".join(collected)
            generation.result_text = text
            actual = used_index if used_index is not None else 0
            generation.model_id = (
                entry_ids[actual] if actual < len(entry_ids) else None
            )
            # JSON 列必须赋新 dict，原地改不会被 SQLAlchemy 感知
            generation.request_summary = {
                **(generation.request_summary or {}),
                "modelCode": candidates[actual][1],
                "modelId": generation.model_id,
                "fallbackUsed": actual > 0,
                "defaultModelIgnored": selection["defaultModelIgnored"],
            }
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
    db,
    user: models.User,
    content_item_id: str,
    text: str,
    payload: schemas.TextGenerationIn,
    model_id: str | None = None,
    skill_ref: str | None = None,
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
    draft = ai_article.parse_llm_output(text)
    if draft.ok:
        ai_title, ai_body = draft.title, draft.markdown
        summary = draft.summary[:300] if draft.summary else f"{payload.task_type} 生成"
    else:
        ai_title, ai_body = ai_article.article_from_raw_output(text)
        summary = f"{payload.task_type} 生成"
    version = models.ContentVersion(
        content_item_id=item.id,
        version_no=(max_no or 0) + 1,
        parent_version_id=item.current_version_id,
        source_type="ai",
        # 记实际产出的模型；降级后仍写请求值会让复盘对不上
        model_id=model_id,
        prompt_template_version=skill_ref[:32] if skill_ref else None,
        title=ai_title or item.title,
        body=ai_body,
        summary=summary,
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
