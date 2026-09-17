"""Dialog orchestration: one customer message → one decided reply (docs/architecture/05-ai.md §5).

The LLM only *reads* the message (``understand``) and *words* some replies (``Responder``). Every
decision is taken here, deterministically, against the database:

1. AI switched off or the conversation is with a human → no reply, ``needs_attention``.
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

from app.ai.confirmation import (
    ConfirmationDecision,
    classify_cancel_confirmation,
    classify_confirmation,
    is_hesitation,
    mentions_cancellation,
)
from app.ai.handoff import detect_operator_request
from app.ai.language import detect_language
from app.ai.llm_client import LLMClient, LLMError, get_llm_client
from app.ai.product_matcher import MatchStatus, ProductMatcher, is_generic_mention
from app.ai.responder import TEMPLATE_KINDS, Reply, ReplyKind, ReplyPlan, Responder
from app.ai.small_talk import SmallTalk, detect_small_talk
from app.ai.templates import MAX_QUESTIONS, render
from app.ai.text_normalize import normalize_fold
from app.ai.tools import ToolContext, ToolRegistry, product_view, tool_definitions
from app.ai.understanding import (
    MAX_QUANTITY,
    Entities,
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
from app.core.time import business_now, business_today
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
)
from app.models.faq import FaqItem
from app.models.order import Order
from app.models.product import Product
from app.repositories.conversations import MessageRepository
from app.repositories.faq import FaqRepository
from app.repositories.orders import OrderRepository
from app.repositories.products import ProductRepository
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
from app.services.order_pricing import money
from app.services.order_service import BOT_CANCELLABLE_STATUSES, OrderService
from app.services.order_validator import (
    DATE_PAST,
    DELIVERY_DATE,
    DELIVERY_TIME,
    TOO_FAR,
    TOO_SOON,
    USABLE_GEOCODE_STATUSES,
    OrderValidator,
)
from app.services.phone import normalize_phone
from app.services.settings_service import SettingsService

logger = get_logger(__name__)

__all__ = ["FAILED_ATTEMPTS_LIMIT", "DialogOutcome", "DialogService"]

#: 03 §6: two failed attempts in a row hand the dialog over.
FAILED_ATTEMPTS_LIMIT = 2
HISTORY_LIMIT = 20
REPLY_HISTORY_LIMIT = 6  # recent turns the reply step sees for continuity (05 §6)
MAX_TOOL_ROUNDS = 2

REASON_OPERATOR_REQUEST = "Клиент попросил менеджера"
REASON_COMPLAINT = "Жалоба клиента"
REASON_AI_UNAVAILABLE = "AI-ассистент недоступен"
REASON_NOT_UNDERSTOOD = "Бот не смог понять клиента"
REASON_IMAGE = "Клиент прислал изображение"
REASON_ORDER_LOCKED = "Клиент просит изменить заказ №{order_id}, который уже оформлен"
REASON_CANCEL_LOCKED = "Клиент просит отменить заказ №{order_id}, который уже в работе"
CANCEL_REASON = "Отменён клиентом в Instagram"
CANCEL_REQUEST_TEXT = "отменить заказ. Сообщение клиента: {text}"

_BARE_NUMBER_RE = re.compile(r"^\s*(\d{1,3})\s*(?:шт\.?|штук[аи]?|дона|pcs)?\s*[.!]?\s*$", re.IGNORECASE)

#: Intents that ask something and must be answered, even when the message carries order data.
QUERY_INTENTS = frozenset(
    {Intent.FAQ, Intent.PRODUCT_QUERY, Intent.DELIVERY_QUERY, Intent.PAYMENT_QUERY, Intent.ORDER_STATUS}
)
ORDER_INTENTS = frozenset({Intent.CREATE_ORDER, Intent.CHANGE_ORDER})


@dataclass(slots=True)
class DialogOutcome:
    reply: Reply | None = None
    handoff: bool = False
    actions: list[str] = field(default_factory=list)


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
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self._llm = llm
        self._llm_resolved = llm is not None
        self._llm_factory = llm_factory
        self._geocoder = geocoder
        self.use_tools = self.settings.LLM_TOOLS_ENABLED if use_tools is None else use_tools
        self.products = ProductRepository(db)
        self.orders = OrderRepository(db)
        self.faq = FaqRepository(db)
        self.messages = MessageRepository(db)
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

        language = detect_language(turn.text, None, self._known_language(turn), self._catalog_phrases(turn))
        if message.message_type == MessageType.IMAGE and not turn.text:
            return self._handoff(turn, REASON_IMAGE, "image", language)
        if not turn.text:
            return self._finish(turn, ReplyPlan(ReplyKind.CLARIFY, language), reset_attempts=False)
        if detect_operator_request(turn.text):
            return self._handoff(turn, REASON_OPERATOR_REQUEST, "operator_request", language)

        if turn.state.awaiting in (AWAITING_CONFIRMATION, AWAITING_CANCEL_CONFIRMATION):
            outcome = self._answer_to_question(turn, language)
            if outcome is not None:
                return outcome
        outcome = self._bare_number(turn, language)
        if outcome is not None:
            return outcome
        talk = detect_small_talk(turn.text)
        if talk is not SmallTalk.NONE:
            return self._small_talk(turn, talk.value, language)

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
        if turn.state.draft_order_id != order.id or order.status not in DRAFT_ORDER_STATUSES:
            return DialogOutcome(actions=["not_current_draft"])
        turn.draft, turn.draft_loaded = order, True
        turn.state.address_candidates = []
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
        """05 §5 step 1 / 03 §6: the bot is silent; the operator sees the new message."""
        if turn.business.ai_enabled and turn.conversation.mode != ConversationMode.HUMAN_HANDOFF:
            return None
        reason = "ai_disabled" if not turn.business.ai_enabled else "human_handoff"
        turn.conversation.needs_attention = True
        self.db.commit()
        log_event(logger, "dialog.skipped", conversation_id=turn.conversation.id, reason=reason)
        return DialogOutcome(actions=[reason])

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

        faq_items = self._faq_first(turn, result)
        if faq_items:
            return self._faq_answer(turn, result, language, items=faq_items)
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
        if result.intent in ORDER_INTENTS or ORDER_INTENTS & set(result.secondary_intents):
            return True
        if result.intent in QUERY_INTENTS or result.intent == Intent.CANCEL_ORDER:
            return False
        entities = result.entities
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
        if (
            kind == SmallTalk.ACK.value
            and order is not None
            and state.awaiting in (AWAITING_MISSING_FIELDS, AWAITING_ADDRESS_CHOICE, None)
        ):
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

    def _greeting(self, turn: _Turn, result: UnderstandingResult, language: str) -> DialogOutcome:
        facts, fields = self._reminder(turn)
        facts.update({"business_name": turn.business.business_name, "greeting": True})
        return self._finish(turn, ReplyPlan(ReplyKind.GREETING, language, facts, fields))

    def _faq_first(self, turn: _Turn, result: UnderstandingResult) -> list[FaqItem]:
        """A question the FAQ answers is answered from the FAQ, whatever intent the model chose.

        "Насколько свежие торты?" often comes back as PRODUCT_QUERY (the catalog would be listed) or
        OTHER (the dialog would count a failed attempt). Model-chosen ids count for GREETING too
        ("Здравствуйте, вы работаете в воскресенье?"); the admin's keywords only for PRODUCT_QUERY
        without a named product and OTHER. FAQ / delivery / payment intents keep their own handlers.
        """
        intent = result.intent
        if intent not in (Intent.PRODUCT_QUERY, Intent.OTHER, Intent.GREETING):
            return []
        if intent == Intent.PRODUCT_QUERY and (result.product_ids_asked or result.entities.items):
            return []
        return self._faq_items(result, turn.text, keywords=intent != Intent.GREETING)

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
        by_id = {product.id: product for product in products}
        asked = [by_id[product_id] for product_id in result.product_ids_asked if product_id in by_id]
        unknown: list[str] = []
        if not asked:
            matcher = ProductMatcher(products)
            for mention in result.entities.items:
                if mention.product_id in by_id:
                    asked.append(by_id[mention.product_id])
                    continue
                match = matcher.match(mention.product_text)
                if match.status == MatchStatus.NONE:
                    if mention.product_text and not is_generic_mention(mention.product_text):
                        unknown.append(mention.product_text)
                    continue
                asked.extend(by_id[candidate] for candidate in match.candidates if candidate in by_id)
        asked = list(dict.fromkeys(asked))
        facts, fields = self._reminder(turn)
        if unknown and not asked:
            facts.update({"unknown_products": unknown, "available_products": [_product_fact(p) for p in products]})
            return self._finish(turn, ReplyPlan(ReplyKind.UNKNOWN_PRODUCT, language, facts, fields))
        facts.update({"products": [_product_fact(p) for p in (asked or products)], "asked_specific": bool(asked)})
        return self._finish(turn, ReplyPlan(ReplyKind.PRODUCT_INFO, language, facts, fields))

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
        facts, fields = self._reminder(turn)
        facts.update({**info, "faq": faq})
        return self._finish(turn, ReplyPlan(ReplyKind.DELIVERY_INFO, language, facts, fields))

    def _payment_query(self, turn: _Turn, result: UnderstandingResult, language: str) -> DialogOutcome:
        methods = turn.business.payment_methods_text.strip() or None
        faq = [_faq_fact(item, language) for item in self._faq_items(result, turn.text)]
        if methods is None and not faq:
            return self._need_manager(turn, language)
        facts, fields = self._reminder(turn)
        facts.update({"payment_methods": methods, "faq": faq})
        return self._finish(turn, ReplyPlan(ReplyKind.PAYMENT_INFO, language, facts, fields))

    def _need_manager(self, turn: _Turn, language: str) -> DialogOutcome:
        """SPEC §40: "Мне нужно уточнить эту информацию у менеджера." + the operator is alerted."""
        turn.conversation.needs_attention = True
        turn.actions.append("needs_manager")
        return self._finish(turn, ReplyPlan(ReplyKind.NEED_MANAGER, language))

    def _faq_items(self, result: UnderstandingResult, text: str, *, keywords: bool = True) -> list[FaqItem]:
        """FAQ entries chosen by the model (ids validated), else by the admin's keywords."""
        active = self.faq.list()
        by_id = {item.id: item for item in active}
        chosen = [by_id[faq_id] for faq_id in result.faq_ids if faq_id in by_id]
        if chosen or not keywords:
            return chosen
        padded = f" {normalize_fold(text)} "
        return [
            item
            for item in active
            if any((key := normalize_fold(keyword)) and f" {key} " in padded for keyword in item.keywords or [])
        ]

    def _reminder(self, turn: _Turn) -> tuple[dict[str, Any], list[str]]:
        """An open draft keeps being asked about while the customer asks something else."""
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
        order = self._draft(turn) or self.orders.latest_active_for_customer(turn.customer.id)
        if order is None:
            facts, fields = self._reminder(turn)
            facts["orders"] = []
            return self._finish(turn, ReplyPlan(ReplyKind.ORDER_STATUS_INFO, language, facts, fields))
        if order.status not in BOT_CANCELLABLE_STATUSES:
            return self._order_locked(turn, order, CANCEL_REQUEST_TEXT.format(text=turn.text), language, cancel=True)
        turn.state.awaiting = AWAITING_CANCEL_CONFIRMATION
        turn.state.cancel_order_id = order.id
        return self._finish(turn, ReplyPlan(ReplyKind.CANCEL_CONFIRM, language, {"order_id": order.id}))

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
        if address_changed:
            outcome = self._geocode(turn, order, language)
            if outcome is not None:
                return outcome
        return self._continue_order(turn, order, language)

    # ------------------------------------------------------------------ items

    def _apply_items(self, turn: _Turn, order: Order, entities: Entities) -> None:
        mentions = entities.items
        if not mentions:
            return
        state = turn.state
        products = self._catalog(turn)
        by_id = {product.id: product for product in products}
        matcher = ProductMatcher(products)
        mode = entities.items_mode

        resolved: list[dict[str, Any]] = []
        new_pending: list[dict[str, Any]] = []
        unknown: list[str] = []
        for mention in mentions:
            product = by_id.get(mention.product_id) if mention.product_id is not None else None
            text = mention.product_text or (product.name if product is not None else "")
            if product is None:
                match = matcher.match(text)
                if match.product_id is not None:
                    product = by_id.get(match.product_id)
                elif match.status == MatchStatus.MULTIPLE:
                    new_pending.append(
                        {
                            "kind": PENDING_GENERIC if is_generic_mention(text) else PENDING_AMBIGUOUS,
                            "product_text": text,
                            "quantity": mention.quantity,
                            "product_id": None,
                            "name": None,
                            "options": [candidate for candidate in match.candidates if candidate in by_id],
                            "comment": mention.comment,
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
                }
            )

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
            self._write_items(turn, order, ready, "replace" if mode == ItemsMode.REPLACE else "add")

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
        if entities.comment:
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
            return
        if new_date is not None:
            changes["delivery_date"] = new_date
        if new_time is not None:
            changes["delivery_time"] = new_time

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

    def _note_timing(self, turn: _Turn, problem: str, problem_date: date | None = None) -> None:
        turn.notes["timing_problem"] = problem
        turn.notes["min_lead_time_hours"] = turn.business.min_lead_time_hours
        turn.notes["max_days_ahead"] = turn.business.max_days_ahead
        if problem_date is not None:
            turn.notes["problem_date"] = problem_date.isoformat()
        if problem == TOO_SOON:
            turn.notes.update(_earliest_slot(turn.business.min_lead_time_hours))

    # ------------------------------------------------------------------ address and map pin (03 §7)

    def _geocode(self, turn: _Turn, order: Order, language: str) -> DialogOutcome | None:
        delivery = order.delivery
        if delivery is None or not (delivery.address_raw or "").strip():
            return None
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
        missing = OrderValidator.missing_fields(order)
        item_questions = bool(state.pending_items)
        askable = self._askable_fields(turn, missing)
        blocking_notes = any(turn.notes.get(key) for key in ("unknown_products", "timing_problem", "phone_invalid"))

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
        if problem in (TOO_FAR, DATE_PAST) or (problem == TOO_SOON and still_bad):
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
        }

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
        names = {product.id: product.name for product in self._catalog(turn)}
        facts: dict[str, Any] = dict(turn.notes)
        if turn.state.pending_items:
            facts["pending_items"] = [
                {
                    "kind": entry["kind"],
                    "product_text": entry.get("product_text"),
                    "quantity": entry.get("quantity"),
                    "name": entry.get("name"),
                    "options": [names[option] for option in entry.get("options") or [] if option in names],
                }
                for entry in turn.state.pending_items
            ]
        facts["order_so_far"] = {
            "items": [{"name": item.product_name, "quantity": item.quantity} for item in order.items],
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
        plan = ReplyPlan(ReplyKind.HANDOFF, language, {"reason_code": reason_code, **(facts or {})})
        return self._finish(turn, plan, handoff=True, reset_attempts=reset_attempts)

    def _finish(
        self,
        turn: _Turn,
        plan: ReplyPlan,
        *,
        handoff: bool = False,
        reset_attempts: bool = True,
    ) -> DialogOutcome:
        conversation = turn.conversation
        if reset_attempts:
            conversation.failed_ai_attempts = 0
        if plan.kind not in TEMPLATE_KINDS and not plan.context:
            plan = replace(plan, context=self._reply_context(turn))
        turn.state.language = plan.language
        if turn.customer.language != Language(plan.language):
            turn.customer.language = Language(plan.language)
        conversation.state = turn.state.to_json()
        if turn.message is not None and not turn.message.ai_processed:
            turn.message.ai_processed = True
        self.db.commit()

        wording = plan.kind not in TEMPLATE_KINDS and self.settings.LLM_REPLY_WORDING_ENABLED
        llm = self.llm if wording else None
        reply = Responder(llm, catalog_names=[product.name for product in self._catalog(turn)]).generate_reply(plan)
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
        return DialogOutcome(reply=reply, handoff=handoff, actions=list(turn.actions))


# ====================================================================== module helpers


def _has_order_fields(entities: Entities) -> bool:
    values = (
        entities.delivery_date,
        entities.delivery_time,
        entities.delivery_type,
        entities.address,
        entities.recipient_name,
        entities.recipient_phone,
        entities.courier_comment,
        entities.customer_name,
        entities.phone,
        entities.payment_method,
    )
    return any(value is not None for value in values)


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


def _earliest_slot(min_lead_time_hours: int) -> dict[str, Any]:
    """The first slot the bakery can take (business time, rounded up to the hour) — for "самое раннее"."""
    moment = business_now() + timedelta(hours=max(0, int(min_lead_time_hours)))
    if moment.minute or moment.second or moment.microsecond:
        moment = moment.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
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
