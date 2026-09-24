"""Dialog orchestration: one customer message → one decided reply (docs/architecture/05-ai.md §5).

The LLM only *reads* the message (``understand``) and *words* some replies (``Responder``). Every
decision is taken here, deterministically, against the database:

1. AI switched off or the conversation is with a human → no reply, ``needs_attention``.
1b. The customer is blacklisted (``Customer.is_blocked``, staff-only toggle, 03 §2): a fixed refusal
    is sent once, then the bot stays silent on every later message — never re-decided by the LLM.
2. An explicit request for a human (keywords, 03 §6) → handoff.
3. Waiting for "Да" (order summary / cancellation) → ``classify_confirmation``; only ``YES`` with an
   unchanged order (content hash) confirms (03 §5). A bare number answers "how many?" or picks an
   offered address without the LLM.
4. ``understand()``; an LLM failure → handoff.
5. Language: Tajik letters / words, then the model, then the customer's profile.
6. Routing by intent; ordering goes through the draft: items (catalog ids checked, ``ProductMatcher``,
   "какие именно?"), date/time (lead time, horizon), delivery type, name, phone, address → synchronous
   geocoding → map link; missing data is asked two fields at a time; a complete draft gets the
   template summary and waits for "Да".

Documented decisions beyond the contract text:

- a DELIVERY order's recipient defaults to the customer's own name (the summary shows it, so the
  customer can still correct it) — otherwise "for myself" would be asked forever;
- with several unnamed items pending ("2 торта") and the kinds named without quantities, the pending
  quantity is split: one kind takes it all, as many kinds as units take one each (SPEC §11/§12);
- in "replace" mode ("вместо медовика — наполеон") an unspecified quantity is 1 — visible in the
  summary before confirmation;
- an ``OTHER`` message while a draft is open re-asks the draft's questions (and still counts as a
  failed attempt, so two in a row hand the dialog over).
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, time, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.ai.catalog import flavour_bases, sized_products
from app.ai.confirmation import (
    ConfirmationDecision,
    classify_cancel_confirmation,
    classify_confirmation,
    is_hesitation,
    mentions_cancellation,
)
from app.ai.handoff import detect_operator_request, detect_our_fault
from app.ai.language import detect_language
from app.ai.llm_client import LLMClient, LLMError, get_llm_client
from app.ai.product_matcher import (
    MatchStatus,
    ProductMatcher,
    is_general_mention,
    is_generic_mention,
    is_mix_mention,
    off_catalog_mentions,
)
from app.ai.receipt import (
    MIN_CONFIDENCE,
    EarlierReceipt,
    ReceiptInspection,
    ReceiptReading,
    ReceiptVerdict,
    check_receipt,
    credited_amount,
    expected_prepayment,
    file_digest,
    inspect_receipt,
    mentions_payment_done,
    normalize_reference,
    paid_at_bounds,
    read_receipt,
    receipt_note,
    transfer_fingerprint,
)
from app.ai.responder import Reply, ReplyKind, ReplyPlan, Responder, uses_template
from app.ai.small_talk import Greeting, SmallTalk, detect_greeting, detect_small_talk
from app.ai.templates import MAX_QUESTIONS, render
from app.ai.text_normalize import latin_readings, normalize_fold
from app.ai.tools import ToolContext, ToolRegistry, product_view, tool_definitions
from app.ai.understanding import (
    MAX_QUANTITY,
    Entities,
    ItemMention,
    ItemsMode,
    OtherTopic,
    UnderstandingContext,
    UnderstandingResult,
    to_spec_extraction,
    understand,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import BusinessRuleError, IntegrationNotConfiguredError
from app.core.logging import get_logger, log_event
from app.core.time import business_now, business_today, ensure_utc, now_utc
from app.integrations.maps.types import PRECISION_HOUSE, Geocoder
from app.models.conversation import Conversation, Message
from app.models.customer import Customer
from app.models.delivery import Delivery
from app.models.enums import (
    ActorType,
    ConversationMode,
    DeliveryType,
    GeocodeStatus,
    Intent,
    Language,
    LocationSource,
    MessageDirection,
    MessageSender,
    MessageType,
    OrderStatus,
    PaymentKind,
    PaymentMethod,
)
from app.models.faq import FaqItem
from app.models.order import Order
from app.models.product import Product
from app.models.receipt import PaymentReceipt
from app.repositories.conversations import MessageRepository
from app.repositories.faq import FaqRepository
from app.repositories.orders import OrderRepository
from app.repositories.products import ProductRepository
from app.repositories.receipts import ReceiptRepository
from app.schemas.settings import BusinessSettings
from app.services.constants import DRAFT_ORDER_STATUSES, IN_PROGRESS_ORDER_STATUSES, is_inside_tajikistan
from app.services.customer_service import CustomerService
from app.services.delivery_service import DeliveryService
from app.services.dialog_state import (
    AWAITING_ADDRESS_CHOICE,
    AWAITING_CANCEL_CONFIRMATION,
    AWAITING_CONFIRMATION,
    AWAITING_MISSING_FIELDS,
    MAX_ADDRESS_CANDIDATES,
    PENDING_AMBIGUOUS,
    PENDING_GENERIC,
    PENDING_QUANTITY,
    DialogState,
    order_content_hash,
)
from app.services.geocoding_service import GeocodingService
from app.services.location_service import LocationService
from app.services.media_storage import MediaStorage, media_type_for
from app.services.order_pricing import money
from app.services.order_service import BOT_CANCELLABLE_STATUSES, OrderService
from app.services.order_validator import (
    CLOSED_DAY,
    DATE_PAST,
    DELIVERY_DATE,
    DELIVERY_TIME,
    OUT_OF_HOURS,
    TOO_FAR,
    TOO_SOON,
    USABLE_GEOCODE_STATUSES,
    OrderValidator,
    allowed_quantities_around,
    allowed_quantity_examples,
    next_open_day,
    order_quantity,
    quantity_problem,
    smallest_allowed_quantity,
)
from app.services.phone import normalize_phone
from app.services.settings_service import SettingsService

logger = get_logger(__name__)

__all__ = ["FAILED_ATTEMPTS_LIMIT", "DialogOutcome", "DialogService"]

#: 03 §6: two failed attempts in a row hand the dialog over.
FAILED_ATTEMPTS_LIMIT = 2
HISTORY_LIMIT = 20
#: At most this many FAQ answers in one reply: more is the wall of text again.
MAX_FAQ_ANSWERS = 3
REPLY_HISTORY_LIMIT = 6  # recent turns the reply step sees for continuity (05 §6)
MAX_TOOL_ROUNDS = 2

REASON_OPERATOR_REQUEST = "Клиент попросил менеджера"
REASON_COMPLAINT = "Жалоба клиента"
#: 03 §6 (22.09.2026): a mistake on our side is never explained away by the bot — the manager answers.
REASON_OUR_FAULT = "Клиент пишет о проблеме с нашей стороны"
REASON_AI_UNAVAILABLE = "AI-ассистент недоступен"
REASON_NOT_UNDERSTOOD = "Бот не смог понять клиента"
REASON_REPEATED_REPLY = "Бот ответил бы теми же словами второй раз подряд — клиент спрашивает о другом"
REASON_IMAGE = "Клиент прислал изображение"
REASON_RECEIPT_UNREAD = "Клиент прислал чек об оплате, распознать его не удалось"
AUTO_PAYMENT_NOTE = "По чеку из Instagram (отмечено ботом автоматически)"
REASON_ORDER_LOCKED = "Клиент просит изменить заказ №{order_id}, который уже оформлен"
REASON_CANCEL_LOCKED = "Клиент просит отменить заказ №{order_id}, который уже в работе"
CANCEL_REASON = "Отменён клиентом в Instagram"
#: The customer said no before the order existed for them — nothing was placed, nothing is cancelled.
DRAFT_DISCARDED_REASON = "Клиент отказался до оформления заказа"
CANCEL_REQUEST_TEXT = "отменить заказ. Сообщение клиента: {text}"

#: Structured parts of a delivery address, parsed from the customer's text (06 §2).
ADDRESS_PART_FIELDS = ("district", "microdistrict", "street", "house", "apartment", "entrance", "floor", "landmark")

#: A draft nobody touched for this long is finished business: new items start a new order instead of
#: being merged into it ("2 коробки" written last week are not part of today's "3 классических").
DRAFT_ABANDON_HOURS = 48
ABANDON_REASON = "Черновик не завершён клиентом — начат новый заказ"
#: Facts of a question asked together with order data ("а доставка платная?"), answered in the same reply.
ANSWERS_FACT = "answers"

#: Handoffs the bot caused itself (03 §6). Until a person writes to the customer, the bot keeps
#: answering plain questions from the data — where we are, the flavours, delivery — instead of
#: leaving them in silence: in dialog #3 (23.09.2026) six questions in a row went unanswered for an
#: hour after a handoff. A customer who asked for a person or complained is left to the person.
ASSIST_REASON_PREFIXES: tuple[str, ...] = (
    REASON_REPEATED_REPLY,
    REASON_NOT_UNDERSTOOD,
    REASON_IMAGE,
    REASON_RECEIPT_UNREAD,
    REASON_ORDER_LOCKED.split("{", 1)[0],
    REASON_CANCEL_LOCKED.split("{", 1)[0],
)

#: "Сколько стоит", "цена", "нархаш чанд": the message asks the price, so the catalog answers it even
#: when an FAQ keyword matches too — "Асалом цена за шт?" got "по одной штуке не продаём" and no
#: price at all (audit of the Direct archive, 23.09.2026). Matched on ``normalize_fold`` text.
_PRICE_RE = re.compile(
    r"\b(?:цен[аыуеой]?|ценник\w*|стоит(?!\s+(?:ли|брать|того|попробовать))|стоят|стоимост\w*|почем|прайс\w*|"
    r"сколько\s+будет|нарх\w*|кимат\w*|"
    # "соати чанба" is "at what hour", not "for how much" (archive, 24.09.2026)
    r"чанд?\s*пул\w*|чанд?\s*с[уо]м\w*|(?<!соати )чанба|(?<!соати )чандба|донаш\s+чанд?|чанд?\s+ба\s+мешад|"
    # "4шт 69с?", "А классические получается 40 сом?" — a price named to be confirmed (archive, 24.09)
    r"\d+\s*(?:сом\w*|смн)|\d+с)\b"
)

_BARE_NUMBER_RE = re.compile(
    r"^\s*(?:(?:давайте|давай|тогда|ну|хорошо|ладно|ок|пусть|майлаш|майли|хоп|боша)[\s,]+)*"
    r"(\d{1,3})\s*(?:шт\.?|штук[аи]?|дона|pcs|кор\.?|коробк[а-я]*|қуттӣ|куттӣ)?\s*[.!]?\s*$",
    re.IGNORECASE,
)
#: "по одному каждого", "по 1", "аз ҳар кадом якто": one of each flavour.
_ONE_OF_EACH_RE = re.compile(r"\bпо\s+(?:одному|одной|одну|1)\b|\bякто\b|\bякта\b")

#: Agreement to an offered time that the strict order confirmation reads as UNCERTAIN ("давайте", "ок",
#: "майлаш"): enough to take the slot the bot itself proposed, never to confirm an order.
_SLOT_AGREEMENTS = [
    phrase.split()
    for phrase in (
        "давайте", "ну давайте", "давай", "хорошо", "ну хорошо", "ок", "окей", "ok", "ладно", "пойдет",
        "можно", "устраивает", "подходит", "согласна", "согласен", "хоп", "хуп", "майлаш", "майли",
        "мешад", "мешава", "шудаст", "хуб", "нагз", "ха майлаш", "хоп майлаш",
    )
]
_AGREEMENT_PUNCT_RE = re.compile(r"[^\w\s]")

#: "Какие…?" — the customer wants the list, and the catalog is the only thing that can give it.
WHICH_WORDS = frozenset(
    {"какие", "каких", "какой", "какая", "чего", "кадом", "кадомаш", "кадомхо", "чихел", "намуд", "намудаш"}
)
#: Keywords that ask "есть ли это сейчас" on their own, but name the assortment as soon as a
#: "какие…" question is built around them: «какие у вас есть в наличии?» is about the flavours, not
#: about today (dialog #42, 21.09.2026). An FAQ entry matched only by these loses to the catalog.
STOCK_KEYWORDS = frozenset(
    {"в наличии", "есть в наличии", "готовые есть", "есть сейчас", "сейчас есть", "баного", "хаст ми", "хастми"}
)

#: Replies that answer a question, and the intents each of them already covers: the message's other
#: questions are answered under them (``_side_answers``).
_SIDE_COVERED: dict[ReplyKind, frozenset[Intent]] = {
    ReplyKind.FAQ_ANSWER: frozenset({Intent.FAQ}),
    ReplyKind.PRODUCT_INFO: frozenset({Intent.PRODUCT_QUERY}),
    ReplyKind.UNKNOWN_PRODUCT: frozenset({Intent.PRODUCT_QUERY}),
    ReplyKind.DELIVERY_INFO: frozenset({Intent.DELIVERY_QUERY}),
    ReplyKind.PAYMENT_INFO: frozenset({Intent.PAYMENT_QUERY}),
    ReplyKind.ORDER_STATUS_INFO: frozenset({Intent.ORDER_STATUS}),
    ReplyKind.NEED_MANAGER: frozenset(),
}
#: Replies that have a second wording for when they would come out the same twice in a row.
AGAIN_KINDS = frozenset({ReplyKind.NEED_MANAGER, ReplyKind.SMALL_TALK, ReplyKind.GREETING, ReplyKind.UNKNOWN_PRODUCT})
#: What the bot may still say while the dialog waits for the manager (03 §6): answers from the data.
ASSIST_KINDS = frozenset(
    {
        ReplyKind.FAQ_ANSWER,
        ReplyKind.PRODUCT_INFO,
        ReplyKind.UNKNOWN_PRODUCT,
        ReplyKind.DELIVERY_INFO,
        ReplyKind.PAYMENT_INFO,
        ReplyKind.ORDER_STATUS_INFO,
    }
)

#: Intents that ask something and must be answered, even when the message carries order data.
QUERY_INTENTS = frozenset(
    {Intent.FAQ, Intent.PRODUCT_QUERY, Intent.DELIVERY_QUERY, Intent.PAYMENT_QUERY, Intent.ORDER_STATUS}
)
ORDER_INTENTS = frozenset({Intent.CREATE_ORDER, Intent.CHANGE_ORDER})
#: Questions ``_note_answers`` can answer inside an order reply (facts ``answers``).
ANSWERED_QUERY_INTENTS = frozenset({Intent.FAQ, Intent.DELIVERY_QUERY, Intent.PAYMENT_QUERY})


@dataclass(slots=True)
class DialogOutcome:
    reply: Reply | None = None
    handoff: bool = False
    actions: list[str] = field(default_factory=list)
    #: A picture to send just before the reply, by media file name: the price list photo (03 §1.4).
    image: str | None = None


@dataclass(slots=True)
class _Turn:
    """Scratch state of one message being handled."""

    conversation: Conversation
    customer: Customer
    message: Message | None
    text: str
    state: DialogState
    business: BusinessSettings
    tools: ToolRegistry
    actions: list[str] = field(default_factory=list)
    #: notes gathered while applying the message (unknown products, timing, invalid phone)
    notes: dict[str, Any] = field(default_factory=dict)
    draft: Order | None = None
    draft_loaded: bool = False
    catalog: list[Product] | None = None
    history: list[dict[str, str]] | None = None
    #: The price list picture this reply goes with (03 §1.4), by media file name.
    image: str | None = None
    #: What the model read in this message — the other questions in it are answered too (``answers``).
    result: UnderstandingResult | None = None
    #: The dialog is with the manager after a handoff the bot caused, and nobody has answered yet:
    #: only plain questions are answered, from the data, and nothing about an order is touched (03 §6).
    assist: bool = False
    #: ``_asked_products`` of ``result``, computed once: (result, known products, unknown words).
    asked: tuple[Any, list[Product], list[str]] | None = None
    #: The model failed on this message: the reply is a template, not a second call that may fail too.
    no_model: bool = False
    #: ``state.offered_slot`` as this message found it (``_keep_offered_slot``).
    offer_at_start: dict[str, str] | None = None


class DialogService:
    def __init__(
        self,
        db: Session,
        *,
        llm: LLMClient | None = None,
        geocoder: Geocoder | None = None,
        settings: Settings | None = None,
        use_tools: bool | None = None,
        llm_factory: Callable[[Settings], LLMClient] = get_llm_client,
        media: MediaStorage | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self._llm = llm
        self._llm_resolved = llm is not None
        self._llm_factory = llm_factory
        self._geocoder = geocoder
        self._media = media
        self.use_tools = self.settings.LLM_TOOLS_ENABLED if use_tools is None else use_tools
        self.products = ProductRepository(db)
        self.orders = OrderRepository(db)
        self.faq = FaqRepository(db)
        self.messages = MessageRepository(db)
        self.receipts = ReceiptRepository(db)
        self.order_service = OrderService(db)
        self.customer_service = CustomerService(db)

    @property
    def llm(self) -> LLMClient | None:
        """Resolved on first use; ``None`` when ``LLM_API_KEY`` is not configured (05 §2)."""
        if not self._llm_resolved:
            self._llm_resolved = True
            try:
                self._llm = self._llm_factory(self.settings)
            except IntegrationNotConfiguredError:
                log_event(logger, "ai.not_configured", level=logging.WARNING)
                self._llm = None
        return self._llm

    # ================================================================== entry points

    def handle_incoming(self, conversation: Conversation, message: Message) -> DialogOutcome:
        """05 §5 for one incoming customer message (already stored and committed)."""
        turn = self._turn(conversation, message)
        skipped = self._skip_reason(turn)
        if skipped is not None:
            return skipped
        blocked = self._blocked_reason(turn)
        if blocked is not None:
            return blocked

        language = detect_language(turn.text, None, self._known_language(turn), self._catalog_phrases(turn))
        if message.message_type == MessageType.IMAGE:
            outcome = self._receipt(turn, language)  # a prepayment receipt, when one is awaited (03 §3)
            if outcome is not None:
                return outcome
            if not turn.text:
                return self._handoff(turn, REASON_IMAGE, "image", language)
        if not turn.text:
            return self._finish(turn, ReplyPlan(ReplyKind.CLARIFY, language), reset_attempts=False)
        # Valid while the time is still open (``_keep_offered_slot``): "На завтра можем не раньше 18:00.
        # К какому времени? Номер телефона?" → the phone → "К какому времени?" → "да" takes 18:00.
        offered = turn.offer_at_start = turn.state.offered_slot
        if detect_operator_request(turn.text):
            return self._handoff(turn, REASON_OPERATOR_REQUEST, "operator_request", language)
        if detect_our_fault(turn.text):
            # "заказ не привезли", "перепутали вкусы": ours to fix, so the manager takes it over at
            # once — the bot neither explains nor answers from the FAQ (03 §6, 22.09.2026).
            return self._handoff(turn, REASON_OUR_FAULT, "complaint", language)
        if turn.assist:
            return self._assist(turn, language)

        if turn.state.awaiting in (AWAITING_CONFIRMATION, AWAITING_CANCEL_CONFIRMATION):
            outcome = self._answer_to_question(turn, language)
            if outcome is not None:
                return outcome
        outcome = self._accept_offered_slot(turn, offered, language)
        if outcome is not None:
            return outcome
        outcome = self._bare_number(turn, language)
        if outcome is not None:
            return outcome
        talk = detect_small_talk(turn.text)
        if talk is SmallTalk.GREETING:
            return self._greeting(turn, None, language)
        if talk is not SmallTalk.NONE:
            return self._small_talk(turn, talk.value, language)
        if mentions_payment_done(turn.text):
            outcome = self._ask_receipt(turn, language)  # "оплатил" while a prepayment is awaited
            if outcome is not None:
                return outcome

        try:
            result = self._understand(turn)
        except (LLMError, IntegrationNotConfiguredError) as exc:
            conversation.failed_ai_attempts = (conversation.failed_ai_attempts or 0) + 1
            log_event(
                logger,
                "dialog.understanding_failed",
                level=logging.WARNING,
                conversation_id=conversation.id,
                error=type(exc).__name__,
                reason=getattr(exc, "reason", None),
            )
            outcome = self._without_model(turn, language)
            if outcome is not None:
                return outcome
            return self._handoff(turn, REASON_AI_UNAVAILABLE, "ai_unavailable", language, reset_attempts=False)

        language = detect_language(turn.text, result.language, self._known_language(turn), self._catalog_phrases(turn))
        self._record_understanding(turn, result, language)
        return self._route(turn, result, language)

    def continue_after_location(self, order: Order) -> DialogOutcome:
        """03 §7: the customer placed the pin — re-check the draft and send the summary or the next question."""
        conversation = order.conversation
        if conversation is None:
            return DialogOutcome(actions=["no_conversation"])
        turn = self._turn(conversation, None)
        skipped = self._skip_reason(turn)
        if skipped is not None:
            return skipped
        blocked = self._blocked_reason(turn)
        if blocked is not None:
            return blocked
        if turn.state.draft_order_id != order.id or order.status not in DRAFT_ORDER_STATUSES:
            return DialogOutcome(actions=["not_current_draft"])
        turn.draft, turn.draft_loaded = order, True
        turn.state.address_candidates = []
        turn.state.offered_slot = None  # this reply asks something else
        if turn.state.awaiting == AWAITING_ADDRESS_CHOICE:
            turn.state.awaiting = None
        return self._continue_order(turn, order, self._known_language(turn))

    # ================================================================== steps 1-4

    def _turn(self, conversation: Conversation, message: Message | None) -> _Turn:
        customer = conversation.customer
        return _Turn(
            conversation=conversation,
            customer=customer,
            message=message,
            text=(message.text or "").strip() if message is not None else "",
            state=DialogState.from_json(conversation.state),
            business=SettingsService(self.db).get(),
            tools=ToolRegistry(
                self.db, ToolContext(customer=customer, conversation=conversation), order_service=self.order_service
            ),
        )

    def _skip_reason(self, turn: _Turn) -> DialogOutcome | None:
        """05 §5 step 1 / 03 §6: the bot is silent; the operator sees the new message.

        One exception: a handoff the bot caused itself, while no person has written yet — then
        plain questions are still answered from the data (``_may_assist``).
        """
        if turn.business.ai_enabled and turn.conversation.mode != ConversationMode.HUMAN_HANDOFF:
            return None
        reason = "ai_disabled" if not turn.business.ai_enabled else "human_handoff"
        turn.conversation.needs_attention = True
        self.db.commit()
        if reason == "human_handoff" and self._may_assist(turn):
            turn.assist = True
            turn.actions.append("assist")
            return None
        log_event(logger, "dialog.skipped", conversation_id=turn.conversation.id, reason=reason)
        return DialogOutcome(actions=[reason])

    def _may_assist(self, turn: _Turn) -> bool:
        """03 §6: may the bot answer a plain question while the dialog waits for the manager?

        Only after a handoff the bot caused itself (``ASSIST_REASON_PREFIXES``), only for a text
        message, and only until a person takes the dialog or writes to the customer — from then on
        the manager leads and the bot is silent, as before.
        """
        conversation = turn.conversation
        message = turn.message
        if message is None or message.message_type == MessageType.IMAGE or not turn.text:
            return False
        if conversation.assigned_user_id is not None:
            return False
        if not (conversation.handoff_reason or "").startswith(ASSIST_REASON_PREFIXES):
            return False
        return not self.messages.has_operator_message_since(conversation.id, conversation.handoff_at)

    def _blocked_reason(self, turn: _Turn) -> DialogOutcome | None:
        """A staff-blacklisted customer (03 §2): one fixed refusal, then silence — never the LLM."""
        if not turn.customer.is_blocked:
            return None
        turn.conversation.needs_attention = True
        if turn.state.blocked_notice_sent:
            self.db.commit()
            log_event(logger, "dialog.skipped", conversation_id=turn.conversation.id, reason="blocked_silent")
            return DialogOutcome(actions=["blocked_silent"])
        turn.state.blocked_notice_sent = True
        language = self._known_language(turn)
        return self._finish(turn, ReplyPlan(ReplyKind.BLOCKED, language), reset_attempts=False)

    def _known_language(self, turn: _Turn) -> str:
        if turn.state.language:
            return turn.state.language
        language = turn.customer.language
        return language.value if isinstance(language, Language) else str(language or "ru")

    def _answer_to_question(self, turn: _Turn, language: str) -> DialogOutcome | None:
        """05 §5 step 3. ``None`` → the message goes on to understanding."""
        state = turn.state
        if state.awaiting == AWAITING_CONFIRMATION:
            order = self._draft(turn)
            if order is None or order.status != OrderStatus.WAITING_CONFIRMATION:
                state.awaiting, state.summary_hash = None, None
                return None
            decision = classify_confirmation(turn.text)
            if decision == ConfirmationDecision.NO and mentions_cancellation(turn.text):
                return None  # "отмените заказ" at the summary: understanding routes it to CANCEL_ORDER
            if decision == ConfirmationDecision.UNCERTAIN and not is_hesitation(turn.text):
                return None  # "А как оплатить?" is a question, not a hesitation: answer it (and remind)
            self._mark_processed(turn, {"confirmation": decision.value})
            if decision == ConfirmationDecision.YES:
                return self._confirm(turn, order, language)
            if decision == ConfirmationDecision.NO:
                return self._finish(turn, ReplyPlan(ReplyKind.ASK_WHAT_TO_CHANGE, language, {"order_id": order.id}))
            if decision == ConfirmationDecision.UNCERTAIN:
                return self._finish(turn, ReplyPlan(ReplyKind.CONFIRMATION_REPEAT, language, {"order_id": order.id}))
            return None  # CHANGE: extract what to change

        order = self.orders.get(state.cancel_order_id) if state.cancel_order_id else None
        if order is None or order.customer_id != turn.customer.id or order.status == OrderStatus.CANCELLED:
            state.awaiting, state.cancel_order_id = None, None
            return None
        decision = classify_cancel_confirmation(turn.text)
        if decision == ConfirmationDecision.CHANGE:
            state.awaiting, state.cancel_order_id = None, None
            return None
        if decision == ConfirmationDecision.UNCERTAIN and not is_hesitation(turn.text):
            return None  # something else was asked; the cancellation question stays open
        self._mark_processed(turn, {"cancel_confirmation": decision.value})
        if decision == ConfirmationDecision.YES:
            return self._cancel(turn, order, language)
        if decision == ConfirmationDecision.NO:
            state.awaiting, state.cancel_order_id = None, None
            return self._finish(turn, ReplyPlan(ReplyKind.CANCEL_KEPT, language, {"order_id": order.id}))
        return self._finish(turn, ReplyPlan(ReplyKind.CANCEL_CONFIRM, language, {"order_id": order.id}))

    def _accept_offered_slot(self, turn: _Turn, offered: dict[str, str] | None, language: str) -> DialogOutcome | None:
        """ "Да" / "ха" / "давайте" to "На завтра можем не раньше 18:00" takes that slot (live, 18.09.2026:
        the bot kept asking "к какому времени?" after the customer agreed). Decided without the model:
        the offer is stored with the reply, and the slot is checked again before it is written."""
        if offered is None or turn.state.awaiting != AWAITING_MISSING_FIELDS:
            return None
        order = self._draft(turn)
        if order is None or order.delivery_time is not None:
            return None
        if order.delivery_date is not None and order.delivery_date.isoformat() != offered["date"]:
            return None  # the customer has moved to another day since the offer
        agrees = classify_confirmation(turn.text) == ConfirmationDecision.YES
        if not agrees and _AGREEMENT_PUNCT_RE.sub(" ", normalize_fold(turn.text)).split() not in _SLOT_AGREEMENTS:
            return None
        slot_date, slot_time = date.fromisoformat(offered["date"]), time.fromisoformat(offered["time"])
        if OrderValidator.slot_problems(slot_date, slot_time, turn.business):
            return None
        self._mark_processed(turn, {"offered_slot": offered})
        turn.state.offered_slot = None
        turn.tools.update_order_draft(order, **{DELIVERY_DATE: slot_date, DELIVERY_TIME: slot_time})
        turn.actions.append("offered_slot_taken")
        return self._continue_order(turn, order, language)

    def _bare_number(self, turn: _Turn, language: str) -> DialogOutcome | None:
        """A bare "2" / "1 шт" answers the only open question that expects a number — no LLM needed."""
        match = _BARE_NUMBER_RE.match(turn.text)
        if match is None:
            return None
        number = int(match.group(1))
        state = turn.state
        order = self._draft(turn)
        if order is None:
            return None
        if state.awaiting == AWAITING_ADDRESS_CHOICE and 1 <= number <= len(state.address_candidates):
            self._mark_processed(turn, {"address_choice": number})
            return self._apply_address_choice(turn, order, number, language)
        mix_pending = [entry for entry in state.pending_items if entry["kind"] == PENDING_GENERIC and entry.get("mix")]
        if state.awaiting == AWAITING_MISSING_FIELDS and len(state.pending_items) == 1 and mix_pending:
            return self._mix_count(turn, order, mix_pending[0], number, language)
        quantity_pending = state.pending_of(PENDING_QUANTITY)
        other_pending = [entry for entry in state.pending_items if entry.get("kind") != PENDING_QUANTITY]
        if (
            state.awaiting == AWAITING_MISSING_FIELDS
            and len(quantity_pending) == 1
            and not other_pending
            and 1 <= number <= MAX_QUANTITY
        ):
            pending = quantity_pending[0]
            state.pending_items = []
            self._mark_processed(turn, {"quantity": number})
            item = {"product_id": pending["product_id"], "quantity": number, "comment": pending.get("comment")}
            self._write_items(turn, order, [item], "add")
            return self._continue_order(turn, order, language)
        return None

    def _mix_count(
        self, turn: _Turn, order: Order, pending: dict[str, Any], number: int, language: str
    ) -> DialogOutcome | None:
        """ "6" / "давайте 6" to "Сколько штук?" or "Или сделаем 6 — по одному каждого?" about a mix.

        A count the flavours divide evenly assembles the mix at once; another count becomes the
        question "which ones?" for exactly that many (03 §1.3). No model is needed for a number.
        """
        flavours = flavour_bases(self._catalog(turn))
        if len(flavours) < 2 or not 1 <= number <= MAX_QUANTITY:
            return None
        self._mark_processed(turn, {"mix_count": number})
        if number % len(flavours):
            pending["quantity"] = number
            return self._continue_order(turn, order, language)
        turn.state.pending_items = []
        each = number // len(flavours)
        items = [{"product_id": product.id, "quantity": each, "comment": None} for product in flavours]
        self._write_items(turn, order, items, "add")
        return self._continue_order(turn, order, language)

    def _understand(self, turn: _Turn) -> UnderstandingResult:
        llm = self.llm
        if llm is None:
            raise IntegrationNotConfiguredError("AI-ассистент не настроен: не задан LLM_API_KEY")
        customer = turn.customer
        context = UnderstandingContext(
            now_business=business_now(),
            catalog=[
                {"id": p.id, "name": p.name, "aliases": list(p.aliases or []), "price": str(p.price), "unit": p.unit}
                for p in self._catalog(turn)
            ],
            faq=[{"id": item.id, "question": _faq_question_for_prompt(item)} for item in self.faq.list()],
            customer={
                "name": customer.name,
                "phone": customer.phone,
                "language": self._known_language(turn),
                "is_regular": not customer.is_new,
            },
            draft=self._draft_view(turn),
            awaiting=turn.state.awaiting,
            missing_fields=list(turn.state.missing_fields),
            history=self._history(turn),
        )
        return understand(
            llm,
            context,
            turn.text,
            tools=tool_definitions() if self.use_tools else None,
            tool_executor=turn.tools.execute if self.use_tools else None,
            max_tool_rounds=MAX_TOOL_ROUNDS,
        )

    def _assist(self, turn: _Turn, language: str) -> DialogOutcome:
        """03 §6: a message while the dialog waits for the manager after a handoff the bot caused.

        A plain question is answered from the data — the FAQ, the catalog, delivery and payment
        settings, the customer's own orders. Nothing else gets a word from the bot: no greeting back,
        no order taken, no draft touched, no "уточню у менеджера" (the manager is already called).
        """
        if detect_small_talk(turn.text) is not SmallTalk.NONE:
            return self._assist_silent(turn)
        try:
            result = self._understand(turn)
        except (LLMError, IntegrationNotConfiguredError):
            return self._assist_silent(turn)
        language = detect_language(turn.text, result.language, self._known_language(turn), self._catalog_phrases(turn))
        self._record_understanding(turn, result, language)
        turn.result = result
        if result.intent == Intent.OPERATOR_REQUEST:
            return self._handoff(turn, REASON_OPERATOR_REQUEST, "operator_request", language)
        if result.intent == Intent.COMPLAINT:
            return self._handoff(turn, REASON_COMPLAINT, "complaint", language)
        entities = result.entities
        if result.intent in ORDER_INTENTS or result.intent == Intent.CANCEL_ORDER or entities.items:
            return self._assist_silent(turn)  # the order is the manager's now
        if _has_order_data(entities):
            return self._assist_silent(turn)
        if _payment_topic(turn.text) and self._payment_off(turn):
            return self._assist_silent(turn)  # payment is the manager's while it is switched off (03 §3)
        main = self._main_intent(turn, result)
        faq_items = self._faq_first(turn, result, main)
        if faq_items:
            return self._faq_answer(turn, result, language, items=faq_items)
        if main == Intent.PRODUCT_QUERY or self._asks_about_products(turn, result, main):
            return self._product_query(turn, result, language)
        handlers: dict[Intent, Callable[[_Turn, UnderstandingResult, str], DialogOutcome]] = {
            Intent.FAQ: self._faq_answer,
            Intent.ORDER_STATUS: self._order_status,
            Intent.DELIVERY_QUERY: self._delivery_query,
            Intent.PAYMENT_QUERY: self._payment_query,
        }
        handler = handlers.get(main)
        return handler(turn, result, language) if handler is not None else self._assist_silent(turn)

    def _without_model(self, turn: _Turn, language: str) -> DialogOutcome | None:
        """The model did not answer (a timeout, a rate limit — 05 §2). A plain question the data
        answers on its own is still answered, by template: the price list, "какие есть?", an FAQ
        entry found by the owner's keywords, a food we do not make. In the replay of the Direct
        archive (23.09.2026) "Где находится ваш кафе" got "передаю менеджеру" only because the model
        timed out. ``None`` — nothing safe to say: the manager takes the dialog, as before.

        An order being filled is never guessed at without the model: its data needs reading.
        """
        if self._draft(turn) is not None or not self._catalog(turn):
            return None
        turn.no_model = True
        # "Какая калорийность?" has a "which" word and is not about the assortment: without the model
        # only a price or a "which flavours / what do you have" question gets the price list.
        asks_catalog = _price_question(turn.text) or (_asks_which(turn.text) and _ASSORTMENT_RE.search(
            normalize_fold(turn.text)
        ) is not None)
        result = UnderstandingResult(intent=Intent.PRODUCT_QUERY if asks_catalog else Intent.OTHER)
        turn.result = result
        self._mark_processed(turn, {"understanding": None, "without_model": True})
        if _payment_topic(turn.text) and self._payment_off(turn):
            return self._need_manager(turn, language)  # 03 §3: payment is the manager's while it is off
        if asks_catalog:
            return self._product_query(turn, result, language)
        known, unknown = self._asked_products(turn, result)
        if unknown or known:
            return self._product_query(turn, result, language)
        named = self._named_products(turn)
        if named:
            # "фисташковый?" — the price of what was named. Not for an order ("4 фисташковых на
            # завтра"): its data needs the model, so that one goes to the manager.
            result = result.model_copy(update={"intent": Intent.PRODUCT_QUERY, "product_ids_asked": named})
            turn.result = result
            return self._product_query(turn, result, language)
        items = self._faq_items(result, turn.text)
        if items:
            return self._faq_answer(turn, result, language, items=items)
        return None

    def _named_products(self, turn: _Turn) -> list[int]:
        """Catalog products a short question names without the model ("фисташковый?"), or ``[]`` when
        the message looks like an order (a number, "хочу", "закажу", "мегирам") or names a group."""
        folded = normalize_fold(turn.text)
        if any(char.isdigit() for char in folded) or _ORDER_WORDS_RE.search(folded):
            return []
        match = ProductMatcher(self._catalog(turn)).match(turn.text)
        return [] if match.status == MatchStatus.NONE or match.generic else list(match.candidates)

    def _assist_silent(self, turn: _Turn) -> DialogOutcome:
        """Nothing the bot may say while the manager is being waited for: the message waits for them."""
        if turn.message is not None:
            turn.message.ai_processed = True
        turn.conversation.state = turn.state.to_json()
        self.db.commit()
        log_event(logger, "dialog.skipped", conversation_id=turn.conversation.id, reason="assist_silent")
        return DialogOutcome(actions=[*turn.actions, "assist_silent"])

    def _record_understanding(self, turn: _Turn, result: UnderstandingResult, language: str) -> None:
        turn.state.last_intent = result.intent.value
        if turn.message is not None:
            turn.message.ai_processed = True
            turn.message.intent = result.intent.value
            turn.message.ai_payload = {
                "understanding": result.to_payload(),
                "extraction": to_spec_extraction(result),
                "language": language,
            }

    def _mark_processed(self, turn: _Turn, payload: dict[str, Any]) -> None:
        if turn.message is not None:
            turn.message.ai_processed = True
            turn.message.ai_payload = payload

    # ================================================================== step 6: routing

    def _route(self, turn: _Turn, result: UnderstandingResult, language: str) -> DialogOutcome:
        turn.result = result
        intent = result.intent
        if intent == Intent.OPERATOR_REQUEST:
            return self._handoff(turn, REASON_OPERATOR_REQUEST, "operator_request", language)
        if intent == Intent.COMPLAINT:
            return self._handoff(turn, REASON_COMPLAINT, "complaint", language)

        state = turn.state
        choice = result.address_candidate_choice
        if state.awaiting == AWAITING_ADDRESS_CHOICE and choice and choice <= len(state.address_candidates):
            order = self._draft(turn)
            if order is not None and not result.entities.address:
                return self._apply_address_choice(turn, order, choice, language)

        if self._is_order_message(turn, result):
            return self._order_flow(turn, result, language)

        intent = self._main_intent(turn, result)
        if _payment_topic(turn.text) and self._payment_off(turn):
            # 03 §3: while payment is switched off, a payment question is the manager's. The model
            # answered «Предоплата нужна?» with the FAQ about trust ("в первый раз всегда так") —
            # the closest entry it could find (audit 23.09.2026). Its FAQ choice stands only where
            # the owner's own keywords confirm it; the payment part is "уточню у менеджера".
            result = result.model_copy(update={"faq_ids": self._confirmed_faq_ids(result, turn.text)})
            turn.result = result
        faq_items = self._faq_first(turn, result, intent)
        if faq_items:
            return self._faq_answer(turn, result, language, items=faq_items)
        if self._asks_about_products(turn, result, intent):
            # "а пирожки вы печёте?" read as OTHER or as a greeting: a question about products is
            # answered from the catalog — "такого у нас нет" included — never by the manager.
            return self._product_query(turn, result, language)
        if intent == Intent.OTHER and _asks_which(turn.text) and _ASSORTMENT_RE.search(normalize_fold(turn.text)):
            # "Чихел хаст?" (what is there?) read as small talk (archive replay, 24.09.2026): a "which"
            # question about the assortment is the price list's, whatever the model called it.
            return self._product_query(turn, result, language)
        if intent == Intent.OTHER and result.other_topic is OtherTopic.SMALL_TALK:
            return self._small_talk(turn, "chat", language)
        if intent == Intent.OTHER and result.other_topic is OtherTopic.QUESTION:
            # SPEC §40: a question the data does not answer → the manager answers; not a failed attempt.
            return self._need_manager(turn, language)

        handlers: dict[Intent, Callable[[_Turn, UnderstandingResult, str], DialogOutcome]] = {
            Intent.CANCEL_ORDER: self._cancel_request,
            Intent.GREETING: self._greeting,
            Intent.FAQ: self._faq_answer,
            Intent.PRODUCT_QUERY: self._product_query,
            Intent.ORDER_STATUS: self._order_status,
            Intent.DELIVERY_QUERY: self._delivery_query,
            Intent.PAYMENT_QUERY: self._payment_query,
        }
        handler = handlers.get(intent)
        if handler is not None:
            return handler(turn, result, language)
        return self._not_understood(turn, language)

    def _is_order_message(self, turn: _Turn, result: UnderstandingResult) -> bool:
        if result.intent in ORDER_INTENTS:
            return True
        if self._draft(turn) is not None and _open_generic(turn.state) and is_mix_mention(turn.text):
            return True  # "все разные" answers the open "какие именно?", whatever the model called it
        entities = result.entities
        if ORDER_INTENTS & set(result.secondary_intents):
            # "Хочу заказать синнамоны, какие у вас есть в наличии?" (dialog #42, 21.09.2026): the
            # intent to order is the preamble and the question is the message, so the question is
            # answered. A named item or any order data means the order has actually started.
            return result.intent not in QUERY_INTENTS or bool(entities.items) or _has_order_fields(entities)
        if result.intent in QUERY_INTENTS or result.intent == Intent.CANCEL_ORDER:
            # "доставка мешава ми? 19-мкр, дом 5, подъезд 2" while the draft is open (live, 18.09.2026):
            # the address is order data, the question is answered on the way (``_note_answers``). A bare
            # delivery_type / payment_method is the topic of the question, not a choice — not enough.
            return (
                result.intent in ANSWERED_QUERY_INTENTS and self._draft(turn) is not None and _has_order_data(entities)
            )
        if entities.items:
            return True
        return self._draft(turn) is not None and (_has_order_fields(entities) or entities.comment is not None)

    def _not_understood(self, turn: _Turn, language: str) -> DialogOutcome:
        conversation = turn.conversation
        conversation.failed_ai_attempts = (conversation.failed_ai_attempts or 0) + 1
        if conversation.failed_ai_attempts >= FAILED_ATTEMPTS_LIMIT:
            return self._handoff(turn, REASON_NOT_UNDERSTOOD, "not_understood", language, reset_attempts=False)
        order = self._draft(turn)
        if order is not None and turn.state.awaiting in (AWAITING_MISSING_FIELDS, AWAITING_ADDRESS_CHOICE, None):
            return self._continue_order(turn, order, language, reset_attempts=False)
        facts, fields = self._reminder(turn)
        return self._finish(turn, ReplyPlan(ReplyKind.CLARIFY, language, facts, fields), reset_attempts=False)

    # ------------------------------------------------------------------ small talk (05 §5, step 3a)

    def _small_talk(self, turn: _Turn, kind: str, language: str) -> DialogOutcome:
        """Thanks, goodbye, "ок", chat: answered warmly, never counted as a failed attempt.

        "Ок"/"дальше" while the draft is being filled means "go on": the next question or the
        summary (also the way out of the map-pin request, 03 §7). Otherwise the reply carries the
        usual reminder of an open draft.
        """
        if turn.message is not None and not turn.message.ai_processed:
            self._mark_processed(turn, {"small_talk": kind})
        order = self._draft(turn)
        state = turn.state
        if kind == SmallTalk.DECLINE.value:
            # The customer is backing out. An order they have already seen is cancelled the usual
            # way, with a confirmation; a draft they have never seen is simply dropped, and a "не
            # надо" with nothing open is answered politely (dialog #46, 21.09.2026).
            turn.actions.append("small_talk:decline")
            return self._cancel_target(turn, language, declined=True)
        if (
            kind in (SmallTalk.ACK.value, SmallTalk.DONE.value)
            and order is not None
            and state.awaiting in (AWAITING_MISSING_FIELDS, AWAITING_ADDRESS_CHOICE, None)
        ):
            if kind == SmallTalk.DONE.value and order.items:
                # "Это всё": nothing more to pick — the open "какие именно?" is not asked again.
                choices = [entry for entry in state.pending_items if entry["kind"] != PENDING_QUANTITY]
                if choices:
                    state.pending_items = [entry for entry in state.pending_items if entry not in choices]
                    turn.actions.append("item_choices_dropped")
            state.address_candidates = []
            if state.awaiting == AWAITING_ADDRESS_CHOICE:
                state.awaiting = None
            turn.actions.append("small_talk:continue")
            return self._continue_order(turn, order, language)
        facts, fields = self._reminder(turn)
        facts["small_talk"] = kind
        recent = self.orders.latest_active_for_customer(turn.customer.id, IN_PROGRESS_ORDER_STATUSES)
        if recent is not None:
            facts["recent_order"] = _order_status_fact(recent)
        turn.actions.append(f"small_talk:{kind}")
        return self._finish(turn, ReplyPlan(ReplyKind.SMALL_TALK, language, facts, fields))

    # ------------------------------------------------------------------ informational intents

    def _greeting(self, turn: _Turn, result: UnderstandingResult | None, language: str) -> DialogOutcome:
        """The customer's greeting back, by template: "Добрый день! Что желаете заказать? 😊" (05 §6).

        ``result`` is ``None`` for a bare greeting recognised without the model (step 3a). An open
        draft keeps its questions: "Добрый день! На какую дату нужен заказ?".
        """
        greeting = detect_greeting(turn.text) or Greeting.HELLO
        if result is None and turn.message is not None:
            self._mark_processed(turn, {"greeting": greeting.value})
            turn.message.intent = Intent.GREETING.value
        facts, fields = self._reminder(turn)
        facts["greeting"] = greeting.value
        return self._finish(turn, ReplyPlan(ReplyKind.GREETING, language, facts, fields))

    def _main_intent(self, turn: _Turn, result: UnderstandingResult) -> Intent:
        """The question the reply leads with. A price or assortment question asked next to another
        one leads: the price list (or its photo) is the bulk of the answer, and the other question is
        answered under it (``answers``) — "Они свежие? И сколько стоят?" used to lose the prices."""
        intent = result.intent
        if Intent.PRODUCT_QUERY not in result.secondary_intents:
            return intent
        if intent not in (Intent.FAQ, Intent.DELIVERY_QUERY, Intent.PAYMENT_QUERY, Intent.GREETING, Intent.OTHER):
            return intent
        asks = (
            _price_question(turn.text)
            or _asks_which(turn.text)
            or bool(result.product_ids_asked)
            or any(self._asked_products(turn, result))
        )
        return Intent.PRODUCT_QUERY if asks else intent

    def _asks_about_products(self, turn: _Turn, result: UnderstandingResult, intent: Intent) -> bool:
        """Does a message that is not a product question still ask about products ("а пирожки вы
        печёте?" read as OTHER)? For an FAQ question only when the FAQ has no answer to it."""
        if intent == Intent.PRODUCT_QUERY:
            return False
        if intent == Intent.FAQ and self._faq_items(result, turn.text):
            return False  # "трайфл есть? можно у вас посидеть?" — the FAQ answers, the rest goes under it
        if intent not in (Intent.OTHER, Intent.FAQ, Intent.GREETING):
            return False
        known, unknown = self._asked_products(turn, result)
        return bool(known or unknown)

    def _asked_products(self, turn: _Turn, result: UnderstandingResult) -> tuple[list[Product], list[str]]:
        """What a question asks about: catalog products, and the words that name nothing we sell.

        From the model's ids, its ``products_asked_text`` and — for a product question — the items it
        read; a word for the assortment as a whole ("синнамоны", "десерты", "коробка") is neither,
        the whole price list answers it. When the model recorded no words at all, the message is
        searched for foods the catalog lacks (``off_catalog_mentions``): "а пирожки вы печёте?" must
        get "пирожков нет" whatever the model did with it (dialog #155, 23.09.2026).
        """
        if turn.asked is not None and turn.asked[0] is result:
            return turn.asked[1], turn.asked[2]
        products = self._catalog(turn)
        by_id = {product.id: product for product in products}
        matcher = ProductMatcher(products)
        known = [by_id[product_id] for product_id in result.product_ids_asked if product_id in by_id]
        unknown: list[str] = []
        texts = list(result.products_asked_text)
        if result.intent == Intent.PRODUCT_QUERY:
            for mention in result.entities.items:
                if mention.product_id in by_id and not is_generic_mention(mention.product_text):
                    known.append(by_id[mention.product_id])
                elif mention.product_text:
                    texts.append(mention.product_text)
        for text in texts:
            sized = sized_products(products, text)
            if sized:
                known.extend(sized)  # "а большие есть?" — the large ones, not "большие у нас нет"
                continue
            match = matcher.match(text)
            if match.status == MatchStatus.NONE:
                if not is_general_mention(text):
                    unknown.append(text)
                continue
            if match.generic and len(match.candidates) >= len(by_id):
                continue  # "синнамоны", "коробка": the whole list answers it
            known.extend(by_id[candidate] for candidate in match.candidates if candidate in by_id)
        question = result.intent in (Intent.PRODUCT_QUERY, Intent.OTHER, Intent.FAQ, Intent.GREETING)
        if not texts and not known and question:
            # The model recorded nothing: "ман калонашро мепурсам" still asks for the large ones, and
            # "а пирожки вы печёте?" still names a food we do not make.
            known = sized_products(products, turn.text)
            if not known:
                unknown = off_catalog_mentions(turn.text, matcher)
        known = list(dict.fromkeys(known))
        unknown = [word for word in _unique_words([word.lower() for word in unknown]) if self._may_be_food(turn, word)]
        turn.asked = (result, known, unknown)
        return known, unknown

    def _may_be_food(self, turn: _Turn, word: str) -> bool:
        """Is this word worth a "такого у нас нет"? Not a word of our own address or delivery text —
        «К додо пицца вынесите?» names the pickup landmark, not a pizza — and not a word in Latin
        letters: the model writes products in Cyrillic (prompt rule 14), so "garmakak" is a Tajik word
        it failed to read ("warm"), not a product (archive replay, 24.09.2026)."""
        folded = normalize_fold(word)
        if not folded or re.search(r"[a-z]", folded):
            return False
        business = turn.business
        own_texts = normalize_fold(
            " ".join((business.pickup_address, business.delivery_info_text, business.working_hours))
        )
        return not any(token[:4] in own_texts for token in folded.split() if len(token) >= 4)

    def _faq_first(self, turn: _Turn, result: UnderstandingResult, intent: Intent | None = None) -> list[FaqItem]:
        """A question the FAQ answers is answered from the FAQ, whatever intent the model chose.

        "Насколько свежие торты?" often comes back as PRODUCT_QUERY (the catalog would be listed) or
        OTHER (the dialog would count a failed attempt). Model-chosen ids count for GREETING too
        ("Здравствуйте, вы работаете в воскресенье?"); the admin's keywords only for PRODUCT_QUERY
        without a named product and OTHER. FAQ / delivery / payment intents keep their own handlers.

        The keywords must not take a question away from the catalog: "какие у вас есть в наличии?"
        asks what the flavours are, not whether there is any left today (dialog #42, 21.09.2026),
        and "цена за шт?" asks the price — the entry "по одной не продаём" goes under the prices,
        it does not replace them (audit 23.09.2026). Neither does a product the question names.
        """
        intent = intent or result.intent
        if intent not in (Intent.PRODUCT_QUERY, Intent.OTHER, Intent.GREETING):
            return []
        if intent == Intent.PRODUCT_QUERY and (
            result.product_ids_asked
            or result.entities.items
            or _price_question(turn.text)
            or sized_products(self._catalog(turn), turn.text)
        ):
            return []  # a named product, a price or a size: the catalog answers, the FAQ goes under it
        if self._asked_products(turn, result)[1]:
            return []  # "а пирожки свежие?" — first of all, there are no pirozhki
        items = self._faq_items(result, turn.text, keywords=intent != Intent.GREETING)
        if result.faq_ids or intent != Intent.PRODUCT_QUERY or not _asks_which(turn.text):
            return items  # the model picked these itself, or nothing asks for the assortment
        return [item for item in items if not _matched_stock_words_only(item, turn.text)]

    def _faq_answer(
        self, turn: _Turn, result: UnderstandingResult, language: str, *, items: list[FaqItem] | None = None
    ) -> DialogOutcome:
        items = items if items is not None else self._faq_items(result, turn.text)
        if not items:
            return self._need_manager(turn, language)
        facts, fields = self._reminder(turn)
        facts["faq"] = [_faq_fact(item, language) for item in items]
        return self._finish(turn, ReplyPlan(ReplyKind.FAQ_ANSWER, language, facts, fields))

    def _product_query(self, turn: _Turn, result: UnderstandingResult, language: str) -> DialogOutcome:
        products = self._catalog(turn)
        if not products:
            return self._need_manager(turn, language)
        asked, unknown = self._asked_products(turn, result)
        facts, fields = self._reminder(turn)
        if unknown and not asked:
            # "А пирожки вы печёте?" — no, and here is what we do make (dialog #155, 23.09.2026). The
            # price list photo is not sent: nobody asked for the prices.
            facts.update({"unknown_products": unknown, "available_products": [_product_fact(p) for p in products]})
            return self._finish(turn, ReplyPlan(ReplyKind.UNKNOWN_PRODUCT, language, facts, fields))
        facts.update({"products": [_product_fact(p) for p in (asked or products)], "asked_specific": bool(asked)})
        if unknown:
            facts["unknown_products"] = unknown  # "круассаны есть? а фисташковый сколько?"
        if not asked and self._price_list_photo(turn):
            # The whole assortment was asked about and the owner keeps a photo of the price list
            # (03 §1.4): it goes instead of the twelve lines, with a short caption under it.
            facts["price_list_photo"] = True
        if turn.business.order_quantity_step > 1 or turn.business.min_order_quantity > 1:
            # Prices are per piece while the bakery sells boxes: "сколько стоит коробка?" needs the
            # rule — the smallest order and the totals that fit it (03 §1.3).
            facts["packing_min"] = smallest_allowed_quantity(turn.business)
            facts["packing_examples"] = allowed_quantity_examples(turn.business)
            if not asked:
                # "Сколько стоит коробка?" answered the way the owner does in Direct: «40 сомон за
                # коробку классических, в коробке 4 шт» — the cheapest flavour, the smallest box,
                # computed from the catalog so it follows every price change (archive, 24.09.2026).
                cheapest = min(flavour_bases(products), key=lambda product: product.price)
                facts["packing_example"] = {
                    "name": cheapest.name,
                    "quantity": facts["packing_min"],
                    "total": f"{money(cheapest.price * facts['packing_min']):.2f}",
                }
        return self._finish(turn, ReplyPlan(ReplyKind.PRODUCT_INFO, language, facts, fields))

    def _price_list_photo(self, turn: _Turn) -> bool:
        """Attach the price list picture to this reply, once per dialog (03 §1.4).

        Sent again only when the owner has uploaded a different picture — then the prices on it have
        changed and the customer has an old one. A missing file (deleted by hand) is simply skipped:
        the bot answers with the price list as text, as it did before there was a photo.
        """
        filename = (turn.business.price_list_image or "").strip()
        if not filename or filename == turn.state.price_list_sent:
            return False
        media = self._media or MediaStorage(self.settings.media_root)
        if media.path_for(filename) is None:
            log_event(logger, "dialog.price_list_missing", level=logging.WARNING, conversation_id=turn.conversation.id)
            return False
        turn.image = filename
        return True

    def _order_status(self, turn: _Turn, result: UnderstandingResult, language: str) -> DialogOutcome:
        orders = self.orders.active_for_customer(turn.customer.id, IN_PROGRESS_ORDER_STATUSES)
        facts, fields = self._reminder(turn)
        facts["orders"] = [_order_status_fact(order) for order in orders]
        return self._finish(turn, ReplyPlan(ReplyKind.ORDER_STATUS_INFO, language, facts, fields))

    def _delivery_query(self, turn: _Turn, result: UnderstandingResult, language: str) -> DialogOutcome:
        business = turn.business
        info = {
            "delivery_info": business.delivery_info_text.strip() or None,
            "pickup_address": business.pickup_address.strip() or None,
            "working_hours": business.working_hours.strip() or None,
        }
        faq = [_faq_fact(item, language) for item in self._faq_items(result, turn.text)]
        if not any(info.values()) and not faq:
            return self._need_manager(turn, language)
        if language == "tg" and faq:
            # The settings texts are Russian only; the FAQ answers exist in Tajik. A Tajik customer
            # got the Russian delivery text under a Tajik reply (archive replay, 23.09.2026).
            info = dict.fromkeys(info)
        facts, fields = self._reminder(turn)
        facts.update({**info, "faq": faq})
        return self._finish(turn, ReplyPlan(ReplyKind.DELIVERY_INFO, language, facts, fields))

    def _confirmed_faq_ids(self, result: UnderstandingResult, text: str) -> list[int]:
        """The model's FAQ ids that one of the entry's own keywords also finds in the message."""
        padded = f" {normalize_fold(text)} "
        by_id = {item.id: item for item in self.faq.list()}
        return [
            faq_id
            for faq_id in result.faq_ids
            if faq_id in by_id
            and any((key := normalize_fold(word)) and f" {key} " in padded for word in by_id[faq_id].keywords or [])
        ]

    def _payment_off(self, turn: _Turn) -> bool:
        """No payment policy to quote: no payment text in the settings and the prepayment switched off."""
        return not turn.business.payment_methods_text.strip() and self._prepayment(turn) is None

    def _payment_query(self, turn: _Turn, result: UnderstandingResult, language: str) -> DialogOutcome:
        methods = turn.business.payment_methods_text.strip() or None
        prepayment = self._prepayment(turn)
        if methods is None and prepayment is None:
            # Payment switched off (03 §3): the payment answers of the FAQ are off with it, so any other
            # entry is the wrong one — the model's nearest guess ("в первый раз всегда так") or a
            # keyword that happened to match ("Алифми эсхата хайми" → «на сегодня нельзя»).
            return self._need_manager(turn, language)
        faq = [_faq_fact(item, language) for item in self._faq_items(result, turn.text)]
        facts, fields = self._reminder(turn)
        facts.update({"payment_methods": methods, "faq": faq, "prepayment": prepayment})
        return self._finish(turn, ReplyPlan(ReplyKind.PAYMENT_INFO, language, facts, fields))

    # ------------------------------------------------------------------ prepayment and receipts (03 §3)

    def _prepayment(self, turn: _Turn) -> dict[str, Any] | None:
        """The wallet facts of the prepayment policy, or ``None`` while it is switched off."""
        business = turn.business
        wallet = business.prepayment_wallet.strip()
        if not business.prepayment_enabled or not wallet:
            return None
        return {
            "wallet": wallet,
            "banks": business.prepayment_wallet_banks.strip() or None,
            "percent": business.prepayment_percent,
        }

    def _awaiting_payment(self, turn: _Turn) -> Order | None:
        """The placed order a prepayment (and so a receipt) may belong to."""
        if self._prepayment(turn) is None:
            return None
        return self.orders.latest_awaiting_payment_for_customer(turn.customer.id)

    def _receipt(self, turn: _Turn, language: str) -> DialogOutcome | None:
        """An image while a prepayment is awaited: read as a receipt and checked against the order.

        ``None`` means "not our case" (policy off, no such order, no stored file, not a receipt): the
        picture goes to the operator exactly as before. The model only reads the screenshot; the
        amount, the recipient, the status, a reused receipt and a doubtful date or look are judged
        here (``check_receipt``), and the order is marked paid only when the owner switched
        ``prepayment_auto_confirm`` on and nothing raised a doubt — otherwise the operator confirms.
        Every recognised receipt is stored (``payment_receipts``), so the next one can be compared.
        """
        order = self._awaiting_payment(turn)
        if order is None or turn.message is None:
            return None
        image = self._image_bytes(turn.message)
        llm = self.llm
        if image is None or llm is None:
            return None
        data, media_type = image
        try:
            reading = read_receipt(llm, data, media_type)
        except LLMError as exc:
            log_event(
                logger,
                "dialog.receipt_failed",
                level=logging.WARNING,
                conversation_id=turn.conversation.id,
                reason=exc.reason,
            )
            return self._handoff(turn, REASON_RECEIPT_UNREAD, "receipt_unread", language)
        business = turn.business
        stored = self.receipts.for_order(order.id)
        credited = credited_amount(order.paid_amount, ((receipt.amount, receipt.problems) for receipt in stored))
        expected = expected_prepayment(order.total_amount, credited, business.prepayment_percent)
        digest = file_digest(data)
        recognised = reading.is_receipt and reading.confidence >= MIN_CONFIDENCE
        inspection = self._inspect_receipt(turn, llm, data, media_type) if recognised else None
        verdict = check_receipt(
            reading,
            expected,
            business.prepayment_wallet,
            earlier=self._earlier_receipt(order, reading, digest) if recognised else None,
            inspection=inspection,
            ordered_at=order.created_at,
        )
        payload: dict[str, Any] = {"reading": reading.model_dump(mode="json"), **verdict.as_dict()}
        if inspection is not None:
            payload["inspection"] = inspection.model_dump(mode="json")
        self._mark_processed(turn, {"receipt": payload})
        if "not_receipt" in verdict.problems:
            return None
        self._store_receipt(turn, order, reading, verdict, digest)
        auto_paid = False
        if verdict.ok and business.prepayment_auto_confirm and verdict.amount is not None:
            self.order_service.payments.register_payment(
                order,
                PaymentKind.PAYMENT,
                verdict.amount,
                method=PaymentMethod.TRANSFER,
                note=AUTO_PAYMENT_NOTE,
                actor_type=ActorType.AI,
            )
            auto_paid = True
            turn.actions.append("payment_registered")
        self.order_service.record_receipt(order, receipt_note(reading, verdict, auto_paid=auto_paid))
        turn.conversation.needs_attention = True  # the operator checks the bank app either way
        turn.actions.append("receipt_checked")
        facts: dict[str, Any] = {
            "order_id": order.id,
            "amount": f"{verdict.amount:.2f}" if verdict.amount is not None else None,
            "expected": f"{verdict.expected:.2f}",
            "shortfall": f"{verdict.shortfall:.2f}" if verdict.shortfall is not None else None,
            "currency": reading.currency,
            "recipient": reading.recipient,
            "problems": list(verdict.problems),
            "auto_paid": auto_paid,
            **(self._prepayment(turn) or {}),
        }
        return self._finish(turn, ReplyPlan(ReplyKind.RECEIPT_RESULT, language, facts))

    def _inspect_receipt(self, turn: _Turn, llm: LLMClient, data: bytes, media_type: str) -> ReceiptInspection | None:
        """The look of the screenshot (05 §9): only a hint, so a failed call just leaves it out."""
        try:
            return inspect_receipt(llm, data, media_type)
        except LLMError as exc:
            log_event(
                logger,
                "dialog.receipt_inspection_failed",
                level=logging.WARNING,
                conversation_id=turn.conversation.id,
                reason=exc.reason,
            )
            return None

    def _earlier_receipt(self, order: Order, reading: ReceiptReading, digest: str) -> EarlierReceipt | None:
        """A stored receipt this one repeats: the same file, transaction number or transfer."""
        reference = normalize_reference(reading.reference)
        found = self.receipts.find_earlier(
            file_sha256=digest, reference=reference, fingerprint=transfer_fingerprint(reading)
        )
        if found is None:
            return None
        if found.file_sha256 == digest:
            match = "file"
        elif reference is not None and found.reference == reference:
            match = "reference"
        else:
            match = "transfer"
        return EarlierReceipt(order_id=found.order_id, same_order=found.order_id == order.id, match=match)

    def _store_receipt(
        self, turn: _Turn, order: Order, reading: ReceiptReading, verdict: ReceiptVerdict, digest: str
    ) -> None:
        assert turn.message is not None  # _receipt runs for an incoming image only
        bounds = paid_at_bounds(reading.paid_at_iso)
        self.receipts.add(
            PaymentReceipt(
                order_id=order.id,
                customer_id=turn.customer.id,
                message_id=turn.message.id,
                file_sha256=digest,
                reference=normalize_reference(reading.reference),
                fingerprint=transfer_fingerprint(reading),
                amount=reading.amount,
                currency=_clip(reading.currency, 16),
                recipient=_clip(reading.recipient, 64),
                sender=_clip(reading.sender, 64),
                provider=_clip(reading.provider, 64),
                paid_at=bounds[0] if bounds is not None and bounds[2] else None,
                status=reading.status,
                ok=verdict.ok,
                problems=verdict.codes,
            )
        )

    def _ask_receipt(self, turn: _Turn, language: str) -> DialogOutcome | None:
        """ "Оплатил" / "перевёл" while a prepayment is awaited: the receipt is asked for, no model needed."""
        order = self._awaiting_payment(turn)
        if order is None:
            return None
        self._mark_processed(turn, {"payment_done": True})
        turn.actions.append("receipt_requested")
        facts = {"order_id": order.id, **(self._prepayment(turn) or {})}
        return self._finish(turn, ReplyPlan(ReplyKind.ASK_RECEIPT, language, facts))

    def _image_bytes(self, message: Message) -> tuple[bytes, str] | None:
        """The stored file of an incoming image (``InboundMessageService`` downloads it right away)."""
        filename = MediaStorage.filename_from_url(message.media_url)
        if filename is None:
            return None
        media = self._media or MediaStorage(self.settings.media_root)
        data = media.read(filename)
        return (data, media_type_for(filename)) if data else None

    def _need_manager(self, turn: _Turn, language: str) -> DialogOutcome:
        """SPEC §40: "Мне нужно уточнить эту информацию у менеджера." + the operator is alerted."""
        turn.conversation.needs_attention = True
        turn.actions.append("needs_manager")
        return self._finish(turn, ReplyPlan(ReplyKind.NEED_MANAGER, language))

    def _faq_items(
        self, result: UnderstandingResult, text: str, *, keywords: bool | str = True
    ) -> list[FaqItem]:
        """FAQ entries chosen by the model (ids validated), else by the admin's keywords.

        Next to the model's choice, an entry named by one of the owner's multi-word phrases is added
        too: "сможете сегодня", "на сегодня" say exactly what they ask. "Во сколько сможете сегодня
        доставить?" got only the delivery-time answer and never heard that today is impossible
        (archive replay, 23.09.2026). A single keyword ("крем", "фото") is too broad for that.
        ``keywords="phrases"`` — the model's choice plus those phrases only, never a single word.
        """
        active = self.faq.list()
        by_id = {item.id: item for item in active}
        chosen = [by_id[faq_id] for faq_id in result.faq_ids if faq_id in by_id]
        if not keywords:
            return _not_about_today(chosen, text)
        # "Garmakak mefisonidmi?" is "гармакак" to the owner's keywords (archive replay, 24.09.2026)
        padded = " | ".join(f" {reading} " for reading in [normalize_fold(text), *latin_readings(text)])
        matched: list[tuple[FaqItem, bool]] = []
        for item in active:
            keys = [key for keyword in item.keywords or [] if (key := normalize_fold(keyword)) and f" {key} " in padded]
            if keys:
                matched.append((item, any(len(key.split()) > 1 for key in keys)))
        if not chosen and keywords != "phrases":
            return _not_about_today([item for item, _ in matched], text)
        extra = [item for item, phrase in matched if phrase and item not in chosen]
        return _not_about_today((chosen + extra)[:MAX_FAQ_ANSWERS], text)

    def _reminder(self, turn: _Turn) -> tuple[dict[str, Any], list[str]]:
        """An open draft keeps being asked about while the customer asks something else."""
        if turn.assist:
            return {}, []  # the order is the manager's now
        order = self._draft(turn)
        if order is None:
            return {}, []
        if turn.state.awaiting == AWAITING_CONFIRMATION and order.status == OrderStatus.WAITING_CONFIRMATION:
            return {"confirmation_pending_order_id": order.id}, []
        if turn.state.pending_items:
            return {}, []
        return {}, self._askable_fields(turn, OrderValidator.missing_fields(order))[:MAX_QUESTIONS]

    # ------------------------------------------------------------------ cancellation

    def _cancel_request(self, turn: _Turn, result: UnderstandingResult, language: str) -> DialogOutcome:
        return self._cancel_target(turn, language)

    def _cancel_target(self, turn: _Turn, language: str, *, declined: bool = False) -> DialogOutcome:
        """What "отмените"/"не надо" applies to: the open draft, else the last active order."""
        order = self._draft(turn) or self.orders.latest_active_for_customer(turn.customer.id)
        if order is None:
            if declined:
                return self._finish(turn, ReplyPlan(ReplyKind.DRAFT_DISCARDED, language))
            facts, fields = self._reminder(turn)
            facts["orders"] = []
            return self._finish(turn, ReplyPlan(ReplyKind.ORDER_STATUS_INFO, language, facts, fields))
        if self._is_unseen_draft(turn, order):
            return self._discard_draft(turn, order, language)
        if order.status not in BOT_CANCELLABLE_STATUSES:
            return self._order_locked(turn, order, CANCEL_REQUEST_TEXT.format(text=turn.text), language, cancel=True)
        turn.state.awaiting = AWAITING_CANCEL_CONFIRMATION
        turn.state.cancel_order_id = order.id
        return self._finish(turn, ReplyPlan(ReplyKind.CANCEL_CONFIRM, language, {"order_id": order.id}))

    def _is_unseen_draft(self, turn: _Turn, order: Order) -> bool:
        """Is this a draft the customer has never been shown as an order?

        The draft is the bot's own bookkeeping: it appears as soon as a flavour is named, long
        before anything is placed. Until the summary has been shown (``summary_hash``) and the
        order confirmed, the customer has seen no number and agreed to nothing.
        """
        return (
            order.id == turn.state.draft_order_id
            and order.status == OrderStatus.NEW
            and turn.state.summary_hash is None
        )

    def _discard_draft(self, turn: _Turn, order: Order, language: str) -> DialogOutcome:
        """"Тогда не надо" before anything was placed (dialog #46, 21.09.2026).

        Asking «Отменить заказ №29?» invents an order the customer never made and turns a polite
        "no, thanks" into paperwork. The draft is dropped without a word about it, and the door is
        left open.
        """
        turn.tools.cancel_order(order, ConfirmationDecision.YES, reason=DRAFT_DISCARDED_REASON)
        turn.state.close_draft()
        turn.actions.append("draft_discarded")
        return self._finish(turn, ReplyPlan(ReplyKind.DRAFT_DISCARDED, language))

    def _cancel(self, turn: _Turn, order: Order, language: str) -> DialogOutcome:
        state = turn.state
        state.awaiting, state.cancel_order_id = None, None
        if order.status not in BOT_CANCELLABLE_STATUSES:
            return self._order_locked(turn, order, CANCEL_REQUEST_TEXT.format(text=turn.text), language, cancel=True)
        turn.tools.cancel_order(order, ConfirmationDecision.YES, reason=CANCEL_REASON)
        if state.draft_order_id == order.id:
            state.close_draft()
        turn.actions.append("order_cancelled")
        return self._finish(turn, ReplyPlan(ReplyKind.ORDER_CANCELLED, language, {"order_id": order.id}))

    def _order_locked(self, turn: _Turn, order: Order, request: str, language: str, *, cancel: bool) -> DialogOutcome:
        """03 §1.5: a placed order is changed/cancelled by staff — journal the request and hand over."""
        turn.tools.record_change_request(order, request)
        reason = (REASON_CANCEL_LOCKED if cancel else REASON_ORDER_LOCKED).format(order_id=order.id)
        return self._handoff(turn, reason, "order_locked", language, facts={"order_id": order.id})

    # ================================================================== step 7: ordering

    def _order_flow(self, turn: _Turn, result: UnderstandingResult, language: str) -> DialogOutcome:
        state = turn.state
        order = self._draft(turn)
        if order is not None and _brings_items(result.entities) and _is_abandoned(order):
            self._abandon_draft(turn, order)
            order = None
        if order is None:
            placed = self.orders.latest_active_for_customer(turn.customer.id, IN_PROGRESS_ORDER_STATUSES)
            if placed is not None and result.intent == Intent.CHANGE_ORDER:
                return self._order_locked(turn, placed, turn.text, language, cancel=False)
            order = turn.tools.create_order_draft()
            state.forget_draft()
            state.draft_order_id = order.id
            turn.draft, turn.draft_loaded = order, True
            turn.actions.append("draft_created")

        self._apply_items(turn, order, result.entities)
        address_changed = self._apply_fields(turn, order, result.entities)
        if _asks_price(result):
            self._note_prices(turn, order, result)
        self._note_answers(turn, result, language)
        if address_changed:
            outcome = self._geocode(turn, order, language)
            if outcome is not None:
                return outcome
        return self._continue_order(turn, order, language)

    def _abandon_draft(self, turn: _Turn, order: Order) -> None:
        """The customer starts ordering again after ``DRAFT_ABANDON_HOURS`` of silence: the old draft is
        cancelled (visible in the journal), not merged with the new items."""
        self.order_service.cancel(order, reason=ABANDON_REASON, actor_type=ActorType.SYSTEM)
        turn.state.forget_draft()
        turn.draft, turn.draft_loaded = None, True
        turn.actions.append("draft_abandoned")
        log_event(logger, "dialog.draft_abandoned", conversation_id=turn.conversation.id, order_id=order.id)

    # ------------------------------------------------------------------ items

    def _apply_items(self, turn: _Turn, order: Order, entities: Entities) -> None:
        mentions = entities.items
        if not mentions and _open_generic(turn.state) and is_mix_mention(turn.text):
            # The model read "все разные" as no item at all (audit 23.09.2026): it is the answer to
            # the open "какие именно?" all the same.
            mentions = [ItemMention(product_text=turn.text)]
        if not mentions:
            return
        state = turn.state
        products = self._catalog(turn)
        by_id = {product.id: product for product in products}
        matcher = ProductMatcher(products)
        mode = entities.items_mode
        quantity_answer = _quantity_answer(state, mentions, matcher)

        resolved: list[dict[str, Any]] = []
        new_pending: list[dict[str, Any]] = []
        unknown: list[str] = []
        for position, mention in enumerate(mentions):
            product = by_id.get(mention.product_id) if mention.product_id is not None else None
            text = mention.product_text or (product.name if product is not None else "")
            if product is not None and is_generic_mention(mention.product_text):
                # "2 синнамон" came back with the id of "Классические синнамоны" (live, 18.09.2026): a bare
                # category word names no flavour, so the id is the model's guess (prompt rule 3). The
                # matcher decides instead: the only product of the kind, "какие именно?", or "нет в каталоге".
                product = None
            if product is None and quantity_answer is not None:
                product = by_id.get(quantity_answer)  # "2 коробки" answers "Сколько коробочек: Шоколадные?"
            if product is not None and product.id in _excepted_ids(products, turn.text):
                # "по одному виду кроме фисташкового": the flavour named is the one left out, not the one wanted
                product, text = None, turn.text
            if product is None:
                match = matcher.match(text)
                if match.product_id is not None and not _excepting_mix(text):
                    product = by_id.get(match.product_id)
                elif (mix := _mix_items(_mix_text(text, turn.text), mention, state, products, position)) is not None:
                    resolved.extend(mix)
                    continue
                elif is_mix_mention(text) and mention.quantity is None and (open_ := _open_generic(state)):
                    # "Хочу 4 синнамона" → "какие именно?" → "все разные": the answer to the question
                    # already open, which 4 on 6 flavours cannot fill evenly — so the question becomes
                    # "which 4, or 6 — one of each?", never the same "какие именно?" again.
                    open_["mix"] = True
                    continue
                elif match.status == MatchStatus.MULTIPLE:
                    new_pending.append(
                        {
                            # ``match.generic``: the text named a group, whatever words stood around
                            # it ("Обычная стандартная коробка") — the question is "какие именно?"
                            "kind": PENDING_GENERIC if match.generic or is_generic_mention(text) else PENDING_AMBIGUOUS,
                            "product_text": text,
                            "quantity": mention.quantity,
                            "product_id": None,
                            "name": None,
                            "options": [candidate for candidate in match.candidates if candidate in by_id],
                            "comment": mention.comment,
                            "position": position,
                            # a mix the bot could not assemble (no count, or it does not divide):
                            # then the question is not "какие именно?" but "сколько штук?" (03 §1.3)
                            "mix": is_mix_mention(text),
                        }
                    )
                    continue
            if product is None:
                if text:
                    unknown.append(text)
                continue
            resolved.append(
                {
                    "product_id": product.id,
                    "name": product.name,
                    "quantity": mention.quantity,
                    "comment": mention.comment,
                    "position": position,
                }
            )
        new_pending = _split_totals(new_pending, resolved)
        for entry in new_pending:
            del entry["position"]

        if unknown:
            turn.notes["unknown_products"] = unknown
            turn.notes["available_products"] = [_product_fact(product) for product in products]

        if mode == ItemsMode.REMOVE:
            specs = [{"product_id": entry["product_id"], "quantity": entry["quantity"]} for entry in resolved]
            if specs:
                self._write_items(turn, order, specs, "remove")
            removed = {entry["product_id"] for entry in resolved}
            state.pending_items = [entry for entry in state.pending_items if entry.get("product_id") not in removed]
            return

        choice_pending = [
            entry for entry in state.pending_items if entry["kind"] in (PENDING_GENERIC, PENDING_AMBIGUOUS)
        ]
        if choice_pending and resolved:
            if mode == ItemsMode.SET:
                mode = ItemsMode.ADD  # an answer to "какие именно?" adds to the order, whatever the model called it
            _distribute_quantities(choice_pending, resolved)
            state.pending_items = [entry for entry in state.pending_items if entry not in choice_pending]
        if mode == ItemsMode.REPLACE:
            for entry in resolved:
                entry["quantity"] = entry["quantity"] or 1

        answered = {entry["product_id"] for entry in resolved if entry["quantity"] is not None}
        state.pending_items = [
            entry
            for entry in state.pending_items
            if not (entry["kind"] == PENDING_QUANTITY and entry.get("product_id") in answered)
        ]
        for entry in resolved:
            if entry["quantity"] is not None:
                continue
            if any(
                p["kind"] == PENDING_QUANTITY and p.get("product_id") == entry["product_id"]
                for p in state.pending_items
            ):
                continue
            state.pending_items.append(
                {
                    "kind": PENDING_QUANTITY,
                    "product_text": entry["name"],
                    "quantity": None,
                    "product_id": entry["product_id"],
                    "name": entry["name"],
                    "options": [],
                    "comment": entry["comment"],
                }
            )
        known = {(entry["kind"], entry.get("product_text")) for entry in state.pending_items}
        state.pending_items.extend(
            entry for entry in new_pending if (entry["kind"], entry["product_text"]) not in known
        )

        ready = [
            {"product_id": entry["product_id"], "quantity": entry["quantity"], "comment": entry["comment"]}
            for entry in resolved
            if entry["quantity"] is not None
        ]
        if ready or (mode == ItemsMode.REPLACE and resolved):
            write_mode = mode.value if mode in (ItemsMode.REPLACE, ItemsMode.SET) else ItemsMode.ADD.value
            self._write_items(turn, order, ready, write_mode)

    def _write_items(self, turn: _Turn, order: Order, specs: list[dict[str, Any]], mode: str) -> None:
        try:
            turn.tools.update_order_draft(order, items=specs, items_mode=mode)
        except BusinessRuleError as exc:
            if exc.code != "product_unavailable":
                raise
            # A product was switched off between reading the catalog and writing the draft. The check
            # runs before anything is changed, so the session needs no rollback.
            log_event(logger, "dialog.items_rejected", level=logging.WARNING, order_id=order.id, code=exc.code)
            found = self.products.get_many(spec["product_id"] for spec in specs).values()
            turn.notes["unknown_products"] = [
                product.name for product in found if not product.is_active or product.deleted_at is not None
            ]
            turn.catalog = None
            turn.notes["available_products"] = [_product_fact(product) for product in self._catalog(turn)]

    # ------------------------------------------------------------------ fields

    def _apply_fields(self, turn: _Turn, order: Order, entities: Entities) -> bool:
        """Date/time, type, payment, comment, name/phone, delivery block. Returns "the address changed"."""
        changes: dict[str, Any] = {}
        self._apply_slot(turn, order, entities, changes)

        target_type = order.delivery_type
        if entities.delivery_type is not None:
            changes["delivery_type"] = entities.delivery_type
            target_type = entities.delivery_type
        elif entities.address and order.delivery_type is None:
            changes["delivery_type"] = DeliveryType.DELIVERY  # an address is given only for a delivery
            target_type = DeliveryType.DELIVERY
        if entities.payment_method is not None:
            changes["payment_method"] = entities.payment_method
        if entities.comment and not self._restates_items(turn, entities.comment):
            existing = (order.comment or "").strip()
            if entities.comment not in existing:
                changes["comment"] = f"{existing}; {entities.comment}" if existing else entities.comment

        profile: dict[str, Any] = {}
        if entities.customer_name:
            profile["name"] = entities.customer_name
        phone = self._phone(turn, entities.phone)
        if phone:
            profile["phone"] = phone

        delivery: dict[str, Any] = {}
        if target_type == DeliveryType.DELIVERY:
            if entities.address:
                delivery["address_raw"] = entities.address
                current = order.delivery
                if current is not None and normalize_fold(current.address_raw) != normalize_fold(entities.address):
                    # The parts of the previous address (microdistrict, house…) describe the previous place:
                    # left in place they would be geocoded instead of the new text.
                    delivery.update(dict.fromkeys(ADDRESS_PART_FIELDS))
            if entities.recipient_name:
                delivery["recipient_name"] = entities.recipient_name
            recipient_phone = self._phone(turn, entities.recipient_phone)
            if recipient_phone:
                delivery["recipient_phone"] = recipient_phone
            if entities.courier_comment:
                delivery["courier_comment"] = entities.courier_comment

        if profile:
            self.customer_service.set_profile(turn.customer, **profile)
        if changes or delivery:
            if delivery:
                changes["delivery"] = delivery
            turn.tools.update_order_draft(order, **changes)
        self._default_recipient(turn, order)
        return "address_raw" in delivery

    def _apply_slot(self, turn: _Turn, order: Order, entities: Entities, changes: dict[str, Any]) -> None:
        """03 §1.3: a slot earlier than the lead time or beyond the horizon is not written to the draft."""
        new_date = date.fromisoformat(entities.delivery_date) if entities.delivery_date else None
        new_time = time.fromisoformat(entities.delivery_time) if entities.delivery_time else None
        if new_date is None and new_time is None:
            return
        candidate_date = new_date or order.delivery_date
        candidate_time = new_time or order.delivery_time
        problems = OrderValidator.slot_problems(candidate_date, candidate_time, turn.business)
        if problems:
            self._note_timing(turn, problems[0], candidate_date)
            day_is_possible = not OrderValidator.slot_problems(candidate_date, None, turn.business)
            hour_problems = {TOO_SOON, OUT_OF_HOURS}
            keeps_date = set(problems) <= hour_problems and new_time is not None and day_is_possible
            if keeps_date:
                # "18 сентября к 16:00" when 17:00 is the earliest, "завтра в 23:30" when the bakery closes
                # at 20:00: the day stays, only the hour is asked again.
                turn.notes["timing_keeps_date"] = True
                if new_date is not None:
                    changes[DELIVERY_DATE] = new_date
            elif new_date is not None and order.delivery_date is not None:
                # The customer moved the order to a day we refused: the old day is not wanted any more
                # either — cleared, so the date is asked again instead of silently kept in the summary.
                changes[DELIVERY_DATE] = None
            if new_time is not None and order.delivery_time is not None:
                changes[DELIVERY_TIME] = None  # same for an hour that replaced the stored one
            return
        if new_date is not None:
            changes["delivery_date"] = new_date
        if new_time is not None:
            changes["delivery_time"] = new_time

    def _restates_items(self, turn: _Turn, text: str) -> bool:
        """ "Я же сказал всего 5 синамонов, 3 ягодных и 2 фисташковых" is the item list again, not a wish
        for the order: a "comment" naming two or more counted products is not written to the order."""
        tokens = normalize_fold(text).split()
        matcher = ProductMatcher(self._catalog(turn))
        counted = sum(
            1
            for number, word in zip(tokens, tokens[1:], strict=False)
            if number.isdigit() and (is_generic_mention(word) or matcher.match(word))
        )
        return counted >= 2

    def _phone(self, turn: _Turn, raw: str | None) -> str | None:
        if not raw:
            return None
        normalized = normalize_phone(raw)
        if normalized is None:
            turn.notes["phone_invalid"] = True
        return normalized

    def _default_recipient(self, turn: _Turn, order: Order) -> None:
        delivery = order.delivery
        name = (turn.customer.name or "").strip()
        if order.delivery_type != DeliveryType.DELIVERY or delivery is None or not name:
            return
        if (delivery.recipient_name or "").strip():
            return
        turn.tools.update_order_draft(order, delivery={"recipient_name": name})

    def _note_answers(self, turn: _Turn, result: UnderstandingResult, language: str) -> None:
        """ "Хочу 2 коробки на завтра — а доставка платная?": a question asked together with order data is
        answered in the same reply (facts ``answers``: FAQ entries chosen by the model, delivery or
        payment settings) instead of being dropped for the next question. No data → the manager is
        alerted (SPEC §40) and the reply says so; the order goes on either way."""
        intents = set(result.all_intents)
        # "Можно подписать открытку? Хочу 6 шт…": the owner's exact phrase finds the answer even when
        # the model saw only the order (audit 23.09.2026); a single keyword only with an FAQ intent.
        asked_faq = self._faq_items(result, turn.text, keywords=True if Intent.FAQ in intents else "phrases")
        # "4 классических и пирожки есть?" — a food we do not make, asked on the way (03 §1.4).
        noted = {word.casefold() for word in turn.notes.get("unknown_products") or []}
        unknown = [word for word in self._asked_products(turn, result)[1] if word.casefold() not in noted]
        queries = intents & {Intent.FAQ, Intent.DELIVERY_QUERY, Intent.PAYMENT_QUERY}
        if not asked_faq and not queries and not unknown:
            return
        answers: dict[str, Any] = {}
        if asked_faq:
            answers["faq"] = [_faq_fact(item, language) for item in asked_faq]
        business = turn.business
        if Intent.DELIVERY_QUERY in intents:
            answers["delivery_info"] = business.delivery_info_text.strip() or None
            answers["pickup_address"] = business.pickup_address.strip() or None
            answers["working_hours"] = business.working_hours.strip() or None
        if Intent.PAYMENT_QUERY in intents:
            answers["payment_methods"] = business.payment_methods_text.strip() or None
            answers["prepayment"] = self._prepayment(turn)
        answers = {key: value for key, value in answers.items() if value}
        if (asked_faq or queries) and not answers:
            answers = {"need_manager": "payment" if queries == {Intent.PAYMENT_QUERY} else True}
            turn.conversation.needs_attention = True
            turn.actions.append("needs_manager")
        if unknown:
            answers["unknown_products"] = unknown
        turn.notes[ANSWERS_FACT] = answers

    def _note_timing(self, turn: _Turn, problem: str, problem_date: date | None = None) -> None:
        turn.notes["timing_problem"] = problem
        turn.notes["min_lead_time_hours"] = turn.business.min_lead_time_hours
        turn.notes["max_days_ahead"] = turn.business.max_days_ahead
        if problem_date is not None:
            turn.notes["problem_date"] = problem_date.isoformat()
        if problem == TOO_SOON:
            earliest = _earliest_slot(turn.business)
            turn.notes.update(earliest)
            turn.state.offered_slot = {"date": earliest["earliest_date"], "time": earliest["earliest_time"]}
        if problem == OUT_OF_HOURS:
            start, end = turn.business.order_hours_start, turn.business.order_hours_end
            turn.notes["order_hours_start"] = start.strftime("%H:%M") if start is not None else None
            turn.notes["order_hours_end"] = end.strftime("%H:%M") if end is not None else None
        if problem == CLOSED_DAY and problem_date is not None:
            # "В субботу мы не работаем — ближайший рабочий день понедельник."
            open_day = next_open_day(problem_date + timedelta(days=1), turn.business)
            turn.notes["closed_weekday"] = problem_date.weekday()
            turn.notes["next_open_date"] = open_day.isoformat()
            turn.notes["next_open_weekday"] = open_day.weekday()

    def _note_quantity(self, turn: _Turn, order: Order) -> None:
        """03 §1.3: the order total must fit the packing rule ("у нас либо 4, либо 8").

        Nothing is noted while the items themselves are still being clarified — one question at a time.
        """
        turn.notes.pop("quantity_problem", None)
        if turn.state.pending_items:
            return
        problems = OrderValidator.quantity_problems(order, turn.business)
        if not problems:
            return
        total = order_quantity(order)
        lower, upper = allowed_quantities_around(total, turn.business)
        turn.notes["quantity_problem"] = problems[0]
        turn.notes["quantity_total"] = total
        turn.notes["quantity_step"] = turn.business.order_quantity_step
        turn.notes["quantity_min"] = turn.business.min_order_quantity
        turn.notes["quantity_examples"] = allowed_quantity_examples(turn.business)
        turn.notes["quantity_lower"] = lower
        turn.notes["quantity_upper"] = upper

    # ------------------------------------------------------------------ address and map pin (03 §7)

    def _geocode(self, turn: _Turn, order: Order, language: str) -> DialogOutcome | None:
        delivery = order.delivery
        if delivery is None or not (delivery.address_raw or "").strip():
            return None
        town = _other_town(delivery.address_raw)
        if town is not None:
            # "Душанбе, Рудаки 45" must not be looked up in Khujand, where a Rudaki 45 exists too: the
            # courier would drive to the wrong city. Delivery out of town is by arrangement (FAQ
            # «Доставляете ли за город?»), so the manager confirms it; the customer can still pin it.
            turn.notes["out_of_town"] = town
            turn.conversation.needs_attention = True
            turn.actions.append("out_of_town")
            # Looked at, not found in our city: the order may go on without the point (03 §7), and
            # the operator sees the delivery without coordinates.
            delivery.geocode_status = GeocodeStatus.NOT_FOUND
            return self._address_clarify(turn, order, language)
        GeocodingService(self.db, geocoder=self._geocoder, settings=self.settings).geocode_delivery(delivery)
        if delivery.has_coordinates and delivery.geocode_status in USABLE_GEOCODE_STATUSES:
            turn.state.address_candidates = []
            turn.actions.append("address_geocoded")
            return None
        return self._address_clarify(turn, order, language)

    def _address_clarify(
        self, turn: _Turn, order: Order, language: str, *, reset_attempts: bool = True
    ) -> DialogOutcome:
        delivery = order.delivery
        state = turn.state
        candidates = _address_candidates(delivery) if delivery is not None else []
        approximate = _approximate_place(delivery) if delivery is not None and not candidates else None
        state.address_candidates = candidates
        state.awaiting = AWAITING_ADDRESS_CHOICE if candidates else AWAITING_MISSING_FIELDS
        state.summary_hash = None
        if delivery is not None and delivery.geocode_status == GeocodeStatus.FAILED:
            turn.conversation.needs_attention = True  # 03 §7: the operator checks the address
        self._reopen(order)
        missing = OrderValidator.missing_fields(order)
        state.missing_fields = missing
        # 03 §1.3: the point is not a blocker once the address was looked up — the next questions
        # follow at once, and a complete order may simply go on ("дальше") to the summary.
        fields = self._askable_fields(turn, missing)[:MAX_QUESTIONS]
        facts = {
            "candidates": [candidate["formatted"] for candidate in candidates],
            "approximate": approximate,
            "link": self._location_link(delivery) if delivery is not None else None,
            "status": delivery.geocode_status.value if delivery is not None else None,
            "then_summary": not fields,
            "out_of_town": turn.notes.get("out_of_town"),
            **self._answer_facts(turn),
        }
        turn.actions.append("address_clarify")
        plan = ReplyPlan(ReplyKind.ADDRESS_CLARIFY, language, facts, fields)
        return self._finish(turn, plan, reset_attempts=reset_attempts)

    def _apply_address_choice(self, turn: _Turn, order: Order, number: int, language: str) -> DialogOutcome:
        state = turn.state
        candidate = state.address_candidates[number - 1]
        delivery = order.delivery
        if delivery is None:
            state.address_candidates, state.awaiting = [], None
            return self._continue_order(turn, order, language)
        try:
            DeliveryService(self.db).apply_location(
                delivery, candidate["lat"], candidate["lng"], LocationSource.GEOCODER, candidate["formatted"]
            )
        except BusinessRuleError:  # validated before anything is set: no rollback needed
            return self._address_clarify(turn, order, language)
        self.db.commit()
        state.address_candidates, state.awaiting = [], None
        turn.actions.append("address_chosen")
        return self._continue_order(turn, order, language)

    def _location_link(self, delivery: Delivery) -> str:
        service = LocationService(self.db, self.settings)
        return service.link_url(service.create_link(delivery))

    # ------------------------------------------------------------------ missing data / summary (05 §5.7.4)

    def _continue_order(
        self, turn: _Turn, order: Order, language: str, *, reset_attempts: bool = True
    ) -> DialogOutcome:
        state = turn.state
        # The recipient is never asked while the customer's name is known (``_askable_fields``), so it
        # must be defaulted on every path here — also after "ок", a map pin or a name set by staff.
        self._default_recipient(turn, order)
        missing = OrderValidator.missing_fields(order)
        item_questions = bool(state.pending_items)
        askable = self._askable_fields(turn, missing)
        self._note_quantity(turn, order)
        blocking_notes = any(
            turn.notes.get(key)
            for key in ("unknown_products", "timing_problem", "quantity_problem", "phone_invalid")
        )

        if item_questions or askable or blocking_notes:
            self._reopen(order)
            state.awaiting = AWAITING_MISSING_FIELDS
            state.summary_hash = None
            state.missing_fields = missing
            fields = ["items"] if item_questions else askable[:MAX_QUESTIONS]
            facts = self._ask_facts(turn, order)
            hint = render(ReplyKind.ASK_MISSING, language, facts, fields)
            plan = ReplyPlan(ReplyKind.ASK_MISSING, language, facts, fields, question_hint=hint)
            return self._finish(turn, plan, reset_attempts=reset_attempts)

        if "location" in missing:
            return self._address_clarify(turn, order, language, reset_attempts=reset_attempts)

        problems = OrderValidator.timing_problems(order, turn.business)
        if problems:
            # The slot was fine when written but time has passed since (a stale draft).
            self._note_timing(turn, problems[0], order.delivery_date)
            self._clear_slot(turn, order, problems[0])
            return self._continue_order(turn, order, language, reset_attempts=reset_attempts)

        if order.status == OrderStatus.NEW:
            self.order_service.change_status(order, OrderStatus.WAITING_CONFIRMATION, actor_type=ActorType.AI)
        state.awaiting = AWAITING_CONFIRMATION
        state.missing_fields = []
        state.pending_items = []
        state.summary_hash = order_content_hash(order)
        turn.actions.append("summary_shown")
        facts = self._summary_facts(turn, order)
        return self._finish(turn, ReplyPlan(ReplyKind.ORDER_SUMMARY, language, facts), reset_attempts=reset_attempts)

    def _askable_fields(self, turn: _Turn, missing: list[str]) -> list[str]:
        """Missing fields worth a question now: the map point is asked with a link, and the recipient
        is not asked while the customer's own name is known or still being asked (defaulted later)."""
        fields = [name for name in missing if name != "location"]
        if "recipient_name" in fields and ("customer_name" in fields or (turn.customer.name or "").strip()):
            fields.remove("recipient_name")
        return fields

    def _clear_slot(self, turn: _Turn, order: Order, problem: str) -> None:
        still_bad = OrderValidator.slot_problems(order.delivery_date, None, turn.business)
        if problem in (TOO_FAR, DATE_PAST, CLOSED_DAY) or (problem == TOO_SOON and still_bad):
            turn.tools.update_order_draft(order, **{DELIVERY_DATE: None, DELIVERY_TIME: None})
        else:
            turn.tools.update_order_draft(order, **{DELIVERY_TIME: None})

    def _reopen(self, order: Order) -> None:
        """A draft that is being asked about again is not waiting for confirmation any more."""
        if order.status == OrderStatus.WAITING_CONFIRMATION:
            self.order_service.change_status(order, OrderStatus.NEW, actor_type=ActorType.AI)

    def _confirm(self, turn: _Turn, order: Order, language: str) -> DialogOutcome:
        """03 §5: ``YES`` + unchanged content hash + complete data + a valid slot → CONFIRMED."""
        state = turn.state
        if OrderValidator.missing_fields(order) or state.summary_hash != order_content_hash(order):
            log_event(logger, "dialog.confirmation_outdated", order_id=order.id)
            return self._continue_order(turn, order, language)
        problems = OrderValidator.timing_problems(order, turn.business)
        if problems:
            self._note_timing(turn, problems[0], order.delivery_date)
            self._clear_slot(turn, order, problems[0])
            return self._continue_order(turn, order, language)
        if OrderValidator.quantity_problems(order, turn.business):
            # The packing rule was broken by staff or by an older draft: ask again instead of confirming.
            return self._continue_order(turn, order, language)
        try:
            turn.tools.confirm_order(order, ConfirmationDecision.YES)
        except BusinessRuleError as exc:  # rules are checked before the status changes: no rollback needed
            log_event(logger, "dialog.confirmation_rejected", level=logging.WARNING, order_id=order.id, code=exc.code)
            return self._continue_order(turn, order, language)
        facts = {
            "order_id": order.id,
            "delivery_type": order.delivery_type.value if order.delivery_type else None,
            "delivery_date": order.delivery_date.isoformat() if order.delivery_date else None,
            "delivery_time": order.delivery_time.strftime("%H:%M") if order.delivery_time else None,
            "total": f"{money(order.total_amount):.2f}",
        }
        prepayment = self._prepayment(turn)
        if prepayment is not None:
            # 03 §3: the wallet and the sum are asked for right after "Да"; the receipt comes back here.
            expected = expected_prepayment(order.total_amount, order.paid_amount, prepayment["percent"])
            if expected > 0:
                facts["prepayment"] = {**prepayment, "amount": f"{expected:.2f}"}
        state.close_draft()
        turn.actions.append("order_confirmed")
        return self._finish(turn, ReplyPlan(ReplyKind.ORDER_CONFIRMED, language, facts))

    # ------------------------------------------------------------------ facts

    def _summary_facts(self, turn: _Turn, order: Order) -> dict[str, Any]:
        delivery = order.delivery
        customer = turn.customer
        is_delivery = order.delivery_type == DeliveryType.DELIVERY
        recipient_name = (delivery.recipient_name if is_delivery and delivery is not None else None) or customer.name
        recipient_phone = (delivery.recipient_phone if delivery is not None else None) or customer.phone
        return {
            "order_id": order.id,
            "items": [
                {
                    "name": item.product_name,
                    "quantity": item.quantity,
                    "unit": item.product.unit if item.product is not None else None,
                    "comment": item.comment,
                    "unit_price": f"{money(item.unit_price):.2f}",
                    "total_price": f"{money(item.total_price):.2f}",
                }
                for item in order.items
            ],
            "delivery_date": order.delivery_date.isoformat() if order.delivery_date else None,
            "delivery_time": order.delivery_time.strftime("%H:%M") if order.delivery_time else None,
            "delivery_type": order.delivery_type.value if order.delivery_type else None,
            "address": (delivery.address_raw or None) if is_delivery and delivery is not None else None,
            "pickup_address": None if is_delivery else (turn.business.pickup_address.strip() or None),
            "recipient_name": recipient_name,
            "recipient_phone": recipient_phone,
            "payment_method": order.payment_method.value if order.payment_method else None,
            "comment": order.comment,
            "total": f"{money(order.total_amount):.2f}",
            **self._answer_facts(turn),
        }

    def _side_answers(self, turn: _Turn, plan: ReplyPlan) -> dict[str, Any]:
        """The other questions of the message, answered under the main answer (fact ``answers``).

        "Сколько стоит коробка и есть ли доставка?", "Трайфл и круассаны есть? И можно у вас
        посидеть?" — each part is answered from the data (FAQ entries the model chose, delivery and
        payment settings, the foods we do not make) instead of the reply covering the first question
        only (audit 23.09.2026). A part without data adds "уточню у менеджера" and alerts the manager.
        """
        result = turn.result
        covered = _SIDE_COVERED.get(plan.kind)
        if result is None or covered is None or ANSWERS_FACT in plan.facts:
            return {}
        intents = set(result.all_intents) - covered
        answers: dict[str, Any] = {}
        if plan.kind not in (ReplyKind.PRODUCT_INFO, ReplyKind.UNKNOWN_PRODUCT):
            unknown = self._asked_products(turn, result)[1]
            if unknown:
                answers["unknown_products"] = unknown
                answers["available_products"] = [_product_fact(product) for product in self._catalog(turn)]
        if not plan.facts.get("faq"):
            asked_faq = self._faq_items(result, turn.text, keywords="phrases")
            if asked_faq:
                answers["faq"] = [_faq_fact(item, plan.language) for item in asked_faq]
        business = turn.business
        unanswered: list[str] = []
        if Intent.DELIVERY_QUERY in intents and "faq" not in answers:
            delivery = {
                "delivery_info": business.delivery_info_text.strip() or None,
                "pickup_address": business.pickup_address.strip() or None,
                "working_hours": business.working_hours.strip() or None,
            }
            topic = self._topic_faq(turn, "доставк") if plan.language == "tg" else None
            if topic is not None:
                # The settings texts are Russian only; the owner's delivery answer exists in Tajik.
                answers["faq"] = [_faq_fact(topic, plan.language)]
            else:
                answers.update({key: value for key, value in delivery.items() if value})
                if not any(delivery.values()):
                    unanswered.append("delivery")
        payment_asked = Intent.PAYMENT_QUERY in intents or (
            plan.kind != ReplyKind.PAYMENT_INFO and _payment_topic(turn.text)
        )
        if payment_asked and "faq" not in answers:
            payment = {
                "payment_methods": business.payment_methods_text.strip() or None,
                "prepayment": self._prepayment(turn),
            }
            answers.update({key: value for key, value in payment.items() if value})
            if not any(payment.values()):
                unanswered.append("payment")
        if unanswered and plan.kind != ReplyKind.NEED_MANAGER:
            # "Про оплату уточню у менеджера": the customer knows which of the questions waits.
            answers["need_manager"] = unanswered[0] if len(unanswered) == 1 else True
            turn.conversation.needs_attention = True
            turn.actions.append("needs_manager")
        return answers

    def _topic_faq(self, turn: _Turn, stem: str) -> FaqItem | None:
        """The owner's first FAQ entry about a topic ("доставк") that has a Tajik answer."""
        return next(
            (item for item in self.faq.list() if stem in normalize_fold(item.question) and item.answer_tg),
            None,
        )

    @staticmethod
    def _answer_facts(turn: _Turn) -> dict[str, Any]:
        """The ``answers`` fact (``_note_answers``) for replies whose facts are not ``turn.notes``."""
        return {ANSWERS_FACT: turn.notes[ANSWERS_FACT]} if ANSWERS_FACT in turn.notes else {}

    def _note_prices(self, turn: _Turn, order: Order, result: UnderstandingResult) -> None:
        """ "Хочу медовик и 5 эклеров, сколько будет стоить?" — the question is answered while the draft is
        being filled: catalog prices of the named products and, once every quantity is known, the
        total of the draft (prices always from the database)."""
        by_id = {product.id: product for product in self._catalog(turn)}
        ids = [
            *result.product_ids_asked,
            *(item.product_id for item in order.items),
            *(entry.get("product_id") for entry in turn.state.pending_items),
        ]
        products = [by_id[product_id] for product_id in dict.fromkeys(ids) if product_id in by_id]
        if not products:
            return
        turn.notes["prices"] = [_product_fact(product) for product in products]
        if order.items and not turn.state.pending_items:
            turn.notes["prices_total"] = f"{money(order.total_amount):.2f}"

    def _ask_facts(self, turn: _Turn, order: Order) -> dict[str, Any]:
        products = {product.id: product for product in self._catalog(turn)}
        facts: dict[str, Any] = dict(turn.notes)
        if turn.state.pending_items:
            facts["pending_items"] = [
                {
                    "kind": entry["kind"],
                    "product_text": entry.get("product_text"),
                    "quantity": entry.get("quantity"),
                    "name": entry.get("name"),
                    # the unit decides the wording of "how many?": "Сколько коробочек" for boxes
                    "unit": products[entry["product_id"]].unit if entry.get("product_id") in products else None,
                    "options": [products[option].name for option in entry.get("options") or [] if option in products],
                    "mix": bool(entry.get("mix")),
                }
                for entry in turn.state.pending_items
            ]
            if any(entry.get("mix") and entry.get("quantity") for entry in turn.state.pending_items):
                # "Или возьмём 6 — по одному каждого?" only when that total fits the packing rule.
                flavours = len(flavour_bases(self._catalog(turn)))
                if flavours > 1 and quantity_problem(flavours, turn.business) is None:
                    facts["mix_full"] = flavours
        facts["order_so_far"] = {
            "items": [
                {
                    "name": item.product_name,
                    "quantity": item.quantity,
                    "unit": item.product.unit if item.product is not None else None,
                }
                for item in order.items
            ],
            "delivery_date": order.delivery_date.isoformat() if order.delivery_date else None,
            "delivery_time": order.delivery_time.strftime("%H:%M") if order.delivery_time else None,
            "delivery_type": order.delivery_type.value if order.delivery_type else None,
        }
        return facts

    # ------------------------------------------------------------------ context helpers

    def _draft(self, turn: _Turn) -> Order | None:
        if turn.draft_loaded:
            return turn.draft
        turn.draft_loaded = True
        order_id = turn.state.draft_order_id
        order = self.orders.get(order_id) if order_id else None
        if order is None or order.status not in DRAFT_ORDER_STATUSES or order.customer_id != turn.customer.id:
            if order_id is not None:
                turn.state.forget_draft()
            order = None
        turn.draft = order
        return order

    def _catalog(self, turn: _Turn) -> list[Product]:
        if turn.catalog is None:
            turn.catalog = self.products.list()
        return turn.catalog

    def _catalog_phrases(self, turn: _Turn) -> list[str]:
        """Product names and aliases: not evidence of the message language (they are Russian)."""
        return [phrase for product in self._catalog(turn) for phrase in (product.name, *(product.aliases or []))]

    def _draft_view(self, turn: _Turn) -> dict[str, Any] | None:
        order = self._draft(turn)
        state = turn.state
        if order is None:
            return None
        delivery = order.delivery
        view: dict[str, Any] = {
            "order_id": order.id,
            "status": order.status.value,
            "items": [
                {"product_id": item.product_id, "name": item.product_name, "quantity": item.quantity}
                for item in order.items
            ],
            "delivery_date": order.delivery_date.isoformat() if order.delivery_date else None,
            "delivery_time": order.delivery_time.strftime("%H:%M") if order.delivery_time else None,
            "delivery_type": order.delivery_type.value if order.delivery_type else None,
            "address": delivery.address_raw if delivery is not None else None,
            "recipient_name": delivery.recipient_name if delivery is not None else None,
            "payment_method": order.payment_method.value if order.payment_method else None,
            "comment": order.comment,
        }
        if state.pending_items:
            view["pending_items"] = [
                {key: entry.get(key) for key in ("kind", "product_text", "quantity", "product_id", "options")}
                for entry in state.pending_items
            ]
        if state.address_candidates:
            view["address_candidates"] = [
                f"{index}. {candidate['formatted']}" for index, candidate in enumerate(state.address_candidates, 1)
            ]
        return view

    def _history(self, turn: _Turn) -> list[dict[str, str]]:
        if turn.history is not None:
            return turn.history
        recent = self.messages.recent_for_conversation(turn.conversation.id, HISTORY_LIMIT + 1)
        current_id = turn.message.id if turn.message is not None else None
        history: list[dict[str, str]] = []
        for message in recent:
            if current_id is not None and message.id >= current_id:
                continue
            if not (message.text or "").strip():
                continue
            if message.direction == MessageDirection.INCOMING:
                role = "customer"
            elif message.sender == MessageSender.OPERATOR:
                role = "operator"
            else:
                role = "assistant"
            history.append({"role": role, "text": message.text or ""})
        turn.history = history[-HISTORY_LIMIT:]
        return turn.history

    def _repeats_last_reply(self, turn: _Turn, text: str, depth: int = 1) -> bool:
        """Would this reply repeat one of the bot's last ``depth`` replies word for word?

        Sending the same paragraph twice is the shape "the bot is stuck" takes: the customer
        rephrases, a template comes back unchanged, and nothing moves. Whitespace and case do not
        make two answers different, and an empty text is not a repeat. Only a reply to the
        customer's own message counts — re-showing the summary after a map pin is not a repeat.
        ``depth=2`` catches a bot swinging between two sentences ("уточню у менеджера" / "этот
        вопрос тоже передали менеджеру").
        """
        if turn.message is None:
            return False  # a continuation the system started (a map pin, a receipt), not a re-ask
        replies = [entry for entry in reversed(self._history(turn)) if entry["role"] != "customer"][:depth]
        if not replies or replies[0]["role"] == "operator":
            return False
        squeezed = _squeeze(text)
        return bool(squeezed) and any(
            squeezed == _squeeze(entry["text"]) for entry in replies if entry["role"] == "assistant"
        )

    def _reply_context(self, turn: _Turn) -> dict[str, Any]:
        """Wording context for the reply step (05 §6): the customer's words and the recent turns —
        so the answer picks up the thread — never a source of facts."""
        return {
            "customer_message": turn.text,
            "history": self._history(turn)[-REPLY_HISTORY_LIMIT:],
            "customer_name": (turn.customer.name or "").strip() or None,
        }

    # ================================================================== finishing

    def _handoff(
        self,
        turn: _Turn,
        reason: str,
        reason_code: str,
        language: str,
        *,
        facts: dict[str, Any] | None = None,
        reset_attempts: bool = True,
    ) -> DialogOutcome:
        """03 §6: HUMAN_HANDOFF + one message on the customer's language."""
        turn.tools.request_operator(reason)
        turn.actions.append("handoff")
        if turn.assist:
            # Already waiting for the manager: the new reason is recorded (a request for a person
            # or a complaint ends the bot's answers for good), and "передаю менеджеру" is not said twice.
            return self._assist_silent(turn)
        turn.image = None  # a picture belongs to an answer, not to "передаю менеджеру"
        plan = ReplyPlan(ReplyKind.HANDOFF, language, {"reason_code": reason_code, **(facts or {})})
        return self._finish(turn, plan, handoff=True, reset_attempts=reset_attempts)

    def _keep_offered_slot(self, turn: _Turn) -> None:
        """The offered earliest slot stays open while the draft still has no time and no other day,
        and the customer has only been giving order data since: then a later "да" / "давайте" still
        means that slot (audit 23.09.2026 — the phone came in between, the offer was forgotten, and
        "да" repeated the question until the handoff). A question or anything else in between ends
        it: "а доставка есть?" → "да" is not an answer about the time."""
        slot = turn.state.offered_slot
        if slot is None:
            return
        order = self._draft(turn)
        carried_over = slot == turn.offer_at_start
        data_only = turn.result is not None and turn.result.intent in ORDER_INTENTS
        if (
            order is None
            or order.delivery_time is not None
            or (order.delivery_date is not None and order.delivery_date.isoformat() != slot["date"])
            or (carried_over and not data_only)
        ):
            turn.state.offered_slot = None

    def _finish(
        self,
        turn: _Turn,
        plan: ReplyPlan,
        *,
        handoff: bool = False,
        reset_attempts: bool = True,
    ) -> DialogOutcome:
        conversation = turn.conversation
        if turn.assist and not handoff and plan.kind not in ASSIST_KINDS:
            return self._assist_silent(turn)
        if reset_attempts:
            conversation.failed_ai_attempts = 0
        if plan.kind not in (ReplyKind.GREETING, ReplyKind.BLOCKED) and "greeting" not in plan.facts:
            greeting = detect_greeting(turn.text)
            if greeting is not None and not turn.assist:
                # "Здравствуйте, хочу 2 коробки": the greeting is answered in kind before the reply itself.
                plan = replace(plan, facts={**plan.facts, "greeting": greeting.value})
        side = self._side_answers(turn, plan)
        if side:
            plan = replace(plan, facts={**plan.facts, ANSWERS_FACT: side})
        template_only = uses_template(plan)
        if not template_only and not plan.context:
            plan = replace(plan, context=self._reply_context(turn))
        turn.state.language = plan.language
        if turn.customer.language != Language(plan.language):
            turn.customer.language = Language(plan.language)
        if turn.image:
            # Remembered only once the picture really goes with this reply (a handoff drops it).
            turn.state.price_list_sent = turn.image
        self._keep_offered_slot(turn)
        conversation.state = turn.state.to_json()
        if turn.message is not None and not turn.message.ai_processed:
            turn.message.ai_processed = True
        self.db.commit()

        wording = not template_only and self.settings.LLM_REPLY_WORDING_ENABLED and not turn.no_model
        llm = self.llm if wording else None
        reply = Responder(
            llm,
            catalog_names=[product.name for product in self._catalog(turn)],
            allow_same_day=turn.business.min_lead_time_hours <= 0,
        ).generate_reply(plan)
        depth = 2 if plan.kind in (ReplyKind.NEED_MANAGER, ReplyKind.UNKNOWN_PRODUCT) else 1
        if not handoff and self._repeats_last_reply(turn, reply.text, depth):
            # The customer asked again and would get the same words back: the bot has nothing to add,
            # so a person takes over instead of repeating itself (dialog #42, 21.09.2026).
            log_event(
                logger,
                "dialog.reply_repeated",
                conversation_id=conversation.id,
                message_id=turn.message.id if turn.message is not None else None,
                kind=plan.kind.value,
            )
            if turn.assist:
                return self._assist_silent(turn)
            if plan.kind in AGAIN_KINDS and not plan.facts.get("again"):
                # Not a stuck bot: a second "уточню у менеджера" (the manager is already alerted),
                # "алло" twice, "пирожков нет" asked again. Said in other words, and the dialog stays
                # with the bot — two such questions in a row used to silence it (dialog #3, 23.09.2026).
                again = replace(plan, facts={**plan.facts, "again": True})
                return self._finish(turn, again, reset_attempts=reset_attempts)
            if plan.kind not in (ReplyKind.GREETING, ReplyKind.SMALL_TALK):
                return self._handoff(turn, REASON_REPEATED_REPLY, "repeated_reply", plan.language)
        log_event(
            logger,
            "dialog.reply",
            conversation_id=conversation.id,
            message_id=turn.message.id if turn.message is not None else None,
            kind=plan.kind.value,
            source=reply.source.value,
            language=plan.language,
            handoff=handoff,
            actions=list(turn.actions),
            awaiting=turn.state.awaiting,
            draft_order_id=turn.state.draft_order_id,
        )
        return DialogOutcome(reply=reply, handoff=handoff, actions=list(turn.actions), image=turn.image)


# ====================================================================== module helpers


def _squeeze(text: str) -> str:
    """Text as the customer sees it: case and spacing do not make two answers different."""
    return " ".join((text or "").split()).casefold()


def _asks_which(text: str) -> bool:
    """"Какие у вас есть?", "кадом намудаш ҳаст?" — the catalog answers this, not the FAQ."""
    padded = f" {normalize_fold(text)} "
    return any(f" {word} " in padded for word in WHICH_WORDS)


#: Words of an order: without the model such a message is the manager's, never a price answer.
_ORDER_WORDS_RE = re.compile(r"\b(?:хочу|хотим|закаж\w*|заказ\w*|запиш\w*|оформ\w*|мегирам|мехохам|лозим|дайте)\b")

#: Words that make a "какие…?" a question about the assortment: "какие вкусы?", "что есть?", "кадом намуд?".
_ASSORTMENT_RE = re.compile(
    r"\b(?:вкус\w*|вид\w*|есть|ассортимент\w*|синнамон\w*|синамон\w*|булочк\w*|намуд\w*|хаст\w*|доред|бор)\b"
)

_PAYMENT_RE = re.compile(
    r"\b(?:предоплат\w*|оплат\w*|оплачу|оплачивать|заплат\w*|наличн\w*|наличк\w*|перевод\w*|переведу|перевести|"
    r"картой|карточк\w*|пардохт\w*|пешпардохт\w*|накд|алиф\w*|эсхат\w*|аванс\w*|задат\w*|"
    # "Дс есть?", "Ман пулаша DC кунам ми" — the Dushanbe City wallet (archive, 24.09)
    r"дс|диси|dc|душанбе\s+сити)\b"
)


def _payment_topic(text: str) -> bool:
    """"Предоплата нужна?", "можно наличными?", "переводом можно?", "пардохт" — a payment question."""
    return _PAYMENT_RE.search(normalize_fold(text)) is not None


_TODAY_RE = re.compile(r"\b(?:сегодня|щас|сейчас|имруз|хозир|хозер|баного)\b")
_OTHER_DAY_RE = re.compile(
    r"\b(?:завтра|послезавтра|пагох|пага|пасфардо|басфардо|фардо|понедельник\w*|вторник\w*|сред[уа]|четверг\w*|"
    r"пятниц\w*|суббот\w*|воскресень\w*|душанбе|сешанбе|чоршанбе|панчшанбе|чумъа|шанбе|якшанбе)\b"
)


def _not_about_today(items: list[FaqItem], text: str) -> list[FaqItem]:
    """Without the answers about ordering for today when the message is about another day: «На завтра
    1 коробку возможно?» got «На сегодня, к сожалению, не получится» (archive replay, 24.09.2026). An
    entry is about today when the owner's own question says so."""
    folded = normalize_fold(text)
    if _TODAY_RE.search(folded) or not _OTHER_DAY_RE.search(folded):
        return items
    return [item for item in items if not _TODAY_RE.search(normalize_fold(item.question))]


def _price_question(text: str) -> bool:
    """"Сколько стоит?", "цена за шт?", "нархаш чанд?" — the message asks the price (``_PRICE_RE``)."""
    return _PRICE_RE.search(normalize_fold(text)) is not None


def _unique_words(words: list[str]) -> list[str]:
    """The customer's words once each, case aside, in the order they came."""
    seen: set[str] = set()
    result: list[str] = []
    for word in words:
        key = " ".join(word.casefold().split())
        if key and key not in seen:
            seen.add(key)
            result.append(word)
    return result


def _matched_stock_words_only(item: FaqItem, text: str) -> bool:
    """True when the entry was matched only by "в наличии"-style words (:data:`STOCK_KEYWORDS`)."""
    padded = f" {normalize_fold(text)} "
    matched = {key for keyword in item.keywords or [] if (key := normalize_fold(keyword)) and f" {key} " in padded}
    return bool(matched) and matched <= STOCK_KEYWORDS


def _has_order_fields(entities: Entities) -> bool:
    return _has_order_data(entities) or entities.delivery_type is not None or entities.payment_method is not None


def _has_order_data(entities: Entities) -> bool:
    """Concrete order data — an address, a date, a phone… — unlike ``delivery_type`` / ``payment_method``,
    which the model also fills in from the topic of a question ("доставка есть?")."""
    values = (
        entities.delivery_date,
        entities.delivery_time,
        entities.address,
        entities.recipient_name,
        entities.recipient_phone,
        entities.courier_comment,
        entities.customer_name,
        entities.phone,
    )
    return any(value is not None for value in values)


def _clip(value: str | None, length: int) -> str | None:
    """What the model read, cut to the column (PostgreSQL enforces ``String(n)``)."""
    return value[:length] if value else None


def _brings_items(entities: Entities) -> bool:
    """The message names products to order (not a removal)."""
    return bool(entities.items) and entities.items_mode in (ItemsMode.ADD, ItemsMode.REPLACE, ItemsMode.SET)


def _is_abandoned(order: Order) -> bool:
    """Nobody touched the draft for ``DRAFT_ABANDON_HOURS`` (the customer, the bot or staff)."""
    return ensure_utc(order.updated_at) < now_utc() - timedelta(hours=DRAFT_ABANDON_HOURS)


def _quantity_answer(state: DialogState, mentions: list[Any], matcher: ProductMatcher) -> int | None:
    """ "2 коробки" / "две коробки" to "Сколько коробочек нужно: Шоколадные синнамоны?" is that product's
    quantity, not two more boxes to choose: the only open question is a quantity, the message is one
    counted category word, and the word fits the product (a box for a product sold by the box). An id
    the model attached to the category word is ignored here like everywhere else (``_apply_items``)."""
    pending = state.pending_items
    if len(pending) != 1 or pending[0]["kind"] != PENDING_QUANTITY or len(mentions) != 1:
        return None
    mention = mentions[0]
    text = mention.product_text or ""
    if mention.quantity is None or not is_generic_mention(text):
        return None
    product_id = pending[0].get("product_id")
    return product_id if product_id is not None and product_id in matcher.match(text).candidates else None


def _pending_total(state: DialogState) -> int | None:
    """The count a pending "какие именно?" is still waiting for ("коробка из 6 шт" → 6)."""
    for entry in reversed(state.pending_items):
        if entry["kind"] != PENDING_GENERIC:
            continue
        try:
            total = int(entry.get("quantity"))
        except (TypeError, ValueError):
            continue
        if total > 0:
            return total
    return None


#: "кроме фисташкового", "ба ғайр аз шоколадӣ", "бе фисташка" — a flavour left out of the mix.
_EXCEPT_RE = re.compile(r"\b(?:кроме|без|за исключением|ба гайр аз|гайр аз|бидуни|бе)\s+(.+)$")


def _excepting_mix(text: str) -> bool:
    """"По одному виду кроме фисташкового" — a mix with a flavour left out. Only a mix: "синнамон без
    глазури" is a wish about one product, not an exclusion."""
    return is_mix_mention(text) and _EXCEPT_RE.search(normalize_fold(text)) is not None


def _without_excepted(flavours: list[Product], text: str) -> list[Product]:
    """The flavours of a mix minus the ones the customer leaves out: "по одному виду кроме
    фисташкового" is five rolls, not six (archive, 24.09.2026 — the owner: «это 5, у нас 4 или 8»)."""
    match = _EXCEPT_RE.search(normalize_fold(text))
    if match is None:
        return flavours
    excluded: set[int] = set()
    for word in match.group(1).split():
        if len(word) < 4:
            continue
        # "фисташкового" is not "Фисташковый синнамон" to the matcher (a case ending on half a name),
        # so the stem decides: the first five letters of a word of the flavour's name.
        stem = word[:5]
        named = {
            product.id
            for product in flavours
            if any(token.startswith(stem) for token in normalize_fold(product.name).split() if len(token) >= 4)
        }
        if named and len(named) < len(flavours):  # "кроме синнамонов" names them all, so none
            excluded |= named
    return [product for product in flavours if product.id not in excluded]


def _excepted_ids(products: list[Product], message: str) -> set[int]:
    """The flavours an excepting mix leaves out (empty when the message is no such mix)."""
    if not _excepting_mix(message):
        return set()
    flavours = flavour_bases(products)
    return {product.id for product in flavours} - {product.id for product in _without_excepted(flavours, message)}


def _mix_text(mention_text: str, message: str) -> str:
    """The words that describe the mix: the model may cut "можно все по одной на пробу" down to a bare
    "все" — then the whole message says it (archive replay, 24.09.2026)."""
    if is_general_mention(mention_text) and not is_mix_mention(mention_text) and is_mix_mention(message):
        return message
    return mention_text


def _open_generic(state: DialogState) -> dict[str, Any] | None:
    """The "какие именно?" still waiting for flavours with a count ("4 синнамона"), if any."""
    for entry in reversed(state.pending_items):
        if entry["kind"] == PENDING_GENERIC and entry.get("quantity"):
            return entry
    return None


def _mix_items(
    text: str, mention: Any, state: DialogState, products: list[Product], position: int
) -> list[dict[str, Any]] | None:
    """ "Микс со всеми вкусами" → one of every flavour, when the total divides evenly (03 §1.3).

    The customer is describing how the box is put together, not naming a product, so the bot builds
    it instead of asking "какие именно?" (dialog #3, 23.09.2026). The count comes from the message
    itself ("микс из 6 штук") or from the question still open ("коробка из 6 шт" → "можно микс?").
    Sizes are not mixed: a mix is made of the standard rolls, the large ones are asked for by name.
    Without a count, or when it does not divide evenly, this returns ``None`` and the bot asks which
    flavours — "6 на 4 вкуса" is the customer's choice to make, not ours.
    """
    if not is_mix_mention(text):
        return None
    flavours = _without_excepted(flavour_bases(products), text)
    # "по одному каждого" names the count itself: as many as there are flavours
    total = len(flavours) if _ONE_OF_EACH_RE.search(normalize_fold(text)) else mention.quantity or _pending_total(state)
    if len(flavours) < 2 or not total or total % len(flavours):
        return None
    each = total // len(flavours)
    return [
        {
            "product_id": product.id,
            "name": product.name,
            "quantity": each,
            "comment": mention.comment,
            "position": position,
        }
        for product in flavours
    ]


def _split_totals(pending: list[dict[str, Any]], resolved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ "5 синнамонов: 3 ягодных и 2 фисташковых" — a counted category word followed, in the same message,
    by products of that category is their total, not five more rolls to choose.

    The kinds named after it take the count: kinds without a number share it like an answer to
    "какие именно?" (``_distribute_quantities``), and when every kind has its own number, only the rest
    is still asked about ("5 синнамонов, 3 ягодных" → which 2 more). Returns the pending entries left.
    """
    kept: list[dict[str, Any]] = []
    for entry in pending:
        total = entry.get("quantity")
        parts = [
            item
            for item in resolved
            if item["position"] > entry["position"] and item["product_id"] in (entry.get("options") or [])
        ]
        if entry["kind"] != PENDING_GENERIC or total is None or not parts:
            kept.append(entry)
            continue
        unspecified = [item for item in parts if item["quantity"] is None]
        remaining = total - sum(item["quantity"] for item in parts if item["quantity"] is not None)
        if unspecified:
            _distribute_quantities([{**entry, "quantity": total}], parts)
        elif remaining >= 1:
            kept.append({**entry, "quantity": remaining})
    return kept


def _distribute_quantities(choice_pending: list[dict[str, Any]], resolved: list[dict[str, Any]]) -> None:
    """ "2 торта" → "Красный бархат и медовик": one each; "торт" ×3 → "медовик": 3 (see module docstring)."""
    quantities = [entry.get("quantity") for entry in choice_pending]
    if not quantities or any(quantity is None for quantity in quantities):
        return
    unspecified = [entry for entry in resolved if entry["quantity"] is None]
    if not unspecified:
        return
    remaining = sum(quantities) - sum(entry["quantity"] for entry in resolved if entry["quantity"] is not None)
    if len(unspecified) == 1 and remaining >= 1:
        unspecified[0]["quantity"] = remaining
    elif remaining == len(unspecified):
        for entry in unspecified:
            entry["quantity"] = 1


#: Towns and cities other than Khujand, as customers write them in an address (folded). The suburbs
#: of the FAQ («Гафуров, Бустон, Гулистон») are out of town too: delivery there is by arrangement.
_OTHER_TOWNS = (
    "душанбе",
    "бохтар",
    "куляб",
    "кулоб",
    "истаравшан",
    "ура тюбе",
    "пенджикент",
    "панджакент",
    "исфара",
    "канибадам",
    "конибодом",
    "турсунзаде",
    "вахдат",
    "гиссар",
    "хисор",
    "рогун",
    "нурек",
    "хорог",
    "бустон",
    "чкаловск",
    "гафуров",
    "гулистон",
    "кайраккум",
    "шахристан",
    "зафарабад",
    "спитамен",
    "ташкент",
    "самарканд",
)


#: A word before a town's name that makes it a street of Khujand: "ул. Б. Гафурова", "кучаи Исфара".
_STREET_MARKERS = frozenset(
    {"ул", "улица", "улице", "кучаи", "куча", "проспект", "пр", "им", "имени", "пр-т", "проезд"}
)


def _other_town(address: str | None) -> str | None:
    """The other town an address names ("Душанбе, Рудаки 45" → "Душанбе"), or ``None`` for Khujand.

    A town name used as a street name is Khujand ("ул. Б. Гафурова 12"): after a street word or an
    initial it does not count.
    """
    words = re.findall(r"[^\W\d_]+", str(address or ""))
    folded = [normalize_fold(word) for word in words]
    joined = " ".join(folded)
    for town in _OTHER_TOWNS:
        for match in re.finditer(rf"\b{town}\w*", joined):
            start = len(joined[: match.start()].split())
            previous = folded[start - 1] if start > 0 else ""
            if previous in _STREET_MARKERS or (len(previous) == 1 and previous != "г"):
                continue  # "ул. Б. Гафурова" is a street here; "г. Душанбе" is the city
            return words[start].capitalize() if start < len(words) else town.capitalize()
    return None


_COUNTRY_NAMES = frozenset({"таджикистан", "тоҷикистон", "tajikistan"})
_POSTAL_CODE_RE = re.compile(r"^\d{6}$")


def _short_place(formatted: str) -> str:
    """ "ТехМаркет, 12, улица Айни, Пахтакор, Худжанд, 735700, Таджикистан" → without the postal code and
    the country: the customer knows which country they are in."""
    parts = [part.strip() for part in formatted.split(",")]
    kept = [part for part in parts if part and part.lower() not in _COUNTRY_NAMES and not _POSTAL_CODE_RE.match(part)]
    return ", ".join(kept) or formatted.strip()


def _usable_candidates(delivery: Delivery) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for entry in delivery.geocode_candidates or []:
        try:
            lat, lng = float(entry["lat"]), float(entry["lng"])
        except (KeyError, TypeError, ValueError):
            continue
        if not is_inside_tajikistan(lat, lng):
            continue
        candidates.append(
            {
                "formatted": _short_place(str(entry.get("formatted") or "")),
                "lat": lat,
                "lng": lng,
                "precision": str(entry.get("precision") or ""),
            }
        )
    return candidates


def _address_candidates(delivery: Delivery) -> list[dict[str, Any]]:
    """House-level variants the customer can pick by number (03 §7). A street or a microdistrict
    centroid is never offered as "the" point — it is shown as ``approximate`` and centres the map."""
    if delivery.geocode_status != GeocodeStatus.AMBIGUOUS:
        return []
    houses = [c for c in _usable_candidates(delivery) if c["precision"] == PRECISION_HOUSE]
    return [{"formatted": c["formatted"], "lat": c["lat"], "lng": c["lng"]} for c in houses[:MAX_ADDRESS_CANDIDATES]]


def _approximate_place(delivery: Delivery) -> str | None:
    if delivery.geocode_status != GeocodeStatus.AMBIGUOUS:
        return None
    for candidate in _usable_candidates(delivery):
        if candidate["formatted"]:
            return candidate["formatted"]
    return None


def _earliest_slot(business: BusinessSettings) -> dict[str, Any]:
    """The first slot the bakery can take (business time, rounded up to the hour, inside the order
    hours and never on a day off) — for "самое раннее"."""
    moment = business_now() + timedelta(hours=max(0, int(business.min_lead_time_hours)))
    if moment.minute or moment.second or moment.microsecond:
        moment = moment.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    start, end = business.order_hours_start, business.order_hours_end
    opening = start or time(0, 0)
    if end is not None and moment.time() > end:
        moment = (moment + timedelta(days=1)).replace(hour=opening.hour, minute=opening.minute)
    elif start is not None and moment.time() < start:
        moment = moment.replace(hour=start.hour, minute=start.minute)
    open_day = next_open_day(moment.date(), business)
    if open_day != moment.date():
        # The next working day starts at the opening hour, not at the hour the lead time landed on.
        moment = (moment + timedelta(days=(open_day - moment.date()).days)).replace(
            hour=opening.hour, minute=opening.minute
        )
    today = business_today()
    relative = "today" if moment.date() == today else "tomorrow" if moment.date() == today + timedelta(days=1) else None
    return {
        "earliest_date": moment.date().isoformat(),
        "earliest_time": moment.strftime("%H:%M"),
        "earliest_relative": relative,
    }


def _asks_price(result: UnderstandingResult) -> bool:
    """An order message that also asks what it costs (PRODUCT_QUERY as the main or an extra purpose)."""
    return (
        result.intent == Intent.PRODUCT_QUERY
        or Intent.PRODUCT_QUERY in result.secondary_intents
        or bool(result.product_ids_asked)
    )


def _product_fact(product: Product) -> dict[str, Any]:
    view = product_view(product)
    return {key: view[key] for key in ("name", "price", "unit", "description")}


def _faq_question_for_prompt(item: FaqItem) -> str:
    return f"{item.question} / {item.question_tg}" if item.question_tg else item.question


def _faq_fact(item: FaqItem, language: str) -> dict[str, str]:
    if language == "tg" and item.answer_tg:
        return {"question": item.question_tg or item.question, "answer": item.answer_tg}
    return {"question": item.question, "answer": item.answer}


def _order_status_fact(order: Order) -> dict[str, Any]:
    return {
        "order_id": order.id,
        "status": order.status.value,
        "delivery_type": order.delivery_type.value if order.delivery_type else None,
        "delivery_date": order.delivery_date.isoformat() if order.delivery_date else None,
        "delivery_time": order.delivery_time.strftime("%H:%M") if order.delivery_time else None,
        "payment_status": order.payment_status.value,
        "total": f"{money(order.total_amount):.2f}",
    }
