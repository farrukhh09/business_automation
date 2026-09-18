"""Reading a payment receipt screenshot (docs/architecture/05-ai.md §9, 03-business-rules.md §3).

The bakery asks for a prepayment to its wallet (a phone number in Dushanbe City / Alif / Эсхата) and
the customer sends a screenshot of the transfer. The model only *reads* the picture into a fixed JSON
(``read_receipt``); whether the receipt fits the order — the amount, the recipient wallet, the status,
the currency — is decided here in Python (``check_receipt``). The bot never marks an order paid on its
own unless the owner switched ``prepayment_auto_confirm`` on: a screenshot can be edited, only the bank
app proves that the money arrived, so by default the operator confirms the payment.
"""

import base64
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.ai.llm_client import STAGE_RECEIPT, Block, LLMClient, LLMUnavailableError, image_block
from app.ai.text_normalize import normalize_fold
from app.core.logging import get_logger, log_event
from app.services.order_pricing import ZERO, money

logger = get_logger(__name__)

__all__ = [
    "MIN_CONFIDENCE",
    "ReceiptReading",
    "ReceiptVerdict",
    "check_receipt",
    "expected_prepayment",
    "is_card_number",
    "mentions_payment_done",
    "read_receipt",
    "receipt_json_schema",
    "receipt_note",
    "wallet_matches",
]

#: Below this the picture is treated as "not a receipt" (a cake photo, a chat screenshot).
MIN_CONFIDENCE = 0.5
#: Fewer visible digits in the recipient cannot identify an account ("**** 33").
MIN_VISIBLE_DIGITS = 4
#: This many digits is a card or a bank account; a phone wallet is at most 12 ("+992 92 757 53 33").
CARD_DIGITS = 13
MAX_TEXT_CHARS = 120

STATUSES = ("success", "failed", "pending", "unknown")
#: Ways the apps print somoni.
_SOMONI = frozenset({"tjs", "сомони", "сомон", "смн", "с", "c", "som", "somoni", "tj", "сом"})
_MASK_CHARS = frozenset("*xX•●·#")

# --------------------------------------------------------------------------- what the model returns


class ReceiptReading(BaseModel):
    """What is printed on the screenshot — read by the model, checked by ``check_receipt``."""

    model_config = ConfigDict(extra="ignore")

    is_receipt: bool = False
    status: str = "unknown"
    amount: Decimal | None = None
    currency: str | None = None
    recipient: str | None = None
    recipient_name: str | None = None
    sender: str | None = None
    provider: str | None = None
    paid_at: str | None = None
    reference: str | None = None
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class ReceiptVerdict:
    """The deterministic decision: ``ok`` when nothing contradicts the order; ``problems`` are stable codes."""

    ok: bool
    problems: list[str] = field(default_factory=list)
    amount: Decimal | None = None
    expected: Decimal = ZERO
    shortfall: Decimal | None = None
    wallet_ok: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "problems": list(self.problems),
            "amount": f"{self.amount:.2f}" if self.amount is not None else None,
            "expected": f"{self.expected:.2f}",
            "shortfall": f"{self.shortfall:.2f}" if self.shortfall is not None else None,
            "wallet_ok": self.wallet_ok,
        }


# --------------------------------------------------------------------------- prompt and schema


RECEIPT_RULES = """\
You read screenshots of money transfers made in Tajik banking and wallet apps (Alif Mobi, DC Next /
Dushanbe City, Эсхата, Amonatbank, Spitamen, Humo and the like) that customers of a bakery send as
proof of a prepayment. Return ONE JSON object matching the schema.

1. Never guess: a value you cannot read is null. is_receipt=false when the picture is not a
   transfer / payment receipt at all (a photo of pastry, a menu, a chat, a map).
2. amount — the sum transferred to the recipient as a number ("12 000,50" → 12000.50), without the
   commission when the app shows it separately; currency — as printed ("TJS", "сомони", "смн", "c.").
3. recipient — the recipient identifier exactly as printed: a phone / wallet number (it may be partly
   masked: "+992 92 *** 53 33"), a masked card number or an account; recipient_name — the recipient's
   name when printed. sender — the sender's number or name when printed. Never swap the two.
4. status — "success" only when the app shows the transfer as completed ("Успешно", "Выполнено",
   "Перевод выполнен", "Муваффақ", a green check mark); "failed" for an error or a declined transfer;
   "pending" for "в обработке"; "unknown" otherwise.
5. provider — the app or bank name when recognisable; paid_at — the date and time as printed;
   reference — the transaction / receipt number when printed.
6. confidence — 0.0-1.0 that this is a genuine transfer receipt and you read it correctly.
7. Output only the JSON object — no explanation, no markdown.
"""


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def receipt_json_schema() -> dict[str, Any]:
    properties: dict[str, Any] = {
        "is_receipt": {"type": "boolean"},
        "status": {"type": "string", "enum": list(STATUSES)},
        "amount": _nullable({"type": "number"}),
        "currency": _nullable({"type": "string"}),
        "recipient": _nullable({"type": "string"}),
        "recipient_name": _nullable({"type": "string"}),
        "sender": _nullable({"type": "string"}),
        "provider": _nullable({"type": "string"}),
        "paid_at": _nullable({"type": "string"}),
        "reference": _nullable({"type": "string"}),
        "confidence": {"type": "number"},
    }
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def build_receipt_system() -> list[Block]:
    return [{"type": "text", "text": RECEIPT_RULES, "cache_control": {"type": "ephemeral"}}]


def build_receipt_messages(image: bytes, media_type: str) -> list[Block]:
    return [
        {
            "role": "user",
            "content": [
                image_block(base64.b64encode(image).decode("ascii"), media_type),
                {"type": "text", "text": "Read this receipt and return the JSON object now."},
            ],
        }
    ]


# --------------------------------------------------------------------------- sanitizing


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = " ".join(str(value).split())
    return text[:MAX_TEXT_CHARS] if text else None


def _amount(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    cleaned = re.sub(r"[^\d,.\-]", "", str(value)).replace(",", ".")
    if cleaned.count(".") > 1:  # "12.000.50" — thousands dots
        head, _, tail = cleaned.rpartition(".")
        cleaned = head.replace(".", "") + "." + tail
    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not number.is_finite() or number <= ZERO:
        return None
    return money(number)


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    return 0.0 if number != number else min(1.0, max(0.0, number))


def parse_reading(data: Any) -> ReceiptReading:
    payload = data if isinstance(data, dict) else {}
    status = str(payload.get("status") or "").strip().lower()
    return ReceiptReading(
        is_receipt=payload.get("is_receipt") is True,
        status=status if status in STATUSES else "unknown",
        amount=_amount(payload.get("amount")),
        currency=_text(payload.get("currency")),
        recipient=_text(payload.get("recipient")),
        recipient_name=_text(payload.get("recipient_name")),
        sender=_text(payload.get("sender")),
        provider=_text(payload.get("provider")),
        paid_at=_text(payload.get("paid_at")),
        reference=_text(payload.get("reference")),
        confidence=_confidence(payload.get("confidence")),
    )


def read_receipt(llm: LLMClient, image: bytes, media_type: str) -> ReceiptReading:
    """Ask the model what the screenshot says. ``LLMError`` propagates (the caller hands over)."""
    raw = llm.complete_json(
        system=build_receipt_system(),
        messages=build_receipt_messages(image, media_type),
        schema=receipt_json_schema(),
    )
    if not isinstance(raw, dict):
        raise LLMUnavailableError(reason="invalid_receipt")
    reading = parse_reading(raw)
    log_event(
        logger,
        "ai.response",
        stage=STAGE_RECEIPT,
        is_receipt=reading.is_receipt,
        status=reading.status,
        has_amount=reading.amount is not None,
        has_recipient=reading.recipient is not None,
        confidence=reading.confidence,
    )
    return reading


# --------------------------------------------------------------------------- the decision


def is_card_number(account: str | None) -> bool:
    """True when the bakery's own account is a card / bank account rather than a wallet phone number.

    The owner types the number in the settings; 13 digits or more is a card ("5058 2703 8115 6297"),
    fewer is a phone ("+992 92 757 53 33"). The customer-facing texts pick the right word from this,
    and ``wallet_matches`` compares a long recipient number instead of rejecting it.
    """
    return len(re.sub(r"\D", "", account or "")) >= CARD_DIGITS


def wallet_matches(recipient: str | None, wallet: str) -> bool | None:
    """Compare the recipient printed on the receipt with the bakery's own account number.

    Masked digits are wildcards ("+992 92 *** 53 33", "92 *** 53 33" both fit +992 92 757 53 33;
    "**** 6297" fits a card ending in 6297), a national number is compared with the tail of ours.
    ``None`` when nothing comparable is printed (a name only, fewer than four digits); ``False`` for
    another number — including a card or an account when our own number is a phone wallet.
    """
    wallet_digits = re.sub(r"\D", "", wallet or "")
    if not wallet_digits or not recipient:
        return None
    pattern = "".join(char if char.isdigit() else "*" for char in recipient if char.isdigit() or char in _MASK_CHARS)
    visible = sum(char.isdigit() for char in pattern)
    if visible < MIN_VISIBLE_DIGITS:
        return None
    if visible >= CARD_DIGITS and not is_card_number(wallet):
        return False
    if len(pattern) > len(wallet_digits):
        pattern = pattern[-len(wallet_digits) :]
    tail = wallet_digits[-len(pattern) :]
    return all(expected == "*" or expected == actual for expected, actual in zip(pattern, tail, strict=True))


def _is_somoni(currency: str | None) -> bool:
    if not currency:
        return True  # nothing printed: Tajik apps rarely print the currency of a local transfer
    tokens = normalize_fold(currency).split()
    return any(token in _SOMONI for token in tokens)


def expected_prepayment(total: Decimal | int | str, paid: Decimal | int | str, percent: int) -> Decimal:
    """``percent`` of the total minus what is already paid, never below zero."""
    wanted = money(money(total) * Decimal(int(percent)) / Decimal(100))
    return max(ZERO, money(wanted - money(paid)))


def check_receipt(reading: ReceiptReading, expected: Decimal, wallet: str) -> ReceiptVerdict:
    """03 §3: the receipt fits when it is a completed transfer of at least ``expected`` somoni and the
    recipient does not contradict the wallet (an unreadable recipient is left to the operator)."""
    if not reading.is_receipt or reading.confidence < MIN_CONFIDENCE:
        return ReceiptVerdict(ok=False, problems=["not_receipt"], expected=money(expected))
    problems: list[str] = []
    if reading.status in ("failed", "pending"):
        problems.append("status")
    if not _is_somoni(reading.currency):
        problems.append("currency")
    wallet_ok = wallet_matches(reading.recipient, wallet)
    if wallet_ok is False:
        problems.append("wallet_mismatch")
    shortfall: Decimal | None = None
    if reading.amount is None:
        problems.append("amount_unknown")
    elif reading.amount < money(expected):
        shortfall = money(money(expected) - reading.amount)
        problems.append("amount_short")
    return ReceiptVerdict(
        ok=not problems,
        problems=problems,
        amount=reading.amount,
        expected=money(expected),
        shortfall=shortfall,
        wallet_ok=wallet_ok,
    )


def receipt_note(reading: ReceiptReading, verdict: ReceiptVerdict, *, auto_paid: bool) -> str:
    """The journal line the operator reads next to the order ("Чек из Instagram: …")."""
    amount = f"{reading.amount:.2f} сомони" if reading.amount is not None else "сумма не распознана"
    parts = [f"Чек из Instagram: {amount}"]
    if reading.provider:
        parts.append(reading.provider)
    if verdict.wallet_ok is True:
        parts.append("получатель совпадает")
    elif verdict.wallet_ok is False:
        parts.append(f"получатель НЕ совпадает: {reading.recipient}")
    else:
        parts.append("получатель не виден")
    if reading.paid_at:
        parts.append(reading.paid_at)
    if "status" in verdict.problems:
        parts.append(f"статус перевода: {reading.status}")
    if "amount_short" in verdict.problems and verdict.shortfall is not None:
        parts.append(f"не хватает {verdict.shortfall:.2f} сомони")
    note = ", ".join(parts) + "."
    if auto_paid:
        return f"{note} Оплата отмечена автоматически — сверьте поступление в приложении банка."
    return f"{note} Сверьте поступление в приложении банка и отметьте оплату."


# --------------------------------------------------------------------------- "оплатил"


_PAID_STEMS = (
    "оплатил",
    "оплачено",
    "перевел",
    "перечислил",
    "скинул",
    "закинул",
    "отправил деньг",
    "пардохт кардам",
    "пардохт кардем",
    "пардохт шуд",
    "фиристодам",
    "интикол кардам",
    "гузарондам",
)


def mentions_payment_done(text: str | None) -> bool:
    """ "Оплатил", "перевёл", "пардохт кардам" — the customer says the money was sent (asked for the receipt)."""
    folded = f" {normalize_fold(text)} "
    return any(f" {stem}" in folded for stem in _PAID_STEMS)


def log_unreadable(reason: str) -> None:
    log_event(logger, "ai.error", level=logging.WARNING, stage=STAGE_RECEIPT, reason=reason)
