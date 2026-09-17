"""Outgoing Instagram messages: persistence and delivery (docs/architecture/06-integrations.md §1, §3, §5).

Every reply — the bot's, an operator's, a synthesized voice reply — is first stored as an
``OUTGOING`` ``Message`` with ``delivery_status=PENDING`` and committed; ``deliver`` (the Celery task
``send_instagram_message``) then sends it and records ``SENT`` or ``FAILED`` with a readable error.

Rules:

- the 24-hour window (06 §1): nothing is sent when the customer's last message is older than 24 h;
  the operator API refuses up front with 409 ``messaging_window_closed``;
- throttling / 5xx / network errors are retried by the task (``RetryableDeliveryError``) unless it
  is the final attempt or a part of a split text was already delivered (a retry would duplicate it);
- a voice reply (06 §3) is a separate ``VOICE`` message whose audio Instagram downloads from
  ``{PUBLIC_BASE_URL}/api/media/tts-….m4a``.
"""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, IntegrationError
from app.core.logging import get_logger, log_event
from app.core.time import now_utc
from app.integrations.instagram.client import (
    InstagramAPIError,
    InstagramClient,
    InstagramMessenger,
    InstagramNotConfiguredError,
)
from app.integrations.instagram.window import messaging_window_open
from app.integrations.speech.audio import prepare_instagram_audio
from app.integrations.speech.base import TextToSpeech
from app.models.conversation import Conversation, Message
from app.models.enums import MessageDeliveryStatus, MessageDirection, MessageSender, MessageType
from app.models.user import User
from app.repositories.conversations import MessageRepository
from app.services.instagram_token_service import InstagramTokenService
from app.services.media_storage import TTS_PREFIX, MediaStorage

logger = get_logger(__name__)

__all__ = [
    "WINDOW_CLOSED_DETAIL",
    "MessagingService",
    "MessagingWindowClosedError",
    "RetryableDeliveryError",
]

WINDOW_CLOSED_DETAIL = "Прошло более 24 часов с последнего сообщения клиента — Instagram не разрешает отправить ответ"
NO_INSTAGRAM_ACCOUNT_DETAIL = "У клиента нет Instagram-аккаунта: сообщение некуда отправить"
NOT_CONFIGURED_DETAIL = "Instagram не настроен: сообщение не отправлено"
MAX_ERROR_CHARS = 500


class MessagingWindowClosedError(ConflictError):
    code = "messaging_window_closed"
    default_detail = WINDOW_CLOSED_DETAIL


class RetryableDeliveryError(IntegrationError):
    """A temporary Instagram failure: the Celery task should retry the delivery."""

    code = "instagram_retry"
    default_detail = "Временная ошибка Instagram — отправка будет повторена"


def _api_error_text(exc: InstagramAPIError) -> str:
    parts = ["Ошибка Instagram API"]
    details = [
        f"HTTP {exc.status}" if exc.status else "",
        f"код {exc.error_code}" if exc.error_code is not None else "",
        f"подкод {exc.error_subcode}" if exc.error_subcode is not None else "",
    ]
    details_text = ", ".join(part for part in details if part)
    if details_text:
        parts.append(f"({details_text})")
    text = " ".join(parts)
    if exc.message:
        text = f"{text}: {exc.message}"
    if exc.partial_message_ids:
        text = f"{text}. Часть сообщения уже доставлена"
    return text[:MAX_ERROR_CHARS]


class MessagingService:
    def __init__(
        self,
        db: Session,
        *,
        messenger: InstagramMessenger | None = None,
        settings: Settings | None = None,
        media: MediaStorage | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.messages = MessageRepository(db)
        self.media = media or MediaStorage(self.settings.media_root)
        self._messenger = messenger

    # ------------------------------------------------------------------ outgoing messages

    def ensure_window_open(self, conversation: Conversation) -> None:
        """06 §1: 409 ``messaging_window_closed`` when the customer wrote more than 24 hours ago."""
        if not messaging_window_open(conversation.last_customer_message_at):
            raise MessagingWindowClosedError()

    def create_outgoing(
        self,
        conversation: Conversation,
        text: str | None,
        *,
        sender: MessageSender,
        message_type: MessageType = MessageType.TEXT,
        user: User | None = None,
        audio_url: str | None = None,
        ai_payload: dict[str, Any] | None = None,
    ) -> Message:
        """Store a PENDING outgoing message and commit (the caller enqueues the delivery)."""
        message = Message(
            conversation_id=conversation.id,
            direction=MessageDirection.OUTGOING,
            message_type=message_type,
            sender=sender,
            text=text,
            audio_url=audio_url,
            ai_payload=ai_payload,
            delivery_status=MessageDeliveryStatus.PENDING,
            sent_by_user_id=user.id if user is not None else None,
        )
        self.messages.add(message)
        conversation.last_message_at = now_utc()
        self.db.commit()
        log_event(
            logger,
            "message.outgoing_created",
            message_id=message.id,
            conversation_id=conversation.id,
            sender=sender.value,
            message_type=message_type.value,
            user_id=user.id if user is not None else None,
        )
        return message

    # ------------------------------------------------------------------ delivery

    def deliver(self, message_id: int, *, final_attempt: bool = True) -> Message | None:
        """Send one PENDING outgoing message (idempotent: anything else is left as is). Commits."""
        message = self.messages.get(message_id)
        if message is None:
            log_event(logger, "message.delivery_missing", level=logging.WARNING, message_id=message_id)
            return None
        if message.direction != MessageDirection.OUTGOING or message.delivery_status != MessageDeliveryStatus.PENDING:
            return message

        conversation = message.conversation
        recipient = (conversation.customer.instagram_user_id or "").strip()
        if not recipient:
            return self._fail(message, NO_INSTAGRAM_ACCOUNT_DETAIL)
        if not messaging_window_open(conversation.last_customer_message_at):
            return self._fail(message, WINDOW_CLOSED_DETAIL)

        messenger, owned = self._resolve_messenger()
        try:
            message_ids = self._send(messenger, message, recipient)
        except InstagramNotConfiguredError:
            return self._fail(message, NOT_CONFIGURED_DETAIL)
        except InstagramAPIError as exc:
            if exc.retryable and not exc.partial_message_ids and not final_attempt:
                log_event(
                    logger,
                    "message.delivery_retry",
                    level=logging.WARNING,
                    message_id=message.id,
                    status=exc.status,
                    error_code=exc.error_code,
                )
                raise RetryableDeliveryError() from exc
            return self._fail(message, _api_error_text(exc))
        except ValueError as exc:  # empty text / unusable audio URL
            return self._fail(message, f"Сообщение не отправлено: {exc}"[:MAX_ERROR_CHARS])
        finally:
            if owned and isinstance(messenger, InstagramClient):
                messenger.close()

        message.delivery_status = MessageDeliveryStatus.SENT
        message.error = None
        if message_ids and self.messages.get_by_instagram_message_id(message_ids[0]) is None:
            message.instagram_message_id = message_ids[0]
        self.db.commit()
        log_event(
            logger,
            "message.sent",
            message_id=message.id,
            conversation_id=conversation.id,
            parts=len(message_ids),
            message_type=message.message_type.value,
        )
        return message

    def mark_failed(self, message_id: int, error: str) -> Message | None:
        """Used by the task after the last retry of a temporary failure."""
        message = self.messages.get(message_id)
        if message is None or message.delivery_status != MessageDeliveryStatus.PENDING:
            return message
        return self._fail(message, error)

    # ------------------------------------------------------------------ voice replies (06 §3)

    def create_voice_reply(self, text_message_id: int, tts: TextToSpeech, language: str) -> Message | None:
        """Synthesize the text of an outgoing reply and store it as a separate VOICE message (commits).

        ``None`` when the language is not supported (no Tajik TTS exists) or the audio cannot be made
        sendable; the text reply has been sent anyway.
        """
        source = self.messages.get(text_message_id)
        if source is None or not (source.text or "").strip():
            return None
        if not tts.supports(language):
            log_event(logger, "speech.tts_skipped", message_id=text_message_id, language=language)
            return None
        audio = prepare_instagram_audio(tts.synthesize(source.text or "", language=language))
        if audio is None:
            log_event(logger, "speech.tts_unsendable", level=logging.WARNING, message_id=text_message_id)
            return None
        filename = self.media.save(audio.data, prefix=TTS_PREFIX, extension=audio.extension)
        return self.create_outgoing(
            source.conversation,
            source.text,
            sender=source.sender,
            message_type=MessageType.VOICE,
            audio_url=self.media.url_path(filename),
        )

    # ------------------------------------------------------------------ internals

    def _resolve_messenger(self) -> tuple[InstagramMessenger, bool]:
        if self._messenger is not None:
            return self._messenger, False
        return InstagramTokenService(self.db, self.settings).messenger(), True

    def _send(self, messenger: InstagramMessenger, message: Message, recipient: str) -> list[str]:
        if message.message_type == MessageType.VOICE and message.audio_url:
            filename = self.media.filename_from_url(message.audio_url)
            if filename is None:
                raise ValueError("некорректная ссылка на аудио")
            url = self.media.public_url(filename, self.settings.PUBLIC_BASE_URL)
            return [messenger.send_audio(recipient, url)]
        return messenger.send_text(recipient, message.text or "")

    def _fail(self, message: Message, error: str) -> Message:
        message.delivery_status = MessageDeliveryStatus.FAILED
        message.error = error[:MAX_ERROR_CHARS]
        self.db.commit()
        log_event(
            logger,
            "message.failed",
            level=logging.WARNING,
            message_id=message.id,
            conversation_id=message.conversation_id,
            error=message.error,
        )
        return message
