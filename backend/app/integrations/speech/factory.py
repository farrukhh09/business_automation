"""Speech adapter factories (06 §3): pick the implementation from settings.

A provider that is not configured (empty provider, missing key, unknown name) raises
``IntegrationNotConfiguredError`` with a Russian message; callers degrade gracefully
(voice message saved with ``text=null`` + ``needs_attention``, or a text-only reply).
"""

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import IntegrationNotConfiguredError
from app.integrations.speech.base import (
    STT_NOT_CONFIGURED_MESSAGE,
    TTS_NOT_CONFIGURED_MESSAGE,
    SpeechToText,
    TextToSpeech,
)
from app.integrations.speech.elevenlabs import ElevenLabsSTT
from app.integrations.speech.openai_tts import OpenAITTS

_DISABLED = frozenset({"", "none", "disabled", "off"})


def get_stt(settings: Settings | None = None, *, http: httpx.Client | None = None) -> SpeechToText:
    settings = settings or get_settings()
    provider = (settings.STT_PROVIDER or "").strip().lower()
    if provider in _DISABLED:
        raise IntegrationNotConfiguredError(
            "Распознавание голосовых сообщений не настроено: не задан STT_PROVIDER", provider=provider or None
        )
    if provider == "elevenlabs":
        if not (settings.STT_API_KEY or "").strip():
            raise IntegrationNotConfiguredError(STT_NOT_CONFIGURED_MESSAGE, provider=provider)
        return ElevenLabsSTT(settings, http=http)
    raise IntegrationNotConfiguredError(f"Неизвестный провайдер распознавания речи: {provider}", provider=provider)


def get_tts(settings: Settings | None = None, *, http: httpx.Client | None = None) -> TextToSpeech:
    settings = settings or get_settings()
    provider = (settings.TTS_PROVIDER or "").strip().lower()
    if provider in _DISABLED:
        raise IntegrationNotConfiguredError(
            "Голосовые ответы не настроены: не задан TTS_PROVIDER", provider=provider or None
        )
    if provider == "openai":
        if not (settings.TTS_API_KEY or "").strip():
            raise IntegrationNotConfiguredError(TTS_NOT_CONFIGURED_MESSAGE, provider=provider)
        return OpenAITTS(settings, http=http)
    raise IntegrationNotConfiguredError(f"Неизвестный провайдер синтеза речи: {provider}", provider=provider)
