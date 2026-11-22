"""宠物对话 API：成稿台悬浮助手的会话读写。

技能来自独立的 `author_skills` 技能库；正文只有用户点「新建成稿」才落库，
助手回复本身不改当前稿件。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .. import schemas
from ..core.deps import CurrentUser, DbSession, not_found
from ..core.rbac import require_permission
from ..services import pet_conversation_service

router = APIRouter(tags=["pet"], dependencies=[require_permission("studio.use")])


@router.post("/pet/conversations", response_model=schemas.PetConversationOut, status_code=201)
async def start_pet_conversation(
    payload: schemas.PetConversationIn, user: CurrentUser, db: DbSession
) -> schemas.PetConversationOut:
    service = pet_conversation_service.PetConversationService(db)
    conv = service.get_or_create_conversation(user.id, payload.studio_article_id)
    await service.process_message(conv, payload.message, payload.active_skill_ids)
    db.commit()
    return conv


@router.post(
    "/pet/conversations/{conversation_id}/message",
    response_model=schemas.PetConversationOut,
)
async def send_pet_message(
    conversation_id: str, payload: schemas.PetMessageIn, user: CurrentUser, db: DbSession
) -> schemas.PetConversationOut:
    repo = pet_conversation_service.PetConversationRepository(db)
    conv = repo.get_conversation(conversation_id, user.id)
    if not conv:
        raise not_found("对话")
    await pet_conversation_service.PetConversationService(db).process_message(
        conv, payload.message, payload.active_skill_ids
    )
    db.commit()
    return conv


@router.get(
    "/pet/conversations/{conversation_id}", response_model=schemas.PetConversationOut
)
def get_pet_conversation(
    conversation_id: str, user: CurrentUser, db: DbSession
) -> schemas.PetConversationOut:
    conv = pet_conversation_service.PetConversationRepository(db).get_conversation(
        conversation_id, user.id
    )
    if not conv:
        raise not_found("对话")
    return conv


@router.get("/pet/conversations", response_model=list[schemas.PetConversationOut])
def list_pet_conversations(
    user: CurrentUser,
    db: DbSession,
    studio_article_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[schemas.PetConversationOut]:
    return pet_conversation_service.PetConversationRepository(db).list_conversations(
        user.id, studio_article_id, limit
    )
