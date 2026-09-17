"""Conversations ("Диалоги"): list, detail, handoff and the operator's replies (04-api.md §11, 03 §6).

Handoff (03 §6): ``mode=HUMAN_HANDOFF``, ``handoff_reason``, ``handoff_at``; an automatic handoff by
the bot also raises ``needs_attention``. While the conversation is with a human the bot stays
silent. "Вернуть боту" sets ``mode=AI``, clears the attention flag, the reason and
``failed_ai_attempts``.

An operator reply is refused with 409 ``messaging_window_closed`` outside Instagram's 24-hour window.
Replying while the bot is still active takes the conversation over first, so the bot and the
operator never answer the same customer at once (the admin panel asks to "Взять на себя" anyway).
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger, log_event
from app.core.time import now_utc
from app.models.conversation import Conversation, Message
from app.models.customer import Customer
from app.models.enums import ConversationMode, Language, MessageSender
from app.models.user import User
from app.repositories.conversations import DEFAULT_MESSAGES_LIMIT, ConversationRepository, MessageRepository
from app.repositories.orders import OrderRepository
from app.schemas.common import Page
from app.schemas.conversation import (
    ConversationDetail,
    ConversationListItem,
    ConversationStateSummary,
    MessageOut,
    build_conversation_list_item,
)
from app.services.constants import DRAFT_ORDER_STATUSES, IN_PROGRESS_ORDER_STATUSES
from app.services.dialog_state import AWAITING_CANCEL_CONFIRMATION, DialogState
from app.services.messaging_service import MessagingService
from app.services.task_queue import TaskQueue, get_task_queue

logger = get_logger(__name__)

TAKEOVER_REASON = "Оператор взял диалог на себя"
OPERATOR_REPLY_REASON = "Оператор ответил клиенту вручную"


class ConversationService:
    def __init__(
        self,
        db: Session,
        *,
        queue: TaskQueue | None = None,
        settings: Settings | None = None,
        messaging: MessagingService | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.repository = ConversationRepository(db)
        self.messages = MessageRepository(db)
        self.orders = OrderRepository(db)
        self._queue = queue
        self._messaging = messaging

    @property
    def queue(self) -> TaskQueue:
        if self._queue is None:
            self._queue = get_task_queue()
        return self._queue

    @property
    def messaging(self) -> MessagingService:
        if self._messaging is None:
            self._messaging = MessagingService(self.db, settings=self.settings)
        return self._messaging

    # ------------------------------------------------------------------ reads

    def get(self, conversation_id: int) -> Conversation:
        return self.repository.get_or_raise(conversation_id)

    def list(
        self,
        mode: ConversationMode | str | None = None,
        needs_attention: bool | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[ConversationListItem]:
        conversations, total = self.repository.list_filtered(
            mode=mode, needs_attention=needs_attention, search=search, page=page, page_size=page_size
        )
        last_messages = self.messages.last_for_conversations(conversation.id for conversation in conversations)
        active_orders = self.orders.latest_active_ids_for_customers(
            (conversation.customer_id for conversation in conversations), statuses=IN_PROGRESS_ORDER_STATUSES
        )
        return Page[ConversationListItem](
            items=[
                build_conversation_list_item(
                    conversation,
                    last_messages.get(conversation.id),
                    active_orders.get(conversation.customer_id),
                )
                for conversation in conversations
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

    def detail(
        self, conversation_id: int, before_id: int | None = None, limit: int = DEFAULT_MESSAGES_LIMIT
    ) -> ConversationDetail:
        conversation = self.get(conversation_id)
        return self.build_detail(conversation, before_id=before_id, limit=limit)

    def build_detail(
        self, conversation: Conversation, before_id: int | None = None, limit: int = DEFAULT_MESSAGES_LIMIT
    ) -> ConversationDetail:
        if before_id is not None:
            messages = self.messages.page_before(conversation.id, before_id, limit)
        else:
            messages = self.messages.recent_for_conversation(conversation.id, limit)
        last = self.messages.last_for_conversations([conversation.id]).get(conversation.id)
        active = self.orders.latest_active_ids_for_customers(
            [conversation.customer_id], statuses=IN_PROGRESS_ORDER_STATUSES
        ).get(conversation.customer_id)
        base = build_conversation_list_item(conversation, last, active)
        return ConversationDetail(
            **base.model_dump(),
            messages=[MessageOut.model_validate(message) for message in messages],
            state_summary=self._state_summary(conversation),
        )

    def _state_summary(self, conversation: Conversation) -> ConversationStateSummary:
        state = DialogState.from_json(conversation.state)
        draft_id = state.draft_order_id
        if draft_id is not None:
            draft = self.orders.get(draft_id)
            if draft is None or draft.status not in DRAFT_ORDER_STATUSES:
                draft_id = None  # confirmed or cancelled from the admin panel meanwhile
        awaiting = state.awaiting
        if draft_id is None and awaiting != AWAITING_CANCEL_CONFIRMATION:
            awaiting = None  # the question was about a draft that no longer exists
        language = state.language or conversation.customer.language or Language.RU
        return ConversationStateSummary(draft_order_id=draft_id, awaiting=awaiting, language=Language(language))

    # ------------------------------------------------------------------ instagram threads

    def get_or_create_instagram(self, customer: Customer, conversation_key: str) -> Conversation:
        """The customer's thread ``"{account_id}:{igsid}"`` (02). Safe against a concurrent webhook."""
        conversation = self.repository.get_by_instagram_conversation_id(conversation_key)
        if conversation is not None:
            return conversation
        conversation = Conversation(
            customer_id=customer.id,
            instagram_conversation_id=conversation_key,
            mode=ConversationMode.AI,
            state={},
        )
        self.db.add(conversation)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            existing = self.repository.get_by_instagram_conversation_id(conversation_key)
            if existing is None:
                raise
            return existing
        log_event(logger, "conversation.created", conversation_id=conversation.id, customer_id=customer.id)
        return conversation

    # ------------------------------------------------------------------ handoff

    def handoff(
        self,
        conversation: Conversation,
        reason: str | None,
        *,
        user: User | None = None,
        needs_attention: bool = True,
        commit: bool = True,
    ) -> Conversation:
        """03 §6. The bot calls it with ``needs_attention=True``; staff taking over with ``False``."""
        conversation.mode = ConversationMode.HUMAN_HANDOFF
        conversation.handoff_reason = (reason or "").strip() or None
        conversation.handoff_at = now_utc()
        conversation.needs_attention = needs_attention
        if user is not None:
            conversation.assigned_user_id = user.id
        self.db.flush()
        if commit:
            self.db.commit()
        log_event(
            logger,
            "conversation.handoff",
            conversation_id=conversation.id,
            reason=conversation.handoff_reason,
            user_id=user.id if user is not None else None,
            automatic=user is None,
        )
        return conversation

    def take_over(self, conversation_id: int, reason: str | None, user: User) -> ConversationDetail:
        """``POST /conversations/{id}/handoff``."""
        conversation = self.get(conversation_id)
        self.handoff(conversation, reason or TAKEOVER_REASON, user=user, needs_attention=False)
        return self.build_detail(conversation)

    def resume(self, conversation_id: int, user: User) -> ConversationDetail:
        """``POST /conversations/{id}/resume``: back to the bot (03 §6)."""
        conversation = self.get(conversation_id)
        conversation.mode = ConversationMode.AI
        conversation.needs_attention = False
        conversation.failed_ai_attempts = 0
        conversation.handoff_reason = None
        conversation.handoff_at = None
        conversation.assigned_user_id = None
        self.db.commit()
        log_event(logger, "conversation.resumed", conversation_id=conversation.id, user_id=user.id)
        return self.build_detail(conversation)

    def mark_read(self, conversation_id: int, user: User) -> None:
        """``POST /conversations/{id}/read`` → ``needs_attention=false``."""
        conversation = self.get(conversation_id)
        if conversation.needs_attention:
            conversation.needs_attention = False
            self.db.commit()
            log_event(logger, "conversation.read", conversation_id=conversation.id, user_id=user.id)

    # ------------------------------------------------------------------ operator replies

    def send_operator_message(self, conversation_id: int, text: str, user: User) -> Message:
        """``POST /conversations/{id}/messages``: store, then queue the delivery (06 §1)."""
        conversation = self.get(conversation_id)
        self.messaging.ensure_window_open(conversation)
        if conversation.mode == ConversationMode.AI:
            self.handoff(conversation, OPERATOR_REPLY_REASON, user=user, needs_attention=False, commit=False)
        conversation.needs_attention = False
        message = self.messaging.create_outgoing(conversation, text, sender=MessageSender.OPERATOR, user=user)
        self.queue.send_message(message.id)
        return message
