"""OpenAI TTS adapter and TTS factory (06 §3, docs/research/voice.md §4.3). No network: respx."""

import json
import logging
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.core.exceptions import IntegrationError, IntegrationNotConfiguredError
from app.integrations.speech.base import SpeechProviderError, SynthesizedAudio, TextToSpeech
from app.integrations.speech.factory import get_tts
from app.integrations.speech.openai_tts import OPENAI_SPEECH_URL, OpenAITTS
from app.models.enums import Language

TTS_KEY = "sk-test-tts-key-0123456789abcdef"
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00" + b"\x00" * 16


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"TTS_PROVIDER": "openai", "TTS_API_KEY": TTS_KEY, "TTS_VOICE": "marin"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_synthesize_request_body_and_bytes(respx_mock: Any) -> None:
    route = respx_mock.post(OPENAI_SPEECH_URL).mock(
        return_value=httpx.Response(200, content=WAV, headers={"content-type": "audio/wav"})
    )
    text = "Здравствуйте! Ваш торт «Наполеон» будет готов завтра к 15:00."

    audio = OpenAITTS(make_settings()).synthesize(f"  {text} ", language="ru")

    assert route.call_count == 1
    request = route.calls.last.request
    assert str(request.url) == "https://api.openai.com/v1/audio/speech"
    assert request.headers["authorization"] == f"Bearer {TTS_KEY}"
    assert request.headers["content-type"].startswith("application/json")
    assert json.loads(request.read()) == {
        "model": "gpt-4o-mini-tts",
        "voice": "marin",
        "input": text,
        "response_format": "wav",
    }
    assert audio == SynthesizedAudio(data=WAV, mime_type="audio/wav", extension="wav")
    assert "RIFF" not in repr(audio)


def test_default_voice_from_settings(respx_mock: Any) -> None:
    route = respx_mock.post(OPENAI_SPEECH_URL).mock(return_value=httpx.Response(200, content=WAV))

    OpenAITTS(make_settings(TTS_VOICE="")).synthesize("Привет", language=Language.RU)

    assert json.loads(route.calls.last.request.read())["voice"] == "alloy"


@pytest.mark.parametrize(
    ("language", "supported"),
    [("ru", True), ("RU", True), ("ru-RU", True), ("rus", True), (Language.RU, True),
     ("tg", False), ("tgk", False), ("tg-TJ", False), (Language.TG, False), ("en", False), ("", False)],
)  # fmt: skip
def test_supports_only_russian(language: str, supported: bool) -> None:
    assert OpenAITTS(make_settings()).supports(language) is supported


@pytest.mark.parametrize("language", ["tg", Language.TG, "en"])
def test_unsupported_language_raises_value_error_without_http(respx_mock: Any, language: str) -> None:
    route = respx_mock.post(OPENAI_SPEECH_URL).mock(return_value=httpx.Response(200, content=WAV))

    with pytest.raises(ValueError):
        OpenAITTS(make_settings()).synthesize("Салом! Фармоиши шумо қабул шуд.", language=language)

    assert route.call_count == 0


@pytest.mark.parametrize("text", ["", "   ", "а" * 4097])
def test_invalid_text_raises_value_error(respx_mock: Any, text: str) -> None:
    route = respx_mock.post(OPENAI_SPEECH_URL).mock(return_value=httpx.Response(200, content=WAV))

    with pytest.raises(ValueError):
        OpenAITTS(make_settings()).synthesize(text, language="ru")

    assert route.call_count == 0


@pytest.mark.parametrize(
    ("status", "kwargs", "provider_code", "retryable"),
    [
        (401, {"json": {"error": {"message": f"Incorrect API key provided: {TTS_KEY[:6]}***",
                                  "type": "invalid_request_error", "code": "invalid_api_key"}}},
         "invalid_api_key", False),
        (400, {"json": {"error": {"message": "Invalid voice", "type": "invalid_request_error", "code": None}}},
         "invalid_request_error", False),
        (408, {"json": {"error": {"message": "Request timeout", "type": "timeout", "code": None}}},
         "timeout", True),
        (429, {"json": {"error": {"message": "Rate limit", "type": "requests", "code": "rate_limit_exceeded"}}},
         "rate_limit_exceeded", True),
        (500, {"text": "upstream error"}, None, True),
    ],
)  # fmt: skip
def test_http_errors(
    respx_mock: Any, status: int, kwargs: dict[str, Any], provider_code: str | None, retryable: bool
) -> None:
    respx_mock.post(OPENAI_SPEECH_URL).mock(return_value=httpx.Response(status, **kwargs))

    with pytest.raises(SpeechProviderError) as caught:
        OpenAITTS(make_settings()).synthesize("Привет", language="ru")

    exc = caught.value
    assert isinstance(exc, IntegrationError)
    assert exc.provider == "openai"
    assert exc.status == status
    assert exc.provider_code == provider_code
    assert exc.retryable is retryable
    dumped = f"{exc!s} {exc!r} {exc.to_dict()}"
    assert TTS_KEY not in dumped
    assert "Incorrect API key" not in dumped


@pytest.mark.parametrize("shared_client", [True, False])
def test_redirects_are_never_followed(respx_mock: Any, shared_client: bool) -> None:
    """A 3xx must not repost the reply text (and the Bearer header) to another host."""
    respx_mock.post(OPENAI_SPEECH_URL).mock(
        return_value=httpx.Response(307, headers={"location": "https://attacker.example/collect"})
    )
    leak = respx_mock.post("https://attacker.example/collect").mock(return_value=httpx.Response(200, content=WAV))
    permissive = httpx.Client(follow_redirects=True) if shared_client else None

    try:
        tts = OpenAITTS(make_settings(), http=permissive)
        with pytest.raises(SpeechProviderError) as caught:
            tts.synthesize("Ваш заказ принят", language="ru")
    finally:
        if permissive is not None:
            permissive.close()

    assert leak.call_count == 0
    assert caught.value.status == 307


def test_timeout(respx_mock: Any) -> None:
    respx_mock.post(OPENAI_SPEECH_URL).mock(side_effect=httpx.ReadTimeout)

    with pytest.raises(SpeechProviderError) as caught:
        OpenAITTS(make_settings()).synthesize("Привет", language="ru")

    assert caught.value.reason == "timeout"
    assert caught.value.retryable is True


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b""),
        httpx.Response(200, json={"error": {"message": "unexpected"}}),
        httpx.Response(200, text="not audio"),
    ],
)
def test_invalid_success_response(respx_mock: Any, response: httpx.Response) -> None:
    respx_mock.post(OPENAI_SPEECH_URL).mock(return_value=response)

    with pytest.raises(SpeechProviderError) as caught:
        OpenAITTS(make_settings()).synthesize("Привет", language="ru")

    assert caught.value.reason == "invalid_response"


def test_missing_key_is_not_configured() -> None:
    with pytest.raises(IntegrationNotConfiguredError) as caught:
        OpenAITTS(make_settings(TTS_API_KEY=""))
    assert "TTS_API_KEY" in caught.value.detail


def test_logs_have_no_key_or_text(respx_mock: Any, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.integrations.speech")
    respx_mock.post(OPENAI_SPEECH_URL).mock(return_value=httpx.Response(200, content=WAV))
    text = "Ваш заказ номер 42 будет доставлен по адресу улица Рудаки 15"

    tts = OpenAITTS(make_settings())
    tts.synthesize(text, language="ru")

    events = [getattr(record, "event", None) for record in caplog.records]
    assert "speech.tts_request" in events
    assert "speech.tts_response" in events
    dumped = " ".join(str(record.__dict__) for record in caplog.records)
    assert TTS_KEY not in dumped
    assert text not in dumped
    assert TTS_KEY not in repr(tts)


# --------------------------------------------------------------------------- factory


def test_get_tts_returns_openai() -> None:
    tts = get_tts(make_settings(TTS_PROVIDER="OpenAI"))
    assert isinstance(tts, OpenAITTS)
    assert isinstance(tts, TextToSpeech)
    assert tts.supports("tg") is False


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"TTS_API_KEY": "   "}, "TTS_API_KEY"),
        ({"TTS_PROVIDER": ""}, "TTS_PROVIDER"),
        ({"TTS_PROVIDER": "none"}, "TTS_PROVIDER"),
        ({"TTS_PROVIDER": "yandex"}, "yandex"),
    ],
)
def test_get_tts_not_configured(overrides: dict[str, Any], fragment: str) -> None:
    with pytest.raises(IntegrationNotConfiguredError) as caught:
        get_tts(make_settings(**overrides))
    assert fragment in caught.value.detail
    assert caught.value.status_code == 503
