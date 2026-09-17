"""Tests for ``app/ai/llm_client.py`` (docs/architecture/05-ai.md §2).

No network: a duck-typed fake of ``anthropic.Anthropic`` is injected into ``AnthropicLLMClient``
and returns real SDK objects (``anthropic.types.Message`` and friends), so the assertions are made
against the same shapes production code receives.
"""

import json
import logging
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from app.ai.llm_client import (
    FALLBACKS_BETA,
    FALLBACKS_MODE,
    JSON_MAX_TOKENS,
    MAX_RETRIES,
    TEXT_MAX_TOKENS,
    AnthropicLLMClient,
    LLMClient,
    LLMRefusalError,
    LLMUnavailableError,
    get_llm_client,
    is_llm_configured,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import IntegrationError, IntegrationNotConfiguredError

API_KEY = "sk-ant-test-secret-key-value"
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"intent": {"type": "string"}},
    "required": ["intent"],
    "additionalProperties": False,
}
SYSTEM: list[dict[str, Any]] = [
    {"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": "catalog", "cache_control": {"type": "ephemeral"}},
]
MESSAGES: list[dict[str, Any]] = [{"role": "user", "content": [{"type": "text", "text": "привет"}]}]


# --------------------------------------------------------------------------- fakes


class _FakeMessagesAPI:
    """``client.messages`` / ``client.beta.messages`` — records kwargs, replays queued responses."""

    def __init__(self, path: str, queue: list[Any], calls: list[tuple[str, dict[str, Any]]]) -> None:
        self._path = path
        self._queue = queue
        self._calls = calls

    def create(self, **kwargs: Any) -> Any:
        self._calls.append((self._path, kwargs))
        if not self._queue:
            raise AssertionError("the fake Anthropic client ran out of queued responses")
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeAnthropic:
    """Duck-typed stand-in for ``anthropic.Anthropic`` (only what the adapter touches)."""

    def __init__(self, *responses: Any) -> None:
        queue = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.messages = _FakeMessagesAPI("standard", queue, self.calls)
        self.beta = SimpleNamespace(messages=_FakeMessagesAPI("beta", queue, self.calls))

    @property
    def paths(self) -> list[str]:
        return [path for path, _ in self.calls]

    @property
    def requests(self) -> list[dict[str, Any]]:
        return [kwargs for _, kwargs in self.calls]

    @property
    def last_request(self) -> dict[str, Any]:
        return self.requests[-1]


def make_settings(**overrides: Any) -> Settings:
    base = get_settings().model_copy(
        update={
            "LLM_API_KEY": API_KEY,
            "LLM_MODEL": "claude-opus-5",
            "LLM_EFFORT": "medium",
            "LLM_TIMEOUT_SECONDS": 30.0,
            "LLM_FALLBACKS_ENABLED": True,
        }
    )
    return base.model_copy(update=overrides) if overrides else base


def make_message(
    *,
    text: str | None = None,
    stop_reason: str = "end_turn",
    tool_uses: tuple[tuple[str, str, dict[str, Any]], ...] = (),
    stop_details: dict[str, Any] | None = None,
    request_id: str = "req_test_1",
) -> Message:
    content: list[Any] = [
        ToolUseBlock(id=block_id, name=name, input=payload, type="tool_use")
        for block_id, name, payload in tool_uses
    ]
    if text is not None:
        content.append(TextBlock(type="text", text=text))
    message = Message(
        id="msg_test",
        content=content,
        model="claude-opus-5",
        role="assistant",
        stop_reason=stop_reason,
        stop_details=stop_details,
        type="message",
        usage=Usage(input_tokens=120, output_tokens=42, cache_read_input_tokens=100),
    )
    message._request_id = request_id
    return message


def http_response(status: int) -> httpx2.Response:
    return httpx2.Response(status, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))


def build_client(*responses: Any, **settings_overrides: Any) -> tuple[AnthropicLLMClient, FakeAnthropic]:
    fake = FakeAnthropic(*responses)
    return AnthropicLLMClient(make_settings(**settings_overrides), client=fake), fake


def json_call(client: AnthropicLLMClient, **kwargs: Any) -> dict[str, Any]:
    return client.complete_json(system=SYSTEM, messages=MESSAGES, schema=SCHEMA, **kwargs)


# --------------------------------------------------------------------------- configuration


def test_missing_api_key_raises_not_configured() -> None:
    with pytest.raises(IntegrationNotConfiguredError):
        AnthropicLLMClient(make_settings(LLM_API_KEY=""))
    with pytest.raises(IntegrationNotConfiguredError):
        get_llm_client(make_settings(LLM_API_KEY="   "))
    assert is_llm_configured(make_settings()) is True
    assert is_llm_configured(make_settings(LLM_API_KEY="")) is False


def test_invalid_model_or_effort_is_a_configuration_error() -> None:
    with pytest.raises(IntegrationNotConfiguredError):
        AnthropicLLMClient(make_settings(LLM_MODEL=""))
    with pytest.raises(IntegrationNotConfiguredError):
        AnthropicLLMClient(make_settings(LLM_EFFORT="turbo"))


def test_injected_client_does_not_require_a_key_and_satisfies_the_protocol() -> None:
    client, _ = build_client(LLM_API_KEY="")
    assert isinstance(client, LLMClient)
    assert API_KEY not in repr(client)


def test_sdk_client_is_created_lazily_once_with_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> FakeAnthropic:
        created.append(kwargs)
        return FakeAnthropic(make_message(text='{"intent": "GREETING"}'), make_message(text="ok"))

    monkeypatch.setattr(anthropic, "Anthropic", factory)
    client = AnthropicLLMClient(make_settings(LLM_TIMEOUT_SECONDS=17.0))
    assert created == []  # nothing is built before the first call

    json_call(client)
    client.complete_text(system=SYSTEM, messages=MESSAGES)
    assert created == [{"api_key": API_KEY, "timeout": 17.0, "max_retries": MAX_RETRIES}]


def test_get_llm_client_uses_settings() -> None:
    client = get_llm_client(make_settings(LLM_MODEL="claude-opus-5-custom"), client=FakeAnthropic())
    assert isinstance(client, AnthropicLLMClient)
    assert client.model == "claude-opus-5-custom"


# --------------------------------------------------------------------------- request shape


def test_complete_json_request_matches_the_contract() -> None:
    client, fake = build_client(
        make_message(text='{"intent": "CREATE_ORDER"}'), LLM_MODEL="claude-opus-5-x", LLM_EFFORT="low"
    )

    assert json_call(client) == {"intent": "CREATE_ORDER"}

    path, request = fake.calls[0]
    assert path == "beta"  # LLM_FALLBACKS_ENABLED
    assert request["betas"] == [FALLBACKS_BETA]
    assert request["fallbacks"] == FALLBACKS_MODE
    assert request["model"] == "claude-opus-5-x"  # never hardcoded: comes from Settings.LLM_MODEL
    assert request["max_tokens"] == JSON_MAX_TOKENS
    assert request["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": SCHEMA},
    }
    assert request["system"] == SYSTEM
    assert request["messages"] == MESSAGES
    # Claude Opus 5: adaptive thinking is on by default, sampling params are rejected with 400.
    for forbidden in ("thinking", "temperature", "top_p", "top_k", "stream", "tools", "tool_choice"):
        assert forbidden not in request


def test_system_blocks_keep_their_cache_breakpoints_and_carry_no_volatile_data() -> None:
    client, fake = build_client(make_message(text="{}"))
    json_call(client)

    system = fake.last_request["system"]
    assert [block["cache_control"] for block in system] == [{"type": "ephemeral"}, {"type": "ephemeral"}]
    assert fake.last_request["messages"] is not system
    # Everything volatile belongs to `messages`, after the cache breakpoints.
    assert all("привет" not in block["text"] for block in system)


def test_fallbacks_disabled_uses_the_standard_endpoint() -> None:
    client, fake = build_client(make_message(text="{}"), LLM_FALLBACKS_ENABLED=False)
    json_call(client)

    path, request = fake.calls[0]
    assert path == "standard"
    assert "betas" not in request
    assert "fallbacks" not in request


def test_complete_text_request_and_result() -> None:
    client, fake = build_client(make_message(text="  Здравствуйте! 😊  "))

    assert client.complete_text(system=SYSTEM, messages=MESSAGES, max_tokens=256) == "Здравствуйте! 😊"

    request = fake.last_request
    assert request["max_tokens"] == 256
    assert request["output_config"] == {"effort": "medium"}  # no structured format for free text
    assert "thinking" not in request


def test_complete_text_default_max_tokens() -> None:
    client, fake = build_client(make_message(text="ok"))
    client.complete_text(system=SYSTEM, messages=MESSAGES)
    assert fake.last_request["max_tokens"] == TEXT_MAX_TOKENS


# --------------------------------------------------------------------------- refusals and bad output


def test_refusal_is_raised_before_content_is_read() -> None:
    client, _ = build_client(
        make_message(
            text="not json at all",
            stop_reason="refusal",
            stop_details={"type": "refusal", "category": "cyber", "explanation": "nope"},
        )
    )

    with pytest.raises(LLMRefusalError) as excinfo:
        json_call(client)
    assert excinfo.value.category == "cyber"
    assert excinfo.value.reason == "refusal"
    assert isinstance(excinfo.value, IntegrationError)


def test_refusal_on_the_reply_step() -> None:
    client, _ = build_client(make_message(text="", stop_reason="refusal"))
    with pytest.raises(LLMRefusalError):
        client.complete_text(system=SYSTEM, messages=MESSAGES)


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        (make_message(text="{oops"), "invalid_json"),
        (make_message(text="[1, 2]"), "invalid_json"),
        (make_message(text=None), "empty_response"),
        (make_message(text='{"intent": "FAQ"}', stop_reason="max_tokens"), "max_tokens"),
    ],
)
def test_unusable_json_answers(message: Message, reason: str) -> None:
    client, _ = build_client(message)
    with pytest.raises(LLMUnavailableError) as excinfo:
        json_call(client)
    assert excinfo.value.reason == reason


def test_empty_reply_text_is_an_error() -> None:
    client, _ = build_client(make_message(text="   "))
    with pytest.raises(LLMUnavailableError) as excinfo:
        client.complete_text(system=SYSTEM, messages=MESSAGES)
    assert excinfo.value.reason == "empty_response"


@pytest.mark.parametrize(
    ("error", "reason", "retryable"),
    [
        (anthropic.RateLimitError("429", response=http_response(429), body=None), "rate_limit", True),
        (anthropic.APIStatusError("503", response=http_response(503), body=None), "server_error", True),
        (anthropic.APIStatusError("409", response=http_response(409), body=None), "api_error", False),
        (anthropic.BadRequestError("400", response=http_response(400), body=None), "bad_request", False),
        (anthropic.AuthenticationError("401", response=http_response(401), body=None), "api_error", False),
        (anthropic.APITimeoutError(httpx2.Request("POST", "https://api.anthropic.com")), "timeout", True),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com")), "network", True),
    ],
)
def test_sdk_errors_become_llm_unavailable(error: Exception, reason: str, retryable: bool) -> None:
    client, _ = build_client(error)
    with pytest.raises(LLMUnavailableError) as excinfo:
        json_call(client)
    assert excinfo.value.reason == reason
    assert excinfo.value.retryable is retryable
    assert isinstance(excinfo.value, IntegrationError)
    assert excinfo.value.status_code == 502


# --------------------------------------------------------------------------- tool loop (05 §7)


def test_two_tool_calls_come_back_in_one_user_message() -> None:
    first = make_message(
        stop_reason="tool_use",
        tool_uses=(
            ("toolu_1", "get_products", {}),
            ("toolu_2", "get_order", {"order_id": 12}),
        ),
    )
    client, fake = build_client(first, make_message(text='{"intent": "ORDER_STATUS"}'))
    seen: list[tuple[str, dict[str, Any]]] = []

    def executor(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        seen.append((name, arguments))
        return {"ok": name}

    result = json_call(client, tools=[{"name": "get_products"}], tool_executor=executor, max_tool_rounds=3)

    assert result == {"intent": "ORDER_STATUS"}
    assert seen == [("get_products", {}), ("get_order", {"order_id": 12})]  # arguments parsed as objects
    assert len(fake.calls) == 2

    follow_up = fake.requests[1]["messages"]
    assert len(follow_up) == len(MESSAGES) + 2
    assistant_turn = follow_up[-2]
    assert assistant_turn["role"] == "assistant"
    assert assistant_turn["content"] == list(first.content)  # the whole assistant turn is replayed
    tool_turn = follow_up[-1]
    assert tool_turn["role"] == "user"
    assert [block["tool_use_id"] for block in tool_turn["content"]] == ["toolu_1", "toolu_2"]
    assert [block["type"] for block in tool_turn["content"]] == ["tool_result", "tool_result"]
    assert [block["is_error"] for block in tool_turn["content"]] == [False, False]
    assert json.loads(tool_turn["content"][0]["content"]) == {"ok": "get_products"}
    assert MESSAGES == [{"role": "user", "content": [{"type": "text", "text": "привет"}]}]  # input untouched


def test_failed_tool_is_returned_as_an_error_result() -> None:
    client, fake = build_client(
        make_message(stop_reason="tool_use", tool_uses=(("toolu_1", "get_order", {"order_id": 5}),)),
        make_message(text="{}"),
    )

    def executor(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("no such order")

    assert json_call(client, tools=[{"name": "get_order"}], tool_executor=executor) == {}

    block = fake.requests[1]["messages"][-1]["content"][0]
    assert block["is_error"] is True
    assert "no such order" not in block["content"]  # provider/internal messages are not leaked back
    assert json.loads(block["content"]) == {"error": "RuntimeError"}


def test_tool_rounds_are_capped_and_the_last_call_forbids_tools() -> None:
    tool_turn = make_message(stop_reason="tool_use", tool_uses=(("toolu_1", "get_products", {}),))
    client, fake = build_client(tool_turn, tool_turn, tool_turn)
    calls: list[str] = []

    with pytest.raises(LLMUnavailableError) as excinfo:
        json_call(
            client,
            tools=[{"name": "get_products"}],
            tool_executor=lambda name, arguments: calls.append(name) or {},
            max_tool_rounds=1,
        )

    assert excinfo.value.reason == "tool_rounds_exceeded"
    assert calls == ["get_products"]  # exactly max_tool_rounds executions
    assert len(fake.calls) == 2
    assert "tool_choice" not in fake.requests[0]
    assert fake.requests[1]["tool_choice"] == {"type": "none"}
    assert fake.requests[1]["tools"] == [{"name": "get_products"}]  # tools stay in the cached prefix


def test_tools_are_not_offered_without_an_executor() -> None:
    client, fake = build_client(make_message(text="{}"))
    json_call(client, tools=[{"name": "get_products"}])
    assert "tools" not in fake.last_request


def test_unexpected_tool_use_without_tools_is_an_error() -> None:
    client, _ = build_client(make_message(stop_reason="tool_use", tool_uses=(("toolu_1", "x", {}),)))
    with pytest.raises(LLMUnavailableError) as excinfo:
        json_call(client)
    assert excinfo.value.reason == "unexpected_tool_use"


def test_string_tool_input_is_parsed_as_an_object() -> None:
    tool_block = ToolUseBlock(id="toolu_1", name="get_order", input={}, type="tool_use")
    object.__setattr__(tool_block, "input", '{"order_id": 7}')  # some transports hand back raw JSON
    message = make_message(stop_reason="tool_use")
    object.__setattr__(message, "content", [tool_block])
    client, _ = build_client(message, make_message(text="{}"))
    seen: list[dict[str, Any]] = []

    json_call(
        client,
        tools=[{"name": "get_order"}],
        tool_executor=lambda name, arguments: seen.append(arguments) or {},
    )
    assert seen == [{"order_id": 7}]


# --------------------------------------------------------------------------- logging (SPEC §41)


def test_events_are_logged_without_secrets(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.ai.llm_client")
    client, _ = build_client(make_message(text='{"intent": "GREETING"}'))
    json_call(client)

    events = {record.event: record for record in caplog.records if hasattr(record, "event")}
    assert set(events) == {"ai.request", "ai.response"}
    assert events["ai.request"].model == "claude-opus-5"
    assert events["ai.request"].messages_count == 1
    assert events["ai.response"].stop_reason == "end_turn"
    assert events["ai.response"].request_id == "req_test_1"
    assert events["ai.response"].input_tokens == 120
    assert events["ai.response"].cache_read_input_tokens == 100
    assert API_KEY not in caplog.text
    assert "привет" not in caplog.text  # message bodies are never logged


def test_errors_are_logged_as_ai_error(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.ai.llm_client")
    client, _ = build_client(anthropic.RateLimitError("429", response=http_response(429), body=None))

    with pytest.raises(LLMUnavailableError):
        json_call(client)

    errors = [record for record in caplog.records if getattr(record, "event", None) == "ai.error"]
    assert [record.reason for record in errors] == ["rate_limit"]
    assert errors[0].error == "RateLimitError"
    assert errors[0].status == 429
