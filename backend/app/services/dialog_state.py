"""Typed view of ``Conversation.state`` and the order content hash (docs/architecture/05-ai.md §4).

The JSON column is written only by the backend. ``DialogState.from_json`` is tolerant: an unknown
key is dropped and a malformed value falls back to its default, so a state written by an older
version never breaks the dialog.

``order_content_hash(order)`` fingerprints everything the order summary shows. The confirmation
"Да" is accepted only while the hash stored when the summary was sent still matches (03 §5): an
order changed afterwards — by the customer, the map pin or staff — needs a new summary.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.models.order import Order

AWAITING_MISSING_FIELDS = "missing_fields"
AWAITING_CONFIRMATION = "confirmation"
AWAITING_CANCEL_CONFIRMATION = "cancel_confirmation"
AWAITING_ADDRESS_CHOICE = "address_choice"
AWAITING_VALUES = frozenset(
    {AWAITING_MISSING_FIELDS, AWAITING_CONFIRMATION, AWAITING_CANCEL_CONFIRMATION, AWAITING_ADDRESS_CHOICE}
)

PENDING_GENERIC = "generic"  # "2 торта" — which ones?
PENDING_AMBIGUOUS = "ambiguous"  # "бархат" matches several products
PENDING_QUANTITY = "quantity"  # a product without a quantity
PENDING_KINDS = frozenset({PENDING_GENERIC, PENDING_AMBIGUOUS, PENDING_QUANTITY})

MAX_ADDRESS_CANDIDATES = 3
#: How many follow-up marks the state keeps (03 §6a): enough for the stages of the order in hand.
MAX_FOLLOW_UPS_REMEMBERED = 8


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _pending(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict) or entry.get("kind") not in PENDING_KINDS:
            continue
        result.append(
            {
                "kind": entry["kind"],
                "product_text": entry.get("product_text") if isinstance(entry.get("product_text"), str) else None,
                "quantity": _int_or_none(entry.get("quantity")),
                "product_id": _int_or_none(entry.get("product_id")),
                "name": entry.get("name") if isinstance(entry.get("name"), str) else None,
                "options": [option for option in entry.get("options") or [] if _int_or_none(option) is not None],
                "comment": entry.get("comment") if isinstance(entry.get("comment"), str) else None,
            }
        )
    return result


def _candidates(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        try:
            lat, lng = float(entry["lat"]), float(entry["lng"])
        except (KeyError, TypeError, ValueError):
            continue
        result.append({"formatted": str(entry.get("formatted") or ""), "lat": lat, "lng": lng})
    return result[:MAX_ADDRESS_CANDIDATES]


_SLOT_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLOT_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def _slot(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    slot_date, slot_time = value.get("date"), value.get("time")
    if not (isinstance(slot_date, str) and _SLOT_DATE_RE.match(slot_date)):
        return None
    if not (isinstance(slot_time, str) and _SLOT_TIME_RE.match(slot_time)):
        return None
    return {"date": slot_date, "time": slot_time}


@dataclass(slots=True)
class DialogState:
    draft_order_id: int | None = None
    awaiting: str | None = None
    missing_fields: list[str] = field(default_factory=list)
    summary_hash: str | None = None
    pending_items: list[dict[str, Any]] = field(default_factory=list)
    address_candidates: list[dict[str, Any]] = field(default_factory=list)
    cancel_order_id: int | None = None
    language: str | None = None
    last_intent: str | None = None
    blocked_notice_sent: bool = False
    #: "Самое раннее — завтра после 18:00" offered in the last reply: ``{"date": ISO, "time": "HH:MM"}``.
    #: A "Да" to that reply takes the slot; any other message forgets it.
    offered_slot: dict[str, str] | None = None
    #: Follow-ups already sent (03 §6a), as ``"{stage}:{order_id}"`` — one per stage of one order, so
    #: a customer is never chased twice about the same thing. Only the last few are kept.
    follow_ups_sent: list[str] = field(default_factory=list)
    #: The price list picture already sent in this dialog, by file name (03 §1.4): asked a second
    #: time, the customer gets the prices as text instead of the same photo again. A new picture
    #: (another name) is sent again — the prices on it have changed.
    price_list_sent: str | None = None

    @classmethod
    def from_json(cls, data: Any) -> "DialogState":
        if not isinstance(data, dict):
            return cls()
        awaiting = data.get("awaiting")
        language = data.get("language")
        return cls(
            draft_order_id=_int_or_none(data.get("draft_order_id")),
            awaiting=awaiting if awaiting in AWAITING_VALUES else None,
            missing_fields=_str_list(data.get("missing_fields")),
            summary_hash=data.get("summary_hash") if isinstance(data.get("summary_hash"), str) else None,
            pending_items=_pending(data.get("pending_items")),
            address_candidates=_candidates(data.get("address_candidates")),
            cancel_order_id=_int_or_none(data.get("cancel_order_id")),
            language=language if language in ("ru", "tg") else None,
            last_intent=data.get("last_intent") if isinstance(data.get("last_intent"), str) else None,
            blocked_notice_sent=bool(data.get("blocked_notice_sent")),
            offered_slot=_slot(data.get("offered_slot")),
            follow_ups_sent=_str_list(data.get("follow_ups_sent"))[-MAX_FOLLOW_UPS_REMEMBERED:],
            price_list_sent=data.get("price_list_sent") if isinstance(data.get("price_list_sent"), str) else None,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "draft_order_id": self.draft_order_id,
            "awaiting": self.awaiting,
            "missing_fields": list(self.missing_fields),
            "summary_hash": self.summary_hash,
            "pending_items": [dict(entry) for entry in self.pending_items],
            "address_candidates": [dict(entry) for entry in self.address_candidates],
            "cancel_order_id": self.cancel_order_id,
            "language": self.language,
            "last_intent": self.last_intent,
            "blocked_notice_sent": self.blocked_notice_sent,
            "offered_slot": dict(self.offered_slot) if self.offered_slot else None,
            "follow_ups_sent": list(self.follow_ups_sent)[-MAX_FOLLOW_UPS_REMEMBERED:],
            "price_list_sent": self.price_list_sent,
        }

    def pending_of(self, kind: str) -> list[dict[str, Any]]:
        return [entry for entry in self.pending_items if entry.get("kind") == kind]

    def follow_up_key(self, stage: str, order_id: int | None) -> str:
        return f"{stage}:{order_id if order_id is not None else '-'}"

    def follow_up_sent(self, stage: str, order_id: int | None) -> bool:
        return self.follow_up_key(stage, order_id) in self.follow_ups_sent

    def remember_follow_up(self, stage: str, order_id: int | None) -> None:
        key = self.follow_up_key(stage, order_id)
        if key not in self.follow_ups_sent:
            self.follow_ups_sent = [*self.follow_ups_sent, key][-MAX_FOLLOW_UPS_REMEMBERED:]

    def close_draft(self) -> None:
        """The draft was confirmed or cancelled: everything order-related is forgotten."""
        self.forget_draft()
        self.awaiting = None
        self.cancel_order_id = None

    def forget_draft(self) -> None:
        """The draft no longer exists as a draft (e.g. staff confirmed it): drop the questions about it.

        A pending "Отменить заказ №N?" is kept — it may be about an order that is not a draft.
        """
        self.draft_order_id = None
        self.missing_fields = []
        self.summary_hash = None
        self.pending_items = []
        self.address_candidates = []
        self.offered_slot = None
        if self.awaiting != AWAITING_CANCEL_CONFIRMATION:
            self.awaiting = None


def _plain(value: Any) -> str | None:
    return None if value is None else str(getattr(value, "value", value))


def _fixed(value: Any, places: int) -> str | None:
    """Numeric columns come back as floats-turned-Decimals on SQLite: a fixed format keeps the hash stable."""
    return None if value is None else f"{Decimal(str(value)):.{places}f}"


def order_content_hash(order: Order) -> str:
    """sha256 of the canonical JSON of what the summary shows (05 §4)."""
    delivery = order.delivery
    customer = order.customer
    content = {
        "items": [
            [item.product_id, item.product_name, item.quantity, _fixed(item.unit_price, 2), item.comment or ""]
            for item in order.items
        ],
        "delivery_type": _plain(order.delivery_type),
        "delivery_date": _plain(order.delivery_date),
        "delivery_time": order.delivery_time.strftime("%H:%M") if order.delivery_time else None,
        "payment_method": _plain(order.payment_method),
        "comment": order.comment or "",
        "total": _fixed(order.total_amount, 2),
        "customer": [customer.name or "", customer.phone or ""] if customer is not None else None,
        "delivery": (
            [
                delivery.address_raw or "",
                delivery.address_formatted or "",
                _fixed(delivery.latitude, 6),
                _fixed(delivery.longitude, 6),
                delivery.recipient_name or "",
                delivery.recipient_phone or "",
            ]
            if delivery is not None
            else None
        ),
    }
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
