"""Instagram tasks (docs/architecture/06-integrations.md §5).

| task | when |
|---|---|
| ``process_instagram_event(event)`` | per customer message from the webhook (retry 3, backoff) |
| ``send_instagram_message(message_id)`` | per outgoing message (retry on throttling / 5xx) |
| ``continue_dialog_after_location(order_id)`` | the customer placed the map pin (03 §7) |
| ``refresh_instagram_token()`` | beat, once a day |

Every task has a testable ``run_*`` function that takes the session; the task body only adds the
session scope, the per-conversation lock and the retry policy.
"""

from typing import Any

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.database import session_scope
from app.core.locks import ConversationBusyError, conversation_lock
from app.core.logging import get_logger
from app.integrations.instagram.schemas import InstagramEvent
from app.repositories.orders import OrderRepository
from app.services.inbound_service import InboundMessageService, InboundResult
from app.services.instagram_token_service import InstagramTokenService
from app.services.messaging_service import MessagingService, RetryableDeliveryError
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)

PROCESS_MAX_RETRIES = 3
SEND_MAX_RETRIES = 5
MAX_BACKOFF_SECONDS = 300


def backoff_seconds(retries: int) -> int:
    """5, 10, 20, 40 … seconds, capped at 5 minutes."""
    return min(5 * 2**retries, MAX_BACKOFF_SECONDS)


def _result(result: InboundResult) -> dict[str, Any]:
    return {"status": result.status, "message_id": result.message_id, "replies": result.reply_message_ids}


# --------------------------------------------------------------------------- run functions


def run_process_instagram_event(db: Session, event: InstagramEvent | dict[str, Any], **deps: Any) -> InboundResult:
    service = InboundMessageService(db, **deps)
    try:
        return service.handle_event(event)
    finally:
        service.close()


def run_send_instagram_message(db: Session, message_id: int, *, final_attempt: bool = True, **deps: Any) -> None:
    MessagingService(db, **deps).deliver(message_id, final_attempt=final_attempt)


def run_continue_dialog_after_location(db: Session, order_id: int, **deps: Any) -> InboundResult:
    service = InboundMessageService(db, **deps)
    try:
        return service.continue_after_location(order_id)
    finally:
        service.close()


def run_refresh_instagram_token(db: Session) -> bool:
    return InstagramTokenService(db).refresh()


def conversation_key_for_order(order_id: int) -> str | None:
    with session_scope() as db:
        order = OrderRepository(db).get(order_id)
        if order is None or order.conversation is None:
            return None
        return order.conversation.instagram_conversation_id or f"conversation:{order.conversation.id}"


# --------------------------------------------------------------------------- tasks


@celery_app.task(name="app.tasks.instagram.process_instagram_event", bind=True, max_retries=PROCESS_MAX_RETRIES)
def process_instagram_event(self: Any, event: dict[str, Any]) -> dict[str, Any]:
    parsed = InstagramEvent.model_validate(event)
    try:
        with conversation_lock(parsed.conversation_key), session_scope() as db:
            return _result(run_process_instagram_event(db, parsed))
    except (OperationalError, ConversationBusyError) as exc:
        raise self.retry(exc=exc, countdown=backoff_seconds(self.request.retries)) from exc


@celery_app.task(name="app.tasks.instagram.send_instagram_message", bind=True, max_retries=SEND_MAX_RETRIES)
def send_instagram_message(self: Any, message_id: int) -> None:
    final_attempt = self.request.retries >= self.max_retries
    try:
        with session_scope() as db:
            run_send_instagram_message(db, message_id, final_attempt=final_attempt)
    except (RetryableDeliveryError, OperationalError) as exc:
        raise self.retry(exc=exc, countdown=backoff_seconds(self.request.retries)) from exc


@celery_app.task(name="app.tasks.instagram.continue_dialog_after_location", bind=True, max_retries=PROCESS_MAX_RETRIES)
def continue_dialog_after_location(self: Any, order_id: int) -> dict[str, Any] | None:
    key = conversation_key_for_order(order_id)
    if key is None:
        return None
    try:
        with conversation_lock(key), session_scope() as db:
            return _result(run_continue_dialog_after_location(db, order_id))
    except (OperationalError, ConversationBusyError) as exc:
        raise self.retry(exc=exc, countdown=backoff_seconds(self.request.retries)) from exc


@celery_app.task(name="app.tasks.instagram.refresh_instagram_token")
def refresh_instagram_token() -> bool:
    with session_scope() as db:
        return run_refresh_instagram_token(db)
