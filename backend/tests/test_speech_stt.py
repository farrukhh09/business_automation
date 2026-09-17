"""ElevenLabs STT adapter, Tajik retry policy and STT factory (06 §3, docs/research/voice.md §5).

No network: HTTP is mocked with respx; the policy is tested with a fake ``SpeechToText``.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.core.exceptions import IntegrationError, IntegrationNotConfiguredError
from app.integrations.speech.base import (
    SpeechProviderError,
    SpeechToText,
    TranscriptionResult,
    normalize_language_code,
    text_preview,
)
from app.integrations.speech.elevenlabs import (
    ELEVENLABS_STT_URL,
    ElevenLabsSTT,
    elevenlabs_language_code,
    filename_for_mime,
    normalize_mime_type,
    upload_content_type,
)
from app.integrations.speech.factory import get_stt
from app.integrations.speech.policy import (
    RetryReason,
    choose_better,
    cyrillic_ratio,
    tajik_retry_reason,
    transcribe_with_retry,
)
from app.models.enums import Language

STT_KEY = "el-test-key-0123456789abcdef"
AUDIO = b"\x00\x00\x00\x18ftypmp42\x00fake-voice-bytes\xff\xfe"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"STT_PROVIDER": "elevenlabs", "STT_API_KEY": STT_KEY}
    values.update(overrides)
    return Settings(_env_file=None, **values)


@dataclass
class Part:
    value: bytes
    filename: str | None
    content_type: str | None


def multipart_parts(request: httpx.Request) -> dict[str, Part]:
    content_type = request.headers["content-type"]
    assert content_type.startswith("multipart/form-data")
    boundary = content_type.split("boundary=", 1)[1].strip('"').encode()
    parts: dict[str, Part] = {}
    for chunk in request.read().split(b"--" + boundary):
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        if not chunk or chunk.startswith(b"--"):
            continue
        head, _, value = chunk.partition(b"\r\n\r\n")
        if value.endswith(b"\r\n"):
            value = value[:-2]
        headers: dict[str, str] = {}
        for line in head.decode("utf-8").split("\r\n"):
            key, _, val = line.partition(":")
            headers[key.strip().lower()] = val.strip()
        disposition = headers["content-disposition"]
        name_match = re.search(r'(?<![a-z])name="([^"]*)"', disposition)
        assert name_match is not None
        filename_match = re.search(r'filename="([^"]*)"', disposition)
        parts[name_match.group(1)] = Part(
            value, filename_match.group(1) if filename_match else None, headers.get("content-type")
        )
    return parts


def ok(text: str, language: str | None = "rus", probability: float | None = 0.97, **extra: Any) -> httpx.Response:
    body: dict[str, Any] = {"text": text, "language_code": language, "language_probability": probability, "words": []}
    body.update(extra)
    return httpx.Response(200, json=body)


def tr(text: str, language: str | None, probability: float | None, hint: str | None = None) -> TranscriptionResult:
    return TranscriptionResult(
        text=text, language=language, language_probability=probability, provider="fake", language_hint=hint
    )


# --------------------------------------------------------------------------- request shape & parsing


def test_first_pass_multipart_request_shape_and_parsing(respx_mock: Any) -> None:
    route = respx_mock.post(ELEVENLABS_STT_URL).mock(
        return_value=ok(" Салом, як торти медовик мехоҳам ", language="tgk", probability=0.91)
    )

    result = ElevenLabsSTT(make_settings()).transcribe(AUDIO, mime_type="audio/mp4")

    assert route.call_count == 1
    request = route.calls.last.request
    assert request.method == "POST"
    assert str(request.url) == "https://api.elevenlabs.io/v1/speech-to-text"
    assert request.headers["xi-api-key"] == STT_KEY
    assert "authorization" not in request.headers
    parts = multipart_parts(request)
    assert set(parts) == {"file", "model_id", "tag_audio_events"}  # no language_code on the first pass
    assert parts["model_id"].value == b"scribe_v2"
    assert parts["tag_audio_events"].value == b"false"
    assert parts["file"].filename == "voice.mp4"
    assert parts["file"].content_type == "audio/mp4"
    assert parts["file"].value == AUDIO

    assert result == TranscriptionResult(
        text="Салом, як торти медовик мехоҳам",
        language="tgk",
        language_probability=0.91,
        provider="elevenlabs",
        language_hint=None,
    )
    assert result.app_language == "tg"
    assert result.confidence == 0.91


def test_language_hint_is_sent_as_iso_639_3(respx_mock: Any) -> None:
    route = respx_mock.post(ELEVENLABS_STT_URL).mock(return_value=ok("Салом", language="tgk", probability=1.0))

    result = ElevenLabsSTT(make_settings()).transcribe(AUDIO, mime_type="audio/x-m4a", language_hint=Language.TG)

    parts = multipart_parts(route.calls.last.request)
    assert parts["language_code"].value == b"tgk"
    assert parts["model_id"].value == b"scribe_v2"
    assert parts["file"].filename == "voice.m4a"
    assert parts["file"].content_type == "audio/x-m4a"
    assert result.language_hint == "tgk"


@pytest.mark.parametrize(
    ("mime_type", "filename", "content_type"),
    [
        ("audio/mp4", "voice.mp4", "audio/mp4"),
        ("video/mp4", "voice.mp4", "video/mp4"),
        ("Audio/MP4; codecs=mp4a.40.2", "voice.mp4", "audio/mp4"),
        ("audio/m4a", "voice.m4a", "audio/m4a"),
        ("audio/x-m4a", "voice.m4a", "audio/x-m4a"),
        ("audio/aac", "voice.aac", "audio/aac"),
        ("audio/ogg", "voice.ogg", "audio/ogg"),
        ("audio/mpeg", "voice.mp3", "audio/mpeg"),
        ("audio/wav", "voice.wav", "audio/wav"),
        ("application/octet-stream", "voice.mp4", "audio/mp4"),
        ("", "voice.mp4", "audio/mp4"),
    ],
)
def test_filename_and_content_type_from_mime(mime_type: str, filename: str, content_type: str) -> None:
    assert filename_for_mime(mime_type) == filename
    assert upload_content_type(mime_type) == content_type


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        (None, None),
        ("", None),
        ("tg", "tgk"),
        ("TG", "tgk"),
        ("tg-TJ", "tgk"),
        ("tgk", "tgk"),
        (Language.TG, "tgk"),
        ("ru", "rus"),
        ("ru-RU", "rus"),
        ("rus", "rus"),
        (Language.RU, "rus"),
        ("uz", "uz"),
    ],
)
def test_elevenlabs_language_code(hint: str | None, expected: str | None) -> None:
    assert elevenlabs_language_code(hint) == expected


def test_invalid_language_hint_rejected() -> None:
    with pytest.raises(ValueError):
        elevenlabs_language_code("tajik!")


@pytest.mark.parametrize(
    ("code", "expected"),
    [("rus", "ru"), ("ru", "ru"), ("ru-RU", "ru"), ("tgk", "tg"), ("tg-TJ", "tg"), ("tgk_Cyrl", "tg"),
     ("fas", "fas"), ("UZ", "uz"), ("", None), (None, None)],
)  # fmt: skip
def test_normalize_language_code(code: str | None, expected: str | None) -> None:
    assert normalize_language_code(code) == expected


@pytest.mark.parametrize(
    ("body", "language", "probability"),
    [
        ({"text": "Привет", "language_code": "ru"}, "ru", None),
        ({"text": "Привет", "language_code": "rus", "language_probability": True}, "rus", None),
        ({"text": "Привет", "language_code": "  ", "language_probability": 1}, None, 1.0),
        ({"text": "Привет", "language_probability": 1.7}, None, None),
    ],
)
def test_response_optional_fields(
    respx_mock: Any, body: dict[str, Any], language: str | None, probability: Any
) -> None:
    respx_mock.post(ELEVENLABS_STT_URL).mock(return_value=httpx.Response(200, json=body))

    result = ElevenLabsSTT(make_settings()).transcribe(AUDIO, mime_type="audio/mp4")

    assert result.text == "Привет"
    assert result.language == language
    assert result.language_probability == probability


def test_injected_http_client_is_used_and_not_closed(respx_mock: Any) -> None:
    route = respx_mock.post(ELEVENLABS_STT_URL).mock(return_value=ok("Здравствуйте"))
    with httpx.Client() as http:
        ElevenLabsSTT(make_settings(), http=http).transcribe(AUDIO, mime_type="audio/mp4")
        assert not http.is_closed
    assert route.call_count == 1


@pytest.mark.parametrize("shared_client", [True, False])
def test_redirects_are_never_followed(respx_mock: Any, shared_client: bool) -> None:
    """httpx drops ``Authorization`` across origins but not ``xi-api-key``: never follow a 3xx."""
    respx_mock.post(ELEVENLABS_STT_URL).mock(
        return_value=httpx.Response(307, headers={"location": "https://attacker.example/collect"})
    )
    leak = respx_mock.post("https://attacker.example/collect").mock(return_value=ok("pwned"))
    # A caller may legitimately share a client that follows redirects; the adapter must still refuse.
    permissive = httpx.Client(follow_redirects=True) if shared_client else None

    try:
        stt = ElevenLabsSTT(make_settings(), http=permissive)
        with pytest.raises(SpeechProviderError) as caught:
            stt.transcribe(AUDIO, mime_type="audio/mp4")
    finally:
        if permissive is not None:
            permissive.close()

    assert leak.call_count == 0
    assert caught.value.status == 307
    assert caught.value.retryable is False


@pytest.mark.parametrize(
    ("mime_type", "normalized"),
    [(None, ""), ("", ""), (b"audio/mp4", ""), (42, ""), ("  AUDIO/MP4 ; codecs=x ", "audio/mp4")],
)
def test_normalize_mime_type_tolerates_junk(mime_type: Any, normalized: str) -> None:
    assert normalize_mime_type(mime_type) == normalized


@pytest.mark.parametrize("mime_type", [None, b"audio/mp4", 42, "   ", "audio/mp4\r\nX-Injected: 1", "audio/x-unknown"])
def test_unusable_mime_type_falls_back_to_mp4(respx_mock: Any, mime_type: Any) -> None:
    """Nothing attacker-controlled reaches the part headers: unknown types become voice.mp4/audio/mp4."""
    route = respx_mock.post(ELEVENLABS_STT_URL).mock(return_value=ok("Привет"))

    ElevenLabsSTT(make_settings()).transcribe(AUDIO, mime_type=mime_type)

    parts = multipart_parts(route.calls.last.request)
    assert parts["file"].filename == "voice.mp4"
    assert parts["file"].content_type == "audio/mp4"
    assert parts["file"].value == AUDIO


# --------------------------------------------------------------------------- errors


@pytest.mark.parametrize(
    ("status", "kwargs", "provider_code", "retryable"),
    [
        (401, {"json": {"detail": {"status": "invalid_api_key", "message": "Invalid API key"}}},
         "invalid_api_key", False),
        (422, {"json": {"detail": [{"loc": ["body", "file"], "msg": "Field required", "type": "missing"}]}},
         "missing", False),
        (429, {"json": {"detail": {"status": "too_many_concurrent_requests", "message": "Slow down"}}},
         "too_many_concurrent_requests", True),
        (500, {"text": "Internal Server Error"}, None, True),
        (503, {}, None, True),
    ],
)  # fmt: skip
def test_http_errors_raise_integration_error(
    respx_mock: Any, status: int, kwargs: dict[str, Any], provider_code: str | None, retryable: bool
) -> None:
    respx_mock.post(ELEVENLABS_STT_URL).mock(return_value=httpx.Response(status, **kwargs))

    with pytest.raises(SpeechProviderError) as caught:
        ElevenLabsSTT(make_settings()).transcribe(AUDIO, mime_type="audio/mp4")

    exc = caught.value
    assert isinstance(exc, IntegrationError)
    assert exc.status == status
    assert exc.reason == "http_error"
    assert exc.provider == "elevenlabs"
    assert exc.provider_code == provider_code
    assert exc.retryable is retryable
    assert exc.status_code == 502
    assert str(status) in exc.detail
    dumped = f"{exc!s} {exc!r} {exc.to_dict()}"
    assert STT_KEY not in dumped
    assert "Invalid API key" not in dumped


@pytest.mark.parametrize(
    ("side_effect", "reason"),
    [(httpx.ReadTimeout, "timeout"), (httpx.ConnectTimeout, "timeout"), (httpx.ConnectError, "network")],
)
def test_timeouts_and_network_errors(respx_mock: Any, side_effect: type[Exception], reason: str) -> None:
    respx_mock.post(ELEVENLABS_STT_URL).mock(side_effect=side_effect)

    with pytest.raises(SpeechProviderError) as caught:
        ElevenLabsSTT(make_settings()).transcribe(AUDIO, mime_type="audio/mp4")

    assert caught.value.reason == reason
    assert caught.value.status is None
    assert caught.value.retryable is True


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"language_code": "rus"}),
        httpx.Response(200, json=["text"]),
        httpx.Response(200, json={"text": None}),
    ],
)
def test_invalid_success_response(respx_mock: Any, response: httpx.Response) -> None:
    respx_mock.post(ELEVENLABS_STT_URL).mock(return_value=response)

    with pytest.raises(SpeechProviderError) as caught:
        ElevenLabsSTT(make_settings()).transcribe(AUDIO, mime_type="audio/mp4")

    assert caught.value.reason == "invalid_response"
    assert caught.value.retryable is False


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(400, False), (401, False), (403, False), (404, False), (408, True), (413, False), (422, False),
     (429, True), (500, True), (502, True), (503, True), (504, True), (None, False)],
)  # fmt: skip
def test_retryable_statuses(status: int | None, retryable: bool) -> None:
    """Celery backoff (06 §5) must retry transient statuses only; 408 counts, 4xx otherwise does not."""
    assert SpeechProviderError("x", provider="elevenlabs", status=status).retryable is retryable


@pytest.mark.parametrize(
    ("reason", "retryable"),
    [("timeout", True), ("network", True), ("invalid_response", False), ("empty_audio", False)],
)
def test_retryable_reasons_without_status(reason: str, retryable: bool) -> None:
    assert SpeechProviderError("x", provider="elevenlabs", reason=reason).retryable is retryable


def test_empty_audio_is_rejected_without_http_call(respx_mock: Any) -> None:
    route = respx_mock.post(ELEVENLABS_STT_URL).mock(return_value=ok("never"))

    with pytest.raises(SpeechProviderError) as caught:
        ElevenLabsSTT(make_settings()).transcribe(b"", mime_type="audio/mp4")

    assert caught.value.reason == "empty_audio"
    assert route.call_count == 0


def test_missing_key_is_not_configured() -> None:
    with pytest.raises(IntegrationNotConfiguredError) as caught:
        ElevenLabsSTT(make_settings(STT_API_KEY="  "))
    assert "STT_API_KEY" in caught.value.detail


def test_logs_contain_events_but_no_key_or_full_transcript(respx_mock: Any, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.integrations.speech")
    transcript = "Здравствуйте! " + "Хочу торт Наполеон на завтра к шести вечера, " * 5
    respx_mock.post(ELEVENLABS_STT_URL).mock(return_value=ok(transcript))

    stt = ElevenLabsSTT(make_settings())
    result = stt.transcribe(AUDIO, mime_type="audio/mp4")

    events = [getattr(record, "event", None) for record in caplog.records]
    assert "speech.request" in events
    assert "speech.response" in events
    dumped = " ".join(str(record.__dict__) for record in caplog.records)
    assert STT_KEY not in dumped
    assert result.text not in dumped
    assert text_preview(result.text) in dumped
    assert len(text_preview(result.text)) <= 40
    assert STT_KEY not in repr(stt)


# --------------------------------------------------------------------------- retry policy


@pytest.mark.parametrize(
    ("result", "customer_language", "expected"),
    [
        (tr("Здравствуйте, хочу торт", "rus", 0.98), None, None),
        (tr("Салом, як торт мехоҳам", "tgk", 0.9), None, None),
        (tr("Салом", "tg", 0.7), "tg", None),
        (tr("", "rus", 0.99), None, None),
        (tr("salom men tort xohlayman", "uzb", 0.7), None, RetryReason.UNEXPECTED_LANGUAGE),
        (tr("سلام، تورت می‌خواهم", "fas", 0.8), None, RetryReason.UNEXPECTED_LANGUAGE),
        (tr("Хочу торт", None, None), None, RetryReason.UNEXPECTED_LANGUAGE),
        (tr("salom, men tort xohlayman", "rus", 0.9), None, RetryReason.NON_CYRILLIC_SCRIPT),
        (tr("سلام تورت", "tgk", 0.9), None, RetryReason.NON_CYRILLIC_SCRIPT),
        (tr("Хочу торт OK", "ru", 0.9), None, None),
        (tr("Хочу торт", "ru", 0.55), None, RetryReason.LOW_PROBABILITY),
        (tr("Хочу торт", "ru", 0.599), None, RetryReason.LOW_PROBABILITY),
        (tr("Хочу торт", "ru-RU", 0.6), None, None),
        (tr("Хочу торт", "ru", 0.8), "tg", RetryReason.TAJIK_CUSTOMER_UNCERTAIN_RU),
        (tr("Хочу торт", "rus", 0.84), Language.TG, RetryReason.TAJIK_CUSTOMER_UNCERTAIN_RU),
        (tr("Хочу торт", "rus", None), "tg", RetryReason.TAJIK_CUSTOMER_UNCERTAIN_RU),
        (tr("Хочу торт", "ru", 0.85), "tg", None),
        (tr("Хочу торт", "ru", 0.8), "ru", None),
        (tr("Хочу торт", "ru", 0.8), None, None),
    ],
)
def test_tajik_retry_reason(
    result: TranscriptionResult, customer_language: str | None, expected: RetryReason | None
) -> None:
    assert tajik_retry_reason(result, customer_language) == expected


@pytest.mark.parametrize(
    ("text", "ratio"),
    [
        ("Здравствуйте", 1.0),
        ("Хочу торт Red Velvet", 8 / 17),  # Latin brand name inside a Russian sentence
        ("Ok", 0.0),
        ("8 918 123 45 67", None),  # digits only — no letters to judge
        ("🎂🎂🎂 !!! ...", None),
        ("", None),
        ("   ", None),
        ("Мехоҳам ғ ӣ қ ӯ ҳ ҷ", 1.0),  # Tajik letters count as Cyrillic
    ],
)
def test_cyrillic_ratio(text: str, ratio: float | None) -> None:
    assert cyrillic_ratio(text) == ratio


@pytest.mark.parametrize(
    ("result", "customer_language", "expected"),
    [
        # Emoji, punctuation and digits are not letters and never trigger a retry on their own.
        (tr("Здравствуйте! Хочу торт «Наполеон» 🎂 на завтра.", "rus", 0.97), None, None),
        (tr("8 918 123 45 67", "rus", 0.95), None, None),
        (tr("🎂🎂🎂", "rus", 0.95), None, None),
        (tr("   ", "rus", 0.99), None, None),
        (tr("Ок 👍", "rus", 0.9), None, None),  # Cyrillic "Ок"
        # A Latin transliteration artefact is exactly what voice.md §5 wants re-run in Tajik.
        (tr("Ok", "rus", 0.9), None, RetryReason.NON_CYRILLIC_SCRIPT),
        (tr("Salom, man tort mexoham", "tgk", 0.8), "tg", RetryReason.NON_CYRILLIC_SCRIPT),
        # Accepted cost: a Latin brand name drags the ratio under 0.5 and buys one extra call.
        (tr("Хочу торт Red Velvet", "rus", 0.95), None, RetryReason.NON_CYRILLIC_SCRIPT),
        (tr("Хочу торт Ред Вельвет", "rus", 0.95), None, None),
        # Mixed RU/TG speech from a Tajik-speaking customer.
        (tr("Салом, мне нужен торт на завтра", "rus", 0.62), "tg", RetryReason.TAJIK_CUSTOMER_UNCERTAIN_RU),
        (tr("Привет. " * 400, "rus", 0.99), None, None),  # long message
        # Language-code shapes ElevenLabs may return.
        (tr("Здравствуйте", "RUS", 0.99), None, None),
        (tr("Здравствуйте", "tgk_Cyrl", 0.99), None, None),
        (tr("Здравствуйте", "rus-x-custom", 0.99), None, None),
        (tr("Здравствуйте", "russian", 0.99), None, RetryReason.UNEXPECTED_LANGUAGE),
        (tr("Здравствуйте", "  ", 0.99), None, RetryReason.UNEXPECTED_LANGUAGE),
        # 0.0 must not be confused with "no probability" (falsy-zero trap).
        (tr("Здравствуйте", "rus", 0.0), None, RetryReason.LOW_PROBABILITY),
        (tr("Здравствуйте", "rus", 0.849999), "tg", RetryReason.TAJIK_CUSTOMER_UNCERTAIN_RU),
        (tr("Здравствуйте", "rus", 1.0), "TG", None),
    ],
)
def test_tajik_retry_reason_adversarial(
    result: TranscriptionResult, customer_language: str | None, expected: RetryReason | None
) -> None:
    assert tajik_retry_reason(result, customer_language) == expected


def test_choose_better_prefers_tajik_letters_over_confidence() -> None:
    """voice.md §5.2: "оставить тот, у которого выше уверенность ИЛИ есть таджикские буквы".

    Pinned deliberately: a hinted Tajik pass wins on a single ӯ even with a much lower probability,
    so this row is the one to revisit after the benchmark in voice.md §7.
    """
    first = tr("Здравствуйте, хочу торт Наполеон на завтра", "rus", 0.92)
    second = tr("Здравствуйте, хочу тӯрт Наполеон на завтра", "tgk", 0.21)

    assert choose_better(first, second) is second


@pytest.mark.parametrize(
    ("first", "second", "winner"),
    [
        # Cyrillic beats Latin, whatever the probabilities
        (tr("salom men tort", "uzb", 0.9), tr("салом ман торт", "tgk", 0.5), "second"),
        (tr("Хочу торт", "rus", 0.5), tr("xochu tort", "tgk", 0.9), "first"),
        # Tajik letters beat plain Cyrillic
        (tr("Хочу торт", "ru", 0.55), tr("Мехоҳам торт", "tgk", 0.4), "second"),
        (tr("Мехоҳам торт", "tg", 0.5), tr("Мехохам торт", "tgk", 0.9), "first"),
        # then the higher probability; ties keep the first pass
        (tr("Хочу торт", "ru", 0.55), tr("Хочу торт", "tgk", 0.9), "second"),
        (tr("Хочу торт", "ru", 0.7), tr("Хочу торт", "tgk", 0.7), "first"),
        (tr("Хочу торт", "ru", 0.3), tr("Хочу торт", "tgk", None), "first"),
        # an empty transcript never wins against a non-empty one
        (tr("Хочу торт", "ru", 0.5), tr("", "tgk", 0.99), "first"),
        (tr("", "ru", 0.99), tr("Салом", "tgk", 0.2), "second"),
        (tr("", "ru", 0.99), tr("", "tgk", 0.2), "first"),
    ],
)
def test_choose_better(first: TranscriptionResult, second: TranscriptionResult, winner: str) -> None:
    chosen = choose_better(first, second)
    assert chosen is (first if winner == "first" else second)


class FakeSTT:
    """Protocol implementation with keyword-only arguments (exactly as in 06 §3)."""

    provider = "fake"

    def __init__(self, *outcomes: TranscriptionResult | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[bytes, str, str | None]] = []

    def transcribe(self, audio: bytes, *, mime_type: str, language_hint: str | None = None) -> TranscriptionResult:
        self.calls.append((audio, mime_type, language_hint))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_fake_and_real_adapters_satisfy_protocol() -> None:
    assert isinstance(FakeSTT(), SpeechToText)
    assert isinstance(ElevenLabsSTT(make_settings()), SpeechToText)


def test_no_retry_for_confident_russian() -> None:
    first = tr("Здравствуйте, хочу медовик", "rus", 0.97)
    stt = FakeSTT(first)

    result = transcribe_with_retry(stt, AUDIO, "audio/mp4", customer_language="ru")

    assert result is first
    assert stt.calls == [(AUDIO, "audio/mp4", None)]


def test_retry_with_tajik_hint_picks_second(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.integrations.speech")
    first = tr("salom men tort xohlayman", "uzb", 0.62)
    second = tr("Салом, ман торт мехоҳам", "tgk", 0.88, hint="tgk")
    stt = FakeSTT(first, second)

    result = transcribe_with_retry(stt, AUDIO, "audio/mp4")

    assert result is second
    assert [call[2] for call in stt.calls] == [None, "tg"]
    events = {getattr(record, "event", None): record for record in caplog.records}
    assert events["speech.retry"].reason == "unexpected_language"
    assert events["speech.retry_result"].chosen == "second"


def test_retry_keeps_first_when_second_is_worse() -> None:
    first = tr("Хочу торт", "ru", 0.8)
    second = tr("Хочу торт", "tgk", 0.5)
    stt = FakeSTT(first, second)

    assert transcribe_with_retry(stt, AUDIO, "audio/mp4", customer_language=Language.TG) is first
    assert len(stt.calls) == 2


def test_failed_second_pass_keeps_first() -> None:
    first = tr("Хочу торт", "ru", 0.4)
    stt = FakeSTT(first, SpeechProviderError("boom", provider="fake", status=503))

    assert transcribe_with_retry(stt, AUDIO, "audio/mp4") is first
    assert len(stt.calls) == 2


def test_failed_first_pass_propagates() -> None:
    stt = FakeSTT(SpeechProviderError("boom", provider="fake", status=500))

    with pytest.raises(IntegrationError):
        transcribe_with_retry(stt, AUDIO, "audio/mp4")
    assert len(stt.calls) == 1


def test_retry_end_to_end_with_elevenlabs(respx_mock: Any) -> None:
    route = respx_mock.post(ELEVENLABS_STT_URL).mock(
        side_effect=[
            ok("salom, men tort xohlayman", language="uzb", probability=0.62),
            ok("Салом, ман торт мехоҳам", language="tgk", probability=0.88),
        ]
    )

    result = transcribe_with_retry(ElevenLabsSTT(make_settings()), AUDIO, "audio/mp4", customer_language=None)

    assert route.call_count == 2
    assert "language_code" not in multipart_parts(route.calls[0].request)
    assert multipart_parts(route.calls[1].request)["language_code"].value == b"tgk"
    assert result.text == "Салом, ман торт мехоҳам"
    assert result.language_hint == "tgk"
    assert result.app_language == "tg"


# --------------------------------------------------------------------------- factory


def test_get_stt_returns_elevenlabs() -> None:
    stt = get_stt(make_settings(STT_PROVIDER="ElevenLabs"))
    assert isinstance(stt, ElevenLabsSTT)
    assert stt.provider == "elevenlabs"


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"STT_API_KEY": ""}, "STT_API_KEY"),
        ({"STT_PROVIDER": ""}, "STT_PROVIDER"),
        ({"STT_PROVIDER": "whisper"}, "whisper"),
    ],
)
def test_get_stt_not_configured(overrides: dict[str, Any], fragment: str) -> None:
    with pytest.raises(IntegrationNotConfiguredError) as caught:
        get_stt(make_settings(**overrides))
    assert fragment in caught.value.detail
    assert caught.value.status_code == 503
    assert STT_KEY not in str(caught.value.to_dict())


def test_package_exports_public_api() -> None:
    """Names other tracks import as ``from app.integrations.speech import ...`` (06 §3)."""
    import app.integrations.speech as speech

    expected = {
        "ElevenLabsSTT", "OpenAITTS", "SpeechProviderError", "SpeechToText", "SynthesizedAudio",
        "TextToSpeech", "TranscriptionResult", "convert_to_m4a", "ffmpeg_available", "get_stt",
        "get_tts", "normalize_language_code", "prepare_instagram_audio", "probe_duration",
        "transcribe_with_retry",
    }  # fmt: skip
    assert expected <= set(speech.__all__)
    assert all(hasattr(speech, name) for name in speech.__all__)
