"""Reading a payment receipt screenshot (docs/architecture/05-ai.md §9, 03-business-rules.md §3).

The bakery asks for a prepayment to its account (a wallet phone or a card) and the customer sends a
screenshot of the transfer. The model only *reads* the picture into a fixed JSON (``read_receipt``);
whether the receipt fits the order — the amount, the recipient, the status, the currency — is decided
here in Python (``check_receipt``). The bot never marks an order paid on its own unless the owner
switched ``prepayment_auto_confirm`` on: a screenshot can be edited, only the bank app proves that the
money arrived, so by default the operator confirms the payment.

Authenticity. Whether a screenshot was retouched or generated cannot be *proven* from the picture:
Instagram re-encodes it and drops the metadata, and a model asked "is this fake?" answers confidently
either way. What can be checked is checked here, deterministically:

- **reuse** — the same file, the same transaction number or the same transfer (amount + minute +
  sender) already sent, for another order (``duplicate``: the customer is told) or for this one
  (``resent``);
- **dates** — a transfer dated in the future or before the order existed;
- **visible traces of editing** — a *separate*, narrow model call (``inspect_receipt``) compares how
  the lines are drawn: one value in another typeface, colour or on a patch of another background.
  Tested live (18.09.2026, Gemini flash-lite and flash): a checkbox inside the reading prompt missed an
  amount pasted in a serif font every time, the dedicated question caught it every time and did not
  flag the clean original. A careful forgery in the same font passes — that is what the bank app is for.

Date and editing flags are *alerts*: the operator sees them in the order journal, the automatic "paid"
mark is blocked, but the customer is not accused — a false accusation costs more than a forgery the
operator will catch in the bank app anyway.
"""

import base64
import hashlib
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.ai.llm_client import (
    STAGE_RECEIPT,
    STAGE_RECEIPT_INSPECTION,
    Block,
    LLMClient,
    LLMUnavailableError,
    image_block,
)
from app.ai.text_normalize import normalize_fold
from app.core.logging import get_logger, log_event
from app.core.time import business_tz, ensure_utc, now_utc
from app.services.order_pricing import ZERO, money

logger = get_logger(__name__)

__all__ = [
    "ALERTS",
    "MIN_CONFIDENCE",
    "EarlierReceipt",
    "ReceiptInspection",
    "ReceiptReading",
    "ReceiptVerdict",
    "check_receipt",
    "credited_amount",
    "expected_prepayment",
    "file_digest",
    "inspect_receipt",
    "inspection_json_schema",
    "is_card_number",
    "mentions_payment_done",
    "normalize_reference",
    "paid_at_bounds",
    "parse_inspection",
    "read_receipt",
    "receipt_json_schema",
    "receipt_note",
    "transfer_fingerprint",
    "wallet_matches",
]

#: Below this the picture is treated as "not a receipt" (a cake photo, a chat screenshot).
MIN_CONFIDENCE = 0.5
#: Fewer visible digits in the recipient cannot identify an account ("**** 33").
MIN_VISIBLE_DIGITS = 4
#: This many digits is a card or a bank account; a phone wallet is at most 12 ("+992 92 757 53 33").
CARD_DIGITS = 13
MAX_TEXT_CHARS = 120

#: A transaction number shorter than this is too common to identify a transfer ("1234").
MIN_REFERENCE_CHARS = 6
#: Clock drift between the customer's phone and ours before "dated in the future" is raised.
FUTURE_SKEW = timedelta(minutes=15)
#: A transfer this much earlier than the order itself is suspicious (a receipt from another purchase).
BEFORE_ORDER_SKEW = timedelta(hours=1)
#: Operator-only flags: they block the automatic "paid" mark but are never told to the customer.
ALERTS = ("date_future", "date_before_order", "looks_edited")
#: A stored receipt still counts towards the order when nothing is wrong with it but the sum.
COUNTED_CODES = frozenset({"amount_short"})

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
    paid_at_iso: str | None = None
    reference: str | None = None
    confidence: float = 0.0


class ReceiptInspection(BaseModel):
    """How the lines of the screenshot are drawn (``inspect_receipt``) — a hint, never a verdict."""

    model_config = ConfigDict(extra="ignore")

    #: one value drawn unlike the rest: another typeface family, colour, size or background patch
    mismatch: bool = False
    #: which line ("1500,00 TJS"), for the operator
    where: str | None = None
    #: the model's per-line description, kept in ``ai_payload`` for the operator
    lines: list[str] = []


@dataclass(frozen=True, slots=True)
class EarlierReceipt:
    """A stored receipt the new one repeats (``ReceiptRepository.find_earlier``)."""

    order_id: int
    same_order: bool
    #: what matched: "file" (the very same file), "reference" (transaction number), "transfer"
    match: str


@dataclass(frozen=True, slots=True)
class ReceiptVerdict:
    """The deterministic decision: ``ok`` when nothing contradicts the order.

    ``problems`` are told to the customer (stable codes), ``alerts`` only to the operator; either one
    makes ``ok`` false, so the order is never marked paid automatically over a doubt.
    """

    ok: bool
    problems: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    amount: Decimal | None = None
    expected: Decimal = ZERO
    shortfall: Decimal | None = None
    wallet_ok: bool | None = None
    earlier: EarlierReceipt | None = None
    #: the line the inspection pointed at when it saw traces of editing
    edited_where: str | None = None

    @property
    def codes(self) -> list[str]:
        """Problems and alerts together — what is stored with the receipt."""
        return [*self.problems, *self.alerts]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "problems": list(self.problems),
            "alerts": list(self.alerts),
            "amount": f"{self.amount:.2f}" if self.amount is not None else None,
            "expected": f"{self.expected:.2f}",
            "shortfall": f"{self.shortfall:.2f}" if self.shortfall is not None else None,
            "wallet_ok": self.wallet_ok,
            "earlier_order_id": self.earlier.order_id if self.earlier else None,
            "earlier_match": self.earlier.match if self.earlier else None,
            "edited_where": self.edited_where,
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
   paid_at_iso — the same moment as "YYYY-MM-DDTHH:MM" ("YYYY-MM-DD" when no time is printed; numeric
   dates are day first: "18.09.2026" is 18 September), null when no date is printed; reference — the
   transaction / receipt number when printed.
6. confidence — 0.0-1.0 that this is a transfer receipt and you read it correctly.
7. Output only the JSON object — no explanation, no markdown.
"""

#: A separate, narrow question: asked together with the reading, it was ignored (see the module doc).
INSPECTION_RULES = """\
You inspect a screenshot of a bank transfer receipt for visible traces of editing. Do not read or
judge the content; look only at how the text is drawn.
1. For every text line note its typeface family look (sans-serif / serif / monospace), weight, colour,
   and whether its background differs from the surrounding background — one short string per line.
2. Receipt apps draw every value in the same typeface family. A value (the amount above all, the
   recipient, the date, the operation number) in a different family, colour or size, or on a rectangle
   of a different background, is a trace of editing: mismatch=true and "where" is that line as printed.
3. A blurry, cropped, dark or low-quality screenshot is NOT a trace of editing; headings and the app
   logo may legitimately use another style. When unsure — mismatch=false, where=null.
4. Output only the JSON object — no explanation, no markdown.
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
        "paid_at_iso": _nullable({"type": "string"}),
        "reference": _nullable({"type": "string"}),
        "confidence": {"type": "number"},
    }
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def inspection_json_schema() -> dict[str, Any]:
    properties: dict[str, Any] = {
        "lines": {"type": "array", "items": {"type": "string"}},
        "mismatch": {"type": "boolean"},
        "where": _nullable({"type": "string"}),
    }
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def build_receipt_system() -> list[Block]:
    return [{"type": "text", "text": RECEIPT_RULES, "cache_control": {"type": "ephemeral"}}]


def build_receipt_messages(image: bytes, media_type: str, request: str = "Read this receipt") -> list[Block]:
    return [
        {
            "role": "user",
            "content": [
                image_block(base64.b64encode(image).decode("ascii"), media_type),
                {"type": "text", "text": f"{request} and return the JSON object now."},
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
        paid_at_iso=_text(payload.get("paid_at_iso")),
        reference=_text(payload.get("reference")),
        confidence=_confidence(payload.get("confidence")),
    )


MAX_INSPECTION_LINES = 20


def parse_inspection(data: Any) -> ReceiptInspection:
    payload = data if isinstance(data, dict) else {}
    lines = payload.get("lines") if isinstance(payload.get("lines"), list) else []
    mismatch = payload.get("mismatch") is True
    return ReceiptInspection(
        mismatch=mismatch,
        where=_text(payload.get("where")) if mismatch else None,
        lines=[text for line in lines[:MAX_INSPECTION_LINES] if (text := _text(line))],
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


def inspect_receipt(llm: LLMClient, image: bytes, media_type: str) -> ReceiptInspection:
    """Look at how the lines are drawn (a second call). ``LLMError`` propagates; the caller goes on without it."""
    raw = llm.complete_json(
        system=[{"type": "text", "text": INSPECTION_RULES, "cache_control": {"type": "ephemeral"}}],
        messages=build_receipt_messages(image, media_type, "Inspect how this receipt is drawn"),
        schema=inspection_json_schema(),
    )
    if not isinstance(raw, dict):
        raise LLMUnavailableError(reason="invalid_inspection")
    inspection = parse_inspection(raw)
    log_event(
        logger,
        "ai.response",
        stage=STAGE_RECEIPT_INSPECTION,
        mismatch=inspection.mismatch,
        lines=len(inspection.lines),
    )
    return inspection


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


def credited_amount(paid: Decimal | int | str, earlier: Iterable[tuple[Decimal | None, Sequence[str]]]) -> Decimal:
    """What already counts towards the prepayment: registered payments or earlier receipts of the order.

    ``earlier`` is ``(amount, codes)`` of the receipts stored for this order; only those with nothing
    wrong but the sum count ("переведите ещё 200" → the 200 receipt must be enough). Registered
    payments usually describe the same money as the receipts, so the larger of the two is taken.
    """
    receipts = sum(
        (money(amount) for amount, codes in earlier if amount is not None and set(codes) <= COUNTED_CODES), ZERO
    )
    return max(money(paid), money(receipts))


# --------------------------------------------------------------------------- keys of a reused receipt


def file_digest(data: bytes) -> str:
    """SHA-256 of the stored file: the very same screenshot sent again."""
    return hashlib.sha256(data).hexdigest()


def normalize_reference(value: str | None) -> str | None:
    """ "AF-2026-0918-884512" → "AF20260918884512"; too short to identify a transfer → ``None``."""
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]", "", value or "").upper()
    return cleaned[:64] if len(cleaned) >= MIN_REFERENCE_CHARS else None


_ISO_RE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ](\d{1,2}):(\d{2})(?::\d{2})?)?")


def paid_at_bounds(value: str | None) -> tuple[datetime, datetime, bool] | None:
    """``paid_at_iso`` (business-local, as printed) → ``(earliest, latest, precise)`` in UTC.

    A date without a time covers the whole business day, so the date checks give it the benefit of
    the doubt. Anything unparsable or impossible → ``None`` (no date check at all).
    """
    match = _ISO_RE.match(value or "")
    if match is None:
        return None
    year, month, day, hour, minute = match.groups()
    precise = hour is not None
    try:
        start = datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0), tzinfo=business_tz())
    except ValueError:
        return None
    end = start + (timedelta(minutes=1) if precise else timedelta(days=1)) - timedelta(seconds=1)
    return ensure_utc(start), ensure_utc(end), precise


def _identifier(value: str | None) -> str | None:
    """Digits and mask characters of a number as printed ("+992 93 *** 11 22" → "99293***1122")."""
    kept = (char for char in (value or "") if char.isdigit() or char in _MASK_CHARS)
    pattern = "".join(char if char.isdigit() else "*" for char in kept)
    return pattern if sum(char.isdigit() for char in pattern) >= MIN_VISIBLE_DIGITS else None


def transfer_fingerprint(reading: ReceiptReading) -> str | None:
    """Amount + minute + sender: the same transfer on another screenshot (a crop, a photo of the screen).

    All three are required — without the sender, two customers paying the same sum in the same minute
    would look alike.
    """
    bounds = paid_at_bounds(reading.paid_at_iso)
    sender = _identifier(reading.sender)
    if reading.amount is None or bounds is None or not bounds[2] or sender is None:
        return None
    return file_digest(f"{reading.amount:.2f}|{bounds[0]:%Y-%m-%dT%H:%M}|{sender}".encode())


# --------------------------------------------------------------------------- the verdict


def _alerts(
    reading: ReceiptReading, inspection: ReceiptInspection | None, ordered_at: datetime | None, now: datetime
) -> list[str]:
    alerts: list[str] = []
    bounds = paid_at_bounds(reading.paid_at_iso)
    if bounds is not None:
        earliest, latest, _ = bounds
        if earliest > now + FUTURE_SKEW:
            alerts.append("date_future")
        elif ordered_at is not None and latest < ensure_utc(ordered_at) - BEFORE_ORDER_SKEW:
            alerts.append("date_before_order")
    if inspection is not None and inspection.mismatch:
        alerts.append("looks_edited")
    return alerts


def check_receipt(
    reading: ReceiptReading,
    expected: Decimal,
    wallet: str,
    *,
    earlier: EarlierReceipt | None = None,
    inspection: ReceiptInspection | None = None,
    ordered_at: datetime | None = None,
    now: datetime | None = None,
) -> ReceiptVerdict:
    """03 §3: the receipt fits when it is a completed transfer of at least ``expected`` somoni, the
    recipient does not contradict our account (an unreadable recipient is left to the operator), it
    was not sent before, and nothing about its date or look raises a doubt."""
    if not reading.is_receipt or reading.confidence < MIN_CONFIDENCE:
        return ReceiptVerdict(ok=False, problems=["not_receipt"], expected=money(expected))
    problems: list[str] = []
    if reading.status in ("failed", "pending"):
        problems.append("status")
    if not _is_somoni(reading.currency):
        problems.append("currency")
    if earlier is not None:
        problems.append("resent" if earlier.same_order else "duplicate")
    wallet_ok = wallet_matches(reading.recipient, wallet)
    if wallet_ok is False:
        problems.append("wallet_mismatch")
    shortfall: Decimal | None = None
    if reading.amount is None:
        problems.append("amount_unknown")
    elif reading.amount < money(expected):
        shortfall = money(money(expected) - reading.amount)
        problems.append("amount_short")
    alerts = _alerts(reading, inspection, ordered_at, now or now_utc())
    return ReceiptVerdict(
        ok=not problems and not alerts,
        problems=problems,
        alerts=alerts,
        amount=reading.amount,
        expected=money(expected),
        shortfall=shortfall,
        wallet_ok=wallet_ok,
        earlier=earlier,
        edited_where=inspection.where if inspection is not None and inspection.mismatch else None,
    )


_MATCH_LABELS = {
    "file": "тот же файл",
    "reference": "тот же номер операции",
    "transfer": "та же сумма, время и отправитель",
}


def _warnings(reading: ReceiptReading, verdict: ReceiptVerdict) -> list[str]:
    """What the operator must look at twice (never shown to the customer)."""
    warnings: list[str] = []
    earlier = verdict.earlier
    if earlier is not None and not earlier.same_order:
        match = _MATCH_LABELS.get(earlier.match, earlier.match)
        warnings.append(f"этот чек уже присылали к заказу №{earlier.order_id} ({match})")
    if "date_future" in verdict.alerts:
        warnings.append(f"дата перевода в будущем ({reading.paid_at})")
    if "date_before_order" in verdict.alerts:
        warnings.append(f"перевод сделан раньше, чем оформлен заказ ({reading.paid_at})")
    if "looks_edited" in verdict.alerts:
        detail = f" (строка «{verdict.edited_where}» нарисована не так, как остальные)" if verdict.edited_where else ""
        warnings.append(f"на изображении видны признаки правки{detail}")
    return warnings


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
    if reading.reference:
        parts.append(f"операция {reading.reference}")
    if "status" in verdict.problems:
        parts.append(f"статус перевода: {reading.status}")
    if "amount_short" in verdict.problems and verdict.shortfall is not None:
        parts.append(f"не хватает {verdict.shortfall:.2f} сомони")
    if "resent" in verdict.problems:
        parts.append("клиент прислал этот чек повторно")
    note = ", ".join(parts) + "."
    warnings = _warnings(reading, verdict)
    if warnings:
        note += " ВНИМАНИЕ: " + "; ".join(warnings) + "."
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
