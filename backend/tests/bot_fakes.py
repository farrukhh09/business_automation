"""Test doubles for the bot pipeline (05-ai.md §8: no network in tests).

- ``ScriptedLLM`` — ``LLMClient``: the understanding answer is chosen by the customer's text; reply
  wording fails by default so replies are the deterministic templates (tests assert exact texts).
- ``FakeMessenger`` — ``InstagramMessenger`` recording what was sent.
- ``FakeGeocoder``, ``FakeSTT``, ``FakeTTS``.
- ``InlineQueue`` — ``TaskQueue`` running the task functions synchronously in the test session with
  the same fakes (what Celery does in production, minus the broker).
"""

import json
import re
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.ai.llm_client import LLMUnavailableError
from app.core.time import business_today
from app.integrations.instagram.client import InstagramAPIError
from app.integrations.maps.types import GeoCandidate, GeocodeResult
from app.integrations.speech.base import SynthesizedAudio, TranscriptionResult
from app.models.enums import GeocodeStatus
from app.services.inbound_service import InboundMessageService
from app.services.messaging_service import MessagingService

_CUSTOMER_TEXT_RE = re.compile(r"<customer_message>\n(.*?)\n</customer_message>", re.DOTALL)

# Khujand city centre, inside CITY_BBOX.
CITY_LAT, CITY_LNG = 40.2842, 69.6191


def future_day(days: int = 3) -> date:
    """A delivery date far enough ahead for the default 24-hour lead time at any time of day."""
    return business_today() + timedelta(days=days)


def understanding(**overrides: Any) -> dict[str, Any]:
    """A complete model answer (05 §3); ``entities`` is merged into the empty defaults."""
    entities: dict[str, Any] = {
        "customer_name": None,
        "phone": None,
        "items": [],
        "items_mode": "none",
        "delivery_date": None,
        "delivery_time": None,
        "delivery_type": None,
        "address": None,
        "recipient_name": None,
        "recipient_phone": None,
        "courier_comment": None,
        "payment_method": None,
        "comment": None,
    }
    entities.update(overrides.pop("entities", {}))
    return {
        "language": "ru",
        "intent": "OTHER",
        "secondary_intents": [],
        "entities": entities,
        "faq_ids": [],
        "product_ids_asked": [],
        "confirmation_signal": "none",
        "address_candidate_choice": None,
        "confidence": 0.9,
        **overrides,
    }


def item(product_text: str, quantity: int | None = None, product_id: int | None = None) -> dict[str, Any]:
    return {"product_id": product_id, "product_text": product_text, "quantity": quantity, "comment": None}


class ScriptedLLM:
    """``script`` maps the exact customer text to an understanding answer (or an exception)."""

    def __init__(
        self,
        script: Mapping[str, dict[str, Any] | Exception] | None = None,
        *,
        default: dict[str, Any] | Exception | None = None,
        reply: str | Callable[[dict[str, Any]], str] | Exception | None = None,
        receipt: dict[str, Any] | Exception | None = None,
    ) -> None:
        self.script = dict(script or {})
        self.default = default
        self.reply = reply
        self.receipt = receipt  # what the model "reads" on an image (app/ai/receipt.py)
        self.json_calls: list[dict[str, Any]] = []
        self.text_calls: list[dict[str, Any]] = []
        self.receipt_calls: list[list[dict[str, Any]]] = []

    def complete_json(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        tools: list[dict[str, Any]] | None = None,
        tool_executor: Any = None,
        max_tool_rounds: int = 3,
    ) -> dict[str, Any]:
        if _has_image(messages):
            self.receipt_calls.append(messages)
            if self.receipt is None:
                raise AssertionError("ScriptedLLM has no receipt reading")
            if isinstance(self.receipt, Exception):
                raise self.receipt
            return json.loads(json.dumps(self.receipt))
        text = self.customer_text(messages)
        self.json_calls.append({"text": text, "messages": messages, "tools": tools, "tool_executor": tool_executor})
        answer = self.script.get(text, self.default)
        if answer is None:
            raise AssertionError(f"ScriptedLLM has no understanding for {text!r}")
        if isinstance(answer, Exception):
            raise answer
        return json.loads(json.dumps(answer))

    def complete_text(
        self, *, system: list[dict[str, Any]], messages: list[dict[str, Any]], max_tokens: int = 1024
    ) -> str:
        prompt = messages[0]["content"][0]["text"]
        self.text_calls.append({"prompt": prompt})
        if self.reply is None:
            raise LLMUnavailableError(reason="test_templates_only")
        if isinstance(self.reply, Exception):
            raise self.reply
        if callable(self.reply):
            return self.reply({"prompt": prompt})
        return self.reply

    @staticmethod
    def customer_text(messages: list[dict[str, Any]]) -> str:
        for block in messages[-1]["content"]:
            match = _CUSTOMER_TEXT_RE.search(block.get("text", ""))
            if match:
                return match.group(1)
        raise AssertionError("no customer message in the prompt")


def _has_image(messages: list[dict[str, Any]]) -> bool:
    content = messages[-1].get("content") if messages else None
    return isinstance(content, list) and any(block.get("type") == "image" for block in content)


def receipt_reading(**overrides: Any) -> dict[str, Any]:
    """A model answer for a receipt screenshot: a completed 300 somoni transfer to the bakery's wallet."""
    return {
        "is_receipt": True,
        "status": "success",
        "amount": 300,
        "currency": "TJS",
        "recipient": "+992 92 *** 53 33",
        "recipient_name": None,
        "sender": None,
        "provider": "Alif",
        "paid_at": "17.09.2026 12:31",
        "reference": "A1",
        "confidence": 0.95,
        **overrides,
    }


class FakeMessenger:
    def __init__(self, *, profile: dict[str, str | None] | None = None, media: bytes = b"media") -> None:
        self.profile = profile
        self.media = media
        self.media_type = "audio/mp4"
        self.sent_texts: list[tuple[str, str]] = []
        self.sent_audio: list[tuple[str, str]] = []
        self.downloads: list[str] = []
        self.send_error: Exception | None = None
        self.download_error: Exception | None = None
        self._counter = 0

    def send_text(self, recipient_id: str, text: str) -> list[str]:
        if self.send_error is not None:
            raise self.send_error
        self._counter += 1
        self.sent_texts.append((recipient_id, text))
        return [f"mid.out.{self._counter}"]

    def send_audio(self, recipient_id: str, url: str) -> str:
        if self.send_error is not None:
            raise self.send_error
        self._counter += 1
        self.sent_audio.append((recipient_id, url))
        return f"mid.audio.{self._counter}"

    def get_user_profile(self, igsid: str) -> dict[str, str | None] | None:
        return self.profile

    def download_attachment(self, url: str, max_bytes: int = 25 * 1024 * 1024) -> tuple[bytes, str]:
        self.downloads.append(url)
        if self.download_error is not None:
            raise self.download_error
        return self.media, self.media_type

    def refresh_long_lived_token(self) -> tuple[str, int]:
        return "refreshed-token", 5_184_000

    @property
    def texts(self) -> list[str]:
        return [text for _, text in self.sent_texts]


def throttling_error() -> InstagramAPIError:
    return InstagramAPIError(status=400, code=80002, message="throttled", retryable=True)


class FakeGeocoder:
    name = "fake"

    def __init__(self, result: GeocodeResult | None = None) -> None:
        self.result = result or single_house()
        self.queries: list[str] = []

    def geocode(self, query: str, *, city: str, country_code: str = "tj") -> GeocodeResult:
        self.queries.append(query)
        return self.result


def single_house(formatted: str = "Худжанд, улица Рудаки, 45") -> GeocodeResult:
    candidate = GeoCandidate(formatted=formatted, lat=CITY_LAT, lng=CITY_LNG, precision="house")
    return GeocodeResult(status=GeocodeStatus.OK, candidates=[candidate], provider="fake")


def ambiguous(*names: str, precision: str = "house") -> GeocodeResult:
    """Several variants (houses by default — the ones the customer may pick by number, 03 §7)."""
    candidates = [
        GeoCandidate(formatted=name, lat=CITY_LAT + index * 0.001, lng=CITY_LNG, precision=precision)
        for index, name in enumerate(names)
    ]
    return GeocodeResult(status=GeocodeStatus.AMBIGUOUS, candidates=candidates, provider="fake")


class FakeSTT:
    provider = "fake-stt"

    def __init__(self, *outcomes: TranscriptionResult | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[bytes, str, str | None]] = []

    def transcribe(self, audio: bytes, *, mime_type: str, language_hint: str | None = None) -> TranscriptionResult:
        self.calls.append((audio, mime_type, language_hint))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def transcript(text: str, language: str = "rus", probability: float = 0.98) -> TranscriptionResult:
    return TranscriptionResult(text=text, language=language, language_probability=probability, provider="fake-stt")


class FakeTTS:
    provider = "fake-tts"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def supports(self, language: str) -> bool:
        return language == "ru"

    def synthesize(self, text: str, *, language: str) -> SynthesizedAudio:
        self.calls.append((text, language))
        return SynthesizedAudio(data=b"m4a-bytes", mime_type="audio/mp4", extension="m4a")


class RecordingQueue:
    """Records what would be enqueued; nothing runs."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def _record(self, name: str, value: Any) -> bool:
        self.calls.append((name, value))
        return True

    def process_instagram_event(self, event: dict[str, Any]) -> bool:
        return self._record("process_instagram_event", event)

    def send_message(self, message_id: int) -> bool:
        return self._record("send_message", message_id)

    def transcribe_voice(self, message_id: int) -> bool:
        return self._record("transcribe_voice", message_id)

    def synthesize_voice_reply(self, message_id: int) -> bool:
        return self._record("synthesize_voice_reply", message_id)

    def continue_after_location(self, order_id: int) -> bool:
        return self._record("continue_after_location", order_id)

    def of(self, name: str) -> list[Any]:
        return [value for call, value in self.calls if call == name]


class InlineQueue(RecordingQueue):
    """Runs every task at once in the given session with the given fakes."""

    def __init__(self, db: Session, **deps: Any) -> None:
        super().__init__()
        self.db = db
        self.deps = deps

    def _inbound(self) -> InboundMessageService:
        return InboundMessageService(self.db, queue=self, **self.deps)

    def process_instagram_event(self, event: dict[str, Any]) -> bool:
        self._record("process_instagram_event", event)
        self._inbound().handle_event(event)
        return True

    def send_message(self, message_id: int) -> bool:
        self._record("send_message", message_id)
        messaging_deps = {key: self.deps[key] for key in ("messenger", "settings", "media") if key in self.deps}
        MessagingService(self.db, **messaging_deps).deliver(message_id)
        return True

    def transcribe_voice(self, message_id: int) -> bool:
        self._record("transcribe_voice", message_id)
        self._inbound().transcribe(message_id)
        return True

    def synthesize_voice_reply(self, message_id: int) -> bool:
        self._record("synthesize_voice_reply", message_id)
        self._inbound().synthesize_voice_reply(message_id)
        return True

    def continue_after_location(self, order_id: int) -> bool:
        self._record("continue_after_location", order_id)
        self._inbound().continue_after_location(order_id)
        return True
