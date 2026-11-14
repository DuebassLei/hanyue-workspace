"""小说项目：建档、列表、详情、改规则、删除、切章、手调章节边界。"""

from urllib.parse import quote

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, select

from .. import models, schemas
from ..core import queue
from ..core.deps import ApiError, CurrentUser, DbSession, default_workspace, not_found
from ..services import job_runner, novel_continue, novel_rewrite
from ..services.novel_split import split_chapters

router = APIRouter(tags=["novels"])

_RULE_KEYS = ("person", "style", "banned")
_EMPTY_FACTS = {"characterChanges": [], "itemHoldings": [], "events": [], "openHooks": []}


def _project(db, user, project_id) -> models.NovelProject:
    row = db.get(models.NovelProject, project_id)
    if not row or row.owner_user_id != user.id:
        raise not_found("小说项目")
    return row


def _chapter(db, project, chapter_id) -> models.NovelChapter:
    row = db.get(models.NovelChapter, chapter_id)
    if not row or row.project_id != project.id:
        raise not_found("章节")
    return row


def _rules(raw) -> schemas.NovelRules:
    data = raw if isinstance(raw, dict) else {}
    return schemas.NovelRules(**{key: str(data.get(key) or "") for key in _RULE_KEYS})


def _facts(raw) -> schemas.NovelFacts:
    data = raw if isinstance(raw, dict) else {}
    return schemas.NovelFacts(
        character_changes=list(data.get("characterChanges") or []),
        item_holdings=list(data.get("itemHoldings") or []),
        events=list(data.get("events") or []),
        open_hooks=list(data.get("openHooks") or []),
    )


def _chapter_out(row: models.NovelChapter) -> schemas.NovelChapterOut:
    return schemas.NovelChapterOut(
        id=row.id,
        sort_order=row.sort_order,
        source=row.source,
        title=row.title,
        source_text=row.source_text,
        rewrite_text=row.rewrite_text,
        draft_text=row.draft_text,
        accepted_text=row.accepted_text,
        draft_version=row.draft_version,
        status=row.status,
        facts=_facts(row.facts),
        stale=row.stale,
        impact=row.impact or {},
        pending_candidate=row.pending_candidate,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _project_out(db, project: models.NovelProject) -> schemas.NovelProjectOut:
    chapters = db.scalars(
        select(models.NovelChapter)
        .where(models.NovelChapter.project_id == project.id)
        .order_by(models.NovelChapter.sort_order)
    ).all()
    characters = db.scalars(
        select(models.NovelCharacter)
        .where(models.NovelCharacter.project_id == project.id)
        .order_by(models.NovelCharacter.created_at)
    ).all()
    aliases = db.scalars(
        select(models.NovelAlias)
        .where(models.NovelAlias.project_id == project.id)
        .order_by(models.NovelAlias.created_at)
    ).all()
    return schemas.NovelProjectOut(
        id=project.id,
        workspace_id=project.workspace_id,
        title=project.title,
        source_text=project.source_text,
        rights_confirmed_at=project.rights_confirmed_at,
        rules=_rules(project.rules),
        status=project.status,
        created_at=project.created_at,
        updated_at=project.updated_at,
        chapters=[_chapter_out(row) for row in chapters],
        characters=[
            schemas.NovelCharacterOut(
                id=row.id,
                original_name=row.original_name,
                new_name=row.new_name,
                relations=row.relations,
                speech_style=row.speech_style,
                state=row.state,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in characters
        ],
        aliases=[
            schemas.NovelAliasOut(
                id=row.id,
                kind=row.kind,
                original_name=row.original_name,
                new_name=row.new_name,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in aliases
        ],
    )


@router.post("/novels", response_model=schemas.NovelProjectOut, status_code=201)
def create_novel(payload: schemas.NovelProjectCreate, user: CurrentUser, db: DbSession):
    if payload.rights_confirmed is not True:
        raise ApiError("rights_required", "请先确认原文权利")
    source = payload.source_text.strip()
    if not source:
        raise ApiError("empty_source", "原文不能为空")
    title = (payload.title or "").strip() or "未命名小说"
    row = models.NovelProject(
        owner_user_id=user.id,
        workspace_id=default_workspace(db, user).id,
        title=title,
        source_text=source,
        rights_confirmed_at=models.utcnow(),
        rules={},
        status="draft",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _project_out(db, row)


@router.get("/novels", response_model=list[schemas.NovelProjectListItem])
def list_novels(user: CurrentUser, db: DbSession):
    chapter_count = func.count(models.NovelChapter.id).label("chapter_count")
    counts = (
        select(models.NovelChapter.project_id, chapter_count)
        .group_by(models.NovelChapter.project_id)
        .subquery()
    )
    rows = db.execute(
        select(models.NovelProject, func.coalesce(counts.c.chapter_count, 0))
        .outerjoin(counts, counts.c.project_id == models.NovelProject.id)
        .where(models.NovelProject.owner_user_id == user.id)
        .order_by(models.NovelProject.updated_at.desc(), models.NovelProject.id.desc())
    ).all()
    return [
        schemas.NovelProjectListItem(
            id=project.id,
            title=project.title,
            status=project.status,
            chapter_count=int(count),
            updated_at=project.updated_at,
        )
        for project, count in rows
    ]


@router.get("/novels/{project_id}", response_model=schemas.NovelProjectOut)
def get_novel(project_id: str, user: CurrentUser, db: DbSession):
    return _project_out(db, _project(db, user, project_id))


@router.patch("/novels/{project_id}", response_model=schemas.NovelProjectOut)
def patch_novel(project_id: str, payload: schemas.NovelProjectPatch, user: CurrentUser, db: DbSession):
    row = _project(db, user, project_id)
    if payload.title is not None:
        title = payload.title.strip()
        if title:
            row.title = title
    if payload.rules is not None:
        row.rules = {key: getattr(payload.rules, key) for key in _RULE_KEYS}
    db.commit()
    db.refresh(row)
    return _project_out(db, row)


@router.delete("/novels/{project_id}", status_code=204)
def delete_novel(project_id: str, user: CurrentUser, db: DbSession):
    row = _project(db, user, project_id)
    for model in (models.NovelChapter, models.NovelCharacter, models.NovelAlias):
        for child in db.scalars(select(model).where(model.project_id == row.id)).all():
            db.delete(child)
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.post("/novels/{project_id}/split", response_model=list[schemas.NovelChapterOut])
def split_novel(project_id: str, user: CurrentUser, db: DbSession):
    row = _project(db, user, project_id)
    pieces = split_chapters(row.source_text)
    for chapter in db.scalars(
        select(models.NovelChapter).where(models.NovelChapter.project_id == row.id)
    ).all():
        db.delete(chapter)
    db.flush()
    created = []
    for index, piece in enumerate(pieces):
        chapter = models.NovelChapter(
            project_id=row.id,
            sort_order=index,
            source="rewrite",
            title=piece["title"][:200],
            source_text=piece["source_text"],
            status="pending",
            facts=dict(_EMPTY_FACTS),
            stale=False,
        )
        db.add(chapter)
        created.append(chapter)
    db.commit()
    for chapter in created:
        db.refresh(chapter)
    return [_chapter_out(chapter) for chapter in created]


@router.patch("/novels/{project_id}/chapters/{chapter_id}", response_model=schemas.NovelChapterOut)
def patch_chapter(
    project_id: str,
    chapter_id: str,
    payload: schemas.NovelChapterPatch,
    user: CurrentUser,
    db: DbSession,
):
    project = _project(db, user, project_id)
    row = _chapter(db, project, chapter_id)
    if payload.title is not None:
        row.title = payload.title
    if payload.source_text is not None:
        row.source_text = payload.source_text
        row.status = "pending"
        row.rewrite_text = ""
        row.draft_text = ""
        row.accepted_text = ""
        row.facts = dict(_EMPTY_FACTS)
        row.stale = False
    db.commit()
    db.refresh(row)
    return _chapter_out(row)


def _novel_job_busy(db, *, owner_user_id: str, project_id: str, kind: str, chapter_id: str | None = None) -> bool:
    rows = db.scalars(
        select(models.BackgroundJob).where(
            models.BackgroundJob.owner_user_id == owner_user_id,
            models.BackgroundJob.kind == kind,
            models.BackgroundJob.status.in_(("queued", "running")),
        )
    ).all()
    for row in rows:
        payload = row.payload or {}
        if payload.get("novelProjectId") != project_id:
            continue
        if chapter_id is not None and payload.get("novelChapterId") != chapter_id:
            continue
        return True
    return False


class _CharacterPatch(BaseModel):
    newName: str | None = None
    relations: str | None = None
    speechStyle: str | None = None
    state: str | None = None


class _AliasPatch(BaseModel):
    newName: str


@router.post("/novels/{project_id}/extract", status_code=202)
def extract_novel(project_id: str, user: CurrentUser, db: DbSession):
    project = _project(db, user, project_id)
    if _novel_job_busy(db, owner_user_id=user.id, project_id=project.id, kind="novel.extract"):
        raise ApiError("job_in_progress", "抽取任务进行中", status_code=409)
    db.commit()
    job_id = queue.enqueue(
        db,
        owner_user_id=user.id,
        queue="novel",
        kind="novel.extract",
        payload={"novelProjectId": project.id},
    )
    return {"jobId": job_id}


@router.post("/novels/{project_id}/chapters/{chapter_id}/rewrite", status_code=202)
def rewrite_chapter(project_id: str, chapter_id: str, user: CurrentUser, db: DbSession):
    project = _project(db, user, project_id)
    chapter = _chapter(db, project, chapter_id)
    if chapter.source != "rewrite" or not str(chapter.source_text or "").strip():
        raise ApiError("nothing_to_rewrite", "没有可改写的原文")
    if _novel_job_busy(
        db,
        owner_user_id=user.id,
        project_id=project.id,
        kind="novel.rewrite",
        chapter_id=chapter.id,
    ):
        raise ApiError("job_in_progress", "改写任务进行中", status_code=409)
    expected = chapter.draft_version
    db.commit()
    job_id = queue.enqueue(
        db,
        owner_user_id=user.id,
        queue="novel",
        kind="novel.rewrite",
        payload={
            "novelProjectId": project.id,
            "novelChapterId": chapter.id,
            "expectedVersion": expected,
        },
    )
    return {"jobId": job_id, "expectedVersion": expected}


@router.post("/novels/{project_id}/chapters/{chapter_id}/accept", response_model=schemas.NovelChapterOut)
def accept_novel_chapter(project_id: str, chapter_id: str, user: CurrentUser, db: DbSession):
    """同步接受：先生成事实，再落库。不入队，也不改其它章节。"""
    project = _project(db, user, project_id)
    chapter = _chapter(db, project, chapter_id)
    if not str(chapter.draft_text or "").strip():
        raise ApiError("empty_draft", "没有可接受的草稿")
    characters = db.scalars(
        select(models.NovelCharacter).where(models.NovelCharacter.project_id == project.id)
    ).all()
    prompt = novel_rewrite.build_facts_prompt(chapter.draft_text, characters)
    try:
        raw = job_runner._novel_complete(db, user.id, prompt)
    except job_runner.JobFailure as exc:
        status = 400 if exc.code == "provider_unavailable" else 502
        raise ApiError(exc.code, exc.message, status_code=status) from exc
    facts = novel_rewrite.parse_facts(raw)
    novel_rewrite.accept_chapter(db, chapter, facts)
    if chapter.stale:
        chapter.stale = False
    later = db.scalars(
        select(models.NovelChapter)
        .where(
            models.NovelChapter.project_id == project.id,
            models.NovelChapter.sort_order > chapter.sort_order,
            models.NovelChapter.status == "accepted",
        )
        .order_by(models.NovelChapter.sort_order)
    ).all()
    chapter.impact = novel_continue.scan_impact(chapter, later, characters)
    db.commit()
    db.refresh(chapter)
    return _chapter_out(chapter)


@router.post("/novels/{project_id}/chapters/{chapter_id}/revert")
def revert_novel_chapter(project_id: str, chapter_id: str, user: CurrentUser, db: DbSession):
    project = _project(db, user, project_id)
    chapter = _chapter(db, project, chapter_id)
    later = db.scalars(
        select(models.NovelChapter)
        .where(
            models.NovelChapter.project_id == project.id,
            models.NovelChapter.sort_order > chapter.sort_order,
        )
        .order_by(models.NovelChapter.sort_order)
    ).all()
    try:
        novel_rewrite.revert_chapter(db, chapter, later)
    except job_runner.JobFailure as exc:
        raise ApiError(exc.code, exc.message) from exc
    stale_ids = [row.id for row in later]
    db.commit()
    db.refresh(chapter)
    return {"chapter": _chapter_out(chapter).model_dump(by_alias=True, mode="json"), "staleChapterIds": stale_ids}


@router.patch("/novels/{project_id}/characters/{character_id}", response_model=schemas.NovelCharacterOut)
def patch_character(
    project_id: str,
    character_id: str,
    payload: _CharacterPatch,
    user: CurrentUser,
    db: DbSession,
):
    project = _project(db, user, project_id)
    row = db.get(models.NovelCharacter, character_id)
    if not row or row.project_id != project.id:
        raise not_found("人物")
    data = payload.model_dump(exclude_unset=True)
    if "state" in data and data["state"] is not None and data["state"] not in ("present", "dead", "absent"):
        raise ApiError("bad_state", "人物状态只许 present、dead、absent")
    if "newName" in data and data["newName"] is not None:
        row.new_name = data["newName"]
        for alias in db.scalars(
            select(models.NovelAlias).where(
                models.NovelAlias.project_id == project.id,
                models.NovelAlias.kind == "person",
                models.NovelAlias.original_name == row.original_name,
            )
        ).all():
            alias.new_name = data["newName"]
    if "relations" in data and data["relations"] is not None:
        row.relations = data["relations"]
    if "speechStyle" in data and data["speechStyle"] is not None:
        row.speech_style = data["speechStyle"]
    if "state" in data and data["state"] is not None:
        row.state = data["state"]
    db.commit()
    db.refresh(row)
    return schemas.NovelCharacterOut(
        id=row.id,
        original_name=row.original_name,
        new_name=row.new_name,
        relations=row.relations,
        speech_style=row.speech_style,
        state=row.state,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.patch("/novels/{project_id}/aliases/{alias_id}", response_model=schemas.NovelAliasOut)
def patch_alias(
    project_id: str,
    alias_id: str,
    payload: _AliasPatch,
    user: CurrentUser,
    db: DbSession,
):
    project = _project(db, user, project_id)
    row = db.get(models.NovelAlias, alias_id)
    if not row or row.project_id != project.id:
        raise not_found("别名")
    row.new_name = payload.newName
    if row.kind == "person":
        for character in db.scalars(
            select(models.NovelCharacter).where(
                models.NovelCharacter.project_id == project.id,
                models.NovelCharacter.original_name == row.original_name,
            )
        ).all():
            character.new_name = payload.newName
    db.commit()
    db.refresh(row)
    return schemas.NovelAliasOut(
        id=row.id,
        kind=row.kind,
        original_name=row.original_name,
        new_name=row.new_name,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/novels/{project_id}/jobs/{job_id}")
def get_novel_job(project_id: str, job_id: str, user: CurrentUser, db: DbSession):
    project = _project(db, user, project_id)
    job = db.get(models.BackgroundJob, job_id)
    payload = (job.payload or {}) if job else {}
    if not job or job.owner_user_id != user.id or payload.get("novelProjectId") != project.id:
        raise not_found("任务")
    return {
        "id": job.id,
        "status": job.status,
        "kind": job.kind,
        "errorCode": job.error_code,
        "errorMessage": job.error_message,
        "result": job.result or {},
    }


def _safe_filename(title: str, ext: str) -> str:
    cleaned = "".join("_" if char in '\\/:*?"<>|\r\n' else char for char in (title or "").strip())
    cleaned = cleaned.strip(" .") or "novel"
    return f"{cleaned}.{ext}"


def _attachment(filename: str) -> str:
    try:
        filename.encode("latin-1")
        return f'attachment; filename="{filename}"'
    except UnicodeEncodeError:
        ascii_name = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
        if not ascii_name.strip("._"):
            ascii_name = "novel" + filename[filename.rfind(".") :]
        return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _export_body(chapters, fmt: str) -> str:
    blocks = []
    for chapter in chapters:
        title = str(chapter.title or "").strip() or "未命名"
        body = str(chapter.accepted_text or "")
        if fmt == "md":
            blocks.append(f"## {title}\n\n{body}")
        else:
            blocks.append(f"{title}\n\n{body}")
    if not blocks:
        return ""
    separator = "\n\n" if fmt == "md" else "\n\n\n"
    return separator.join(blocks)


@router.post("/novels/{project_id}/continue", status_code=202)
def continue_novel(project_id: str, user: CurrentUser, db: DbSession):
    """按已接受事实另开一章续写，不改旧章。"""
    project = _project(db, user, project_id)
    chapters = db.scalars(
        select(models.NovelChapter)
        .where(models.NovelChapter.project_id == project.id)
        .order_by(models.NovelChapter.sort_order)
    ).all()
    accepted = [row for row in chapters if row.status == "accepted"]
    if not accepted:
        raise ApiError("nothing_to_continue", "没有已接受的章节可续写")
    if any(row.source == "continuation" and row.status != "accepted" for row in chapters):
        raise ApiError("draft_pending", "还有未接受的续写草稿")
    if _novel_job_busy(db, owner_user_id=user.id, project_id=project.id, kind="novel.continue"):
        raise ApiError("job_in_progress", "续写任务进行中", status_code=409)
    next_order = max(row.sort_order for row in chapters) + 1 if chapters else 0
    index = sum(1 for row in chapters if row.source == "continuation") + 1
    chapter = models.NovelChapter(
        project_id=project.id,
        sort_order=next_order,
        source="continuation",
        title=f"续写 {index}",
        source_text="",
        status="pending",
        draft_version=0,
        facts=dict(_EMPTY_FACTS),
        stale=False,
    )
    db.add(chapter)
    db.flush()
    db.commit()
    job_id = queue.enqueue(
        db,
        owner_user_id=user.id,
        queue="novel",
        kind="novel.continue",
        payload={
            "novelProjectId": project.id,
            "novelChapterId": chapter.id,
            "expectedVersion": 0,
        },
    )
    return {"jobId": job_id, "chapterId": chapter.id, "expectedVersion": 0}


@router.get("/novels/{project_id}/export")
def export_novel(project_id: str, user: CurrentUser, db: DbSession, format: str = Query(default="md")):
    project = _project(db, user, project_id)
    fmt = (format or "").strip().lower()
    if fmt not in ("md", "txt"):
        raise ApiError("bad_format", "只支持 md 或 txt")
    chapters = db.scalars(
        select(models.NovelChapter)
        .where(
            models.NovelChapter.project_id == project.id,
            models.NovelChapter.status == "accepted",
        )
        .order_by(models.NovelChapter.sort_order)
    ).all()
    media = "text/markdown; charset=utf-8" if fmt == "md" else "text/plain; charset=utf-8"
    filename = _safe_filename(project.title, fmt)
    return Response(
        _export_body(chapters, fmt),
        media_type=media,
        headers={"Content-Disposition": _attachment(filename)},
    )
