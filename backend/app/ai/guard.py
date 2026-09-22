"""ResponseGuard — anti-hallucination check for LLM replies (05-ai.md §6, SPEC §40).

``responder.generate_reply`` sends the guard the text the LLM produced together with the facts
it was allowed to use.  A violation means the reply is thrown away and the deterministic
template of the same ``kind`` is sent instead (05 §6, last bullet).

Checks (05 §6):

1. **Money.**  Every number next to ``сомони``/``смн``/``сом``/``TJS``/``somoni`` must be one of
   the amounts from FACTS — prices and totals come from the DB only (03 §1.4, SPEC §40).
   ``"1 500"``, ``"1500.00"``, ``"1,5 тыс"`` and ``"TJS 1500"`` are all understood.
2. **Confirmation claims.**  "заказ подтверждён", "подтвердили ваш заказ", "заказ принят",
   "тасдиқ шуд" are allowed only for ``kind == "ORDER_CONFIRMED"``: the bot may not announce a
   confirmation that ``classify_confirmation`` (03 §5) has not granted.
3. **Forbidden phrases.**  Stock ("в наличии"), discounts and free-of-charge promises are
   facts the LLM cannot know (SPEC §40).  Pass ``allowed_phrases`` when FACTS really contain
   them, or build the guard with your own ``forbidden_phrases``.
4. **Products.**  Optional: build the guard with ``catalog_names`` and any catalog product
   mentioned outside ``allowed_product_names`` is reported.  Without a catalog the guard
   cannot tell an invented product from an ordinary noun, so it reports none — inventing a
   name still costs the LLM its money check, and the summary/confirmation texts that carry
   product names are templates (05 §6) that never reach the guard.
5. **Script.**  Arabic/Persian, Hebrew or CJK characters in a reply (a model writing Tajik
   sometimes slips into Persian script) → ``foreign_script``; the customer reads Cyrillic.

Length is **not** a violation: 06 §1 splits a reply longer than
:data:`INSTAGRAM_TEXT_LIMIT_BYTES` into several messages inside the Instagram client.
:func:`utf8_len` is exposed for that caller.
"""

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.ai.text_normalize import normalize_fold, tajik_fold
from app.core.logging import get_logger, log_event

__all__ = [
    "DEFAULT_FORBIDDEN_PHRASES",
    "INSTAGRAM_TEXT_LIMIT_BYTES",
    "CONFIRMATION_KIND",
    "GuardResult",
    "ResponseGuard",
    "exceeds_message_limit",
    "utf8_len",
]

logger = get_logger(__name__)

#: 06 §1: Instagram accepts 1000 bytes of UTF-8 per message; longer replies are split there.
INSTAGRAM_TEXT_LIMIT_BYTES = 1000

#: The only ReplyPlan kind allowed to state that an order is confirmed (05 §6).
CONFIRMATION_KIND = "ORDER_CONFIRMED"

#: Everything is baked to order, so the bot may not promise that it goes out today or right now:
#: skipping the lead time is the owner's exception to make, never the bot's. Live on 22.09.2026 the
#: model answered «Я думала сейчас можете отправить» with «сейчас отправим таксистом по Худжанду»,
#: while the owner's own answer in the archive is «У нас предзаказ хотя бы за день». A negated
#: sentence ("сегодня не отправим") is not a promise and stays allowed.
_SAME_DAY_ACTIONS = r"отправ\w+|привез\w+|привоз\w+|достав\w+|испеч\w+|печ[её]м|сдела\w+|успе\w+|готов\w+"
_TODAY_PROMISE_RES = (
    re.compile(rf"\b(?:сегодня|сейчас)\b[^.!?]{{0,40}}?\b(?:{_SAME_DAY_ACTIONS})"),
    re.compile(rf"\b(?:{_SAME_DAY_ACTIONS})[^.!?]{{0,40}}?\b(?:сегодня|сейчас)\b"),
    re.compile(r"\b(?:имруз|хозир)\b[^.!?]{0,40}?\b(?:мефиристем|мефиристам|меорем|мерасонем|тайер|тайёр)"),
)
#: Words that turn such a sentence into a refusal instead of a promise.
_NEGATIONS = (" не ", " нельзя", " никак", " наме", " нест")

#: Claims the bot must never invent (SPEC §40: наличие, скидки, обещания).
DEFAULT_FORBIDDEN_PHRASES = (
    "в наличии",
    "в наличие",
    "скидк",
    "бесплатн",
    "гарантиру",
    "обещаю",
)

_CURRENCY = r"(?:сомони|somoni|смн|сом|tjs)"
# Thousands are written with a space ("1 500"); _plain_spaces() turns every Unicode space
# into a plain one before matching, so a single literal space in the class is enough.
_NUMBER = r"\d{1,3}(?: \d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_THOUSANDS = r"(?:тыс\.?|тысяч\w*|хазор)"

_AMOUNT_RE = re.compile(rf"(?P<number>{_NUMBER})\s*(?P<thousands>{_THOUSANDS})?\s*(?P<currency>{_CURRENCY})\b", re.I)
# Prefix notation exists only for the Latin forms ("TJS 1500"); "сомони 2 торта" is not a price.
_AMOUNT_PREFIX_RE = re.compile(
    rf"\b(?P<currency>tjs|somoni)\s*(?P<number>{_NUMBER})\s*(?P<thousands>{_THOUSANDS})?", re.I
)

# Matched against the normalized + folded text, so punctuation and "ё" do not matter.
_CONFIRMATION_CLAIM_RES = (
    re.compile(r"\bзаказ\w*\s+(?:уже\s+)?(?:успешно\s+)?(?:подтвержд\w+|принят\w*|оформлен\w*)\b"),
    re.compile(r"\bподтвержда\w*\s+(?:ваш\w*\s+)?заказ\w*\b"),
    re.compile(r"\bподтвердил\w*\s+(?:ваш\w*\s+)?заказ\w*\b"),
    re.compile(r"\bприня\w+\s+ваш\w*\s+заказ\w*\b"),
    re.compile(r"\bтасдик\s+шуд\b"),
    re.compile(r"\bфармоиш\w*\s+(?:шумо\s+)?тасдик\w*\b"),
)

_MONEY_QUANT = Decimal("0.01")

# Scripts a reply to a Khujand customer never needs: Arabic/Persian (a model answering in Tajik
# sometimes slips into Persian — "ба شما"), Hebrew, CJK, Hangul. Cyrillic, Latin, digits and emoji pass.
_FOREIGN_SCRIPT_RE = re.compile(
    r"[֐-׿؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿"
    r"぀-ヿ一-鿿가-힯]"
)


def _plain_spaces(text: str) -> str:
    """Non-breaking / narrow spaces (thousand separators) become ordinary spaces."""
    return "".join(" " if char.isspace() else char for char in text)


def utf8_len(text: str | None) -> int:
    """Length of the text in UTF-8 bytes — the unit Instagram counts (06 §1)."""
    return len((text or "").encode("utf-8"))


def exceeds_message_limit(text: str | None, limit: int = INSTAGRAM_TEXT_LIMIT_BYTES) -> bool:
    """True when the Instagram client has to split the reply.  Not a guard violation."""
    return utf8_len(text) > limit


@dataclass(frozen=True)
class GuardResult:
    """``violations`` are stable ``code:detail`` strings, safe to log (no message body)."""

    ok: bool
    violations: list[str] = field(default_factory=list)

    @classmethod
    def from_violations(cls, violations: list[str]) -> "GuardResult":
        return cls(ok=not violations, violations=violations)

    def __bool__(self) -> bool:
        return self.ok


def _to_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _parse_amount(number: str, thousands: str | None) -> Decimal | None:
    """``"1 500"`` → 1500, ``"1500.00"`` → 1500.00, ``"1,5" + "тыс"`` → 1500."""
    cleaned = number.replace(" ", "").replace(",", ".")
    value = _to_decimal(cleaned)
    if value is None:
        return None
    return value * 1000 if thousands else value


def _format_amount(value: Decimal) -> str:
    try:
        return str(value.quantize(_MONEY_QUANT))
    except InvalidOperation:  # pragma: no cover - absurdly large numbers only
        return str(value)


class ResponseGuard:
    """Validates one generated reply against the facts it was allowed to use (05 §6)."""

    def __init__(
        self,
        *,
        catalog_names: Iterable[str] = (),
        forbidden_phrases: Iterable[str] = DEFAULT_FORBIDDEN_PHRASES,
        allow_same_day: bool = False,
    ) -> None:
        self._catalog: dict[str, str] = {}
        self._allow_same_day = allow_same_day
        for name in catalog_names:
            key = normalize_fold(name)
            if key:
                self._catalog[key] = str(name)
        self._forbidden = tuple(phrase for phrase in (normalize_fold(item) for item in forbidden_phrases) if phrase)

    def check(
        self,
        text: str | None,
        *,
        allowed_amounts: Iterable[Decimal] = (),
        allowed_product_names: Iterable[str] = (),
        kind: str = "",
        allowed_phrases: Iterable[str] = (),
    ) -> GuardResult:
        """Return the violations of one reply.  Never raises; empty text is valid."""
        violations: list[str] = []
        if not text or not text.strip():
            return GuardResult.from_violations(violations)

        # Lower-cased, Tajik-folded, every Unicode space flattened; punctuation and digits
        # are kept so that prices can be parsed.
        folded = tajik_fold(_plain_spaces(str(text).lower()))
        normalized = normalize_fold(text)

        violations.extend(self._check_amounts(folded, allowed_amounts))
        violations.extend(self._check_confirmation(normalized, kind))
        violations.extend(self._check_same_day(normalized))
        violations.extend(self._check_forbidden(normalized, allowed_phrases))
        violations.extend(self._check_products(normalized, allowed_product_names))
        if _FOREIGN_SCRIPT_RE.search(str(text)):
            violations.append("foreign_script")

        if violations:
            log_event(
                logger,
                "ai.guard_violation",
                level=logging.WARNING,
                kind=kind or None,
                violations=violations,
                length_bytes=utf8_len(text),
            )
        return GuardResult.from_violations(violations)

    # ------------------------------------------------------------------ checks

    @staticmethod
    def _check_amounts(folded: str, allowed_amounts: Iterable[Decimal]) -> list[str]:
        """05 §6: numbers next to a currency word must come from FACTS (03 §1.4, SPEC §40)."""
        allowed = {value for value in (_to_decimal(amount) for amount in allowed_amounts) if value is not None}
        violations: list[str] = []
        seen: set[Decimal] = set()
        for pattern in (_AMOUNT_RE, _AMOUNT_PREFIX_RE):
            for match in pattern.finditer(folded):
                value = _parse_amount(match.group("number"), match.group("thousands"))
                if value is None or value in seen:
                    continue
                seen.add(value)
                if value not in allowed:
                    violations.append(f"amount_not_allowed:{_format_amount(value)}")
        return violations

    @staticmethod
    def _check_confirmation(normalized: str, kind: str) -> list[str]:
        """05 §6: only ORDER_CONFIRMED may state that the order is confirmed (03 §5)."""
        if str(kind).strip().upper() == CONFIRMATION_KIND:
            return []
        violations: list[str] = []
        for pattern in _CONFIRMATION_CLAIM_RES:
            match = pattern.search(normalized)
            if match:
                violations.append(f"confirmation_claim:{match.group(0)}")
        return violations

    def _check_same_day(self, normalized: str) -> list[str]:
        """A promise to bake, send or deliver today, which only the owner may make (see above)."""
        if self._allow_same_day:
            return []
        violations: list[str] = []
        for pattern in _TODAY_PROMISE_RES:
            match = pattern.search(normalized)
            if match and not any(word in f" {match.group(0)} " for word in _NEGATIONS):
                violations.append(f"same_day_promise:{match.group(0)}")
        return violations

    def _check_forbidden(self, normalized: str, allowed_phrases: Iterable[str]) -> list[str]:
        allowed = {phrase for phrase in (normalize_fold(item) for item in allowed_phrases) if phrase}
        return [
            f"forbidden_phrase:{phrase}"
            for phrase in self._forbidden
            if phrase in normalized and not any(phrase in item or item in phrase for item in allowed)
        ]

    def _check_products(self, normalized: str, allowed_product_names: Iterable[str]) -> list[str]:
        """Catalog products mentioned outside FACTS (only possible with ``catalog_names``)."""
        if not self._catalog:
            return []
        allowed = {name for name in (normalize_fold(item) for item in allowed_product_names) if name}
        padded = f" {normalized} "
        return [
            f"product_not_allowed:{self._catalog[key]}"
            for key in self._catalog
            if key not in allowed and f" {key} " in padded
        ]
