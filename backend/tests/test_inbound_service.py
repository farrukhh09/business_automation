"""Incoming messages and outgoing delivery (SPEC §37, 06-integrations.md §1, §3, §5)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import IntegrationNotConfiguredError
from app.core.time import now_utc
from app.integrations.instagram.client import InstagramAPIError
from app.integrations.speech.base import SpeechProviderError
from app.models import Customer, Message
from app.models.conversation import Conversation
from app.models.enums import (
    ConversationMode,
    MessageDeliveryStatus,
    MessageDirection,
    MessageSender,
    MessageType,
    OrderStatus,
    PaymentStatus,
)
from app.services.inbound_service import InboundMessageService
from app.services.media_storage import MediaStorage
from app.services.messaging_service import MessagingService, RetryableDeliveryError
from app.services.settings_service import SettingsService
from app.tasks.media import run_cleanup_media
from tests.bot_fakes import (
    FakeMessenger,
    FakeSTT,
    FakeTTS,
    InlineQueue,
    RecordingQueue,
    ScriptedLLM,
    receipt_reading,
    throttling_error,
    transcript,
    understanding,
)

ACCOUNT = "17841400000000000"


def event(
    mid: str = "mid.1", text: str | None = "Здравствуйте", sender: str = "5550001", **fields: Any
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "account_id": ACCOUNT,
        "sender_id": sender,
        "recipient_id": ACCOUNT,
        "timestamp": now_utc().isoformat(),
        "mid": mid,
        "text": text,
        "attachments": [],
    }
    data.update(fields)
    return data


@pytest.fixture
def media(tmp_path: Path) -> MediaStorage:
    return MediaStorage(tmp_path / "media")


@pytest.fixture
def messenger() -> FakeMessenger:
    return FakeMessenger(profile={"name": "Алия Каримова", "username": "aliya.k"})


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM(default=understanding(intent="GREETING"))


@pytest.fixture
def queue(db: Session, messenger: FakeMessenger, llm: ScriptedLLM, media: MediaStorage) -> InlineQueue:
    return InlineQueue(db, messenger=messenger, llm=llm, media=media, stt=FakeSTT(), tts=FakeTTS())


def outgoing(db: Session) -> list[Message]:
    return list(
        db.scalars(select(Message).where(Message.direction == MessageDirection.OUTGOING).order_by(Message.id)).all()
    )


# --------------------------------------------------------------------------- text messages


def test_first_message_creates_customer_conversation_and_sends_the_reply(
    db: Session, queue: InlineQueue, messenger: FakeMessenger
) -> None:
    result = queue._inbound().handle_event(event())

    customer = db.scalars(select(Customer)).one()
    assert (customer.instagram_user_id, customer.name, customer.username) == ("5550001", "Алия Каримова", "aliya.k")
    conversation = db.scalars(select(Conversation)).one()
    assert conversation.instagram_conversation_id == f"{ACCOUNT}:5550001"
    assert conversation.last_customer_message_at is not None

    incoming = db.get(Message, result.message_id)
    assert incoming.direction == MessageDirection.INCOMING and incoming.ai_processed and incoming.intent == "GREETING"

    [reply] = outgoing(db)
    assert result.reply_message_ids == [reply.id]
    assert reply.sender == MessageSender.AI and reply.delivery_status == MessageDeliveryStatus.SENT
    assert reply.ai_payload["reply"]["kind"] == "GREETING"
    assert messenger.sent_texts == [("5550001", reply.text)]
    assert reply.instagram_message_id == "mid.out.1"


def test_redelivered_webhook_is_processed_once(db: Session, queue: InlineQueue, messenger: FakeMessenger) -> None:
    service = queue._inbound()
    service.handle_event(event())
    second = service.handle_event(event())

    assert second.status == "duplicate"
    assert db.scalars(select(Message).where(Message.direction == MessageDirection.INCOMING)).all().__len__() == 1
    assert len(messenger.sent_texts) == 1


def test_echo_and_self_messages_are_ignored(db: Session, queue: InlineQueue) -> None:
    service = queue._inbound()
    assert service.handle_event(event(is_echo=True, sender=ACCOUNT, recipient_id="5550001")).status == "ignored"
    assert service.handle_event(event(mid="mid.2", is_deleted=True)).status == "ignored"
    assert db.scalars(select(Message)).all() == []


def test_known_customer_is_reused_and_profile_not_fetched_again(
    db: Session, queue: InlineQueue, messenger: FakeMessenger
) -> None:
    service = queue._inbound()
    service.handle_event(event(mid="mid.1"))
    messenger.profile = {"name": "Другое имя", "username": "other"}
    service.handle_event(event(mid="mid.2", text="Что есть?"))

    assert db.scalars(select(Customer)).one().name == "Алия Каримова"
    assert len(db.scalars(select(Conversation)).all()) == 1


def test_attachment_labels_become_text(db: Session, queue: InlineQueue, llm: ScriptedLLM) -> None:
    queue._inbound().handle_event(event(text=None, attachments=[{"type": "ig_reel", "url": "https://cdn/x"}]))
    assert llm.json_calls[0]["text"] == "[вложение: ig_reel]"


def test_handoff_mode_stores_the_message_without_answering(
    db: Session, queue: InlineQueue, messenger: FakeMessenger
) -> None:
    service = queue._inbound()
    service.handle_event(event(mid="mid.1"))
    conversation = db.scalars(select(Conversation)).one()
    conversation.mode, conversation.needs_attention = ConversationMode.HUMAN_HANDOFF, False
    db.commit()

    result = service.handle_event(event(mid="mid.2", text="Алло?"))

    assert result.reply_message_ids == []
    db.refresh(conversation)
    assert conversation.needs_attention and len(messenger.sent_texts) == 1


def test_unexpected_dialog_error_hands_over_with_a_message(
    db: Session, queue: InlineQueue, messenger: FakeMessenger, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: Any, conversation: Any, message: Any) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr("app.services.dialog_service.DialogService.handle_incoming", boom)

    result = queue._inbound().handle_event(event())

    conversation = db.scalars(select(Conversation)).one()
    assert conversation.mode == ConversationMode.HUMAN_HANDOFF and conversation.needs_attention
    assert db.get(Message, result.message_id).error == "Ошибка обработки сообщения ботом: RuntimeError"
    assert messenger.texts == ["Передаю диалог менеджеру, он скоро ответит."]


# --------------------------------------------------------------------------- images


def test_image_is_stored_and_handed_to_the_operator(
    db: Session, queue: InlineQueue, messenger: FakeMessenger, media: MediaStorage
) -> None:
    messenger.media, messenger.media_type = b"\xff\xd8jpeg", "image/jpeg"

    result = queue._inbound().handle_event(
        event(text=None, attachments=[{"type": "image", "url": "https://cdn.example/photo.jpg"}])
    )

    message = db.get(Message, result.message_id)
    assert message.message_type == MessageType.IMAGE
    filename = MediaStorage.filename_from_url(message.media_url)
    assert filename and filename.endswith(".jpg") and media.read(filename) == b"\xff\xd8jpeg"
    conversation = message.conversation
    assert conversation.mode == ConversationMode.HUMAN_HANDOFF and conversation.needs_attention
    assert messenger.texts == ["Передаю диалог менеджеру, он скоро ответит."]


def test_receipt_image_is_read_when_a_prepayment_is_awaited(
    db: Session, queue: InlineQueue, messenger: FakeMessenger, llm: ScriptedLLM, make_order: Any
) -> None:
    """End to end (03 §3): the stored file of the image is what the model reads; the reply is the receipt result."""
    queue._inbound().handle_event(event(mid="mid.0"))  # creates the customer
    customer = db.scalars(select(Customer)).one()
    order = make_order(customer=customer, status=OrderStatus.CONFIRMED)  # 100 сомони, unpaid
    SettingsService(db).update({"prepayment_enabled": True, "prepayment_wallet": "+992 92 757 53 33"})
    messenger.media, messenger.media_type = b"\x89PNGreceipt", "image/png"
    llm.receipt = receipt_reading(amount=100)

    result = queue._inbound().handle_event(
        event(mid="mid.r", text=None, attachments=[{"type": "image", "url": "https://cdn.example/receipt.png"}])
    )

    message = db.get(Message, result.message_id)
    assert message.ai_payload["receipt"]["ok"] is True
    [call] = llm.receipt_calls
    assert call[0]["content"][0]["source"]["media_type"] == "image/png"
    conversation = message.conversation
    assert conversation.mode == ConversationMode.AI and conversation.needs_attention
    assert messenger.texts[-1].startswith("Чек получили, спасибо! Перевод 100 сомони.")
    db.refresh(order)
    assert order.payment_status == PaymentStatus.UNPAID


# --------------------------------------------------------------------------- voice


def voice_event(mid: str = "mid.v1") -> dict[str, Any]:
    return event(mid=mid, text=None, attachments=[{"type": "audio", "url": "https://cdn.example/voice.mp4"}])


def test_voice_message_is_downloaded_transcribed_and_answered(
    db: Session, messenger: FakeMessenger, media: MediaStorage
) -> None:
    llm = ScriptedLLM({"Здравствуйте, что у вас есть?": understanding(intent="GREETING")})
    stt = FakeSTT(transcript("Здравствуйте, что у вас есть?"))
    queue = InlineQueue(db, messenger=messenger, llm=llm, media=media, stt=stt)

    result = queue._inbound().handle_event(voice_event())

    assert result.status == "voice_queued" and queue.of("transcribe_voice") == [result.message_id]
    message = db.get(Message, result.message_id)
    assert message.message_type == MessageType.VOICE and message.text == "Здравствуйте, что у вас есть?"
    assert media.read(MediaStorage.filename_from_url(message.audio_url)) == b"media"
    assert stt.calls[0][1] == "audio/mp4"
    assert len(messenger.sent_texts) == 1 and message.ai_processed


def test_voice_without_stt_asks_to_write_text(
    db: Session, messenger: FakeMessenger, media: MediaStorage, monkeypatch
) -> None:
    def not_configured(settings: Any = None) -> Any:
        raise IntegrationNotConfiguredError("no key")

    monkeypatch.setattr("app.services.inbound_service.get_stt", not_configured)
    queue = InlineQueue(db, messenger=messenger, llm=ScriptedLLM(), media=media)

    result = queue._inbound().handle_event(voice_event())

    message = db.get(Message, result.message_id)
    assert message.text is None and message.error == "Распознавание голосовых сообщений не настроено (STT_API_KEY)"
    assert message.conversation.needs_attention
    assert messenger.texts == ["Не получилось разобрать голосовое, напишите, пожалуйста, текстом."]


def test_voice_stt_failure_and_empty_transcript(db: Session, messenger: FakeMessenger, media: MediaStorage) -> None:
    failing = SpeechProviderError(provider="fake", status=400)
    queue = InlineQueue(db, messenger=messenger, llm=ScriptedLLM(), media=media, stt=FakeSTT(failing, transcript("  ")))
    service = queue._inbound()

    first = service.handle_event(voice_event("mid.v1"))
    second = service.handle_event(voice_event("mid.v2"))

    assert db.get(Message, first.message_id).error == "Не удалось распознать голосовое сообщение"
    assert db.get(Message, second.message_id).error == "Голосовое сообщение пустое или неразборчивое"
    assert len(messenger.sent_texts) == 2


def test_retryable_stt_error_is_raised_for_the_task_retry(
    db: Session, messenger: FakeMessenger, media: MediaStorage
) -> None:
    retryable = SpeechProviderError(provider="fake", status=503)
    recording = RecordingQueue()
    service = InboundMessageService(
        db, messenger=messenger, llm=ScriptedLLM(), media=media, stt=FakeSTT(retryable), queue=recording
    )
    result = service.handle_event(voice_event())

    with pytest.raises(SpeechProviderError):
        service.transcribe(result.message_id, final_attempt=False)


def test_voice_download_failure(db: Session, messenger: FakeMessenger, media: MediaStorage) -> None:
    messenger.download_error = InstagramAPIError(status=404, message="gone")
    queue = InlineQueue(db, messenger=messenger, llm=ScriptedLLM(), media=media)

    result = queue._inbound().handle_event(voice_event())

    assert db.get(Message, result.message_id).error == "Не удалось скачать голосовое сообщение"
    assert queue.of("transcribe_voice") == []


def test_voice_reply_is_synthesized_for_russian_when_enabled(
    db: Session, messenger: FakeMessenger, media: MediaStorage
) -> None:
    SettingsService(db).update({"voice_replies_enabled": True})
    tts = FakeTTS()
    llm = ScriptedLLM({"Привет": understanding(intent="GREETING")})
    queue = InlineQueue(db, messenger=messenger, llm=llm, media=media, stt=FakeSTT(transcript("Привет")), tts=tts)

    queue._inbound().handle_event(voice_event())

    replies = outgoing(db)
    assert [reply.message_type for reply in replies] == [MessageType.TEXT, MessageType.VOICE]
    voice = replies[1]
    assert voice.delivery_status == MessageDeliveryStatus.SENT and voice.text == replies[0].text
    assert messenger.sent_audio[0][1].startswith("http://localhost:8000/api/media/tts-")
    assert tts.calls == [(replies[0].text, "ru")]


def test_no_voice_reply_for_tajik(db: Session, messenger: FakeMessenger, media: MediaStorage) -> None:
    SettingsService(db).update({"voice_replies_enabled": True})
    llm = ScriptedLLM({"Салом, чӣ доред?": understanding(intent="GREETING", language="tg")})
    stt = FakeSTT(transcript("Салом, чӣ доред?", language="tgk"))
    queue = InlineQueue(db, messenger=messenger, llm=llm, media=media, stt=stt, tts=FakeTTS())

    queue._inbound().handle_event(voice_event())

    assert [reply.message_type for reply in outgoing(db)] == [MessageType.TEXT]


# --------------------------------------------------------------------------- delivery of outgoing messages


def _conversation_with_reply(
    db: Session, queue: RecordingQueue, messenger: FakeMessenger, media: MediaStorage
) -> Message:
    service = InboundMessageService(
        db, messenger=messenger, llm=ScriptedLLM(default=understanding(intent="GREETING")), media=media, queue=queue
    )
    service.handle_event(event())
    [reply] = outgoing(db)
    assert reply.delivery_status == MessageDeliveryStatus.PENDING and queue.of("send_message") == [reply.id]
    return reply


def test_delivery_is_refused_outside_the_24_hour_window(
    db: Session, messenger: FakeMessenger, media: MediaStorage
) -> None:
    reply = _conversation_with_reply(db, RecordingQueue(), messenger, media)
    reply.conversation.last_customer_message_at = now_utc() - timedelta(hours=25)
    db.commit()

    MessagingService(db, messenger=messenger, media=media).deliver(reply.id)

    assert reply.delivery_status == MessageDeliveryStatus.FAILED and "24 часов" in reply.error
    assert messenger.sent_texts == []


def test_throttling_is_retried_until_the_final_attempt(
    db: Session, messenger: FakeMessenger, media: MediaStorage
) -> None:
    reply = _conversation_with_reply(db, RecordingQueue(), messenger, media)
    messenger.send_error = throttling_error()
    service = MessagingService(db, messenger=messenger, media=media)

    with pytest.raises(RetryableDeliveryError):
        service.deliver(reply.id, final_attempt=False)
    assert reply.delivery_status == MessageDeliveryStatus.PENDING

    service.deliver(reply.id, final_attempt=True)
    assert reply.delivery_status == MessageDeliveryStatus.FAILED
    assert reply.error.startswith("Ошибка Instagram API (HTTP 400, код 80002)")


def test_partial_delivery_is_not_retried(db: Session, messenger: FakeMessenger, media: MediaStorage) -> None:
    reply = _conversation_with_reply(db, RecordingQueue(), messenger, media)
    error = throttling_error()
    error.partial_message_ids = ["mid.part.1"]
    messenger.send_error = error

    MessagingService(db, messenger=messenger, media=media).deliver(reply.id, final_attempt=False)

    assert reply.delivery_status == MessageDeliveryStatus.FAILED and reply.error.endswith(
        "Часть сообщения уже доставлена"
    )


def test_delivery_is_idempotent_and_not_configured_instagram_fails_cleanly(db: Session, media: MediaStorage) -> None:
    messenger = FakeMessenger()
    reply = _conversation_with_reply(db, RecordingQueue(), messenger, media)

    MessagingService(db, media=media).deliver(reply.id)  # real client without a token
    assert reply.delivery_status == MessageDeliveryStatus.FAILED
    assert reply.error == "Instagram не настроен: сообщение не отправлено"

    reply.delivery_status = MessageDeliveryStatus.SENT
    db.commit()
    MessagingService(db, messenger=messenger, media=media).deliver(reply.id)
    assert messenger.sent_texts == []


# --------------------------------------------------------------------------- test mode (06 §1a)


@pytest.fixture
def test_mode(db: Session) -> None:
    SettingsService(db).update({"bot_shadow_mode": True})


def manager_reply(
    mid: str = "mid.m1", text: str = "Здравствуйте! 10 сомони за штуку", *, is_echo: bool = True
) -> dict[str, Any]:
    """What the manager writes in the Instagram app: the business account → the customer."""
    return event(mid=mid, text=text, sender=ACCOUNT, recipient_id="5550001", is_echo=is_echo)


@pytest.mark.usefixtures("test_mode")
def test_in_test_mode_the_bot_answers_only_in_the_panel(
    db: Session, queue: InlineQueue, messenger: FakeMessenger
) -> None:
    queue._inbound().handle_event(event())

    [reply] = outgoing(db)
    assert reply.sender == MessageSender.AI and reply.text
    assert reply.delivery_status == MessageDeliveryStatus.NOT_APPLICABLE
    assert reply.error.startswith("Тестовый режим")
    assert messenger.sent_texts == [] and messenger.sent_images == []


@pytest.mark.usefixtures("test_mode")
def test_in_test_mode_the_manager_s_reply_from_instagram_is_recorded_next_to_the_bot_s(
    db: Session, queue: InlineQueue, messenger: FakeMessenger, llm: ScriptedLLM
) -> None:
    service = queue._inbound()
    service.handle_event(event())
    calls = len(llm.json_calls)

    result = service.handle_event(manager_reply())

    assert result.status == "recorded"
    manager = db.get(Message, result.message_id)
    assert (manager.direction, manager.sender, manager.delivery_status) == (
        MessageDirection.OUTGOING,
        MessageSender.OPERATOR,
        MessageDeliveryStatus.SENT,
    )
    assert manager.text == "Здравствуйте! 10 сомони за штуку" and manager.sent_by_user_id is None
    assert manager.ai_payload == {"source": "instagram_app"}
    conversation = db.scalars(select(Conversation)).one()  # the same dialog as the customer's message
    assert conversation.mode == ConversationMode.AI  # the bot keeps preparing answers
    assert len(llm.json_calls) == calls  # nothing to answer: it is not the customer's message
    assert service.handle_event(manager_reply()).status == "duplicate"
    # Without the echo flag (sender == entry.id) it is the manager's message all the same
    assert service.handle_event(manager_reply(mid="mid.m2", is_echo=False)).status == "recorded"
    assert messenger.sent_texts == []

    service.handle_event(event(mid="mid.3", text="Хорошо, давайте 4"))

    # the customer answers the manager, so the model reads what the manager said
    prompt = json.dumps(llm.json_calls[-1]["messages"], ensure_ascii=False)
    assert "[operator] Здравствуйте! 10 сомони за штуку" in prompt


def test_without_test_mode_the_manager_s_reply_is_not_recorded(db: Session, queue: InlineQueue) -> None:
    assert queue._inbound().handle_event(manager_reply()).status == "ignored"
    assert db.scalars(select(Message)).all() == []


@pytest.mark.usefixtures("test_mode")
def test_in_test_mode_a_staff_member_s_message_from_the_panel_is_sent(
    db: Session, messenger: FakeMessenger, media: MediaStorage
) -> None:
    reply = _conversation_with_reply(db, RecordingQueue(), messenger, media)
    service = MessagingService(db, messenger=messenger, media=media)
    operator = service.create_outgoing(reply.conversation, "Добрый день!", sender=MessageSender.OPERATOR)

    service.deliver(reply.id)
    service.deliver(operator.id)

    assert reply.delivery_status == MessageDeliveryStatus.NOT_APPLICABLE
    assert operator.delivery_status == MessageDeliveryStatus.SENT
    assert messenger.sent_texts == [("5550001", "Добрый день!")]


def test_switching_test_mode_off_does_not_send_the_held_answers(
    db: Session, messenger: FakeMessenger, media: MediaStorage
) -> None:
    SettingsService(db).update({"bot_shadow_mode": True})
    reply = _conversation_with_reply(db, RecordingQueue(), messenger, media)
    service = MessagingService(db, messenger=messenger, media=media)
    service.deliver(reply.id)

    SettingsService(db).update({"bot_shadow_mode": False})
    service.deliver(reply.id)  # a late retry of the task

    assert reply.delivery_status == MessageDeliveryStatus.NOT_APPLICABLE and messenger.sent_texts == []


@pytest.mark.usefixtures("test_mode")
def test_in_test_mode_no_voice_reply_is_synthesized(db: Session, messenger: FakeMessenger, media: MediaStorage) -> None:
    SettingsService(db).update({"voice_replies_enabled": True})
    tts = FakeTTS()
    llm = ScriptedLLM({"Привет": understanding(intent="GREETING")})
    queue = InlineQueue(db, messenger=messenger, llm=llm, media=media, stt=FakeSTT(transcript("Привет")), tts=tts)

    queue._inbound().handle_event(voice_event())

    assert [reply.message_type for reply in outgoing(db)] == [MessageType.TEXT]
    assert tts.calls == []


# --------------------------------------------------------------------------- media housekeeping


def test_cleanup_removes_only_old_tts_files(tmp_path: Path) -> None:
    storage = MediaStorage(tmp_path)
    old_tts = storage.save(b"a", prefix="tts", extension="m4a")
    new_tts = storage.save(b"b", prefix="tts", extension="m4a")
    old_incoming = storage.save(b"c", prefix="in", extension="m4a")
    week_ago = (datetime.now(UTC) - timedelta(days=8)).timestamp()
    for name in (old_tts, old_incoming):
        import os

        os.utime(tmp_path / name, (week_ago, week_ago))

    assert run_cleanup_media(storage) == 1
    assert storage.path_for(old_tts) is None
    assert storage.path_for(new_tts) is not None and storage.path_for(old_incoming) is not None


def test_media_names_are_validated(tmp_path: Path) -> None:
    storage = MediaStorage(tmp_path)
    name = storage.save(b"x", prefix="in", extension="EXE")
    assert name.endswith(".bin")
    for bad in ("../secret.txt", "in-short.m4a", "other-abcdefghijklmnopqrstuvwxyz.m4a", "in-abcdefghijklmnop/x.m4a"):
        assert storage.path_for(bad) is None
    with pytest.raises(ValueError):
        storage.save(b"x", prefix="evil", extension="m4a")
