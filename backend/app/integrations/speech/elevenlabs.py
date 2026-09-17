"""ElevenLabs Scribe v2 speech-to-text adapter (06 §3, docs/research/voice.md §4.1, §6.3).

``POST https://api.elevenlabs.io/v1/speech-to-text`` (multipart/form-data, header ``xi-api-key``):
``file`` (original Instagram bytes; filename extension derived from the MIME type), ``model_id=scribe_v2``,
``tag_audio_events=false`` and — only when a hint is given — ``language_code``.

Language code format (API reference re-checked 2026-09-15): the request accepts "an ISO-639-1 or
ISO-639-3 language_code"; we send ISO-639-3 (``tgk`` for Tajik, ``rus`` for Russian — the codes the
ElevenLabs capability page lists, as in voice.md). The reference response example shows ``"en"``, so the
returned ``language_code`` may be either form: callers normalise it with ``normalize_language_code``.
"""

import logging
import math
import re
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.core.exceptions import IntegrationNotConfiguredError
from app.core.logging import get_logger, log_event
from app.integrations.speech.base import (
    STT_NOT_CONFIGURED_MESSAGE,
    SpeechProviderError,
    TranscriptionResult,
    text_preview,
)

logger = get_logger(__name__)

PROVIDER = "elevenlabs"
ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
ELEVENLABS_STT_MODEL = "scribe_v2"
DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

FALLBACK_EXTENSION = "mp4"
FALLBACK_CONTENT_TYPE = "audio/mp4"
_MIME_EXTENSIONS: dict[str, str] = {
    "audio/mp4": "mp4",
    "video/mp4": "mp4",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "aac",
    "audio/x-aac": "aac",
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/webm": "webm",
    "audio/flac": "flac",
}
# Application language (models.enums.Language) → ElevenLabs ISO-639-3 code.
_HINT_CODES: dict[str, str] = {"ru": "rus", "rus": "rus", "tg": "tgk", "tgk": "tgk"}
_ISO_CODE_RE = re.compile(r"^[a-z]{2,3}$")


def normalize_mime_type(mime_type: str | None) -> str:
    """``"Audio/MP4; codecs=mp4a.40.2"`` → ``"audio/mp4"``; anything that is not a string → ``""``."""
    if not isinstance(mime_type, str):  # content types come from an HTTP header / DB column
        return ""
    return mime_type.split(";", 1)[0].strip().lower()


def filename_for_mime(mime_type: str | None) -> str:
    """Upload filename: ``audio/mp4`` → ``voice.mp4``, ``audio/x-m4a`` → ``voice.m4a``, unknown → ``voice.mp4``."""
    return f"voice.{_MIME_EXTENSIONS.get(normalize_mime_type(mime_type), FALLBACK_EXTENSION)}"


def upload_content_type(mime_type: str | None) -> str:
    """Part Content-Type consistent with the filename (unknown types are sent as ``audio/mp4``)."""
    normalized = normalize_mime_type(mime_type)
    return normalized if normalized in _MIME_EXTENSIONS else FALLBACK_CONTENT_TYPE


def elevenlabs_language_code(hint: str | None) -> str | None:
    """``"tg"``/``"tg-TJ"``/``"tgk"`` → ``"tgk"``; ``"ru"``/``"rus"`` → ``"rus"``; other ISO codes pass through."""
    if hint is None:
        return None
    text = str(hint).strip().lower().replace("_", "-")
    if not text:
        return None
    primary = text.split("-", 1)[0]
    if primary in _HINT_CODES:
        return _HINT_CODES[primary]
    if _ISO_CODE_RE.match(primary):
        return primary
    raise ValueError(f"Unsupported language hint: {hint!r}")


def _provider_error_code(response: httpx.Response) -> str | None:
    """Machine code only (``detail.status`` or 422 ``detail[0].type``); provider messages are never kept."""
    try:
        body = response.json()
    except ValueError:
        return None
    detail = body.get("detail") if isinstance(body, dict) else None
    code: Any = None
    if isinstance(detail, dict):
        code = detail.get("status") or detail.get("code")
    elif isinstance(detail, list) and detail and isinstance(detail[0], dict):
        code = detail[0].get("type")
    if isinstance(code, str) and code.strip():
        return code.strip()[:64]
    return None


def _as_probability(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and 0.0 <= number <= 1.0 else None


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class ElevenLabsSTT:
    """``SpeechToText`` implementation. ``http`` (optional) is used as is and never closed here."""

    provider = PROVIDER

    def __init__(
        self,
        settings: Settings,
        http: httpx.Client | None = None,
        *,
        timeout: httpx.Timeout | float = DEFAULT_TIMEOUT,
    ) -> None:
        api_key = (settings.STT_API_KEY or "").strip()
        if not api_key:
            raise IntegrationNotConfiguredError(STT_NOT_CONFIGURED_MESSAGE, provider=PROVIDER)
        self._api_key = api_key
        self._http = http
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"ElevenLabsSTT(model={ELEVENLABS_STT_MODEL!r})"

    def transcribe(self, audio: bytes, mime_type: str, language_hint: str | None = None) -> TranscriptionResult:
        language_code = elevenlabs_language_code(language_hint)
        started = time.monotonic()
        if not audio:
            raise self._fail("empty_audio", started, detail="Пустой аудиофайл: распознавать нечего")

        form: dict[str, str] = {"model_id": ELEVENLABS_STT_MODEL, "tag_audio_events": "false"}
        if language_code:
            form["language_code"] = language_code
        upload_name = filename_for_mime(mime_type)
        files = {"file": (upload_name, audio, upload_content_type(mime_type))}
        headers = {"xi-api-key": self._api_key, "Accept": "application/json"}

        log_event(
            logger,
            "speech.request",
            provider=PROVIDER,
            model=ELEVENLABS_STT_MODEL,
            audio_bytes=len(audio),
            mime_type=normalize_mime_type(mime_type) or None,
            upload_name=upload_name,
            language_code=language_code,
        )
        try:
            response = self._post(headers=headers, data=form, files=files)
        except httpx.TimeoutException as exc:
            raise self._fail("timeout", started, detail="Сервис распознавания речи не ответил вовремя") from exc
        except httpx.HTTPError as exc:
            raise self._fail(
                "network", started, detail="Сервис распознавания речи недоступен", error=type(exc).__name__
            ) from exc

        if not response.is_success:
            raise self._fail(
                "http_error",
                started,
                status=response.status_code,
                provider_code=_provider_error_code(response),
                detail=f"Сервис распознавания речи вернул ошибку HTTP {response.status_code}",
            )
        return self._parse(response, language_code, started)

    # ------------------------------------------------------------------ internals

    def _post(self, **kwargs: Any) -> httpx.Response:
        # follow_redirects is pinned off on the request itself: httpx strips ``Authorization`` on a
        # cross-origin redirect but never a custom header, so a 3xx would resend ``xi-api-key`` to
        # the redirect target — even when the caller shares a client with redirects enabled.
        if self._http is not None:
            return self._http.post(ELEVENLABS_STT_URL, timeout=self._timeout, follow_redirects=False, **kwargs)
        with httpx.Client(timeout=self._timeout, follow_redirects=False) as client:
            return client.post(ELEVENLABS_STT_URL, follow_redirects=False, **kwargs)

    def _parse(self, response: httpx.Response, language_code: str | None, started: float) -> TranscriptionResult:
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("text"), str):
            raise self._fail(
                "invalid_response",
                started,
                status=response.status_code,
                detail="Сервис распознавания речи вернул некорректный ответ",
            )
        raw_language = body.get("language_code")
        language = raw_language.strip() if isinstance(raw_language, str) and raw_language.strip() else None
        probability = _as_probability(body.get("language_probability"))
        result = TranscriptionResult(
            text=body["text"].strip(),
            language=language,
            language_probability=probability,
            provider=PROVIDER,
            language_hint=language_code,
        )
        log_event(
            logger,
            "speech.response",
            provider=PROVIDER,
            status=response.status_code,
            language=language,
            language_probability=probability,
            language_code=language_code,
            text_chars=len(result.text),
            text_preview=text_preview(result.text),
            duration_ms=_elapsed_ms(started),
        )
        return result

    def _fail(
        self,
        reason: str,
        started: float,
        *,
        detail: str,
        status: int | None = None,
        provider_code: str | None = None,
        error: str | None = None,
    ) -> SpeechProviderError:
        log_event(
            logger,
            "speech.error",
            level=logging.WARNING,
            provider=PROVIDER,
            reason=reason,
            status=status,
            provider_code=provider_code,
            error=error,
            duration_ms=_elapsed_ms(started),
        )
        return SpeechProviderError(detail, provider=PROVIDER, status=status, reason=reason, provider_code=provider_code)
