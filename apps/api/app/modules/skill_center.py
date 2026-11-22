"""技能中心 API：用户技能卡的增删改查、蒸馏、导入导出。

独立于 `/skills`（Agent 工具型 Skill 注册表）与 `/writing-rules`（创作规则，已废弃中）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse

from .. import schemas
from ..core.deps import CurrentUser, DbSession
from ..core.rbac import require_permission
from ..services import author_skills

router = APIRouter(tags=["skill-center"], dependencies=[require_permission("studio.use")])


def _out(skill) -> schemas.AuthorSkillOut:
    return schemas.AuthorSkillOut.model_validate(skill)


@router.get("/skill-center/skills", response_model=list[schemas.AuthorSkillOut])
def list_skills(
    user: CurrentUser,
    db: DbSession,
    skill_type: str | None = Query(default=None, alias="type"),
) -> list[schemas.AuthorSkillOut]:
    return [_out(skill) for skill in author_skills.list_skills(db, user.id, skill_type)]


@router.post("/skill-center/skills", response_model=schemas.AuthorSkillOut, status_code=201)
def create_skill(
    payload: schemas.AuthorSkillIn, user: CurrentUser, db: DbSession
) -> schemas.AuthorSkillOut:
    return _out(
        author_skills.create_skill(
            db,
            user.id,
            name=payload.name,
            skill_type=payload.skill_type,
            description=payload.description,
            summary=payload.summary,
            config=payload.config,
        )
    )


@router.get("/skill-center/skills/{skill_id}", response_model=schemas.AuthorSkillOut)
def get_skill(skill_id: str, user: CurrentUser, db: DbSession) -> schemas.AuthorSkillOut:
    return _out(author_skills.get_skill(db, user.id, skill_id))


@router.put("/skill-center/skills/{skill_id}", response_model=schemas.AuthorSkillOut)
def update_skill(
    skill_id: str, payload: schemas.AuthorSkillUpdateIn, user: CurrentUser, db: DbSession
) -> schemas.AuthorSkillOut:
    return _out(
        author_skills.update_skill(
            db,
            user.id,
            skill_id,
            name=payload.name,
            description=payload.description,
            summary=payload.summary,
            config=payload.config,
            note=payload.note,
        )
    )


@router.delete("/skill-center/skills/{skill_id}", status_code=204)
def delete_skill(skill_id: str, user: CurrentUser, db: DbSession) -> None:
    author_skills.delete_skill(db, user.id, skill_id)


@router.post(
    "/skill-center/skills/{skill_id}/duplicate",
    response_model=schemas.AuthorSkillOut,
    status_code=201,
)
def duplicate_skill(skill_id: str, user: CurrentUser, db: DbSession) -> schemas.AuthorSkillOut:
    return _out(author_skills.duplicate_skill(db, user.id, skill_id))


@router.post(
    "/skill-center/skills/{skill_id}/distill",
    response_model=schemas.AuthorSkillDistillOut,
)
async def distill_skill(
    skill_id: str, payload: schemas.AuthorSkillDistillIn, user: CurrentUser, db: DbSession
) -> schemas.AuthorSkillDistillOut:
    run = await author_skills.distill(db, user.id, skill_id, payload.model_dump(by_alias=False))
    return schemas.AuthorSkillDistillOut.model_validate(run)


@router.get("/skill-center/runs/{run_id}", response_model=schemas.AuthorSkillDistillOut)
def get_run(run_id: str, user: CurrentUser, db: DbSession) -> schemas.AuthorSkillDistillOut:
    from .. import models
    from ..core.deps import not_found

    run = db.get(models.AuthorSkillDistillRun, run_id)
    if not run or run.owner_user_id != user.id:
        raise not_found("蒸馏记录")
    return schemas.AuthorSkillDistillOut.model_validate(run)


@router.get("/skill-center/skills/{skill_id}/export")
def export_skill(skill_id: str, user: CurrentUser, db: DbSession) -> JSONResponse:
    from urllib.parse import quote

    skill = author_skills.get_skill(db, user.id, skill_id)
    package = author_skills.export_skill(skill)
    safe_name = "".join(ch for ch in skill.name if ch not in '\\/:*?"<>|').strip() or "skill"
    return JSONResponse(
        package,
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"skill.json\"; filename*=UTF-8''{quote(safe_name)}.skill.json"
            )
        },
    )


@router.get("/skill-center/export")
def export_all(user: CurrentUser, db: DbSession, response: Response) -> dict:
    response.headers["Content-Disposition"] = "attachment; filename=author-skills.skill.json"
    return author_skills.export_package(db, user.id)


@router.post(
    "/skill-center/import",
    response_model=list[schemas.AuthorSkillOut],
    status_code=201,
)
def import_skills(
    payload: dict, user: CurrentUser, db: DbSession
) -> list[schemas.AuthorSkillOut]:
    """接受单张技能或整包；执行的是请求体本身，便于直接粘贴导出的 JSON。"""
    return [_out(skill) for skill in author_skills.import_skill(db, user.id, payload)]
