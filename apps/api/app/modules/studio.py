"""成稿台接口：段落级编辑编排。

与 dsh-wechat-studio 的 `/wechat-studio/*` 一一对应，差异在鉴权与存储：

- 每条查询都按 owner 隔离（dsh 是单机插件，只有一份数据）；
- 状态落 PostgreSQL/SQLite（dsh 是分片 JSON 文件）；
- 模型走项目统一的 ai_gateway，不再由插件选择 provider。

接口分组：

- 稿件：新建 / 详情 / 列表 / 归档 / 删除 / 导出为稿件版本
- 改稿：对话、手改段落、接受、退回、撤销、版本对比
- 编排：写正文、出标题、单段修复、一键处理、停止、横幅
- 质检：朱雀 API 检测、报告 PDF/文字回填、本地兜底
- 其它：排期字段、提示条清理、质检凭据设置
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi import Query
from sqlalchemy import select, text

from .. import models, schemas
from ..core.deps import ApiError, CurrentUser, DbSession, default_workspace, not_found
from ..core.rbac import require_permission
from ..services import (
    ai_gateway,
    author_skills,
    article_blocks as AB,
    article_quality as AQ,
    studio_calendar,
    studio_pipeline as SP,
    studio_repo,
    hot_topics as hot_topics_service,
)

router = APIRouter(tags=["studio"], dependencies=[require_permission("studio.use")])

QUALITY_SETTING_KEY = "studio.quality"
MAX_PDF = 30_000_000


# ---------------- 读模型 ----------------


def _job_out(article_id: str) -> dict | None:
    return SP.JOBS.get(article_id)


def _out(data: dict) -> dict:
    payload = {key: value for key, value in data.items() if key != "content_item_id"}
    payload["job"] = _job_out(data["id"])
    return payload


def _article(db, user, article_id: str) -> models.StudioArticle:
    row = studio_repo.get_article(db, article_id, owner_user_id=user.id)
    if not row:
        raise not_found("稿件")
    return row


def _data(db, user, article_id: str) -> tuple[models.StudioArticle, dict]:
    row = _article(db, user, article_id)
    return row, studio_repo.row_to_dict(row)


def _commit_article(db, row, data: dict) -> None:
    studio_repo.save(db, row, data)
    db.commit()


def _block_index(data: dict, block_id: str) -> int:
    index = next((i for i, block in enumerate(data["blocks"]) if block["id"] == block_id), -1)
    if index == -1:
        raise not_found("段落")
    return index


_CODES = {400: "bad_request", 404: "not_found", 409: "conflict", 413: "payload_too_large"}


def _fail(message: str, status: int = 400) -> ApiError:
    return ApiError(_CODES.get(status, "error"), message, status)


def _busy(article_id: str) -> None:
    if SP.JOBS.busy(article_id):
        raise _fail("这篇文章还有任务在跑，等它结束再试。", 409)


def _style_or_fail(style: str | None) -> str:
    """风格档来自 styles 目录，选了不存在的档直接报错，不静默回落。"""
    value = str(style or "").strip() or SP.DEFAULT_WRITER_STYLE
    if not SP.has_writer_style(value):
        raise _fail("写作风格不存在，刷新页面后重新选择。", 400)
    return value


# ---------------- 质检凭据 ----------------


def _quality_settings(db) -> dict:
    row = db.get(models.AppSetting, QUALITY_SETTING_KEY)
    return dict(row.value or {}) if row else {}


def _quality_public(db) -> dict:
    value = _quality_settings(db)
    key = str(value.get("key") or "")
    return {
        "zhuqueConfigured": bool(key),
        "zhuqueKeyHint": f"…{key[-4:]}" if len(key) >= 4 else "",
        "zhuqueEndpoint": value.get("endpoint") or AQ.DEFAULT_ENDPOINT,
        "fixedLines": value.get("fixedLines") or [],
    }


@router.get("/studio/state", response_model=schemas.StudioStateOut)
def state(db: DbSession, user: CurrentUser, archived: bool = False):
    rows = db.scalars(
        select(models.StudioArticle)
        .where(models.StudioArticle.owner_user_id == user.id, models.StudioArticle.archived == archived)
        .order_by(models.StudioArticle.updated_at.desc())
        .limit(200)
    ).all()
    metas = []
    for row in rows:
        data = studio_repo.row_to_dict(row)
        banner_style = data.get("banner_style") or SP.DEFAULT_BANNER
        banner_ready = bool((data.get("banners") or {}).get(banner_style, {}).get("spec"))
        metas.append(AB.list_meta(data, banner_ready=banner_ready))
    return {"articles": metas, "settings": _quality_public(db), "writer_styles": SP.writer_styles()}


@router.get("/studio/settings", response_model=dict)
def read_settings(db: DbSession, user: CurrentUser):
    return _quality_public(db)


@router.post("/studio/settings", response_model=dict)
def update_settings(payload: schemas.StudioSettingsIn, db: DbSession, user: CurrentUser):
    row = db.get(models.AppSetting, QUALITY_SETTING_KEY)
    value = dict(row.value or {}) if row else {}
    if payload.zhuque_key is not None:
        key = payload.zhuque_key.strip()
        if key:
            value["key"] = key
        else:
            value.pop("key", None)
    if payload.zhuque_endpoint is not None:
        endpoint = payload.zhuque_endpoint.strip()
        if endpoint:
            # 密钥会随请求发到这个地址，只允许 https
            value["endpoint"] = AQ.check_endpoint(endpoint)
        else:
            value.pop("endpoint", None)
    if row:
        row.value = value
    else:
        db.add(models.AppSetting(key=QUALITY_SETTING_KEY, value=value))
    db.commit()
    return _quality_public(db)


# ---------------- 稿件 ----------------


@router.post("/studio/articles/new", response_model=schemas.StudioArticleOut)
def create_article(payload: schemas.StudioNewIn, db: DbSession, user: CurrentUser):
    workspace = default_workspace(db, user)
    style = _style_or_fail(payload.writer_style)
    skill_prompt = author_skills.build_system_prompt(db, user.id, payload.skill_ids) if payload.draft and not payload.markdown.strip() else ""
    row = studio_repo.create(
        db,
        owner_user_id=user.id,
        workspace_id=workspace.id if workspace else None,
        topic=payload.topic,
        material=payload.material,
        markdown=payload.markdown,
        writer_style=style,
    )
    db.commit()
    article_id = row.id
    has_body = bool(payload.markdown.strip())
    if payload.draft and not has_body:
        if skill_prompt:
            SP.PIPELINE.draft(article_id, skill_prompt=skill_prompt)
        else:
            SP.PIPELINE.draft(article_id)
    elif has_body and payload.titles:
        SP.PIPELINE.titles(article_id)
    row = _article(db, user, article_id)
    return _out(studio_repo.row_to_dict(row))


def _begin_sqlite_write(db) -> None:
    # SQLite 忽略 FOR UPDATE；在读关联与最新版本之前先取得写锁。
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))


def _owned_content_for_update(db, content_id, owner_id):
    return db.scalar(
        select(models.ContentItem)
        .where(models.ContentItem.id == content_id, models.ContentItem.owner_user_id == owner_id)
        .with_for_update()
    )


@router.post("/studio/articles/from-content/{content_id}", response_model=schemas.StudioArticleOut)
def article_from_content(
    content_id: str,
    db: DbSession,
    user: CurrentUser,
    hot_cluster_id: str | None = Query(default=None, alias="hotClusterId", max_length=32),
):
    """把文章管理里的一条稿件开成成稿台稿（幂等）。

    文章管理点「进入成稿台」走这里：同一条稿件已经有成稿台稿就直接打开，
    没有才按最新正文建一篇。不做幂等的话，每点一次就多一篇重复稿，
    改稿的人分不清哪篇才是自己在改的那篇。

    热点选题「写这篇」会带 hotClusterId：把热点的标题/赛道/平台/各平台原文整理成
    material 写进成稿台稿，创作小助手才有素材可写。已存在的稿若素材仍为空也会补写，
    已有素材不覆盖。
    """
    _begin_sqlite_write(db)
    item = _owned_content_for_update(db, content_id, user.id)
    if item is None:
        raise not_found("稿件")
    hot_material = (
        hot_topics_service.build_material(db, hot_cluster_id) if hot_cluster_id else ""
    )
    existing = db.scalars(
        select(models.StudioArticle)
        .where(
            models.StudioArticle.content_item_id == content_id,
            models.StudioArticle.owner_user_id == user.id,
        )
        .order_by(models.StudioArticle.updated_at.desc())
    ).first()
    if existing is not None:
        if hot_material and not (existing.material or "").strip():
            # 先释放来源锁；存档按 StudioArticle → ContentItem 的顺序加锁。
            existing_id = existing.id
            db.commit()
            _begin_sqlite_write(db)
            existing = db.scalar(
                select(models.StudioArticle).where(models.StudioArticle.id == existing_id)
                .with_for_update().execution_options(populate_existing=True)
            )
            if existing is None:
                raise not_found("稿件")
            if not (existing.material or "").strip():
                existing.material = hot_material
            db.commit()
        return _out(studio_repo.row_to_dict(existing))

    # 正文以最新版本为准；只有自动草稿、还没存过版本时才退回 draft_body
    body = item.draft_body or ""
    if item.current_version_id:
        version = db.get(models.ContentVersion, item.current_version_id)
        if version is not None and version.body:
            body = version.body
    row = studio_repo.create(
        db,
        owner_user_id=user.id,
        workspace_id=item.workspace_id,
        topic=item.title or "",
        material=hot_material,
        markdown=body,
        writer_style="tutorial",
    )
    # 记住来源稿件：下次再点「进入成稿台」就能命中上面那条已有分支
    row.content_item_id = content_id
    db.commit()
    row = _article(db, user, row.id)
    return _out(studio_repo.row_to_dict(row))


@router.get("/studio/articles/{article_id}", response_model=schemas.StudioArticleOut)
def get_article(article_id: str, db: DbSession, user: CurrentUser):
    _row, data = _data(db, user, article_id)
    return _out(data)


@router.post("/studio/articles/{article_id}/archive", response_model=schemas.StudioArticleOut)
def archive_article(article_id: str, db: DbSession, user: CurrentUser, archived: bool = True):
    row, data = _data(db, user, article_id)
    data["archived"] = archived
    data["archived_at"] = AB.now_iso() if archived else None
    AB.touch(data)
    _commit_article(db, row, data)
    return _out(data)


@router.delete("/studio/articles/{article_id}", status_code=204)
def delete_article(article_id: str, db: DbSession, user: CurrentUser):
    _busy(article_id)
    row = _article(db, user, article_id)
    db.delete(row)
    db.commit()


@router.post("/studio/articles/{article_id}/duplicate", response_model=schemas.StudioArticleOut)
def duplicate_article(article_id: str, db: DbSession, user: CurrentUser):
    _row, data = _data(db, user, article_id)
    copy_row = studio_repo.create(
        db,
        owner_user_id=user.id,
        workspace_id=_row.workspace_id,
        topic=data.get("topic", ""),
        material=data.get("material", ""),
        markdown=AB.to_markdown(data),
        # 复制件沿用原稿的风格档，改稿时不会莫名其妙换风格
        writer_style=data.get("writer_style") or SP.DEFAULT_WRITER_STYLE,
    )
    copy_row.title = data.get("title") or copy_row.title
    db.commit()
    copy_data = studio_repo.row_to_dict(copy_row)
    return _out(copy_data)


@router.get("/studio/articles/{article_id}/markdown", response_model=schemas.StudioMarkdownOut)
def article_markdown(article_id: str, db: DbSession, user: CurrentUser):
    _row, data = _data(db, user, article_id)
    return {"markdown": AB.to_markdown(data)}


@router.patch("/studio/articles/{article_id}/markdown", response_model=schemas.StudioArticleOut)
def save_markdown(article_id: str, payload: schemas.StudioMarkdownPatchIn, db: DbSession, user: CurrentUser):
    _busy(article_id)
    row, data = _data(db, user, article_id)
    current = AB.to_markdown(data)
    if current != payload.expected_markdown:
        raise _fail("稿件已有新版本，未保存的源码已保留。请复制修改后重新载入最新正文。", 409)
    if current == payload.markdown:
        return _out(data)
    edited = dict(data)
    AB.edit_markdown(edited, payload.markdown)
    if len(edited["title"]) > 200 or any(len(block["text"]) > AB.BLOCK_TEXT_MAX for block in edited["blocks"]):
        raise _fail("标题不能超过 200 字，单段不能超过 20000 字。")
    studio_repo.snapshot(db, row, data, "编辑 Markdown 源码")
    AB.touch(edited)
    _commit_article(db, row, edited)
    return _out(edited)


@router.post("/studio/articles/{article_id}/export", response_model=dict)
def export_to_content(article_id: str, db: DbSession, user: CurrentUser):
    """存回关联文章；最新快照相同则复用，否则追加一个版本。"""
    # default_workspace 在需要建工作区时会提交，必须在拿存档锁之前调用。
    workspace_id = default_workspace(db, user).id
    _begin_sqlite_write(db)
    row = db.scalar(
        select(models.StudioArticle)
        .where(models.StudioArticle.id == article_id, models.StudioArticle.owner_user_id == user.id)
        .with_for_update()
    )
    if row is None:
        raise not_found("稿件")
    data = studio_repo.row_to_dict(row)
    title = (data.get("title") or data.get("topic") or "未命名稿件")[:200]
    snapshot = {
        "title": title,
        "body": AB.to_markdown(data),
        "summary": data.get("summary") or "",
        "layout_options": data.get("layout_options") or {},
    }
    item = _owned_content_for_update(db, row.content_item_id, user.id) if row.content_item_id else None
    if item is None:
        item = models.ContentItem(
            workspace_id=row.workspace_id or workspace_id,
            owner_user_id=user.id,
            title=title,
            type="article",
            status="draft",
        )
        db.add(item)
        db.flush()
        row.content_item_id = item.id

    latest = db.scalar(
        select(models.ContentVersion)
        .where(models.ContentVersion.content_item_id == item.id)
        .order_by(models.ContentVersion.version_no.desc())
        .limit(1)
    )
    current = latest
    if item.current_version_id and (latest is None or latest.id != item.current_version_id):
        current = db.get(models.ContentVersion, item.current_version_id)
    cover_id = (data.get("cover") or {}).get("assetId") or None
    if current and current.source_type == "studio" and current.content_item_id == item.id and all(
        getattr(current, key) == value for key, value in snapshot.items()
    ):
        saved_cover = db.scalar(
            select(models.ContentAsset.asset_id)
            .where(models.ContentAsset.content_version_id == current.id, models.ContentAsset.usage_role == "cover")
            .limit(1)
        )
        if saved_cover == cover_id:
            db.commit()
            return {"contentId": item.id, "versionId": current.id, "title": title}

    version = models.ContentVersion(
        content_item_id=item.id,
        version_no=latest.version_no + 1 if latest else 1,
        parent_version_id=item.current_version_id,
        source_type="studio",
        **snapshot,
    )
    db.add(version)
    db.flush()
    if cover_id:
        db.add(models.ContentAsset(content_version_id=version.id, asset_id=cover_id, usage_role="cover"))
    item.title = title
    item.draft_title = title
    item.draft_body = snapshot["body"]
    item.current_version_id = version.id
    db.commit()
    return {"contentId": item.id, "versionId": version.id, "title": title}


# ---------------- 改稿 ----------------


@router.post("/studio/articles/{article_id}/chat", response_model=schemas.StudioArticleOut)
def chat(article_id: str, payload: schemas.StudioChatIn, db: DbSession, user: CurrentUser):
    row, data = _data(db, user, article_id)
    _busy(article_id)
    message = payload.message.strip()
    if not message:
        raise _fail("消息为空", 400)
    if payload.target_block_id is not None:
        _block_index(data, payload.target_block_id)
    if payload.model_id:
        try:
            ai_gateway.resolve_selection(db, user.id, payload.model_id)
        except ai_gateway.InvalidModelSelection as exc:
            raise ApiError(exc.code, exc.message, status_code=400) from exc
    data["chat"] = (data.get("chat") or []) + [{"role": "user", "text": message}]
    data["notice"] = None
    AB.touch(data)
    base = SP.chat_base(data)
    _commit_article(db, row, data)
    SP.PIPELINE.chat(article_id, message, target_block_id=payload.target_block_id, preview=payload.preview, base=base, model_id=payload.model_id)
    return _out(data)


def _proposal_action(article_id, proposal_id, db, user, *, accept):
    _busy(article_id)
    _begin_sqlite_write(db)
    row = db.scalar(select(models.StudioArticle).where(
        models.StudioArticle.id == article_id, models.StudioArticle.owner_user_id == user.id
    ).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise not_found("稿件")
    data = studio_repo.row_to_dict(row)
    proposal = next((turn.get("proposal") for turn in data.get("chat", [])
                     if isinstance(turn, dict) and isinstance(turn.get("proposal"), dict)
                     and turn["proposal"].get("id") == proposal_id), None)
    if proposal is None:
        raise not_found("改稿建议")
    if proposal.get("status") != "pending":
        raise _fail("这条改稿建议已处理。", 409)
    if accept:
        if SP.chat_base(data) != proposal.get("base"):
            raise _fail("稿件在生成建议后发生变化，请重新生成。", 409)
        ops = SP.validate_chat_ops(proposal.get("ops"), data, proposal.get("targetBlockId"))
        before = copy.deepcopy(data)
        receipts, _ = AB.apply_ops(data, ops)
        if receipts:
            studio_repo.snapshot(db, row, before, "接受改稿建议")
        proposal["status"] = "accepted"
    else:
        proposal["status"] = "discarded"
    _commit_article(db, row, data)
    return _out(data)


@router.post("/studio/articles/{article_id}/chat/proposals/{proposal_id}/accept", response_model=schemas.StudioArticleOut)
def accept_chat_proposal(article_id: str, proposal_id: str, db: DbSession, user: CurrentUser):
    return _proposal_action(article_id, proposal_id, db, user, accept=True)


@router.post("/studio/articles/{article_id}/chat/proposals/{proposal_id}/discard", response_model=schemas.StudioArticleOut)
def discard_chat_proposal(article_id: str, proposal_id: str, db: DbSession, user: CurrentUser):
    return _proposal_action(article_id, proposal_id, db, user, accept=False)


@router.post("/studio/articles/{article_id}/titles", response_model=schemas.StudioArticleOut)
def titles(article_id: str, db: DbSession, user: CurrentUser):
    _row, data = _data(db, user, article_id)
    _busy(article_id)
    if not data.get("blocks"):
        raise _fail("还没有正文，先写正文或导入 Markdown。", 400)
    SP.PIPELINE.titles(article_id)
    return _out(data)


@router.post("/studio/articles/{article_id}/title/pick", response_model=schemas.StudioArticleOut)
def pick_title(article_id: str, payload: schemas.StudioTitlePickIn, db: DbSession, user: CurrentUser):
    row, data = _data(db, user, article_id)
    _busy(article_id)
    value = payload.text.strip()[:80]
    if not value or value == data.get("title"):
        return _out(data)
    studio_repo.snapshot(db, row, data, "换标题")
    data["title"] = value
    match = next((item for item in data.get("titles") or [] if item.get("text") == value), None)
    data["hook"] = (match or {}).get("hook")
    AB.touch(data)
    _commit_article(db, row, data)
    return _out(data)


@router.patch("/studio/articles/{article_id}/blocks/{block_id}", response_model=schemas.StudioArticleOut)
def update_block(article_id: str, block_id: str, payload: schemas.StudioBlockPatchIn, db: DbSession, user: CurrentUser):
    row, data = _data(db, user, article_id)
    _busy(article_id)
    index = _block_index(data, block_id)
    block = data["blocks"][index]
    if payload.expected_text is not None and block["text"] != payload.expected_text:
        raise _fail("这一段已有新版本，未保存的编辑已保留。", 409)
    value = payload.text.replace("\r\n", "\n").replace("\r", "\n").strip()[:20000]
    if value == block["text"]:
        return _out(data)
    studio_repo.snapshot(db, row, data, "手动改稿")
    if not value:
        data["blocks"].pop(index)
    else:
        parsed = AB.split_markdown(value, body_only=True)
        pieces = parsed["blocks"] or [value]
        block["text"] = pieces[0]
        block["kind"] = AB.block_kind(pieces[0])
        # 自己动手改过的段落，旧的检测结论不再作数
        block["check"] = None
        if len(pieces) > 1:
            extra = [
                {"id": AB.new_block_id(), "text": text, "kind": AB.block_kind(text),
                 "check": None, "prev_text": None, "inserted_diagram_id": None}
                for text in pieces[1:]
            ]
            data["blocks"][index + 1:index + 1] = extra
    AB.touch(data)
    _commit_article(db, row, data)
    return _out(data)


@router.post("/studio/articles/{article_id}/blocks/{block_id}/keep", response_model=schemas.StudioArticleOut)
def keep_block(article_id: str, block_id: str, db: DbSession, user: CurrentUser):
    row, data = _data(db, user, article_id)
    index = _block_index(data, block_id)
    check = dict(data["blocks"][index].get("check") or {})
    check["status"] = "kept"
    data["blocks"][index]["check"] = check
    AB.touch(data)
    _commit_article(db, row, data)
    return _out(data)


@router.post("/studio/articles/{article_id}/blocks/{block_id}/revert", response_model=schemas.StudioArticleOut)
def revert_block(article_id: str, block_id: str, db: DbSession, user: CurrentUser):
    row, data = _data(db, user, article_id)
    _busy(article_id)
    index = _block_index(data, block_id)
    block = data["blocks"][index]
    if not block.get("prev_text"):
        raise _fail("这一段没有上一版", 400)
    studio_repo.snapshot(db, row, data, "退回上一版")
    block["text"] = block["prev_text"]
    block["prev_text"] = None
    block["kind"] = AB.block_kind(block["text"])
    check = dict(block.get("check") or {})
    block["check"] = {"status": "kept", "round": check.get("round")}
    AB.touch(data)
    _commit_article(db, row, data)
    return _out(data)


@router.post("/studio/articles/{article_id}/undo", response_model=schemas.StudioArticleOut)
def undo(article_id: str, db: DbSession, user: CurrentUser):
    row, data = _data(db, user, article_id)
    _busy(article_id)
    popped = studio_repo.pop_last(db, article_id)
    if not popped:
        raise _fail("没有可以撤销的改动", 400)
    AB.restore_payload(data, popped.get("payload") or {})
    data["notice"] = {"text": f"已撤销：{popped.get('label', '')}", "at": AB.now_iso()}
    _commit_article(db, row, data)
    return _out(data)


@router.get("/studio/articles/{article_id}/revisions", response_model=schemas.StudioRevisionsOut)
def revisions(article_id: str, db: DbSession, user: CurrentUser):
    _row, data = _data(db, user, article_id)
    history = studio_repo.history(db, article_id)
    rows = AB.revision_changes(history, data)
    return {"revisions": rows}


# ---------------- 流水线 ----------------


@router.post("/studio/articles/{article_id}/draft", response_model=schemas.StudioArticleOut)
def draft(
    article_id: str,
    db: DbSession,
    user: CurrentUser,
    payload: schemas.StudioDraftIn | None = None,
    empty_only: bool = Query(default=False, alias="emptyOnly"),
):
    row, data = _data(db, user, article_id)
    skill_prompt = author_skills.build_system_prompt(db, user.id, payload.skill_ids) if payload else ""
    if empty_only:
        if data["blocks"]:
            return _out(data)
        job = _job_out(article_id)
        if not job:
            try:
                job = SP.PIPELINE.draft(article_id, empty_only=True, **({"skill_prompt": skill_prompt} if skill_prompt else {}))
            except SP.PipelineError as exc:
                if exc.status != 409:
                    raise
                job = _job_out(article_id)
        if not job:  # 快速结束的任务可能已落库，不返回启动前的空稿。
            db.rollback()
            _, data = _data(db, user, article_id)
        return {**_out(data), "job": job or _job_out(article_id)}
    _busy(article_id)
    # 带 style 就是「换风格重写」：先落风格再排队，任务读的是落库后的值
    if payload and payload.style:
        style = _style_or_fail(payload.style)
        if style != data.get("writer_style"):
            data["writer_style"] = style
            AB.touch(data)
            _commit_article(db, row, data)
    SP.PIPELINE.draft(article_id, **({"skill_prompt": skill_prompt} if skill_prompt else {}))
    return _out(data)


@router.post("/studio/articles/{article_id}/writer-style", response_model=schemas.StudioArticleOut)
def set_writer_style(article_id: str, payload: schemas.StudioStyleIn, db: DbSession, user: CurrentUser):
    """只记风格，不触发写作。当前有任务在跑也允许切：任务已按旧档取好 prompt。"""
    row, data = _data(db, user, article_id)
    style = _style_or_fail(payload.style)
    if style != (data.get("writer_style") or ""):
        data["writer_style"] = style
        AB.touch(data)
        _commit_article(db, row, data)
    return _out(data)


@router.post("/studio/articles/{article_id}/blocks/{block_id}/fix", response_model=schemas.StudioArticleOut)
def fix_block(article_id: str, block_id: str, db: DbSession, user: CurrentUser):
    _row, data = _data(db, user, article_id)
    _busy(article_id)
    _block_index(data, block_id)
    SP.PIPELINE.fix(article_id, block_id)
    return _out(data)


@router.post("/studio/articles/{article_id}/fix-all", response_model=schemas.StudioArticleOut)
def fix_all(article_id: str, db: DbSession, user: CurrentUser):
    _row, data = _data(db, user, article_id)
    _busy(article_id)
    picked, _skipped = AB.fix_plan(data["blocks"])
    if not picked:
        raise _fail("没有需要处理的标红段", 400)
    SP.PIPELINE.fix_all(article_id)
    return _out(data)


@router.post("/studio/articles/{article_id}/job/stop", response_model=schemas.StudioArticleOut)
def stop_job(article_id: str, db: DbSession, user: CurrentUser):
    _row, data = _data(db, user, article_id)
    SP.PIPELINE.stop(article_id)
    return _out(data)


@router.post("/studio/articles/{article_id}/banner", response_model=schemas.StudioArticleOut)
def banner(article_id: str, payload: schemas.StudioBannerIn, db: DbSession, user: CurrentUser):
    row, data = _data(db, user, article_id)
    _busy(article_id)
    style = payload.style or SP.DEFAULT_BANNER
    if style not in (*SP.BANNER_STYLES, "none"):
        raise _fail("请选择有效的横幅风格。", 400)
    data["banner_style"] = style
    AB.touch(data)
    _commit_article(db, row, data)
    if style != "none" and not (data.get("banners") or {}).get(style):
        SP.PIPELINE.banner(article_id)
    return _out(data)


# ---------------- 质检 ----------------


@router.post("/studio/articles/{article_id}/zhuque/detect", response_model=schemas.StudioArticleOut)
def zhuque_detect(article_id: str, db: DbSession, user: CurrentUser, payload: schemas.StudioDetectIn | None = None):
    _row, data = _data(db, user, article_id)
    _busy(article_id)
    block_ids = (payload.block_ids or []) if payload else []
    if block_ids:
        wanted = set(block_ids)
        scope = [i for i, block in enumerate(data["blocks"]) if block.get("id") in wanted and AB.is_checkable(block["text"])]
        if not scope:
            raise _fail("选中的段落里没有可检测的正文。", 400)
    elif not any(AB.is_checkable(block["text"]) for block in data["blocks"]):
        raise _fail("这篇文章还没有可以检测的正文段落。", 400)
    source = (payload.source or "auto") if payload else "auto"
    if source not in ("auto", "zhuque", "local"):
        source = "auto"
    settings = _quality_settings(db)
    SP.PIPELINE.detect(
        article_id,
        key=settings.get("key") or None,
        endpoint=settings.get("endpoint") or None,
        fixed_lines=settings.get("fixedLines") or [],
        block_ids=block_ids or None,
        source_pref=source,
    )
    return _out(data)


@router.post("/studio/articles/{article_id}/zhuque/report", response_model=schemas.StudioArticleOut)
async def zhuque_report(article_id: str, request: Request, db: DbSession, user: CurrentUser):
    """接受朱雀报告 PDF（原始字节）或 JSON {"text": "..."}。"""
    _row, data = _data(db, user, article_id)
    _busy(article_id)
    body = await request.body()
    if not body:
        raise _fail("请求体为空", 400)
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        import json as _json

        try:
            payload = _json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise _fail("请求不是有效的 JSON", 400) from exc
        report_text = str(payload.get("text") or "")
        if not report_text.strip():
            raise _fail("没有报告文字", 400)
    else:
        if len(body) > MAX_PDF:
            raise _fail("报告文件太大", 413)
        report_text = AQ.pdf_to_text(body)
    SP.PIPELINE.report(article_id, report_text)
    return _out(data)


# ---------------- 排期与提示 ----------------


@router.post("/studio/articles/{article_id}/schedule", response_model=schemas.StudioArticleOut)
def set_schedule(article_id: str, payload: schemas.StudioScheduleIn, db: DbSession, user: CurrentUser):
    """只记「计划什么时候发」，真正的发布队列仍由 publishing 模块负责。

    同时同步一条内容日历条目：在成稿台排的期要能在「内容日历」里看到，
    取消排期也要一起撤掉，日历里手排的条目不受影响。
    """
    row, data = _data(db, user, article_id)
    if payload.at is not None:
        at = payload.at
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at < datetime.now(timezone.utc):
            raise _fail("计划发布时间不能早于现在。", 400)
        data["scheduled_for"] = at.isoformat()
    else:
        data["scheduled_for"] = None
    AB.touch(data)
    studio_repo.save(db, row, data)
    studio_calendar.sync_schedule(db, user, row, data)
    db.commit()
    return _out(data)


@router.post("/studio/articles/{article_id}/notice/clear", response_model=schemas.StudioArticleOut)
def clear_notice(article_id: str, db: DbSession, user: CurrentUser):
    row, data = _data(db, user, article_id)
    data["notice"] = None
    _commit_article(db, row, data)
    return _out(data)


@router.post("/studio/articles/{article_id}/layout/preferences", response_model=schemas.StudioArticleOut)
def save_layout_preferences(article_id: str, payload: dict, db: DbSession, user: CurrentUser):
    row, data = _data(db, user, article_id)
    data["layout_options"] = payload or {}
    AB.touch(data)
    _commit_article(db, row, data)
    return _out(data)
