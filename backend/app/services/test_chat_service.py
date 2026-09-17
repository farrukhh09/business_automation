"""Test chat: talk to the bot from the admin panel without Instagram (local development only).

Mirrors ``scripts/chat_console.py`` as a request/response API instead of a terminal loop: the
frontend keeps a random customer key per browser (``localStorage``), the backend maps it to a
conversation keyed ``"webtest:{key}"`` — a real ``Conversation``/``Customer`` row, visible in the
admin panel's "Диалоги" like any other, so handoff/resume and history work with no extra code.

Every message goes through the exact same path as a real Instagram message
(``InboundMessageService.handle_event``). Nothing is ever sent anywhere: the outgoing reply is
marked FAILED with an explanatory note, exactly like the console script, so the admin panel shows
honestly that it never left the system.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import NotFoundError
from app.core.time import now_utc
from app.models.conversation import Conversation
from app.services.customer_service import CustomerService
from app.services.inbound_service import InboundMessageService
from app.services.messaging_service import MessagingService

__all__ = ["TestChatService", "TestChatDisabledError"]

ACCOUNT_ID = "webtest"
CUSTOMER_NAME = "Тестовый клиент (админка)"
NOT_SENT_NOTE = "Тестовый чат в админке: ответ не отправлялся, показан только в интерфейсе"


class TestChatDisabledError(NotFoundError):
    __test__ = False  # not a pytest test case despite the name


class _SilentQueue:
    """``TaskQueue`` that records outgoing message ids instead of sending them anywhere."""

    def __init__(self) -> None:
        self.outbox: list[int] = []

    def process_instagram_event(self, event: dict[str, Any]) -> bool:
        return False

    def send_message(self, message_id: int) -> bool:
        self.outbox.append(message_id)
        return True

    def transcribe_voice(self, message_id: int) -> bool:
        return False

    def synthesize_voice_reply(self, message_id: int) -> bool:
        return False

    def continue_after_location(self, order_id: int) -> bool:
        return False


class TestChatService:
    __test__ = False  # not a pytest test case despite the name

    def __init__(self, db: Session, *, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()
        if not self.settings.is_development:
            raise TestChatDisabledError()
        self._queue = _SilentQueue()
        self.inbound = InboundMessageService(db, queue=self._queue, settings=self.settings)

    def conversation_key(self, customer_key: str) -> str:
        return f"{ACCOUNT_ID}:{customer_key}"

    def conversation_for(self, customer_key: str) -> Conversation | None:
        return self.db.scalar(
            select(Conversation).where(Conversation.instagram_conversation_id == self.conversation_key(customer_key))
        )

    def send(self, customer_key: str, text: str) -> Conversation:
        """Simulates one incoming customer message; returns the (fresh) conversation."""
        CustomerService(self.db).get_or_create_by_instagram_id(customer_key, name=CUSTOMER_NAME)
        self.db.commit()
        event = {
            "account_id": ACCOUNT_ID,
            "sender_id": customer_key,
            "recipient_id": ACCOUNT_ID,
            "timestamp": now_utc().isoformat(),
            "mid": f"webtest-{uuid.uuid4().hex}",
            "text": text,
        }
        self.inbound.handle_event(event)
        self._mark_not_sent()
        conversation = self.conversation_for(customer_key)
        assert conversation is not None  # handle_event just created it
        return conversation

    def _mark_not_sent(self) -> None:
        messaging = MessagingService(self.db, settings=self.settings, media=self.inbound.media)
        for message_id in self._queue.outbox:
            messaging.mark_failed(message_id, NOT_SENT_NOTE)
        self._queue.outbox.clear()

    def close(self) -> None:
        self.inbound.close()
