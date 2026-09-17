"""Language-hint policy for voice messages (docs/research/voice.md §1, §5; 06 §3).

1. First pass without a hint (auto-detect; never force ``ru`` — RU/TG speech is often mixed).
2. Second pass with the Tajik hint when:
   - the detected language is not ru/tg (``fa``, ``uz``, missing, ...), or
   - the transcript is mostly non-Cyrillic (Latin / Arabic script), or
   - ``language_probability < 0.6``, or
   - the customer previously wrote in Tajik and the first pass says ``ru`` with probability < 0.85
     (or no probability).
3. Keep the better of the two: non-empty, then Cyrillic, then the one with Tajik letters
   ғ ӣ қ ӯ ҳ ҷ, then the higher ``language_probability`` (ties keep the first pass).
If the second pass fails, the first result is kept.
"""

import logging
from enum import StrEnum

from app.core.exceptions import IntegrationError
from app.core.logging import get_logger, log_event
from app.integrations.speech.base import SpeechToText, TranscriptionResult, normalize_language_code

logger = get_logger(__name__)

TAJIK_HINT = "tg"
LOW_PROBABILITY_THRESHOLD = 0.6
TAJIK_CUSTOMER_RU_THRESHOLD = 0.85
MIN_CYRILLIC_RATIO = 0.5
TAJIK_LETTERS = frozenset("ғӣқӯҳҷҒӢҚӮҲҶ")
_EXPECTED_LANGUAGES = frozenset({"ru", "tg"})


class RetryReason(StrEnum):
    UNEXPECTED_LANGUAGE = "unexpected_language"
    NON_CYRILLIC_SCRIPT = "non_cyrillic_script"
    LOW_PROBABILITY = "low_probability"
    TAJIK_CUSTOMER_UNCERTAIN_RU = "tajik_customer_uncertain_ru"


def _is_cyrillic(char: str) -> bool:
    return "Ѐ" <= char <= "ԯ"


def cyrillic_ratio(text: str) -> float | None:
    """Share of Cyrillic among letters; ``None`` when the text has no letters."""
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return None
    return sum(1 for char in letters if _is_cyrillic(char)) / len(letters)


def has_tajik_letters(text: str) -> bool:
    return any(char in TAJIK_LETTERS for char in text)


def tajik_retry_reason(result: TranscriptionResult, customer_language: str | None = None) -> RetryReason | None:
    """Why a second pass with the Tajik hint is needed, or ``None``."""
    language = normalize_language_code(result.language)
    if language not in _EXPECTED_LANGUAGES:
        return RetryReason.UNEXPECTED_LANGUAGE
    ratio = cyrillic_ratio(result.text)
    if ratio is not None and ratio < MIN_CYRILLIC_RATIO:
        return RetryReason.NON_CYRILLIC_SCRIPT
    probability = result.language_probability
    if probability is not None and probability < LOW_PROBABILITY_THRESHOLD:
        return RetryReason.LOW_PROBABILITY
    if (
        normalize_language_code(customer_language) == "tg"
        and language == "ru"
        and (probability is None or probability < TAJIK_CUSTOMER_RU_THRESHOLD)
    ):
        return RetryReason.TAJIK_CUSTOMER_UNCERTAIN_RU
    return None


def choose_better(first: TranscriptionResult, second: TranscriptionResult) -> TranscriptionResult:
    """Pick between the auto-detected (``first``) and the Tajik-hinted (``second``) transcript."""
    first_ratio, second_ratio = cyrillic_ratio(first.text), cyrillic_ratio(second.text)
    if first_ratio is None and second_ratio is not None:
        return second
    if first_ratio is None or second_ratio is None:
        return first
    first_cyrillic, second_cyrillic = first_ratio >= MIN_CYRILLIC_RATIO, second_ratio >= MIN_CYRILLIC_RATIO
    if first_cyrillic != second_cyrillic:
        return first if first_cyrillic else second
    first_tajik, second_tajik = has_tajik_letters(first.text), has_tajik_letters(second.text)
    if first_tajik != second_tajik:
        return first if first_tajik else second
    first_p = first.language_probability if first.language_probability is not None else -1.0
    second_p = second.language_probability if second.language_probability is not None else -1.0
    return second if second_p > first_p else first


def transcribe_with_retry(
    stt: SpeechToText,
    audio: bytes,
    mime_type: str,
    customer_language: str | None = None,
) -> TranscriptionResult:
    """Errors of the first pass propagate (``IntegrationError``); a failed second pass keeps the first."""
    first = stt.transcribe(audio, mime_type=mime_type, language_hint=None)
    reason = tajik_retry_reason(first, customer_language)
    if reason is None:
        return first

    log_event(
        logger,
        "speech.retry",
        provider=stt.provider,
        reason=str(reason),
        first_language=first.language,
        first_probability=first.language_probability,
        customer_language=normalize_language_code(customer_language),
    )
    try:
        second = stt.transcribe(audio, mime_type=mime_type, language_hint=TAJIK_HINT)
    except IntegrationError as exc:
        log_event(
            logger,
            "speech.retry_failed",
            level=logging.WARNING,
            provider=stt.provider,
            reason=str(reason),
            status=getattr(exc, "status", None),
            error_code=exc.code,
        )
        return first

    chosen = choose_better(first, second)
    log_event(
        logger,
        "speech.retry_result",
        provider=stt.provider,
        reason=str(reason),
        chosen="second" if chosen is second else "first",
        first_language=first.language,
        first_probability=first.language_probability,
        second_language=second.language,
        second_probability=second.language_probability,
    )
    return chosen
