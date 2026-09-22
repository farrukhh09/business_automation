"""Follow-up questions: the bot asks again by itself while it waits (03-business-rules.md §6a).

The Instagram archive of «Синнамоны» shows this as the owner's most constant habit: a customer says
"даа", promises a receipt — and goes quiet; a few hours later the owner writes «Вам коробку оставить
или нет», «Заказ кабул кунам е не», «Мне для оформления заказа чек нужен или вы передумали?», and the
order is saved. The bot never did that: it only ever answered. This service is that habit.

One pass (``run``) is started by Celery beat every 15 minutes. It looks only at dialogs where **we
are waiting for the customer** and asks once per stage:

* ``draft`` — an order is being assembled and the bot's question is unanswered;
* ``confirmation`` — the summary was shown and the "Да" never came;
* ``receipt`` — the order is placed and a prepayment is expected (only while «Предоплата» is on).

What it never does: write to a dialog an operator has taken over or been alerted to
(``needs_attention``), write to a blocked customer, write twice about the same stage of the same
order, write outside the bakery's hours, or write when Instagram would refuse it anyway — the 24-hour
window (06 §1) is the hard limit, so a follow-up is sent inside it or not at all.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from sqlalchemy.orm import Session

from app.ai import templates
from app.ai.responder import ReplyKind
from app.core.config import Settings, get_settings
from app.core.logging import get_logger, log_event
from app.core.time import ensure_utc, now_utc, to_business
from app.integrations.instagram.window import MESSAGING_WINDOW
from app.models.conversation import Conversation
from app.models.enums import MessageDirection, MessageSender, OrderStatus
from app.models.order import Order
from app.repositories.conversations import ConversationRepository, MessageRepository
from app.repositories.orders import OrderRepository
from app.schemas.settings import BusinessSettings
from app.services.constants import DRAFT_ORDER_STATUSES
from app.services.dialog_state import AWAITING_CONFIRMATION, DialogState
from app.services.messaging_service import MessagingService
from app.services.settings_service import SettingsService
from app.services.task_queue import TaskQueue, get_task_queue

logger = get_logger(__name__)

__all__ = ["STAGE_CONFIRMATION", "STAGE_DRAFT", "STAGE_RECEIPT", "FollowUpResult", "FollowUpService"]

STAGE_DRAFT = "draft"
STAGE_CONFIRMATION = "confirmation"
STAGE_RECEIPT = "receipt"

#: How many dialogs one pass looks at. A pass runs every 15 minutes, so this is a safety bound, not a
#: quota: a bakery with more waiting dialogs than this has bigger news than a missed follow-up.
DEFAULT_LIMIT = 200

#: Quiet hours are the bakery's order hours; without them a follow-up is not sent at night either.
DEFAULT_HOURS_START = time(9, 0)
DEFAULT_HOURS_END = time(21, 0)


@dataclass(slots=True)
class FollowUpResult:
    """What one pass did — the numbers go to the log and to the tests."""

    sent: list[int] = field(default_factory=list)
    #: ``(conversation_id, stage)`` of every follow-up the pass decided on — also filled by a dry run.
    planned: list[tuple[int, str]] = field(default_factory=list)
    considered: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


class FollowUpService:
    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | None = None,
        queue: TaskQueue | None = None,
        messaging: MessagingService | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.queue = queue or get_task_queue()
        self.conversations = ConversationRepository(db)
        self.messages = MessageRepository(db)
        self.orders = OrderRepository(db)
        self.messaging = messaging or MessagingService(db, settings=self.settings)

    # ------------------------------------------------------------------ one pass

    def run(
        self, *, now: datetime | None = None, limit: int = DEFAULT_LIMIT, dry_run: bool = False
    ) -> FollowUpResult:
        """Send the follow-ups that are due (each one commits).

        ``dry_run`` decides exactly the same way but writes nothing at all — the plan comes back in
        ``result.planned``. It is not a rollback: nothing is stored in the first place.
        """
        result = FollowUpResult()
        business = SettingsService(self.db).get()
        if not business.ai_enabled or not business.follow_up_enabled:
            return result
        moment = ensure_utc(now) if now is not None else now_utc()
        if not self._inside_working_hours(business, moment):
            result.skip("outside_hours")
            return result

        quiet_before = moment - timedelta(hours=business.follow_up_after_hours)
        candidates = self.conversations.quiet_since(quiet_before, moment - MESSAGING_WINDOW, limit)
        result.considered = len(candidates)
        for conversation in candidates:
            self._follow_up(conversation, business, result, dry_run=dry_run)
        if result.planned or result.skipped:
            log_event(
                logger,
                "follow_up.pass_finished",
                sent=len(result.sent),
                planned=len(result.planned),
                considered=result.considered,
                dry_run=dry_run,
                skipped=dict(sorted(result.skipped.items())),
            )
        return result

    # ------------------------------------------------------------------ one dialog

    def _follow_up(
        self, conversation: Conversation, business: BusinessSettings, result: FollowUpResult, *, dry_run: bool
    ) -> None:
        customer = conversation.customer
        if customer is None or customer.is_blocked:
            result.skip("blocked")
            return
        last = self.messages.recent_for_conversation(conversation.id, limit=1)
        if not last or last[-1].direction != MessageDirection.OUTGOING:
            # The customer wrote last: the bot owes an answer, and an answer is not a follow-up.
            result.skip("customer_wrote_last")
            return

        state = DialogState.from_json(conversation.state)
        stage, order = self._stage(conversation, state, business)
        if stage is None:
            result.skip("nothing_to_ask")
            return
        order_id = order.id if order is not None else None
        if state.follow_up_sent(stage, order_id):
            result.skip("already_asked")
            return
        result.planned.append((conversation.id, stage))
        if dry_run:
            return
        result.sent.append(self._send(conversation, state, stage, order_id))

    def _stage(
        self, conversation: Conversation, state: DialogState, business: BusinessSettings
    ) -> tuple[str | None, Order | None]:
        """What this dialog is waiting for — the most advanced stage wins."""
        draft = self._draft(conversation, state)
        if draft is not None:
            if state.awaiting == AWAITING_CONFIRMATION and draft.status == OrderStatus.WAITING_CONFIRMATION:
                return STAGE_CONFIRMATION, draft
            return STAGE_DRAFT, draft
        if business.prepayment_enabled and business.prepayment_wallet.strip():
            awaiting_payment = self.orders.latest_awaiting_payment_for_customer(conversation.customer_id)
            if awaiting_payment is not None:
                return STAGE_RECEIPT, awaiting_payment
        return None, None

    def _draft(self, conversation: Conversation, state: DialogState) -> Order | None:
        if state.draft_order_id is None:
            return None
        order = self.orders.get(state.draft_order_id)
        if order is None or order.customer_id != conversation.customer_id:
            return None
        return order if order.status in DRAFT_ORDER_STATUSES else None

    def _send(self, conversation: Conversation, state: DialogState, stage: str, order_id: int | None) -> int:
        language = state.language or conversation.customer.language.value
        missing = state.missing_fields if stage == STAGE_DRAFT else []
        text = templates.render(ReplyKind.FOLLOW_UP, language, {"stage": stage}, missing)
        message = self.messaging.create_outgoing(
            conversation,
            text,
            sender=MessageSender.AI,
            ai_payload={"follow_up": stage, "order_id": order_id},
        )
        state.remember_follow_up(stage, order_id)
        conversation.state = state.to_json()
        self.db.commit()
        self.queue.send_message(message.id)
        log_event(
            logger,
            "follow_up.sent",
            conversation_id=conversation.id,
            message_id=message.id,
            stage=stage,
            order_id=order_id,
        )
        return message.id

    # ------------------------------------------------------------------ when it is allowed to write

    def _inside_working_hours(self, business: BusinessSettings, moment: datetime) -> bool:
        """A follow-up is a message we start ourselves, so it waits for the bakery's own hours."""
        start = business.order_hours_start or DEFAULT_HOURS_START
        end = business.order_hours_end or DEFAULT_HOURS_END
        current = to_business(moment).time()
        return start <= current <= end


def run_follow_ups(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
    queue: TaskQueue | None = None,
    dry_run: bool = False,
) -> FollowUpResult:
    """One pass, for the Celery task and for tests."""
    try:
        return FollowUpService(db, queue=queue).run(now=now, limit=limit, dry_run=dry_run)
    except Exception:  # pragma: no cover - a pass must never take the beat worker down
        db.rollback()
        log_event(logger, "follow_up.pass_failed", level=logging.ERROR)
        raise
