"""``TestChatService`` — the admin panel's "talk to the bot" page (dev-only, no Instagram)."""

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.enums import MessageDeliveryStatus, MessageSender
from app.repositories.conversations import MessageRepository
from app.services.test_chat_service import NOT_SENT_NOTE, TestChatDisabledError, TestChatService


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"APP_ENV": "development"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_disabled_outside_development(db: Session) -> None:
    with pytest.raises(TestChatDisabledError):
        TestChatService(db, settings=make_settings(APP_ENV="test"))


def test_conversation_for_is_none_before_the_first_message(db: Session) -> None:
    service = TestChatService(db, settings=make_settings())
    assert service.conversation_for("nobody-yet") is None


def test_send_creates_a_conversation_and_a_reply_never_sent_anywhere(db: Session) -> None:
    service = TestChatService(db, settings=make_settings(LLM_API_KEY=""))
    try:
        conversation = service.send("customer-1", "Здравствуйте")
    finally:
        service.close()

    assert conversation.instagram_conversation_id == "webtest:customer-1"
    assert conversation.customer.name == "Тестовый клиент (админка)"

    messages = MessageRepository(db).recent_for_conversation(conversation.id)
    assert [message.sender for message in messages] == [MessageSender.CUSTOMER, MessageSender.AI]
    reply = messages[-1]
    assert reply.delivery_status == MessageDeliveryStatus.FAILED
    assert reply.error == NOT_SENT_NOTE
    assert reply.text  # without an LLM key the dialog still hands off with a template reply


def test_a_fresh_customer_key_starts_a_separate_conversation(db: Session) -> None:
    service = TestChatService(db, settings=make_settings(LLM_API_KEY=""))
    try:
        first = service.send("alice", "Привет")
        second = service.send("bob", "Привет")
    finally:
        service.close()

    assert first.id != second.id
    assert service.conversation_for("alice") is not None
    assert service.conversation_for("bob") is not None
