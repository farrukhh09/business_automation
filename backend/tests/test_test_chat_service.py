"""``TestChatService`` — the admin panel's "talk to the bot" page (dev-only, no Instagram)."""

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import ValidationError
from app.models.enums import MessageDeliveryStatus, MessageSender, MessageType
from app.repositories.conversations import MessageRepository
from app.services.media_storage import MediaStorage
from app.services.test_chat_service import (
    MAX_IMAGE_BYTES,
    NOT_SENT_NOTE,
    TestChatDisabledError,
    TestChatService,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"receipt-screenshot-bytes"


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
    assert reply.text == "Здравствуйте! Что желаете заказать? 😊"  # a bare greeting needs no LLM key


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


# --------------------------------------------------------------------------- uploaded pictures


def test_send_image_stores_the_file_and_the_bot_sees_an_image_message(db: Session, tmp_path: Path) -> None:
    """The upload replaces the Instagram download: nothing is fetched, the file is already ours."""
    service = TestChatService(db, settings=make_settings(LLM_API_KEY="", MEDIA_ROOT=str(tmp_path)))
    try:
        conversation = service.send_image("with-receipt", PNG, "image/png", text="Вот чек")
    finally:
        service.close()

    incoming = MessageRepository(db).recent_for_conversation(conversation.id)[0]
    assert incoming.message_type == MessageType.IMAGE
    assert incoming.text == "Вот чек"
    assert incoming.error is None
    filename = MediaStorage.filename_from_url(incoming.media_url)
    assert filename is not None and filename.endswith(".png")
    assert (tmp_path / filename).read_bytes() == PNG
    assert conversation.needs_attention is True  # a picture is always the operator's business


def test_image_without_a_caption_goes_to_the_operator(db: Session, tmp_path: Path) -> None:
    service = TestChatService(db, settings=make_settings(LLM_API_KEY="", MEDIA_ROOT=str(tmp_path)))
    try:
        conversation = service.send_image("picture-only", PNG, "image/jpeg")
    finally:
        service.close()

    messages = MessageRepository(db).recent_for_conversation(conversation.id)
    assert messages[0].text is None
    reply = messages[-1]
    assert reply.sender == MessageSender.AI
    assert reply.delivery_status == MessageDeliveryStatus.FAILED  # never sent, as every test reply
    assert conversation.handoff_reason is not None


@pytest.mark.parametrize(
    "case,content_type",
    [("empty", "image/png"), ("pdf", "application/pdf"), ("no_type", None), ("too_large", "image/png")],
)
def test_send_image_rejects_what_is_not_a_reasonable_picture(
    db: Session, tmp_path: Path, case: str, content_type: str | None
) -> None:
    data = {"empty": b"", "too_large": b"x" * (MAX_IMAGE_BYTES + 1)}.get(case, PNG)
    service = TestChatService(db, settings=make_settings(LLM_API_KEY="", MEDIA_ROOT=str(tmp_path)))
    try:
        with pytest.raises(ValidationError):
            service.send_image("bad-upload", data, content_type)
    finally:
        service.close()

    assert service.conversation_for("bad-upload") is None
    assert list(tmp_path.glob("*")) == []
