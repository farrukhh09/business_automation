"""Speech tasks (docs/architecture/06-integrations.md §3, §5).

| task | when |
|---|---|
| ``transcribe_voice_message(message_id)`` | a voice message was stored: STT, then the dialog |
| ``synthesize_voice_reply(message_id)`` | voice replies are on and the customer sent a voice message |
"""

from typing import Any

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.database import session_scope
from app.core.locks import ConversationBusyError, conversation_lock
from app.integrations.speech.base import SpeechProviderError
from app.models.conversation import Message
from app.repositories.conversations import MessageRepository
from app.services.inbound_service import InboundMessageService, InboundResult
from app.tasks.celery_app import celery_app
from app.tasks.instagram import backoff_seconds

TRANSCRIBE_MAX_RETRIES = 3


def run_transcribe_voice_message(
    db: Session, message_id: int, *, final_attempt: bool = True, **deps: Any
) -> InboundResult:
    service = InboundMessageService(db, **deps)
    try:
        return service.transcribe(message_id, final_attempt=final_attempt)
    finally:
        service.close()


def run_synthesize_voice_reply(db: Session, message_id: int, **deps: Any) -> Message | None:
    service = InboundMessageService(db, **deps)
    try:
        return service.synthesize_voice_reply(message_id)
    finally:
        service.close()


def _conversation_key(message_id: int) -> str | None:
    with session_scope() as db:
        message = MessageRepository(db).get(message_id)
        if message is None:
            return None
        conversation = message.conversation
        return conversation.instagram_conversation_id or f"conversation:{conversation.id}"


@celery_app.task(name="app.tasks.speech.transcribe_voice_message", bind=True, max_retries=TRANSCRIBE_MAX_RETRIES)
def transcribe_voice_message(self: Any, message_id: int) -> dict[str, Any] | None:
    key = _conversation_key(message_id)
    if key is None:
        return None
    final_attempt = self.request.retries >= self.max_retries
    try:
        with conversation_lock(key), session_scope() as db:
            result = run_transcribe_voice_message(db, message_id, final_attempt=final_attempt)
    except (SpeechProviderError, OperationalError, ConversationBusyError) as exc:
        raise self.retry(exc=exc, countdown=backoff_seconds(self.request.retries)) from exc
    return {"status": result.status, "message_id": result.message_id, "replies": result.reply_message_ids}


@celery_app.task(name="app.tasks.speech.synthesize_voice_reply")
def synthesize_voice_reply(message_id: int) -> int | None:
    with session_scope() as db:
        voice = run_synthesize_voice_reply(db, message_id)
        return voice.id if voice is not None else None
