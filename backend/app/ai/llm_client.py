"""LLM client: ``LLMClient`` protocol and the Anthropic adapter (docs/architecture/05-ai.md §2).

The bot talks to the model through two calls only:

- ``complete_json`` — the understanding step: a structured result constrained by a JSON schema
  (``output_config.format``), optionally with read-only tools (05 §7);
- ``complete_text`` — the wording of a reply whose facts were already decided by the backend.

Request shape (verified against the installed ``anthropic`` 1.5.0 SDK and the Claude Opus 5
migration notes):

- model / effort / timeout come from ``Settings`` (``LLM_MODEL`` defaults to ``claude-opus-5``);
- **no** ``thinking`` parameter — adaptive thinking is on by default on Claude Opus 5, and
  ``budget_tokens`` is rejected with 400; ``temperature``/``top_p``/``top_k`` are rejected too,
  so this module never sends sampling parameters;
- ``output_config={"effort": ..., "format": {"type": "json_schema", "schema": ...}}``;
- ``LLM_FALLBACKS_ENABLED`` → ``client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
  fallbacks="default", ...)``: a refusal is re-run server-side on Anthropic's recommended model;
- no assistant prefill (rejected together with structured outputs), non-streaming.

``stop_reason == "refusal"`` is checked **before** ``content`` is read → ``LLMRefusalError``.
SDK errors are caught most-specific-first and become ``LLMUnavailableError``; ``DialogService``
turns both into a handoff (03 §6). A missing ``LLM_API_KEY`` raises ``IntegrationNotConfiguredError``.

Logging (SPEC §41): ``ai.request`` / ``ai.response`` / ``ai.error`` with model, stage, counters,
``stop_reason``, usage and the Anthropic request id — never keys, prompts or message bodies.
"""

import json
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

import anthropic

from app.core.config import Settings, get_settings
from app.core.exceptions import IntegrationError, IntegrationNotConfiguredError
from app.core.logging import get_logger, log_event

logger = get_logger(__name__)

PROVIDER = "anthropic"
LLM_NOT_CONFIGURED_MESSAGE = "AI-ассистент не настроен: не задан LLM_API_KEY"

# 05 §2: server-side refusal fallbacks. The header is fixed by the API for the `"default"` scalar
# form (the array form uses `server-side-fallback-2026-06-01`; pairing them returns 400).
FALLBACKS_BETA = "server-side-fallback-2026-07-01"
FALLBACKS_MODE = "default"

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

MAX_RETRIES = 2  # SDK-level retries: connection errors, 408/409/429 and >=500
JSON_MAX_TOKENS = 16_000  # understanding step, non-streaming
TEXT_MAX_TOKENS = 1_024  # one Instagram reply (<= 1000 bytes after the guard)
DEFAULT_MAX_TOOL_ROUNDS = 3
TOOL_RESULT_CHARS = 8_000

STAGE_UNDERSTANDING = "understanding"
STAGE_REPLY = "reply"
STAGE_RECEIPT = "receipt"  # a payment receipt screenshot read into JSON (app/ai/receipt.py)

# Prompt/message types: plain dicts, exactly as they are sent to the API.
Block = dict[str, Any]
ToolExecutor = Callable[[str, dict[str, Any]], Any]


def image_block(data_base64: str, media_type: str) -> Block:
    """An image inside a user message, in the Anthropic shape; the Gemini adapter converts it."""
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data_base64}}


class LLMError(IntegrationError):
    """Base class for LLM failures. ``reason`` is a machine code for logs and tests."""

    code = "llm_error"
    default_detail = "Ошибка AI-сервиса"

    def __init__(
        self,
        detail: str | None = None,
        *,
        reason: str = "error",
        status: int | None = None,
        request_id: str | None = None,
    ) -> None:
        self.reason = reason
        self.status = status
        self.request_id = request_id
        super().__init__(detail, reason=reason, status=status)


class LLMUnavailableError(LLMError):
    """The model could not be reached or answered unusably (network, 4xx/5xx, unparsable output)."""

    code = "llm_unavailable"
    default_detail = "AI-сервис временно недоступен"

    @property
    def retryable(self) -> bool:
        """Timeouts, network errors, rate limits and 5xx are worth retrying."""
        if self.reason in ("timeout", "network", "rate_limit", "server_error"):
            return True
        return self.status is not None and (self.status == 429 or self.status >= 500)


class LLMRefusalError(LLMError):
    """``stop_reason == "refusal"``: the model declined. ``content`` must not be used."""

    code = "llm_refusal"
    default_detail = "AI-сервис отклонил запрос"

    def __init__(
        self,
        detail: str | None = None,
        *,
        category: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self.category = category
        super().__init__(detail, reason="refusal", request_id=request_id)


@runtime_checkable
class LLMClient(Protocol):
    """05 §2. Implementations must not touch the database; tests provide fakes."""

    def complete_json(
        self,
        *,
        system: list[Block],
        messages: list[Block],
        schema: dict[str, Any],
        tools: list[Block] | None = None,
        tool_executor: ToolExecutor | None = None,
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    ) -> dict[str, Any]: ...

    def complete_text(
        self, *, system: list[Block], messages: list[Block], max_tokens: int = TEXT_MAX_TOKENS
    ) -> str: ...


def _text_blocks(message: Any) -> list[str]:
    """Text of every ``text`` block, in order (thinking/tool blocks are skipped)."""
    parts: list[str] = []
    for block in getattr(message, "content", None) or ():
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", None)
            if isinstance(text, str) and text:
                parts.append(text)
    return parts


def _tool_use_blocks(message: Any) -> list[Any]:
    return [block for block in getattr(message, "content", None) or () if getattr(block, "type", None) == "tool_use"]


def _tool_input(block: Any) -> dict[str, Any]:
    """Tool arguments as an object. Inputs are parsed, never matched as strings."""
    payload = getattr(block, "input", None)
    if isinstance(payload, dict):
        return dict(payload)
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _tool_result_content(result: Any) -> str:
    """Tool output as text for the ``tool_result`` block (deterministic JSON, bounded length)."""
    if isinstance(result, str):
        text = result
    else:
        text = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
    return text if len(text) <= TOOL_RESULT_CHARS else text[:TOOL_RESULT_CHARS] + "…"


def _request_id_of(message: Any) -> str | None:
    """``Message._request_id`` is public despite the underscore (SDK response helper)."""
    value = getattr(message, "_request_id", None)
    return value if isinstance(value, str) else None


def _usage_fields(message: Any) -> dict[str, Any]:
    usage = getattr(message, "usage", None)
    if usage is None:
        return {}
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
    }


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class AnthropicLLMClient:
    """``LLMClient`` over the official Anthropic SDK.

    ``client`` is injectable so tests can pass a fake; in production it is created lazily on the
    first call (``anthropic.Anthropic(api_key=..., timeout=LLM_TIMEOUT_SECONDS, max_retries=2)``),
    which keeps ``DialogService`` construction free of network setup.
    """

    provider = PROVIDER

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        api_key = (settings.LLM_API_KEY or "").strip()
        if client is None and not api_key:
            raise IntegrationNotConfiguredError(LLM_NOT_CONFIGURED_MESSAGE, provider=PROVIDER)
        model = (settings.LLM_MODEL or "").strip()
        if not model:
            raise IntegrationNotConfiguredError("AI-ассистент не настроен: не задан LLM_MODEL", provider=PROVIDER)
        effort = (settings.LLM_EFFORT or "").strip().lower()
        if effort not in EFFORT_LEVELS:
            raise IntegrationNotConfiguredError(
                f"Некорректное значение LLM_EFFORT: {effort or '(пусто)'}", provider=PROVIDER
            )
        self._api_key = api_key
        self._client = client
        self._model = model
        self._effort = effort
        self._timeout = float(settings.LLM_TIMEOUT_SECONDS)
        self._fallbacks_enabled = bool(settings.LLM_FALLBACKS_ENABLED)

    def __repr__(self) -> str:
        return f"AnthropicLLMClient(model={self._model!r}, effort={self._effort!r})"

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
        """Structured result. Tools (if any) are executed through ``tool_executor`` (read-only)."""
        output_config: dict[str, Any] = {
            "effort": self._effort,
            "format": {"type": "json_schema", "schema": schema},
        }
        active_tools: list[Block] | None = None
        if tools:
            if tool_executor is None:
                # Never offer a tool the caller cannot run; the model would stall on tool_use.
                log_event(
                    logger,
                    "ai.error",
                    level=logging.WARNING,
                    stage=STAGE_UNDERSTANDING,
                    reason="tools_without_executor",
                    tools=len(tools),
                )
            else:
                active_tools = list(tools)

        rounds = max(0, int(max_tool_rounds))
        conversation: list[Block] = list(messages)
        for index in range(rounds + 1):
            is_last = index == rounds
            message = self._create(
                system=system,
                messages=conversation,
                max_tokens=JSON_MAX_TOKENS,
                output_config=output_config,
                tools=active_tools,
                # On the final round tools stay in the prompt (cache-stable) but may not be called,
                # so the model has to produce the JSON answer.
                tool_choice={"type": "none"} if is_last and active_tools else None,
                stage=STAGE_UNDERSTANDING,
                round_index=index,
            )
            if getattr(message, "stop_reason", None) != "tool_use":
                return self._parse_json(message)
            if active_tools is None or tool_executor is None:
                raise self._error("unexpected_tool_use", stage=STAGE_UNDERSTANDING, request_id=_request_id_of(message))
            if is_last:
                raise self._error("tool_rounds_exceeded", stage=STAGE_UNDERSTANDING, request_id=_request_id_of(message))
            # The whole assistant turn is appended (tool_use blocks included), then every result
            # goes back in ONE user message.
            conversation.append({"role": "assistant", "content": list(message.content)})
            conversation.append({"role": "user", "content": self._run_tools(message, tool_executor)})

        raise self._error("tool_rounds_exceeded", stage=STAGE_UNDERSTANDING)

    def complete_text(self, *, system: list[Block], messages: list[Block], max_tokens: int = TEXT_MAX_TOKENS) -> str:
        """Plain reply text. Facts come from the prompt; the guard validates the result (05 §6)."""
        message = self._create(
            system=system,
            messages=list(messages),
            max_tokens=int(max_tokens),
            output_config={"effort": self._effort},
            stage=STAGE_REPLY,
        )
        text = "\n".join(_text_blocks(message)).strip()
        if not text:
            raise self._error("empty_response", stage=STAGE_REPLY, request_id=_request_id_of(message))
        return text

    # ------------------------------------------------------------------ internals

    def _sdk(self) -> Any:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self._api_key, timeout=self._timeout, max_retries=MAX_RETRIES)
        return self._client

    def _create(
        self,
        *,
        system: list[Block],
        messages: list[Block],
        max_tokens: int,
        output_config: dict[str, Any],
        stage: str,
        tools: list[Block] | None = None,
        tool_choice: dict[str, Any] | None = None,
        round_index: int | None = None,
    ) -> Any:
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "output_config": output_config,
        }
        if tools:
            request["tools"] = tools
        if tool_choice is not None:
            request["tool_choice"] = tool_choice

        log_event(
            logger,
            "ai.request",
            stage=stage,
            model=self._model,
            effort=self._effort,
            structured=bool(output_config.get("format")),
            messages_count=len(messages),
            system_blocks=len(system),
            tools=len(tools or ()),
            max_tokens=max_tokens,
            fallbacks=self._fallbacks_enabled,
            round=round_index,
        )
        started = time.monotonic()
        client = self._sdk()
        try:
            if self._fallbacks_enabled:
                message = client.beta.messages.create(betas=[FALLBACKS_BETA], fallbacks=FALLBACKS_MODE, **request)
            else:
                message = client.messages.create(**request)
        except anthropic.RateLimitError as exc:
            raise self._sdk_error("rate_limit", exc, stage=stage, started=started) from exc
        except anthropic.BadRequestError as exc:
            raise self._sdk_error("bad_request", exc, stage=stage, started=started) from exc
        except anthropic.APIStatusError as exc:
            status = getattr(exc, "status_code", None) or 0
            reason = "server_error" if status >= 500 else "api_error"
            raise self._sdk_error(reason, exc, stage=stage, started=started) from exc
        except anthropic.APITimeoutError as exc:  # subclass of APIConnectionError → must come first
            raise self._sdk_error("timeout", exc, stage=stage, started=started) from exc
        except anthropic.APIConnectionError as exc:
            raise self._sdk_error("network", exc, stage=stage, started=started) from exc
        except anthropic.AnthropicError as exc:
            raise self._sdk_error("sdk_error", exc, stage=stage, started=started) from exc

        stop_reason = getattr(message, "stop_reason", None)
        request_id = _request_id_of(message)
        log_event(
            logger,
            "ai.response",
            stage=stage,
            model=getattr(message, "model", None),
            stop_reason=stop_reason,
            request_id=request_id,
            duration_ms=_elapsed_ms(started),
            round=round_index,
            **_usage_fields(message),
        )
        if stop_reason == "refusal":  # checked before `content` is read
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None)
            log_event(
                logger,
                "ai.error",
                level=logging.WARNING,
                stage=stage,
                reason="refusal",
                category=category,
                request_id=request_id,
            )
            raise LLMRefusalError(category=category, request_id=request_id)
        return message

    def _run_tools(self, message: Any, tool_executor: ToolExecutor) -> list[Block]:
        """Execute every ``tool_use`` block of one assistant turn → one list of ``tool_result``."""
        results: list[Block] = []
        for block in _tool_use_blocks(message):
            name = str(getattr(block, "name", "") or "")
            arguments = _tool_input(block)
            is_error = False
            try:
                output: Any = tool_executor(name, arguments)
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
                is_error = True
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": getattr(block, "id", None),
                    "content": _tool_result_content(output),
                    "is_error": is_error,
                }
            )
        return results

    def _parse_json(self, message: Any) -> dict[str, Any]:
        request_id = _request_id_of(message)
        if getattr(message, "stop_reason", None) == "max_tokens":
            raise self._error("max_tokens", stage=STAGE_UNDERSTANDING, request_id=request_id)
        parts = _text_blocks(message)
        if not parts:
            raise self._error("empty_response", stage=STAGE_UNDERSTANDING, request_id=request_id)
        try:
            data = json.loads("".join(parts))
        except ValueError as exc:
            raise self._error("invalid_json", stage=STAGE_UNDERSTANDING, request_id=request_id) from exc
        if not isinstance(data, dict):
            raise self._error("invalid_json", stage=STAGE_UNDERSTANDING, request_id=request_id)
        return data

    def _error(
        self,
        reason: str,
        *,
        stage: str,
        status: int | None = None,
        request_id: str | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> LLMUnavailableError:
        """Log ``ai.error`` and build the exception (provider messages are never kept)."""
        log_event(
            logger,
            "ai.error",
            level=logging.WARNING,
            stage=stage,
            reason=reason,
            status=status,
            error=error,
            request_id=request_id,
            duration_ms=duration_ms,
        )
        return LLMUnavailableError(reason=reason, status=status, request_id=request_id)

    def _sdk_error(self, reason: str, exc: Exception, *, stage: str, started: float) -> LLMUnavailableError:
        return self._error(
            reason,
            stage=stage,
            status=getattr(exc, "status_code", None),
            request_id=getattr(exc, "request_id", None),
            error=type(exc).__name__,
            duration_ms=_elapsed_ms(started),
        )


def get_llm_client(settings: Settings | None = None, *, client: Any | None = None) -> LLMClient:
    """Factory (01 §3.4) by ``LLM_PROVIDER``. Raises ``IntegrationNotConfiguredError`` when ``LLM_API_KEY`` is empty."""
    settings = settings or get_settings()
    if settings.LLM_PROVIDER == "gemini":
        from app.ai.gemini_client import GeminiLLMClient  # imports this module: resolved lazily

        return GeminiLLMClient(settings, http=client)
    return AnthropicLLMClient(settings, client=client)


def is_llm_configured(settings: Settings | None = None) -> bool:
    """Cheap check for the integration-status endpoint / ``DialogService`` degradation."""
    settings = settings or get_settings()
    return bool((settings.LLM_API_KEY or "").strip())


__all__ = [
    "DEFAULT_MAX_TOOL_ROUNDS",
    "EFFORT_LEVELS",
    "FALLBACKS_BETA",
    "FALLBACKS_MODE",
    "JSON_MAX_TOKENS",
    "STAGE_RECEIPT",
    "STAGE_REPLY",
    "STAGE_UNDERSTANDING",
    "TEXT_MAX_TOKENS",
    "AnthropicLLMClient",
    "LLMClient",
    "LLMError",
    "LLMRefusalError",
    "LLMUnavailableError",
    "ToolExecutor",
    "get_llm_client",
    "image_block",
    "is_llm_configured",
]
