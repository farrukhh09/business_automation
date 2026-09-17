"""Conversations ("Диалоги") routes — docs/architecture/04-api.md §11. All STAFF.

- ``GET  /api/conversations``                 ``mode``, ``needs_attention``, ``search``, pagination
  → ``Page[ConversationListItem]``
- ``GET  /api/conversations/{id}``            ``before_id?``, ``limit`` (50) → ``ConversationDetail``
- ``POST /api/conversations/{id}/messages``   ``{text}`` → ``MessageOut`` (409 ``messaging_window_closed``)
- ``POST /api/conversations/{id}/handoff``    ``{reason?}`` → ``ConversationDetail``
- ``POST /api/conversations/{id}/resume``     → ``ConversationDetail``
- ``POST /api/conversations/{id}/read``       → 204

Thin layer (01 §3): every rule lives in ``ConversationService``.
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Response, status

from app.api.deps import DbSession, Pagination, StaffUser
from app.models.enums import ConversationMode
from app.repositories.conversations import DEFAULT_MESSAGES_LIMIT
from app.schemas.common import Page
from app.schemas.conversation import ConversationDetail, ConversationListItem, HandoffIn, MessageOut, SendMessageIn
from app.services.conversation_service import ConversationService
from app.services.task_queue import TaskQueue, get_task_queue

router = APIRouter(prefix="/conversations", tags=["conversations"])

ConversationId = Annotated[int, Path(ge=1, description="Идентификатор диалога")]
Queue = Annotated[TaskQueue, Depends(get_task_queue)]
MAX_MESSAGES_LIMIT = 200


@router.get("", response_model=Page[ConversationListItem], summary="Список диалогов")
def list_conversations(
    db: DbSession,
    user: StaffUser,
    pagination: Pagination,
    mode: Annotated[ConversationMode | None, Query(description="AI или HUMAN_HANDOFF")] = None,
    needs_attention: Annotated[bool | None, Query(description="Только требующие внимания")] = None,
    search: Annotated[str | None, Query(max_length=128, description="Имя, username или телефон клиента")] = None,
) -> Page[ConversationListItem]:
    return ConversationService(db).list(
        mode=mode,
        needs_attention=needs_attention,
        search=search,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/{conversation_id}", response_model=ConversationDetail, summary="Диалог с сообщениями")
def get_conversation(
    conversation_id: ConversationId,
    db: DbSession,
    user: StaffUser,
    before_id: Annotated[int | None, Query(ge=1, description="Загрузить сообщения старше этого id")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_MESSAGES_LIMIT, description="Сколько сообщений")] = DEFAULT_MESSAGES_LIMIT,
) -> ConversationDetail:
    return ConversationService(db).detail(conversation_id, before_id=before_id, limit=limit)


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ответ оператора клиенту в Instagram",
)
def send_message(
    conversation_id: ConversationId, payload: SendMessageIn, db: DbSession, user: StaffUser, queue: Queue
) -> MessageOut:
    message = ConversationService(db, queue=queue).send_operator_message(conversation_id, payload.text, user)
    return MessageOut.model_validate(message)


@router.post("/{conversation_id}/handoff", response_model=ConversationDetail, summary="Взять диалог на себя")
def handoff_conversation(
    conversation_id: ConversationId,
    db: DbSession,
    user: StaffUser,
    payload: Annotated[HandoffIn | None, Body()] = None,
) -> ConversationDetail:
    reason = payload.reason if payload is not None else None
    return ConversationService(db).take_over(conversation_id, reason, user)


@router.post("/{conversation_id}/resume", response_model=ConversationDetail, summary="Вернуть диалог боту")
def resume_conversation(conversation_id: ConversationId, db: DbSession, user: StaffUser) -> ConversationDetail:
    return ConversationService(db).resume(conversation_id, user)


@router.post("/{conversation_id}/read", status_code=status.HTTP_204_NO_CONTENT, summary="Отметить прочитанным")
def mark_conversation_read(conversation_id: ConversationId, db: DbSession, user: StaffUser) -> Response:
    ConversationService(db).mark_read(conversation_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
