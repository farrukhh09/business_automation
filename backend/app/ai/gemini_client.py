"""Google Gemini adapter for ``LLMClient`` (docs/architecture/05-ai.md §2, ``LLM_PROVIDER=gemini``).

REST ``POST {GEMINI_API_URL}/models/{LLM_MODEL}:generateContent`` with the ``x-goog-api-key``
header, over ``httpx`` (no extra SDK). Request shape (checked against ai.google.dev, 2026-09-16):

- the Anthropic-style prompt blocks built by ``app.ai.prompts`` are converted here: ``system`` →
  ``systemInstruction.parts[].text`` (``cache_control`` is dropped — Gemini caches implicitly),
  ``messages`` → ``contents`` with roles ``user``/``model``;
- ``complete_json``: ``generationConfig.responseMimeType="application/json"`` +
  ``responseJsonSchema``; read tools → ``tools[].functionDeclarations[]`` with
  ``parametersJsonSchema``. A model turn with ``functionCall`` parts is appended **verbatim**
  (thought signatures must go back unchanged), the results follow in one ``user`` turn of
  ``functionResponse`` parts carrying the call ``id``. On the last round
  ``toolConfig.functionCallingConfig.mode="NONE"`` forces the JSON answer. Structured output
  together with function calling is supported by Gemini 3 models only; if the API rejects the
  tools (400), the request is repeated once without them — the catalog is in the prompt anyway;
- ``LLM_EFFORT`` and ``LLM_FALLBACKS_ENABLED`` are Anthropic features and are not sent; the
  model's default thinking is used. Thinking tokens count against ``maxOutputTokens``, so a reply
  gets at least ``MIN_OUTPUT_TOKENS`` (the length of the text is limited by ResponseGuard).

``promptFeedback.blockReason`` or a safety ``finishReason`` → ``LLMRefusalError``; transport and
HTTP errors → ``LLMUnavailableError``. 5xx/network/timeouts are retried with backoff; a 429 waits
exactly as long as Google asks (``retryDelay``) within ``MAX_RETRY_WAIT_SECONDS`` — free-tier
per-minute limits are otherwise hit by a customer who writes several messages in a row.
Logs carry the same ``ai.request`` / ``ai.response`` / ``ai.error`` events as the Anthropic adapter,
never keys, prompts or message bodies.

Note: on Google's free tier prompts may be used to improve Google products — use a paid key
(or another provider) for real customer conversations.
"""

import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.ai.llm_client import (
    DEFAULT_MAX_TOOL_ROUNDS,
    JSON_MAX_TOKENS,
    STAGE_REPLY,
    STAGE_UNDERSTANDING,
    TEXT_MAX_TOKENS,
    TOOL_RESULT_CHARS,
    Block,
    LLMRefusalError,
    LLMUnavailableError,
    ToolExecutor,
)
from app.core.config import Settings
from app.core.exceptions import IntegrationNotConfiguredError
from app.core.logging import get_logger, log_event

logger = get_logger(__name__)

PROVIDER = "gemini"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta"
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2  # 2 s, 4 s, 8 s for overload / network errors
# 429 carries the wait the quota needs (RetryInfo.retryDelay / Retry-After). Per-minute limits of the
# free tier ask for up to a minute; anything that does not fit into this budget is not waited for.
MAX_RETRY_WAIT_SECONDS = 75.0
MIN_OUTPUT_TOKENS = 8_192
REFUSAL_FINISH_REASONS = frozenset(
    {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY", "LANGUAGE"}
)

__all__ = ["GEMINI_API_URL", "GeminiLLMClient"]


def _contents(messages: list[Block]) -> list[Block]:
    """Anthropic-style ``messages`` → Gemini ``contents``; already converted turns pass through."""
    contents: list[Block] = []
    for message in messages:
        if "parts" in message:
            contents.append(message)
            continue
        role = "model" if message.get("role") == "assistant" else "user"
        content = message.get("content")
        if isinstance(content, str):
            parts = [{"text": content}]
        else:
            parts = [
                {"text": block["text"]}
                for block in content or ()
                if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
            ]
        if parts:
            contents.append({"role": role, "parts": parts})
    return contents


def _system_instruction(system: list[Block]) -> Block | None:
    parts = [{"text": block["text"]} for block in system if isinstance(block, dict) and block.get("text")]
    return {"parts": parts} if parts else None


def _function_declarations(tools: list[Block]) -> list[Block]:
    return [
        {
            "functionDeclarations": [
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parametersJsonSchema": tool.get("input_schema") or {"type": "object", "properties": {}},
                }
                for tool in tools
            ]
        }
    ]


def _parts(response: Block) -> list[Block]:
    candidates = response.get("candidates") or []
    content = (candidates[0].get("content") or {}) if candidates else {}
    return [part for part in content.get("parts") or () if isinstance(part, dict)]


def _text(parts: list[Block]) -> str:
    return "".join(part["text"] for part in parts if isinstance(part.get("text"), str) and not part.get("thought"))


def _function_calls(parts: list[Block]) -> list[Block]:
    return [part["functionCall"] for part in parts if isinstance(part.get("functionCall"), dict)]


def _finish_reason(response: Block) -> str | None:
    candidates = response.get("candidates") or []
    return candidates[0].get("finishReason") if candidates else None


def _function_response(result: Any) -> Block:
    """``functionResponse.response`` must be an object; long results are cut like the Anthropic adapter's."""
    payload = result if isinstance(result, dict) else {"result": result}
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    if len(text) <= TOOL_RESULT_CHARS:
        return json.loads(text)
    return {"result": text[:TOOL_RESULT_CHARS] + "…"}


def _retry_delay(response: httpx.Response) -> float | None:
    """Seconds a 429 asks to wait: ``Retry-After`` or ``error.details[].retryDelay`` ("37s", "1.5s").

    An exhausted **per-day** quota (``QuotaFailure.violations[].quotaId`` "…PerDay…", e.g. 20 requests a
    day per model on the free tier) still carries a retryDelay of a few seconds — waiting is useless,
    so it is reported as an infinite delay.
    """
    header = response.headers.get("retry-after", "").strip()
    if header.replace(".", "", 1).isdigit():
        return float(header)
    try:
        details = (response.json().get("error") or {}).get("details") or []
    except (ValueError, AttributeError):
        return None
    for detail in details:
        violations = detail.get("violations") if isinstance(detail, dict) else None
        for violation in violations or ():
            if isinstance(violation, dict) and "perday" in str(violation.get("quotaId", "")).lower():
                return float("inf")
    for detail in details:
        value = detail.get("retryDelay") if isinstance(detail, dict) else None
        if isinstance(value, str) and value.endswith("s"):
            try:
                return max(0.0, float(value[:-1]))
            except ValueError:
                return None
    return None


def _usage_fields(response: Block) -> dict[str, Any]:
    usage = response.get("usageMetadata") or {}
    return {
        "input_tokens": usage.get("promptTokenCount"),
        "output_tokens": usage.get("candidatesTokenCount"),
        "thinking_tokens": usage.get("thoughtsTokenCount"),
        "cache_read_input_tokens": usage.get("cachedContentTokenCount"),
    }


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class GeminiLLMClient:
    """``LLMClient`` over the Gemini REST API. ``http`` and ``sleep`` are injectable for tests."""

    provider = PROVIDER

    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        api_key = (settings.LLM_API_KEY or "").strip()
        if not api_key:
            raise IntegrationNotConfiguredError("AI-ассистент не настроен: не задан LLM_API_KEY", provider=PROVIDER)
        model = (settings.LLM_MODEL or "").strip()
        if not model or model.lower().startswith("claude"):
            raise IntegrationNotConfiguredError(
                "AI-ассистент не настроен: для LLM_PROVIDER=gemini укажите модель Gemini в LLM_MODEL",
                provider=PROVIDER,
            )
        self._api_key = api_key
        self._model = model
        self._timeout = float(settings.LLM_TIMEOUT_SECONDS)
        self._http = http
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"GeminiLLMClient(model={self._model!r})"

    @property
    def model(self) -> str:
        return self._model

    # ------------------------------------------------------------------ public API

    def complete_json(
        self,
        *,
        system: list[Block],
        messages: list[Block],
        schema: dict[str, Any],
        tools: list[Block] | None = None,
        tool_executor: ToolExecutor | None = None,
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    ) -> dict[str, Any]:
        generation = {
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
            "maxOutputTokens": JSON_MAX_TOKENS,
        }
        active_tools = list(tools) if tools and tool_executor is not None else None
        if tools and tool_executor is None:
            log_event(
                logger,
                "ai.error",
                level=logging.WARNING,
                stage=STAGE_UNDERSTANDING,
                reason="tools_without_executor",
                tools=len(tools),
            )

        rounds = max(0, int(max_tool_rounds))
        contents = _contents(messages)
        index = 0
        while index <= rounds:
            is_last = index == rounds
            try:
                response = self._generate(
                    system=system,
                    contents=contents,
                    generation=generation,
                    tools=active_tools,
                    tool_mode="NONE" if is_last and active_tools else None,
                    stage=STAGE_UNDERSTANDING,
                    round_index=index,
                )
            except LLMUnavailableError as exc:
                if active_tools and index == 0 and exc.reason == "bad_request":
                    # Structured output + function calling is Gemini 3 only: answer without tools.
                    log_event(
                        logger, "ai.error", level=logging.WARNING, stage=STAGE_UNDERSTANDING, reason="tools_rejected"
                    )
                    active_tools = None
                    continue
                raise
            parts = _parts(response)
            calls = _function_calls(parts)
            if not calls:
                return self._parse_json(response, parts)
            if active_tools is None or tool_executor is None:
                raise self._error("unexpected_tool_use", stage=STAGE_UNDERSTANDING)
            if is_last:
                raise self._error("tool_rounds_exceeded", stage=STAGE_UNDERSTANDING)
            contents.append({"role": "model", "parts": parts})
            contents.append({"role": "user", "parts": self._run_tools(calls, tool_executor)})
            index += 1
        raise self._error("tool_rounds_exceeded", stage=STAGE_UNDERSTANDING)

    def complete_text(self, *, system: list[Block], messages: list[Block], max_tokens: int = TEXT_MAX_TOKENS) -> str:
        response = self._generate(
            system=system,
            contents=_contents(messages),
            generation={"maxOutputTokens": max(int(max_tokens), MIN_OUTPUT_TOKENS)},
            stage=STAGE_REPLY,
        )
        text = _text(_parts(response)).strip()
        if not text:
            raise self._error("empty_response", stage=STAGE_REPLY)
        return text

    # ------------------------------------------------------------------ internals

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(base_url=GEMINI_API_URL, timeout=self._timeout)
        return self._http

    def _generate(
        self,
        *,
        system: list[Block],
        contents: list[Block],
        generation: dict[str, Any],
        stage: str,
        tools: list[Block] | None = None,
        tool_mode: str | None = None,
        round_index: int | None = None,
    ) -> Block:
        body: dict[str, Any] = {"contents": contents, "generationConfig": generation}
        instruction = _system_instruction(system)
        if instruction is not None:
            body["systemInstruction"] = instruction
        if tools:
            body["tools"] = _function_declarations(tools)
            if tool_mode is not None:
                body["toolConfig"] = {"functionCallingConfig": {"mode": tool_mode}}

        log_event(
            logger,
            "ai.request",
            stage=stage,
            provider=PROVIDER,
            model=self._model,
            structured="responseJsonSchema" in generation,
            messages_count=len(contents),
            system_blocks=len(system),
            tools=len(tools or ()),
            max_tokens=generation.get("maxOutputTokens"),
            round=round_index,
        )
        started = time.monotonic()
        response = self._post(body, stage=stage, started=started)
        finish_reason = _finish_reason(response)
        log_event(
            logger,
            "ai.response",
            stage=stage,
            provider=PROVIDER,
            model=response.get("modelVersion") or self._model,
            stop_reason=finish_reason,
            duration_ms=_elapsed_ms(started),
            round=round_index,
            **_usage_fields(response),
        )
        block_reason = (response.get("promptFeedback") or {}).get("blockReason")
        if block_reason or finish_reason in REFUSAL_FINISH_REASONS:
            category = block_reason or finish_reason
            log_event(logger, "ai.error", level=logging.WARNING, stage=stage, reason="refusal", category=category)
            raise LLMRefusalError(category=category)
        return response

    def _post(self, body: Block, *, stage: str, started: float) -> Block:
        url = f"/models/{self._model}:generateContent"
        headers = {"x-goog-api-key": self._api_key, "content-type": "application/json"}
        attempt = 0
        waited = 0.0
        while True:
            reason: str
            status: int | None = None
            error: str | None = None
            delay: float | None = None
            try:
                response = self._client().post(url, json=body, headers=headers)
            except httpx.TimeoutException as exc:
                reason, error = "timeout", type(exc).__name__
            except httpx.TransportError as exc:
                reason, error = "network", type(exc).__name__
            else:
                status = response.status_code
                if status == 200:
                    try:
                        data = response.json()
                    except ValueError:
                        raise self._error(
                            "invalid_response", stage=stage, status=status, duration_ms=_elapsed_ms(started)
                        ) from None
                    if isinstance(data, dict):
                        return data
                    raise self._error("invalid_response", stage=stage, status=status, duration_ms=_elapsed_ms(started))
                if status == 429:
                    reason = "rate_limit"
                    delay = _retry_delay(response)
                elif status >= 500:
                    reason = "server_error"
                elif status == 400:
                    reason = "bad_request"
                else:
                    reason = "api_error"
            retryable = reason in ("timeout", "network", "rate_limit", "server_error")
            wait = float(RETRY_BACKOFF_SECONDS * 2**attempt) if delay is None else delay
            if not retryable or attempt >= MAX_RETRIES or wait > MAX_RETRY_WAIT_SECONDS - waited:
                # A per-day quota answers with a long (or no usable) delay: waiting would only hold
                # the customer's message, so the dialog is handed over at once.
                raise self._error(reason, stage=stage, status=status, error=error, duration_ms=_elapsed_ms(started))
            attempt += 1
            waited += wait
            log_event(
                logger, "ai.retry", level=logging.WARNING, stage=stage, reason=reason, attempt=attempt, wait_s=wait
            )
            self._sleep(wait)

    def _run_tools(self, calls: list[Block], tool_executor: ToolExecutor) -> list[Block]:
        parts: list[Block] = []
        for call in calls:
            name = str(call.get("name") or "")
            arguments = call.get("args") if isinstance(call.get("args"), dict) else {}
            try:
                output: Any = tool_executor(name, dict(arguments))
            except Exception as exc:
                log_event(
                    logger,
                    "ai.error",
                    level=logging.WARNING,
                    stage=STAGE_UNDERSTANDING,
                    reason="tool_failed",
                    tool=name,
                    error=type(exc).__name__,
                )
                output = {"error": type(exc).__name__}
            function_response: Block = {"name": name, "response": _function_response(output)}
            if call.get("id"):
                function_response["id"] = call["id"]
            parts.append({"functionResponse": function_response})
        return parts

    def _parse_json(self, response: Block, parts: list[Block]) -> dict[str, Any]:
        if _finish_reason(response) == "MAX_TOKENS":
            raise self._error("max_tokens", stage=STAGE_UNDERSTANDING)
        text = _text(parts).strip()
        if not text:
            raise self._error("empty_response", stage=STAGE_UNDERSTANDING)
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise self._error("invalid_json", stage=STAGE_UNDERSTANDING) from exc
        if not isinstance(data, dict):
            raise self._error("invalid_json", stage=STAGE_UNDERSTANDING)
        return data

    def _error(
        self,
        reason: str,
        *,
        stage: str,
        status: int | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> LLMUnavailableError:
        log_event(
            logger,
            "ai.error",
            level=logging.WARNING,
            stage=stage,
            provider=PROVIDER,
            reason=reason,
            status=status,
            error=error,
            duration_ms=duration_ms,
        )
        return LLMUnavailableError(reason=reason, status=status)
