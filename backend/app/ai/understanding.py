"""Understanding step: schema, context and ``understand()`` (docs/architecture/05-ai.md §3).

The LLM returns one JSON object constrained by ``understanding_json_schema()`` (structured outputs:
every object has ``additionalProperties: false``, every property is required, and "no value" is
expressed as ``null`` through ``anyOf``). The model is a *reader*, never an authority: everything it
returns is re-checked here before ``DialogService`` sees it —

- ``product_id`` that is not in the catalog handed to the model → ``null`` (``product_text`` kept);
- ``quantity`` outside 1..500 (or not a whole number) → ``null``;
- ``delivery_date`` that is not ISO ``YYYY-MM-DD`` or is more than a year away → ``null``;
- ``delivery_time`` that is not a real 24-hour time → ``null`` (``18.30`` is normalised to ``18:30``);
- ``phone`` is kept exactly as the customer wrote it (``app/services/phone.py`` normalises it);
- ``faq_ids`` / ``product_ids_asked`` are filtered to the ids that were actually offered;
- an unknown ``intent`` becomes ``OTHER``, an unknown enum value becomes ``null``.

SPEC §10 asks for a flat extraction object for logs and ``Message.ai_payload``; it is derived from
``entities.items`` by ``to_spec_extraction()``.
"""

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from app.ai import prompts
from app.ai.llm_client import (
    DEFAULT_MAX_TOOL_ROUNDS,
    STAGE_UNDERSTANDING,
    Block,
    LLMClient,
    LLMUnavailableError,
    ToolExecutor,
)
from app.core.logging import get_logger, log_event
from app.models.enums import DeliveryType, Intent, Language, PaymentMethod

logger = get_logger(__name__)

MIN_QUANTITY = 1
MAX_QUANTITY = 500
MAX_ITEMS = 20
MAX_IDS = 10
MAX_DATE_DRIFT_DAYS = 366  # anything further away is a hallucination, not an order
MAX_ADDRESS_CANDIDATE = 10

MAX_NAME_CHARS = 120
MAX_PHONE_CHARS = 32
MAX_ADDRESS_CHARS = 300
MAX_TEXT_CHARS = 500

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^(\d{1,2})\s*[:.\-]\s*(\d{2})$")


class ItemsMode(StrEnum):
    """How ``entities.items`` applies to the draft order (05 §3)."""

    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"
    NONE = "none"


class ConfirmationSignal(StrEnum):
    """Wording of the message only — the confirmation itself is decided by ``classify_confirmation``."""

    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"
    NONE = "none"


class OtherTopic(StrEnum):
    """What an ``OTHER`` message is (05 §5): decides between a chat reply, a manager and a re-ask."""

    SMALL_TALK = "small_talk"  # thanks, jokes, "как дела", questions about the bot itself
    QUESTION = "question"  # a business question the catalog/FAQ/settings do not answer
    UNCLEAR = "unclear"  # cannot tell what the customer wants


# --------------------------------------------------------------------------- models


class ItemMention(BaseModel):
    model_config = ConfigDict(extra="ignore")

    product_id: int | None = None
    product_text: str | None = None
    quantity: int | None = None
    comment: str | None = None


class Entities(BaseModel):
    model_config = ConfigDict(extra="ignore")

    customer_name: str | None = None
    phone: str | None = None
    items: list[ItemMention] = Field(default_factory=list)
    items_mode: ItemsMode = ItemsMode.NONE
    delivery_date: str | None = None  # ISO YYYY-MM-DD
    delivery_time: str | None = None  # HH:MM, 24-hour
    delivery_type: DeliveryType | None = None
    address: str | None = None
    recipient_name: str | None = None
    recipient_phone: str | None = None
    courier_comment: str | None = None
    payment_method: PaymentMethod | None = None
    comment: str | None = None


class UnderstandingResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    language: Language = Language.RU
    intent: Intent = Intent.OTHER
    secondary_intents: list[Intent] = Field(default_factory=list)
    entities: Entities = Field(default_factory=Entities)
    faq_ids: list[int] = Field(default_factory=list)
    product_ids_asked: list[int] = Field(default_factory=list)
    confirmation_signal: ConfirmationSignal = ConfirmationSignal.NONE
    address_candidate_choice: int | None = None
    other_topic: OtherTopic | None = None
    confidence: float = 0.0

    @property
    def has_items(self) -> bool:
        return bool(self.entities.items)

    @property
    def all_intents(self) -> list[Intent]:
        return [self.intent, *self.secondary_intents]

    def to_payload(self) -> dict[str, Any]:
        """JSON-safe dict for ``Message.ai_payload``."""
        return self.model_dump(mode="json")


# --------------------------------------------------------------------------- JSON schema


def _string(description: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string"}
    if description:
        schema["description"] = description
    return schema


def _integer(description: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer"}
    if description:
        schema["description"] = description
    return schema


def _enum(enum_cls: type[StrEnum], description: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string", "enum": [member.value for member in enum_cls]}
    if description:
        schema["description"] = description
    return schema


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    """Structured outputs support ``anyOf``; ``null`` is always spelled out explicitly."""
    return {"anyOf": [schema, {"type": "null"}]}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def understanding_json_schema() -> dict[str, Any]:
    """The schema for ``output_config.format`` (05 §2): closed objects, every key required."""
    item = _object(
        {
            "product_id": _nullable(_integer("id from the CATALOG block of the system prompt, else null")),
            "product_text": _nullable(_string("the customer's own wording for this product")),
            "quantity": _nullable(_integer("whole number of units, null when not said")),
            "comment": _nullable(_string("wish about this item")),
        }
    )
    entities = _object(
        {
            "customer_name": _nullable(_string()),
            "phone": _nullable(_string("as written by the customer")),
            "items": {"type": "array", "items": item, "description": "products the customer wants to order"},
            "items_mode": _enum(ItemsMode, "how these items change the order"),
            "delivery_date": _nullable(_string("ISO YYYY-MM-DD, resolved against the given current date")),
            "delivery_time": _nullable(_string("HH:MM, 24-hour")),
            "delivery_type": _nullable(_enum(DeliveryType, "DELIVERY = доставка, PICKUP = самовывоз")),
            "address": _nullable(_string()),
            "recipient_name": _nullable(_string()),
            "recipient_phone": _nullable(_string()),
            "courier_comment": _nullable(_string()),
            "payment_method": _nullable(_enum(PaymentMethod)),
            "comment": _nullable(_string()),
        }
    )
    return _object(
        {
            "language": _enum(Language, "language of this message"),
            "intent": _enum(Intent, "main purpose of this message"),
            "secondary_intents": {"type": "array", "items": _enum(Intent)},
            "entities": entities,
            "faq_ids": {"type": "array", "items": _integer("id from the FAQ block")},
            "product_ids_asked": {"type": "array", "items": _integer("catalog id the customer asks about")},
            "confirmation_signal": _enum(ConfirmationSignal, "wording of this message only"),
            "address_candidate_choice": _nullable(_integer("1-based choice from an offered address list")),
            "other_topic": _nullable(
                _enum(
                    OtherTopic,
                    "only for intent OTHER: small_talk — conversational (thanks, how are you, a joke, "
                    "questions about the assistant); question — a business question the catalog, FAQ and "
                    "settings do not answer; unclear — cannot tell what the customer wants",
                )
            ),
            "confidence": {"type": "number", "description": "0.0-1.0"},
        }
    )


# --------------------------------------------------------------------------- context


@dataclass(slots=True)
class UnderstandingContext:
    """Everything the model may see (05 §3). Built by ``DialogService`` from the database."""

    now_business: datetime
    catalog: list[dict[str, Any]] = field(default_factory=list)  # {id, name, aliases, price, unit}
    faq: list[dict[str, Any]] = field(default_factory=list)  # {id, question}
    customer: dict[str, Any] = field(default_factory=dict)  # {name, phone, language, is_regular}
    draft: dict[str, Any] | None = None
    awaiting: str | None = None
    missing_fields: list[str] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)  # {role: customer|assistant|operator, text}

    @property
    def today(self) -> date:
        return self.now_business.date()

    def catalog_ids(self) -> set[int]:
        return {value for value in (_as_int(item.get("id")) for item in self.catalog) if value is not None}

    def faq_ids(self) -> set[int]:
        return {value for value in (_as_int(item.get("id")) for item in self.faq) if value is not None}

    def product_name(self, product_id: int) -> str | None:
        for item in self.catalog:
            if _as_int(item.get("id")) == product_id:
                name = str(item.get("name") or "").strip()
                return name or None
        return None


# --------------------------------------------------------------------------- sanitizing helpers


def _as_int(value: Any) -> int | None:
    """``True``/``1.5``/``"x"`` are not quantities; ``"2"`` and ``2.0`` are."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"[+-]?\d+", text):
            return int(text)
    return None


def _clean(value: Any, limit: int) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = " ".join(str(value).split())
    if not text or text.lower() in ("null", "none", "unknown", "-"):
        return None
    return text[:limit]


def _as_enum(enum_cls: type[StrEnum], value: Any, default: Any = None) -> Any:
    if isinstance(value, enum_cls):
        return value
    if not isinstance(value, str):
        return default
    text = value.strip().lower()
    if not text:
        return default
    for member in enum_cls:
        if text in (member.value.lower(), member.name.lower()):
            return member
    return default


def _enum_list(enum_cls: type[StrEnum], value: Any) -> list[Any]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return []
    members: list[Any] = []
    for entry in value:
        member = _as_enum(enum_cls, entry)
        if member is not None and member not in members:
            members.append(member)
    return members


def _filter_ids(value: Any, allowed: set[int]) -> list[int]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return []
    result: list[int] = []
    for entry in value:
        number = _as_int(entry)
        if number is not None and number in allowed and number not in result:
            result.append(number)
        if len(result) >= MAX_IDS:
            break
    return result


def _clean_date(value: Any, today: date) -> str | None:
    text = _clean(value, 32)
    if not text or not _DATE_RE.match(text):
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None
    if abs((parsed - today).days) > MAX_DATE_DRIFT_DAYS:
        return None
    return parsed.isoformat()


def _clean_time(value: Any) -> str | None:
    text = _clean(value, 16)
    if not text:
        return None
    match = _TIME_RE.match(text)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _clean_confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    if number != number:  # NaN
        return 0.0
    return min(1.0, max(0.0, number))


def _clean_choice(value: Any) -> int | None:
    number = _as_int(value)
    if number is None or not (1 <= number <= MAX_ADDRESS_CANDIDATE):
        return None
    return number


def _normalize_items(raw: Any, ctx: UnderstandingContext) -> list[dict[str, Any]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    catalog_ids = ctx.catalog_ids()
    items: list[dict[str, Any]] = []
    for entry in list(raw)[:MAX_ITEMS]:
        if not isinstance(entry, Mapping):
            continue
        product_id = _as_int(entry.get("product_id"))
        if product_id is not None and product_id not in catalog_ids:
            product_id = None  # the model invented an id: keep the wording, let ProductMatcher decide
        text = _clean(entry.get("product_text"), MAX_NAME_CHARS)
        if product_id is not None and not text:
            text = ctx.product_name(product_id)
        if product_id is None and not text:
            continue
        quantity = _as_int(entry.get("quantity"))
        if quantity is None or not (MIN_QUANTITY <= quantity <= MAX_QUANTITY):
            quantity = None
        items.append(
            {
                "product_id": product_id,
                "product_text": text,
                "quantity": quantity,
                "comment": _clean(entry.get("comment"), MAX_TEXT_CHARS),
            }
        )
    return items


def _normalize(data: Mapping[str, Any], ctx: UnderstandingContext) -> dict[str, Any]:
    raw_entities = data.get("entities")
    raw_entities = raw_entities if isinstance(raw_entities, Mapping) else {}

    items = _normalize_items(raw_entities.get("items"), ctx)
    items_mode = _as_enum(ItemsMode, raw_entities.get("items_mode"), ItemsMode.NONE)
    if items and items_mode is ItemsMode.NONE:
        items_mode = ItemsMode.ADD  # items named without a mode are an addition to the draft
    if not items:
        items_mode = ItemsMode.NONE  # never let an empty list wipe or edit a draft order

    intent = _as_enum(Intent, data.get("intent"), Intent.OTHER)
    secondary = [member for member in _enum_list(Intent, data.get("secondary_intents")) if member is not intent]

    return {
        "language": _as_enum(Language, data.get("language"), Language.RU),
        "intent": intent,
        "secondary_intents": secondary[:MAX_IDS],
        "entities": {
            "customer_name": _clean(raw_entities.get("customer_name"), MAX_NAME_CHARS),
            # Phone stays exactly as written; app/services/phone.py normalises it (03 §2).
            "phone": _clean(raw_entities.get("phone"), MAX_PHONE_CHARS),
            "items": items,
            "items_mode": items_mode,
            "delivery_date": _clean_date(raw_entities.get("delivery_date"), ctx.today),
            "delivery_time": _clean_time(raw_entities.get("delivery_time")),
            "delivery_type": _as_enum(DeliveryType, raw_entities.get("delivery_type")),
            "address": _clean(raw_entities.get("address"), MAX_ADDRESS_CHARS),
            "recipient_name": _clean(raw_entities.get("recipient_name"), MAX_NAME_CHARS),
            "recipient_phone": _clean(raw_entities.get("recipient_phone"), MAX_PHONE_CHARS),
            "courier_comment": _clean(raw_entities.get("courier_comment"), MAX_TEXT_CHARS),
            "payment_method": _as_enum(PaymentMethod, raw_entities.get("payment_method")),
            "comment": _clean(raw_entities.get("comment"), MAX_TEXT_CHARS),
        },
        "faq_ids": _filter_ids(data.get("faq_ids"), ctx.faq_ids()),
        "product_ids_asked": _filter_ids(data.get("product_ids_asked"), ctx.catalog_ids()),
        "confirmation_signal": _as_enum(ConfirmationSignal, data.get("confirmation_signal"), ConfirmationSignal.NONE),
        "address_candidate_choice": _clean_choice(data.get("address_candidate_choice")),
        "other_topic": _as_enum(OtherTopic, data.get("other_topic")) if intent is Intent.OTHER else None,
        "confidence": _clean_confidence(data.get("confidence")),
    }


def parse_understanding(data: Mapping[str, Any], ctx: UnderstandingContext) -> UnderstandingResult:
    """Coerce, validate and sanitize a raw model answer. Unusable output → ``LLMUnavailableError``."""
    payload = data if isinstance(data, Mapping) else {}
    try:
        return UnderstandingResult.model_validate(_normalize(payload, ctx))
    except PydanticValidationError as exc:
        log_event(
            logger,
            "ai.error",
            level=logging.WARNING,
            stage=STAGE_UNDERSTANDING,
            reason="invalid_understanding",
            errors=exc.error_count(),
        )
        raise LLMUnavailableError(reason="invalid_understanding") from exc


# --------------------------------------------------------------------------- entry point


def understand(
    llm: LLMClient,
    ctx: UnderstandingContext,
    text: str,
    *,
    tools: list[Block] | None = None,
    tool_executor: ToolExecutor | None = None,
    max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
) -> UnderstandingResult:
    """Build the prompts, ask the model and return a checked ``UnderstandingResult`` (05 §3).

    ``tools``/``tool_executor`` are the read-only tools of 05 §7; ``DialogService`` passes them when
    the registry is available. Errors (``LLMUnavailableError``, ``LLMRefusalError``) propagate — the
    caller counts the failed attempt and hands the dialog to an operator (03 §6).
    """
    raw = llm.complete_json(
        system=prompts.build_understanding_system(ctx.catalog, ctx.faq),
        messages=prompts.build_understanding_messages(ctx, text),
        schema=understanding_json_schema(),
        tools=tools,
        tool_executor=tool_executor,
        max_tool_rounds=max_tool_rounds,
    )
    result = parse_understanding(raw, ctx)
    entities = result.entities
    log_event(
        logger,
        "ai.response",
        stage=STAGE_UNDERSTANDING,
        intent=result.intent.value,
        secondary_intents=[member.value for member in result.secondary_intents],
        language=result.language.value,
        items=len(entities.items),
        items_mode=entities.items_mode.value,
        items_with_id=sum(1 for item in entities.items if item.product_id is not None),
        faq_ids=len(result.faq_ids),
        confirmation_signal=result.confirmation_signal.value,
        confidence=result.confidence,
        # Personal data is never logged — only whether the message contained it.
        has_name=entities.customer_name is not None,
        has_phone=entities.phone is not None,
        has_address=entities.address is not None,
        has_date=entities.delivery_date is not None,
        has_time=entities.delivery_time is not None,
    )
    return result


def to_spec_extraction(result: UnderstandingResult) -> dict[str, Any]:
    """The flat SPEC §10 extraction object, derived from ``entities`` (for logs / ``ai_payload``)."""
    entities = result.entities
    texts = [item.product_text for item in entities.items if item.product_text]
    only_item = entities.items[0] if len(entities.items) == 1 else None
    return {
        "customer_name": entities.customer_name,
        "phone": entities.phone,
        "product": ", ".join(texts) if texts else None,
        "quantity": only_item.quantity if only_item is not None else None,
        "delivery_date": entities.delivery_date,
        "delivery_time": entities.delivery_time,
        "delivery_type": entities.delivery_type.value if entities.delivery_type else None,
        "address": entities.address,
        "payment_method": entities.payment_method.value if entities.payment_method else None,
        "comment": entities.comment,
    }


__all__ = [
    "MAX_DATE_DRIFT_DAYS",
    "MAX_QUANTITY",
    "MIN_QUANTITY",
    "ConfirmationSignal",
    "Entities",
    "ItemMention",
    "ItemsMode",
    "OtherTopic",
    "UnderstandingContext",
    "UnderstandingResult",
    "parse_understanding",
    "to_spec_extraction",
    "understand",
    "understanding_json_schema",
]
