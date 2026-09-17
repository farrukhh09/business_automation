"""OpenAI text-to-speech adapter (06 §3, docs/research/voice.md §4.3, §6.7).

``POST https://api.openai.com/v1/audio/speech`` with ``Authorization: Bearer TTS_API_KEY`` and JSON
``{"model": "gpt-4o-mini-tts", "voice": TTS_VOICE, "input": text, "response_format": "wav"}``.
Russian only: no hosted provider documents a Tajik voice, so ``supports("tg") is False`` and Tajik
customers get text replies.
"""

import logging
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.core.exceptions import IntegrationNotConfiguredError
from app.core.logging import get_logger, log_event
from app.integrations.speech.base import (
    TTS_NOT_CONFIGURED_MESSAGE,
    SpeechProviderError,
    SynthesizedAudio,
    normalize_language_code,
)

logger = get_logger(__name__)

PROVIDER = "openai"
OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
OPENAI_TTS_FORMAT = "wav"
OPENAI_TTS_MAX_INPUT_CHARS = 4096
DEFAULT_VOICE = "alloy"
DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
SUPPORTED_LANGUAGES = frozenset({"ru"})


def _provider_error_code(response: httpx.Response) -> str | None:
    """``error.code`` or ``error.type`` only — OpenAI error messages may echo part of the key."""
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        for key in ("code", "type"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:64]
    return None


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class OpenAITTS:
    """``TextToSpeech`` implementation. ``http`` (optional) is used as is and never closed here."""

    provider = PROVIDER

    def __init__(
        self,
        settings: Settings,
        http: httpx.Client | None = None,
        *,
        timeout: httpx.Timeout | float = DEFAULT_TIMEOUT,
    ) -> None:
        api_key = (settings.TTS_API_KEY or "").strip()
        if not api_key:
            raise IntegrationNotConfiguredError(TTS_NOT_CONFIGURED_MESSAGE, provider=PROVIDER)
        self._api_key = api_key
        self._voice = (settings.TTS_VOICE or "").strip() or DEFAULT_VOICE
        self._http = http
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"OpenAITTS(model={OPENAI_TTS_MODEL!r}, voice={self._voice!r})"

    def supports(self, language: str) -> bool:
        return normalize_language_code(language) in SUPPORTED_LANGUAGES

    def synthesize(self, text: str, language: str) -> SynthesizedAudio:
        if not self.supports(language):
            raise ValueError(f"TTS does not support language {language!r}; reply with text instead")
        cleaned = (text or "").strip()
        if not cleaned:
            raise ValueError("TTS input text is empty")
        if len(cleaned) > OPENAI_TTS_MAX_INPUT_CHARS:
            raise ValueError(f"TTS input exceeds {OPENAI_TTS_MAX_INPUT_CHARS} characters")

        payload = {
            "model": OPENAI_TTS_MODEL,
            "voice": self._voice,
            "input": cleaned,
            "response_format": OPENAI_TTS_FORMAT,
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "audio/wav"}
        log_event(
            logger,
            "speech.tts_request",
            provider=PROVIDER,
            model=OPENAI_TTS_MODEL,
            voice=self._voice,
            language=normalize_language_code(language),
            text_chars=len(cleaned),
        )
        started = time.monotonic()
        try:
            response = self._post(headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise self._fail("timeout", started, detail="Сервис синтеза речи не ответил вовремя") from exc
        except httpx.HTTPError as exc:
            raise self._fail(
                "network", started, detail="Сервис синтеза речи недоступен", error=type(exc).__name__
            ) from exc

        if not response.is_success:
            raise self._fail(
                "http_error",
                started,
                status=response.status_code,
                provider_code=_provider_error_code(response),
                detail=f"Сервис синтеза речи вернул ошибку HTTP {response.status_code}",
            )
        content_type = response.headers.get("content-type", "").lower()
        data = response.content
        if not data or "json" in content_type or content_type.startswith("text/"):
            raise self._fail(
                "invalid_response",
                started,
                status=response.status_code,
                detail="Сервис синтеза речи вернул некорректный ответ",
            )
        log_event(
            logger,
            "speech.tts_response",
            provider=PROVIDER,
            status=response.status_code,
            audio_bytes=len(data),
            duration_ms=_elapsed_ms(started),
        )
        return SynthesizedAudio(data=data, mime_type="audio/wav", extension="wav")

    # ------------------------------------------------------------------ internals

    def _post(self, **kwargs: Any) -> httpx.Response:
        # Redirects are pinned off on the request itself so that a shared client created with
        # follow_redirects=True cannot make us post the prompt text (and the Bearer header) elsewhere.
        if self._http is not None:
            return self._http.post(OPENAI_SPEECH_URL, timeout=self._timeout, follow_redirects=False, **kwargs)
        with httpx.Client(timeout=self._timeout, follow_redirects=False) as client:
            return client.post(OPENAI_SPEECH_URL, follow_redirects=False, **kwargs)

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
