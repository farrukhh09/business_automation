"""Speech protocols and value objects (docs/architecture/06-integrations.md §3).

``TranscriptionResult.language`` is the provider's code exactly as returned (ElevenLabs Scribe may
return ISO-639-3 such as ``"rus"``/``"tgk"`` or ISO-639-1 such as ``"ru"``); use
``normalize_language_code`` / ``TranscriptionResult.app_language`` to map it to ``"ru"`` / ``"tg"``.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.core.exceptions import IntegrationError

STT_NOT_CONFIGURED_MESSAGE = "Распознавание голосовых сообщений не настроено: не задан STT_API_KEY"
TTS_NOT_CONFIGURED_MESSAGE = "Голосовые ответы не настроены: не задан TTS_API_KEY"

PREVIEW_CHARS = 40

# Transient HTTP statuses (besides 5xx): 408 Request Timeout and 429 Too Many Requests.
RETRYABLE_STATUSES = frozenset({408, 429})


def text_preview(text: str | None, limit: int = PREVIEW_CHARS) -> str:
    """Short single-line preview for logs (transcripts are personal data — never log them whole)."""
    if not text:
        return ""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"

# ISO-639-1 / ISO-639-3 / BCP-47 variants → application language (models.enums.Language values).
_LANGUAGE_ALIASES: dict[str, str] = {
    "ru": "ru",
    "rus": "ru",
    "tg": "tg",
    "tgk": "tg",
}


def normalize_language_code(code: str | None) -> str | None:
    """``"rus"``/``"ru-RU"`` → ``"ru"``; ``"tgk"``/``"tg-TJ"``/``"tgk_Cyrl"`` → ``"tg"``.

    Any other code is reduced to its lowercased primary subtag (``"fa-IR"`` → ``"fa"``).
    """
    if code is None:
        return None
    text = str(code).strip().lower()
    if not text:
        return None
    primary = text.replace("_", "-").split("-", 1)[0]
    return _LANGUAGE_ALIASES.get(primary, primary)


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    language: str | None  # provider code as returned, e.g. "rus", "tgk"
    language_probability: float | None
    provider: str
    language_hint: str | None = None  # provider-level code sent as a hint on this pass, e.g. "tgk"

    @property
    def confidence(self) -> float | None:
        """06 §3 names this field "confidence"."""
        return self.language_probability

    @property
    def app_language(self) -> str | None:
        """``"ru"`` / ``"tg"`` or ``None`` for any other (or missing) language."""
        normalized = normalize_language_code(self.language)
        return normalized if normalized in ("ru", "tg") else None


@dataclass(frozen=True, slots=True)
class SynthesizedAudio:
    data: bytes
    mime_type: str  # e.g. "audio/wav"
    extension: str  # without the dot, e.g. "wav", "m4a"

    def __repr__(self) -> str:  # never dump audio bytes into logs/tracebacks
        return f"SynthesizedAudio(mime_type={self.mime_type!r}, extension={self.extension!r}, size={len(self.data)})"


@runtime_checkable
class SpeechToText(Protocol):
    provider: str

    def transcribe(
        self, audio: bytes, *, mime_type: str, language_hint: str | None = None
    ) -> TranscriptionResult: ...


@runtime_checkable
class TextToSpeech(Protocol):
    provider: str

    def supports(self, language: str) -> bool: ...

    def synthesize(self, text: str, *, language: str) -> SynthesizedAudio: ...


class SpeechProviderError(IntegrationError):
    """STT/TTS provider call failed. ``status`` is the HTTP status (``None`` for timeouts/network errors)."""

    code = "speech_provider_error"
    default_detail = "Ошибка сервиса речи"

    def __init__(
        self,
        detail: str | None = None,
        *,
        provider: str,
        status: int | None = None,
        reason: str = "http_error",
        provider_code: str | None = None,
    ) -> None:
        self.provider = provider
        self.status = status
        self.reason = reason
        self.provider_code = provider_code
        super().__init__(detail, provider=provider, status=status, reason=reason)

    @property
    def retryable(self) -> bool:
        """Timeouts, network errors, 408, 429 and 5xx are worth retrying (Celery backoff)."""
        if self.reason in ("timeout", "network"):
            return True
        return self.status is not None and (self.status in RETRYABLE_STATUSES or self.status >= 500)
