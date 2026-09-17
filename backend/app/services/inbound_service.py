"""Incoming Instagram messages (SPEC §37, docs/architecture/06-integrations.md §1, §3, §5).

``handle_event`` runs in the Celery task ``process_instagram_event`` and follows SPEC §37:

1. identify the customer, create them on the first message (profile name/username from the Graph API);
2. find or create the conversation ``"{account_id}:{igsid}"``;
3. store the message (``mid`` is unique → a re-delivered webhook is a no-op) and move the 24-hour
   window (``last_customer_message_at``);
4. by type: **voice** — the audio is downloaded at once (CDN links expire) and transcription is
   queued (``transcribe_voice_message``, which then continues here); **image** — stored and flagged
   for the operator; **text** (with ``[вложение: …]`` labels for other attachments) — straight on;
5. ``DialogService`` decides the answer; the reply is stored as a PENDING outgoing message and its
   delivery queued (``send_instagram_message``); with voice replies switched on, a voice message is
   synthesized as well (``synthesize_voice_reply``).

If the dialog crashes on an unexpected error, the customer is not left without an answer: the
conversation is handed over to an operator with the standard handoff message.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai import templates
from app.ai.llm_client import LLMClient
from app.ai.responder import Reply, ReplyKind
from app.core.config import Settings, get_settings
from app.core.exceptions import IntegrationError, IntegrationNotConfiguredError
from app.core.logging import get_logger, log_event
from app.core.time import ensure_utc, now_utc
from app.integrations.instagram.client import InstagramClient, InstagramMessenger
from app.integrations.instagram.schemas import InstagramEvent
from app.integrations.maps.types import Geocoder
from app.integrations.speech.base import SpeechToText, TextToSpeech
from app.integrations.speech.factory import get_stt, get_tts
from app.integrations.speech.policy import transcribe_with_retry
from app.models.conversation import Conversation, Message
from app.models.customer import Customer
from app.models.enums import ConversationMode, MessageDeliveryStatus, MessageDirection, MessageSender, MessageType
from app.repositories.conversations import MessageRepository
from app.repositories.customers import CustomerRepository
from app.repositories.orders import OrderRepository
from app.services.conversation_service import ConversationService
from app.services.customer_service import CustomerService
from app.services.dialog_service import DialogOutcome, DialogService
from app.services.dialog_state import DialogState
from app.services.instagram_token_service import InstagramTokenService
from app.services.media_storage import INCOMING_PREFIX, MediaStorage, extension_for, media_type_for
from app.services.messaging_service import MessagingService
from app.services.settings_service import SettingsService
from app.services.task_queue import TaskQueue, get_task_queue

logger = get_logger(__name__)

__all__ = ["InboundMessageService", "InboundResult"]

STATUS_PROCESSED = "processed"
STATUS_DUPLICATE = "duplicate"
STATUS_IGNORED = "ignored"
STATUS_VOICE_QUEUED = "voice_queued"

VOICE_NO_URL = "Голосовое сообщение пришло без ссылки на аудио"
VOICE_DOWNLOAD_FAILED = "Не удалось скачать голосовое сообщение"
VOICE_QUEUE_FAILED = "Не удалось поставить распознавание голосового сообщения в очередь"
VOICE_FILE_MISSING = "Файл голосового сообщения не найден"
VOICE_STT_NOT_CONFIGURED = "Распознавание голосовых сообщений не настроено (STT_API_KEY)"
VOICE_STT_FAILED = "Не удалось распознать голосовое сообщение"
VOICE_EMPTY = "Голосовое сообщение пустое или неразборчивое"
IMAGE_DOWNLOAD_FAILED = "Не удалось сохранить изображение"
PROCESSING_FAILED_REASON = "Ошибка обработки сообщения ботом"


@dataclass(slots=True)
class InboundResult:
    status: str
    message_id: int | None = None
    reply_message_ids: list[int] = field(default_factory=list)


def _latest(current: datetime | None, candidate: datetime) -> datetime:
    return candidate if current is None or ensure_utc(current) < candidate else ensure_utc(current)


class InboundMessageService:
    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | None = None,
        messenger: InstagramMessenger | None = None,
        llm: LLMClient | None = None,
        geocoder: Geocoder | None = None,
        stt: SpeechToText | None = None,
        tts: TextToSpeech | None = None,
        queue: TaskQueue | None = None,
        media: MediaStorage | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.messages = MessageRepository(db)
        self.customers = CustomerRepository(db)
        self.orders = OrderRepository(db)
        self.media = media or MediaStorage(self.settings.media_root)
        self.queue = queue or get_task_queue()
        self.conversations = ConversationService(db, queue=self.queue, settings=self.settings)
        self.messaging = MessagingService(db, settings=self.settings, media=self.media)
        self.dialog = DialogService(db, llm=llm, geocoder=geocoder, settings=self.settings, media=self.media)
        self._messenger = messenger
        self._owned_messenger: InstagramClient | None = None
        self._stt = stt
        self._tts = tts

    def close(self) -> None:
        if self._owned_messenger is not None:
            self._owned_messenger.close()
            self._owned_messenger = None

    # ================================================================== webhook event

    def handle_event(self, event: InstagramEvent | Mapping[str, Any]) -> InboundResult:
        if not isinstance(event, InstagramEvent):
            event = InstagramEvent.model_validate(event)
        if not event.is_customer_message or event.is_deleted:
            reason = "deleted" if event.is_deleted else "echo_or_self"
            log_event(logger, "instagram.event_ignored", mid=event.mid, reason=reason)
            return InboundResult(STATUS_IGNORED)
        if self.messages.get_by_instagram_message_id(event.mid) is not None:
            log_event(logger, "instagram.event_duplicate", mid=event.mid)
            return InboundResult(STATUS_DUPLICATE)

        customer = self._customer(event)
        conversation = self.conversations.get_or_create_instagram(customer, event.conversation_key)
        received_at = min(event.timestamp, now_utc())
        message = Message(
            conversation_id=conversation.id,
            direction=MessageDirection.INCOMING,
            message_type=event.message_type,
            sender=MessageSender.CUSTOMER,
            text=event.display_text if event.message_type != MessageType.VOICE else (event.text or None),
            instagram_message_id=event.mid,
            delivery_status=MessageDeliveryStatus.NOT_APPLICABLE,
            created_at=received_at,
        )
        conversation.last_message_at = _latest(conversation.last_message_at, received_at)
        conversation.last_customer_message_at = _latest(conversation.last_customer_message_at, received_at)
        self.db.add(message)
        try:
            self.db.commit()
        except IntegrityError:  # the same mid is being processed by a concurrent delivery
            self.db.rollback()
            log_event(logger, "instagram.event_duplicate", mid=event.mid, concurrent=True)
            return InboundResult(STATUS_DUPLICATE)
        log_event(
            logger,
            "instagram.message_received",
            conversation_id=conversation.id,
            message_id=message.id,
            customer_id=customer.id,
            message_type=message.message_type.value,
            raw_type=event.raw_type,
        )

        if message.message_type == MessageType.VOICE:
            return self._receive_voice(message, event)
        if message.message_type == MessageType.IMAGE:
            self._store_image(message, event)
        return self.process_message(message)

    def _customer(self, event: InstagramEvent) -> Customer:
        igsid = event.customer_id
        profile: dict[str, str | None] = {}
        if self.customers.get_by_instagram_id(igsid) is None:
            profile = self._profile(igsid) or {}
        return CustomerService(self.db).get_or_create_by_instagram_id(
            igsid, name=profile.get("name"), username=profile.get("username")
        )

    def _profile(self, igsid: str) -> dict[str, str | None] | None:
        """03 §2: the Graph API profile when available; any failure → a customer without a name."""
        try:
            return self._resolve_messenger().get_user_profile(igsid)
        except IntegrationError as exc:
            log_event(logger, "instagram.profile_failed", level=logging.WARNING, error=type(exc).__name__)
            return None

    # ================================================================== media

    def _receive_voice(self, message: Message, event: InstagramEvent) -> InboundResult:
        attachment = event.voice_attachment
        if attachment is None or attachment.url is None:
            return self._voice_failed(message, VOICE_NO_URL)
        try:
            data, content_type = self._resolve_messenger().download_attachment(attachment.url)
            filename = self.media.save(data, prefix=INCOMING_PREFIX, extension=extension_for(content_type, "m4a"))
        except (IntegrationError, OSError) as exc:
            log_event(logger, "speech.voice_download_failed", level=logging.WARNING, error=type(exc).__name__)
            return self._voice_failed(message, VOICE_DOWNLOAD_FAILED)
        message.audio_url = self.media.url_path(filename)
        self.db.commit()
        if not self.queue.transcribe_voice(message.id):
            return self._voice_failed(message, VOICE_QUEUE_FAILED)
        return InboundResult(STATUS_VOICE_QUEUED, message.id)

    def _store_image(self, message: Message, event: InstagramEvent) -> None:
        """Images are kept for the operator (03 §6: a design request is the operator's decision)."""
        attachment = next(iter(event.image_attachments), None)
        if attachment is not None and attachment.url is not None:
            try:
                data, content_type = self._resolve_messenger().download_attachment(attachment.url)
                filename = self.media.save(data, prefix=INCOMING_PREFIX, extension=extension_for(content_type, "jpg"))
                message.media_url = self.media.url_path(filename)
            except (IntegrationError, OSError) as exc:
                log_event(logger, "instagram.image_download_failed", level=logging.WARNING, error=type(exc).__name__)
                message.media_url = attachment.url  # the CDN link works for a while
                message.error = IMAGE_DOWNLOAD_FAILED
        message.conversation.needs_attention = True
        self.db.commit()

    def transcribe(self, message_id: int, *, final_attempt: bool = True) -> InboundResult:
        """Task ``transcribe_voice_message``: STT, then the dialog as for a text message (SPEC §28)."""
        message = self.messages.get(message_id)
        if message is None or message.message_type != MessageType.VOICE:
            return InboundResult(STATUS_IGNORED, message_id)
        if (message.text or "").strip() or message.ai_processed:
            return InboundResult(STATUS_DUPLICATE, message_id)
        filename = MediaStorage.filename_from_url(message.audio_url)
        data = self.media.read(filename) if filename else None
        if data is None or filename is None:
            return self._voice_failed(message, VOICE_FILE_MISSING)
        try:
            stt = self._stt or get_stt(self.settings)
        except IntegrationNotConfiguredError:
            return self._voice_failed(message, VOICE_STT_NOT_CONFIGURED)
        customer_language = message.conversation.customer.language.value
        try:
            result = transcribe_with_retry(stt, data, media_type_for(filename), customer_language)
        except IntegrationError as exc:
            if getattr(exc, "retryable", False) and not final_attempt:
                raise
            log_event(logger, "speech.transcription_failed", level=logging.WARNING, error=type(exc).__name__)
            return self._voice_failed(message, VOICE_STT_FAILED)
        text = (result.text or "").strip()
        if not text:
            return self._voice_failed(message, VOICE_EMPTY)
        message.text = text
        self.db.commit()
        log_event(
            logger,
            "speech.transcribed",
            message_id=message.id,
            provider=result.provider,
            language=result.language,
            chars=len(text),
        )
        return self.process_message(message)

    def _voice_failed(self, message: Message, error: str) -> InboundResult:
        """06 §3: text=null, the operator is alerted, the customer is asked to write instead."""
        conversation = message.conversation
        message.error = error
        conversation.needs_attention = True
        self.db.commit()
        log_event(logger, "speech.voice_failed", level=logging.WARNING, message_id=message.id, error=error)
        if conversation.mode != ConversationMode.AI or not SettingsService(self.db).get().ai_enabled:
            return InboundResult(STATUS_PROCESSED, message.id)
        language = DialogState.from_json(conversation.state).language or conversation.customer.language.value
        text = templates.voice_not_recognized(language)
        reply = self.messaging.create_outgoing(conversation, text, sender=MessageSender.AI)
        self.queue.send_message(reply.id)
        return InboundResult(STATUS_PROCESSED, message.id, [reply.id])

    # ================================================================== dialog

    def process_message(self, message: Message) -> InboundResult:
        conversation = message.conversation
        try:
            outcome = self.dialog.handle_incoming(conversation, message)
        except Exception as exc:  # the customer must never be left without an answer
            self.db.rollback()
            log_event(
                logger,
                "dialog.failed",
                level=logging.ERROR,
                message_id=message.id,
                conversation_id=message.conversation_id,
                error=type(exc).__name__,
                exc_info=exc,
            )
            return self._emergency_handoff(message.id, exc)
        return InboundResult(STATUS_PROCESSED, message.id, self._send_outcome(conversation, outcome, message))

    def continue_after_location(self, order_id: int) -> InboundResult:
        """Task ``continue_dialog_after_location``: the customer placed the map pin (03 §7)."""
        order = self.orders.get(order_id)
        if order is None or order.conversation is None:
            return InboundResult(STATUS_IGNORED)
        conversation = order.conversation
        try:
            outcome = self.dialog.continue_after_location(order)
        except Exception as exc:
            self.db.rollback()
            log_event(
                logger, "dialog.failed", level=logging.ERROR, order_id=order_id, error=type(exc).__name__, exc_info=exc
            )
            conversation = self.db.get(Conversation, conversation.id)
            if conversation is not None:
                conversation.needs_attention = True
                self.db.commit()
            return InboundResult(STATUS_IGNORED)
        return InboundResult(STATUS_PROCESSED, None, self._send_outcome(conversation, outcome, None))

    def synthesize_voice_reply(self, message_id: int) -> Message | None:
        """Task ``synthesize_voice_reply`` (06 §3): only while voice replies are switched on."""
        source = self.messages.get(message_id)
        if source is None or not SettingsService(self.db).get().voice_replies_enabled:
            return None
        try:
            tts = self._tts or get_tts(self.settings)
        except IntegrationNotConfiguredError:
            log_event(logger, "speech.tts_not_configured", level=logging.WARNING, message_id=message_id)
            return None
        conversation = source.conversation
        language = DialogState.from_json(conversation.state).language or conversation.customer.language.value
        try:
            voice = self.messaging.create_voice_reply(message_id, tts, language)
        except IntegrationError as exc:
            log_event(
                logger, "speech.tts_failed", level=logging.WARNING, message_id=message_id, error=type(exc).__name__
            )
            return None
        if voice is not None:
            self.queue.send_message(voice.id)
        return voice

    def _send_outcome(self, conversation: Conversation, outcome: DialogOutcome, incoming: Message | None) -> list[int]:
        if outcome.reply is None:
            return []
        reply = self._store_reply(conversation, outcome.reply)
        self.queue.send_message(reply.id)
        if (
            incoming is not None
            and incoming.message_type == MessageType.VOICE
            and SettingsService(self.db).get().voice_replies_enabled
        ):
            self.queue.synthesize_voice_reply(reply.id)
        return [reply.id]

    def _store_reply(self, conversation: Conversation, reply: Reply) -> Message:
        return self.messaging.create_outgoing(
            conversation,
            reply.text,
            sender=MessageSender.AI,
            ai_payload={
                "reply": {
                    "kind": reply.kind.value,
                    "source": reply.source.value,
                    "language": reply.language,
                    "violations": list(reply.violations),
                }
            },
        )

    def _emergency_handoff(self, message_id: int, exc: Exception) -> InboundResult:
        message = self.messages.get(message_id)
        if message is None:
            raise exc
        conversation = message.conversation
        message.error = f"{PROCESSING_FAILED_REASON}: {type(exc).__name__}"
        if conversation.mode != ConversationMode.AI:
            conversation.needs_attention = True
            self.db.commit()
            return InboundResult(STATUS_PROCESSED, message.id)
        self.conversations.handoff(conversation, PROCESSING_FAILED_REASON, needs_attention=True, commit=True)
        language = DialogState.from_json(conversation.state).language or conversation.customer.language.value
        text = templates.render(ReplyKind.HANDOFF, language, {"reason_code": "ai_unavailable"})
        reply = self.messaging.create_outgoing(conversation, text, sender=MessageSender.AI)
        self.queue.send_message(reply.id)
        return InboundResult(STATUS_PROCESSED, message.id, [reply.id])

    # ================================================================== dependencies

    def _resolve_messenger(self) -> InstagramMessenger:
        if self._messenger is not None:
            return self._messenger
        if self._owned_messenger is None:
            self._owned_messenger = InstagramTokenService(self.db, self.settings).messenger()
        return self._owned_messenger
