"""Reply generation: ``ReplyPlan`` → text (docs/architecture/05-ai.md §6).

``DialogService`` decides *what* to say (the plan: kind, language, facts from the database, missing
fields); this module only decides *how* it is worded:

- kinds in ``TEMPLATE_KINDS`` (greeting, summary, confirmation, cancellation, handoff, map link…) are
  always rendered by ``app.ai.templates`` — their numbers and their meaning must never depend on a model;
  so is an ``ASK_MISSING`` that carries item choices or a refusal (``uses_template``);
- every other kind is worded by the LLM (``complete_text``) under the reply system prompt, then
  checked by ``ResponseGuard`` (money only from FACTS, no confirmation claims, no invented stock or
  discounts, no catalog product outside FACTS) and by a language check. Any LLM error, an empty
  answer or a violation falls back to the template of the same kind.
- a greeting the model opens with when the customer did not greet in this very message is removed
  before the checks (``strip_greeting``): the prompt forbids it, yet in a live Tajik dialog the model
  kept copying the "Ва алейкум ассалом!" of the first reply from the history (18.09.2026). What was
  corrected is reported in ``Reply.fixes``.
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
    "EXACT_ASK_FACTS",
    "TEMPLATE_KINDS",
    "Reply",
    "ReplyKind",
    "ReplyPlan",
    "ReplySource",
    "Responder",
    "fact_amounts",
    "strip_greeting",
    "uses_template",
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
    BLOCKED = "BLOCKED"  # customer is blacklisted by staff — fixed refusal, sent once
    ASK_RECEIPT = "ASK_RECEIPT"  # "оплатил" while a prepayment is awaited → "пришлите чек"
    RECEIPT_RESULT = "RECEIPT_RESULT"  # what the bot read on the receipt and whether it fits the order


#: 05 §6: "Всегда шаблоном (без LLM)".
TEMPLATE_KINDS: frozenset[ReplyKind] = frozenset(
    {
        ReplyKind.GREETING,  # the bakery's own wording: "Добрый день! Что желаете заказать?"
        ReplyKind.ORDER_SUMMARY,
        ReplyKind.ORDER_CONFIRMED,
        ReplyKind.CONFIRMATION_REPEAT,
        ReplyKind.ORDER_CANCELLED,
        ReplyKind.CANCEL_CONFIRM,
        ReplyKind.CANCEL_KEPT,
        ReplyKind.HANDOFF,
        ReplyKind.NEED_MANAGER,
        ReplyKind.ADDRESS_CLARIFY,
        ReplyKind.BLOCKED,
        ReplyKind.ASK_RECEIPT,
        ReplyKind.RECEIPT_RESULT,  # amounts and wallet numbers exactly as checked
    }
)


#: ``ASK_MISSING`` facts the customer must read exactly — the item choices ("какие именно?"), unknown
#: products, a refused date/time, an invalid phone. Worded by the model in live dialogs they turned
#: into invented counts ("какие ещё три?"), a "всё верно" instead of the question, and a repeated
#: refusal of a date that had already been accepted (17.09.2026).
EXACT_ASK_FACTS: tuple[str, ...] = ("pending_items", "unknown_products", "timing_problem", "phone_invalid")


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
    #: What was corrected in the model's text before it was accepted ("greeting_removed").
    fixes: tuple[str, ...] = ()


def uses_template(plan: ReplyPlan) -> bool:
    """Template kinds, questions whose facts must be read exactly (``EXACT_ASK_FACTS``), and "ок" /
    "дальше" / "это всё": a bare "go on" has nothing to word — at an order summary the model answered
    it with "Всё верно, заказ уже ждёт подтверждения" instead of the plain reminder."""
    if plan.kind in TEMPLATE_KINDS:
        return True
    if plan.kind == ReplyKind.SMALL_TALK:
        return plan.facts.get("small_talk") in ("ack", "done")
    return plan.kind == ReplyKind.ASK_MISSING and any(plan.facts.get(key) for key in EXACT_ASK_FACTS)


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


# --------------------------------------------------------------------------- greeting opener

#: A greeting at the very start of a reply, Russian or Tajik, in any of the spellings the customers
#: and the model use: "Здравствуйте!", "Добрый день,", "Салом!", "Ассалому алейкум!", "Ва алейкум
#: ассалом!", "Ваалейкум салом", "Рӯз ба хайр!". A letter right after the words means another word
#: ("Саломат бошед!" is "you're welcome", not a greeting), so the lookahead requires a non-letter.
_GREETING_OPENER_RE = re.compile(
    r"^\s*(?:"
    r"(?:ва\s*)?ал[аеи]йкум\s+(?:ас-?салом[у]?|салом)"
    r"|ас-?салом[у]?(?:\s+ал[аеи]йкум)?"
    r"|салом(?:\s+ал[аеи]йкум)?"
    r"|(?:субҳ|субх|рӯз|руз|шом)\s+ба\s+хайр"
    r"|здравствуйте|здраствуйте|здравствуй|привет(?:ствую|ик)?"
    r"|добр(?:ый|ое|ого)\s+(?:день|утро|вечер|времени\s+суток)"
    r")(?![а-яёӣӯҳқғҷ])[\s!,.…:;—-]*",
    re.IGNORECASE,
)


def strip_greeting(text: str) -> tuple[str, bool]:
    """Remove a greeting opener from a reply: ``"Салом! Навиштем…"`` → ``("Навиштем…", True)``.

    Used when the customer did not greet in this message (no ``greeting`` fact) and the kind is not
    GREETING — the customer was already greeted earlier in the dialog. The remainder starts with a
    capital letter; a reply that was nothing but a greeting comes back empty.
    """
    match = _GREETING_OPENER_RE.match(text)
    if match is None:
        return text, False
    rest = text[match.end() :].lstrip()
    return rest[:1].upper() + rest[1:], True


# --------------------------------------------------------------------------- responder


class Responder:
    """Turns a plan into text. ``llm=None`` (not configured) renders every kind by template."""

    def __init__(self, llm: LLMClient | None = None, *, catalog_names: Iterable[str] = ()) -> None:
        self._llm = llm
        self._catalog_names = [name for name in catalog_names if name and name.strip()]
        self._guard = ResponseGuard(catalog_names=self._catalog_names)

    def generate_reply(self, plan: ReplyPlan) -> Reply:
        template_text = templates.render(plan.kind, plan.language, plan.facts, plan.missing_fields)
        if uses_template(plan) or self._llm is None:
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

        fixes: list[str] = []
        if plan.kind is not ReplyKind.GREETING and not plan.facts.get("greeting"):
            # The customer did not greet in this message: a greeting here is the model repeating itself.
            text, removed = strip_greeting(text)
            if removed:
                fixes.append("greeting_removed")
                log_event(logger, "ai.reply_fixed", kind=plan.kind.value, language=plan.language, fixes=fixes)

        if not text:
            return self._fallback(plan, template_text, ("empty_reply",))

        violations = list(self._check(plan, text))
        if violations:
            return self._fallback(plan, template_text, tuple(violations))
        return Reply(text, plan.kind, plan.language, ReplySource.LLM, fixes=tuple(fixes))

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
