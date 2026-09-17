"""Reply generation: ``ReplyPlan`` → text (docs/architecture/05-ai.md §6).

``DialogService`` decides *what* to say (the plan: kind, language, facts from the database, missing
fields); this module only decides *how* it is worded:

- kinds in ``TEMPLATE_KINDS`` (summary, confirmation, cancellation, handoff, map link…) are always
  rendered by ``app.ai.templates`` — their numbers and their meaning must never depend on a model;
- every other kind is worded by the LLM (``complete_text``) under the reply system prompt, then
  checked by ``ResponseGuard`` (money only from FACTS, no confirmation claims, no invented stock or
  discounts, no catalog product outside FACTS) and by a language check. Any LLM error, an empty
  answer or a violation falls back to the template of the same kind.
"""

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from app.ai import prompts, templates
from app.ai.guard import ResponseGuard
from app.ai.language import detect_language
from app.ai.llm_client import LLMClient, LLMError
from app.ai.text_normalize import normalize_fold
from app.core.logging import get_logger, log_event

logger = get_logger(__name__)

__all__ = [
    "TEMPLATE_KINDS",
    "Reply",
    "ReplyKind",
    "ReplyPlan",
    "ReplySource",
    "Responder",
    "fact_amounts",
]


class ReplyKind(StrEnum):
    GREETING = "GREETING"
    ASK_MISSING = "ASK_MISSING"
    ORDER_SUMMARY = "ORDER_SUMMARY"
    ORDER_CONFIRMED = "ORDER_CONFIRMED"
    CONFIRMATION_REPEAT = "CONFIRMATION_REPEAT"
    ASK_WHAT_TO_CHANGE = "ASK_WHAT_TO_CHANGE"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    CANCEL_CONFIRM = "CANCEL_CONFIRM"
    CANCEL_KEPT = "CANCEL_KEPT"
    FAQ_ANSWER = "FAQ_ANSWER"
    PRODUCT_INFO = "PRODUCT_INFO"
    ORDER_STATUS_INFO = "ORDER_STATUS_INFO"
    DELIVERY_INFO = "DELIVERY_INFO"
    PAYMENT_INFO = "PAYMENT_INFO"
    ADDRESS_CLARIFY = "ADDRESS_CLARIFY"
    HANDOFF = "HANDOFF"
    NEED_MANAGER = "NEED_MANAGER"
    UNKNOWN_PRODUCT = "UNKNOWN_PRODUCT"
    CLARIFY = "CLARIFY"
    SMALL_TALK = "SMALL_TALK"  # thanks, goodbye, "ок", chat — worded by the LLM, template fallback


#: 05 §6: "Всегда шаблоном (без LLM)".
TEMPLATE_KINDS: frozenset[ReplyKind] = frozenset(
    {
        ReplyKind.ORDER_SUMMARY,
        ReplyKind.ORDER_CONFIRMED,
        ReplyKind.CONFIRMATION_REPEAT,
        ReplyKind.ORDER_CANCELLED,
        ReplyKind.CANCEL_CONFIRM,
        ReplyKind.CANCEL_KEPT,
        ReplyKind.HANDOFF,
        ReplyKind.NEED_MANAGER,
        ReplyKind.ADDRESS_CLARIFY,
    }
)


class ReplySource(StrEnum):
    TEMPLATE = "template"  # the kind is template-only, or no LLM is configured
    LLM = "llm"  # worded by the model and accepted by the guard
    FALLBACK = "fallback"  # the model failed or its text was rejected → template


@dataclass(frozen=True, slots=True)
class ReplyPlan:
    """What the backend decided to say (05 §6). ``facts`` must be JSON-serializable."""

    kind: ReplyKind
    language: str
    facts: dict[str, Any] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    question_hint: str | None = None
    #: Wording context only (customer's last message, recent turns, name) — never a source of facts:
    #: the guard still allows only the amounts and products of ``facts``.
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    kind: ReplyKind
    language: str
    source: ReplySource
    violations: tuple[str, ...] = ()


# --------------------------------------------------------------------------- facts → guard inputs

_MONEY_KEY_RE = re.compile(r"(price|total|amount|paid|sum)", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
_SPACES_RE = re.compile(r"[   ]")
_DATE_OR_TIME_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}(:\d{2})?)\s*$")


def _to_decimal(text: str) -> Decimal | None:
    try:
        number = Decimal(_SPACES_RE.sub("", text).replace(",", "."))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _walk(value: Any, key: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            yield from _walk(child, str(child_key))
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk(child, key)
    else:
        yield key, value


def fact_amounts(facts: Mapping[str, Any]) -> set[Decimal]:
    """Every amount the reply may mention: money fields and numbers inside FACTS texts.

    ISO dates / ``HH:MM`` values are skipped (their parts are not prices); a number written inside
    an admin text ("доставка 20 сомони") is allowed because it *is* a fact.
    """
    amounts: set[Decimal] = set()
    for key, value in _walk(facts):
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float, Decimal)):
            if _MONEY_KEY_RE.search(key):
                number = _to_decimal(str(value))
                if number is not None:
                    amounts.add(number)
            continue
        text = str(value)
        if _DATE_OR_TIME_RE.match(text):
            continue
        for match in _NUMBER_RE.finditer(text):
            number = _to_decimal(match.group(0))
            if number is not None:
                amounts.add(number)
    return amounts


def _fact_strings(facts: Mapping[str, Any]) -> list[str]:
    return [str(value) for _, value in _walk(facts) if isinstance(value, str) and value.strip()]


# --------------------------------------------------------------------------- responder


class Responder:
    """Turns a plan into text. ``llm=None`` (not configured) renders every kind by template."""

    def __init__(self, llm: LLMClient | None = None, *, catalog_names: Iterable[str] = ()) -> None:
        self._llm = llm
        self._catalog_names = [name for name in catalog_names if name and name.strip()]
        self._guard = ResponseGuard(catalog_names=self._catalog_names)

    def generate_reply(self, plan: ReplyPlan) -> Reply:
        template_text = templates.render(plan.kind, plan.language, plan.facts, plan.missing_fields)
        if plan.kind in TEMPLATE_KINDS or self._llm is None:
            return Reply(template_text, plan.kind, plan.language, ReplySource.TEMPLATE)

        try:
            text = self._llm.complete_text(
                system=prompts.build_reply_system(),
                messages=prompts.build_reply_messages(
                    plan.kind.value, plan.language, plan.facts, plan.missing_fields, plan.question_hint, plan.context
                ),
            ).strip()
        except LLMError as exc:
            return self._fallback(plan, template_text, (f"llm_error:{exc.reason}",))

        if not text:
            return self._fallback(plan, template_text, ("empty_reply",))

        violations = list(self._check(plan, text))
        if violations:
            return self._fallback(plan, template_text, tuple(violations))
        return Reply(text, plan.kind, plan.language, ReplySource.LLM)

    # ------------------------------------------------------------------ internals

    def _check(self, plan: ReplyPlan, text: str) -> list[str]:
        strings = _fact_strings(plan.facts)
        facts_blob = f" {normalize_fold(' '.join(strings))} "
        allowed_products = [name for name in self._catalog_names if f" {normalize_fold(name)} " in facts_blob]
        result = self._guard.check(
            text,
            allowed_amounts=fact_amounts(plan.facts),
            allowed_product_names=allowed_products,
            kind=plan.kind.value,
            allowed_phrases=strings,
        )
        violations = list(result.violations)
        # A reply in the other language — or a Tajik reply that is really Russian with "сомонӣ" in it —
        # is useless to the customer (05 §6 rule 3 of the prompt). Catalog names and the texts of FACTS
        # (descriptions, FAQ answers) are not evidence; Tajik letters are scored, not decisive.
        detected = detect_language(
            text,
            llm_language=None,
            customer_language=plan.language,
            ignore_phrases=[*self._catalog_names, *strings],
            decisive_letters=False,
        )
        if detected != plan.language:
            violations.append(f"language_mismatch:{detected}")
        return violations

    def _fallback(self, plan: ReplyPlan, template_text: str, violations: tuple[str, ...]) -> Reply:
        log_event(
            logger,
            "ai.reply_fallback",
            level=logging.WARNING,
            kind=plan.kind.value,
            language=plan.language,
            violations=list(violations),
        )
        return Reply(template_text, plan.kind, plan.language, ReplySource.FALLBACK, violations)
