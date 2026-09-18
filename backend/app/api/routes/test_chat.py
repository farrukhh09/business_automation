"""Test chat routes ("Диалоги" without Instagram, local development only) — 04-api.md §11a.

- ``GET  /api/test-chat/{customer_key}``           STAFF → ``TestChatOut`` (empty if never messaged)
- ``POST /api/test-chat/{customer_key}/messages``  STAFF ``{text}`` → ``TestChatOut``
- ``POST /api/test-chat/{customer_key}/images``    STAFF ``multipart{file, text?}`` → ``TestChatOut``

``customer_key`` is chosen by the frontend (a random id kept in ``localStorage``) — a fresh key
starts a brand-new test customer/conversation, exactly like ``scripts/chat_console.py --new``.
Disabled (404) outside ``APP_ENV=development`` (``TestChatService`` raises).
"""

import re
from typing import Annotated

from fastapi import APIRouter, File, Form, Path, UploadFile

from app.api.deps import DbSession, StaffUser
from app.core.exceptions import ValidationError
from app.repositories.conversations import DEFAULT_MESSAGES_LIMIT, MessageRepository
from app.schemas.conversation import MESSAGE_TEXT_MAX, MessageOut, SendMessageIn, TestChatOut
from app.services.test_chat_service import MAX_IMAGE_BYTES, TestChatService

router = APIRouter(prefix="/test-chat", tags=["test-chat"])

_KEY_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
CustomerKey = Annotated[str, Path(description="Идентификатор тестового клиента (генерирует фронтенд)")]


def _validated(customer_key: str) -> str:
    if not _KEY_RE.match(customer_key):
        raise ValidationError(detail="Некорректный идентификатор тестового клиента")
    return customer_key


def _out(service: TestChatService, customer_key: str) -> TestChatOut:
    conversation = service.conversation_for(customer_key)
    if conversation is None:
        return TestChatOut()
    messages = MessageRepository(service.db).recent_for_conversation(conversation.id, DEFAULT_MESSAGES_LIMIT)
    return TestChatOut(
        conversation_id=conversation.id,
        mode=conversation.mode,
        needs_attention=conversation.needs_attention,
        messages=[MessageOut.model_validate(message) for message in messages],
    )


@router.get("/{customer_key}", response_model=TestChatOut, summary="История тестового диалога")
def get_test_chat(customer_key: CustomerKey, db: DbSession, user: StaffUser) -> TestChatOut:
    service = TestChatService(db)
    return _out(service, _validated(customer_key))


@router.post("/{customer_key}/messages", response_model=TestChatOut, summary="Отправить сообщение боту как клиент")
def send_test_chat_message(
    customer_key: CustomerKey, payload: SendMessageIn, db: DbSession, user: StaffUser
) -> TestChatOut:
    service = TestChatService(db)
    key = _validated(customer_key)
    try:
        service.send(key, payload.text)
    finally:
        service.close()
    return _out(service, key)


@router.post("/{customer_key}/images", response_model=TestChatOut, summary="Отправить боту изображение как клиент")
def send_test_chat_image(
    customer_key: CustomerKey,
    db: DbSession,
    user: StaffUser,
    file: Annotated[UploadFile, File(description="Изображение: JPEG, PNG, WebP или GIF, до 10 МБ")],
    text: Annotated[str | None, Form(max_length=MESSAGE_TEXT_MAX, description="Подпись к изображению")] = None,
) -> TestChatOut:
    """A screenshot of a payment (03 §3) or any other picture, as if the customer had sent it."""
    service = TestChatService(db)
    key = _validated(customer_key)
    data = file.file.read(MAX_IMAGE_BYTES + 1)  # one byte past the limit is enough to reject it
    try:
        service.send_image(key, data, file.content_type, text=(text or "").strip() or None)
    finally:
        service.close()
    return _out(service, key)
