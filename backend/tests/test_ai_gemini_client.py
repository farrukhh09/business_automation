"""GeminiLLMClient (05-ai.md §2, ``LLM_PROVIDER=gemini``). No network: ``httpx.MockTransport``."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.ai.gemini_client import GEMINI_API_URL, MIN_OUTPUT_TOKENS, GeminiLLMClient
from app.ai.llm_client import LLMRefusalError, LLMUnavailableError, get_llm_client
from app.core.config import Settings
from app.core.exceptions import IntegrationNotConfiguredError
from app.services.integration_status import llm_status

SYSTEM = [{"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}}]
MESSAGES = [{"role": "user", "content": [{"type": "text", "text": "Хочу медовик"}]}]
SCHEMA = {"type": "object", "properties": {"intent": {"type": "string"}}, "required": ["intent"]}
TOOLS = [{"name": "get_products", "description": "catalog", "strict": True, "input_schema": {"type": "object"}}]


def make_settings(**values: Any) -> Settings:
    base: dict[str, Any] = {"LLM_PROVIDER": "gemini", "LLM_API_KEY": "g-key", "LLM_MODEL": "gemini-3.8-flash"}
    return Settings(_env_file=None, **{**base, **values})


def text_response(text: str, finish: str = "STOP", **extra: Any) -> dict[str, Any]:
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
        **extra,
    }


def call_response(name: str, call_id: str = "c1") -> dict[str, Any]:
    parts = [{"functionCall": {"name": name, "args": {}, "id": call_id}, "thoughtSignature": "sig"}]
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}]}


class Recorder:
    def __init__(self, *replies: httpx.Response | dict[str, Any] | Exception) -> None:
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply)

    def body(self, index: int) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


def build(*replies: Any, **settings: Any) -> tuple[GeminiLLMClient, Recorder, list[float]]:
    recorder = Recorder(*replies)
    sleeps: list[float] = []
    http = httpx.Client(base_url=GEMINI_API_URL, transport=httpx.MockTransport(recorder))
    return GeminiLLMClient(make_settings(**settings), http=http, sleep=sleeps.append), recorder, sleeps


def test_factory_selects_gemini_and_requires_key_and_gemini_model() -> None:
    assert isinstance(get_llm_client(make_settings()), GeminiLLMClient)
    with pytest.raises(IntegrationNotConfiguredError):
        get_llm_client(make_settings(LLM_API_KEY=" "))
    with pytest.raises(IntegrationNotConfiguredError):
        GeminiLLMClient(make_settings(LLM_MODEL="claude-opus-5"))
    assert make_settings(LLM_PROVIDER=" Gemini ").LLM_PROVIDER == "gemini"


def test_integration_status_names_the_provider() -> None:
    status = llm_status(make_settings())
    assert status.configured and status.provider == "gemini"


def test_json_request_shape_and_result() -> None:
    client, recorder, _ = build(text_response('{"intent": "CREATE_ORDER"}'))
    result = client.complete_json(system=SYSTEM, messages=MESSAGES, schema=SCHEMA)

    assert result == {"intent": "CREATE_ORDER"}
    request = recorder.requests[0]
    assert request.url.path.endswith("/v1beta/models/gemini-3.8-flash:generateContent")
    assert request.headers["x-goog-api-key"] == "g-key"
    assert "key=" not in str(request.url)
    body = recorder.body(0)
    assert body["systemInstruction"] == {"parts": [{"text": "rules"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "Хочу медовик"}]}]
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseJsonSchema"] == SCHEMA
    assert "tools" not in body


def test_tool_round_returns_model_turn_verbatim_and_results_with_ids() -> None:
    client, recorder, _ = build(call_response("get_products"), text_response('{"intent": "PRODUCT_QUERY"}'))
    calls: list[tuple[str, dict[str, Any]]] = []

    def executor(name: str, args: dict[str, Any]) -> Any:
        calls.append((name, args))
        return [{"id": 1, "name": "Медовик"}]

    result = client.complete_json(
        system=SYSTEM, messages=MESSAGES, schema=SCHEMA, tools=TOOLS, tool_executor=executor, max_tool_rounds=2
    )

    assert result == {"intent": "PRODUCT_QUERY"}
    assert calls == [("get_products", {})]
    first = recorder.body(0)
    declaration = first["tools"][0]["functionDeclarations"][0]
    assert declaration == {"name": "get_products", "description": "catalog", "parametersJsonSchema": {"type": "object"}}
    assert "toolConfig" not in first
    second = recorder.body(1)["contents"]
    assert second[1]["parts"][0]["thoughtSignature"] == "sig"
    response = second[2]["parts"][0]["functionResponse"]
    assert response == {"name": "get_products", "id": "c1", "response": {"result": [{"id": 1, "name": "Медовик"}]}}


def test_last_round_forbids_tools_and_a_call_there_is_an_error() -> None:
    client, recorder, _ = build(call_response("get_products"), call_response("get_products"))
    with pytest.raises(LLMUnavailableError) as error:
        client.complete_json(
            system=SYSTEM,
            messages=MESSAGES,
            schema=SCHEMA,
            tools=TOOLS,
            tool_executor=lambda n, a: {},
            max_tool_rounds=1,
        )
    assert error.value.reason == "tool_rounds_exceeded"
    assert recorder.body(1)["toolConfig"] == {"functionCallingConfig": {"mode": "NONE"}}


def test_rejected_tools_are_dropped_once() -> None:
    client, recorder, _ = build(httpx.Response(400, json={"error": {}}), text_response('{"intent": "FAQ"}'))
    result = client.complete_json(
        system=SYSTEM, messages=MESSAGES, schema=SCHEMA, tools=TOOLS, tool_executor=lambda n, a: {}
    )
    assert result == {"intent": "FAQ"}
    assert "tools" in recorder.body(0) and "tools" not in recorder.body(1)


def test_text_reply_skips_thoughts_and_reserves_thinking_tokens() -> None:
    response = text_response("Здравствуйте!")
    response["candidates"][0]["content"]["parts"].insert(0, {"text": "reasoning", "thought": True})
    client, recorder, _ = build(response)

    assert client.complete_text(system=SYSTEM, messages=MESSAGES) == "Здравствуйте!"
    assert recorder.body(0)["generationConfig"] == {"maxOutputTokens": MIN_OUTPUT_TOKENS}


@pytest.mark.parametrize(
    "response",
    [
        {"promptFeedback": {"blockReason": "SAFETY"}},
        text_response("", finish="PROHIBITED_CONTENT"),
    ],
)
def test_blocked_content_is_a_refusal(response: dict[str, Any]) -> None:
    client, _, _ = build(response)
    with pytest.raises(LLMRefusalError):
        client.complete_text(system=SYSTEM, messages=MESSAGES)


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (text_response("not json"), "invalid_json"),
        (text_response('{"a"', finish="MAX_TOKENS"), "max_tokens"),
        ({"candidates": []}, "empty_response"),
        (httpx.Response(403, json={}), "api_error"),
        (httpx.Response(200, text="<html>"), "invalid_response"),
    ],
)
def test_unusable_answers(response: Any, reason: str) -> None:
    client, _, sleeps = build(response)
    with pytest.raises(LLMUnavailableError) as error:
        client.complete_json(system=SYSTEM, messages=MESSAGES, schema=SCHEMA)
    assert error.value.reason == reason
    assert sleeps == []


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (lambda: httpx.Response(429, json={}), "rate_limit"),
        (lambda: httpx.Response(503, json={}), "server_error"),
        (lambda: httpx.ConnectError("down"), "network"),
        (lambda: httpx.ReadTimeout("slow"), "timeout"),
    ],
)
def test_temporary_failures_are_retried(failure: Callable[[], Any], reason: str) -> None:
    client, recorder, sleeps = build(failure(), text_response("ok"))
    assert client.complete_text(system=SYSTEM, messages=MESSAGES) == "ok"
    assert sleeps == [2.0]

    client, recorder, sleeps = build(failure(), failure(), failure(), failure())
    with pytest.raises(LLMUnavailableError) as error:
        client.complete_text(system=SYSTEM, messages=MESSAGES)
    assert error.value.reason == reason and error.value.retryable
    assert len(recorder.requests) == 4 and sleeps == [2.0, 4.0, 8.0]


def quota_error(delay: str) -> httpx.Response:
    detail = {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}
    return httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [detail]}})


def test_rate_limit_waits_as_long_as_google_asks() -> None:
    client, recorder, sleeps = build(quota_error("37.5s"), text_response("ok"))
    assert client.complete_text(system=SYSTEM, messages=MESSAGES) == "ok"
    assert sleeps == [37.5]

    client, _, sleeps = build(httpx.Response(429, headers={"Retry-After": "12"}, json={}), text_response("ok"))
    assert client.complete_text(system=SYSTEM, messages=MESSAGES) == "ok"
    assert sleeps == [12.0]


def test_exhausted_daily_quota_is_not_waited_for() -> None:
    violation = {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaValue": "20"}
    body = {
        "error": {
            "code": 429,
            "details": [
                {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [violation]},
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "45s"},
            ],
        }
    }
    client, recorder, sleeps = build(httpx.Response(429, json=body))
    with pytest.raises(LLMUnavailableError) as error:
        client.complete_text(system=SYSTEM, messages=MESSAGES)
    assert error.value.reason == "rate_limit" and sleeps == [] and len(recorder.requests) == 1


def test_long_quota_delay_is_not_waited_for() -> None:
    client, recorder, sleeps = build(quota_error("3600s"))
    with pytest.raises(LLMUnavailableError) as error:
        client.complete_text(system=SYSTEM, messages=MESSAGES)
    assert error.value.reason == "rate_limit" and sleeps == [] and len(recorder.requests) == 1

    client, recorder, sleeps = build(quota_error("50s"), quota_error("50s"))
    with pytest.raises(LLMUnavailableError):
        client.complete_text(system=SYSTEM, messages=MESSAGES)
    assert sleeps == [50.0] and len(recorder.requests) == 2  # the wait budget is shared by all attempts
