"""Structured JSON logging with secret redaction (docs/architecture/01-overview.md §4).

Usage::

    logger = get_logger(__name__)
    logger.info("order status changed", extra={"event": "order.status_changed", "order_id": 12})
    # or
    log_event(logger, "order.status_changed", order_id=12, old="NEW", new="CONFIRMED")

Redaction masks values of keys like ``password``, ``token``, ``secret``, ``api_key``,
``authorization``, ``access_token``, ``refresh_token`` (and ``*_token``, ``*_secret``, ``password_*``,
camelCase ``accessToken``/``clientSecret``, at any nesting depth), ``Bearer ...`` credentials, bare
JWTs, ``key=value`` / ``"key": "value"`` pairs with such keys inside text, passwords in URLs and the
literal values of configured secrets.
"""

import json
import logging
import re
import sys
from collections.abc import Iterable, Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings, get_settings

MASK = "***"
HANDLER_NAME = "app-json"

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

_RESERVED_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",  # uvicorn: ANSI-coloured duplicate of the message
}

_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "access_token",
        "refresh_token",
        "id_token",
        "client_secret",
        "app_secret",
        "jwt_secret",
        "password_hash",
        "cookie",
        "set_cookie",
        "private_key",
        "xi_api_key",
        "x_hub_signature_256",
    }
)
_SENSITIVE_SUFFIXES = ("_password", "_passwd", "_secret", "_token", "_api_key", "_apikey", "_private_key")
_SENSITIVE_PREFIXES = ("password_", "secret_")

_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9\-._~+/]+=*")
# A bare JWT (header and payload are base64url JSON objects, so both start with "eyJ").
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]{4,}\.eyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]*")
_KEY_VALUE_RE = re.compile(
    r"""(?ix)
    (?P<key>[\w.\-]*(?:password|passwd|secret|token(?!s)|api[_\-]?key|authorization)[\w.\-]*)
    (?P<sep>["']?\s*[:=]\s*)
    (?P<value>"[^"]*"|'[^']*'|[^\s"'&,;}\]]+)
    """
)
_URL_CREDENTIALS_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^\s:/@]*:)([^\s@/]+)@")

_NOISY_LOGGERS = ("httpx", "httpcore", "urllib3", "anthropic", "kombu", "amqp")


def is_sensitive_key(key: object) -> bool:
    """``password``, ``access_token``, ``accessToken``, ``X-Hub-Signature-256``, ``hub.verify_token``..."""
    text = _CAMEL_BOUNDARY_RE.sub("_", str(key).strip())
    normalized = text.lower().replace("-", "_").replace(".", "_")
    return (
        normalized in _SENSITIVE_KEYS
        or normalized.endswith(_SENSITIVE_SUFFIXES)
        or normalized.startswith(_SENSITIVE_PREFIXES)
    )


def _mask_key_value(match: re.Match[str]) -> str:
    value = match.group("value")
    quote = value[0] if value[:1] in ("'", '"') else ""
    return f"{match.group('key')}{match.group('sep')}{quote}{MASK}{quote}"


class Redactor:
    """Masks secrets in strings and nested structures."""

    def __init__(self, secret_values: Iterable[str] = ()) -> None:
        self._secret_values = sorted({value for value in secret_values if value}, key=len, reverse=True)

    def redact_text(self, text: str) -> str:
        for secret in self._secret_values:
            if secret in text:
                text = text.replace(secret, MASK)
        text = _URL_CREDENTIALS_RE.sub(rf"\1{MASK}@", text)
        text = _BEARER_RE.sub(rf"\1 {MASK}", text)
        text = _JWT_RE.sub(MASK, text)
        return _KEY_VALUE_RE.sub(_mask_key_value, text)

    def redact(self, value: Any, _depth: int = 0) -> Any:
        if _depth > 10:
            return value
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, Mapping):
            return {
                key: (MASK if is_sensitive_key(key) and item not in (None, "") else self.redact(item, _depth + 1))
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set, frozenset)):
            return [self.redact(item, _depth + 1) for item in value]
        return value


class SecretRedactionFilter(logging.Filter):
    """Redacts the message, its args and ``extra`` fields of a record in place."""

    def __init__(self, redactor: Redactor | None = None) -> None:
        super().__init__()
        self.redactor = redactor or Redactor()

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # malformed %-args: keep the raw template
            message = str(record.msg)
        record.msg = self.redactor.redact_text(message)
        record.args = None
        for key, value in list(record.__dict__.items()):
            if key in _RESERVED_RECORD_ATTRS or key.startswith("_"):
                continue
            if is_sensitive_key(key) and value not in (None, ""):
                setattr(record, key, MASK)
            else:
                setattr(record, key, self.redactor.redact(value))
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, message, request_id, extra fields, exception."""

    def __init__(self, redactor: Redactor | None = None) -> None:
        super().__init__()
        self.redactor = redactor or Redactor()

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_ctx.get()
        if request_id:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_ATTRS or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:
            payload["exception"] = record.exc_text
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(
            self.redactor.redact(payload),
            ensure_ascii=False,
            default=lambda obj: self.redactor.redact_text(str(obj)),
        )


def configure_logging(settings: Settings | None = None) -> None:
    """Install the JSON handler on the root logger (idempotent)."""
    settings = settings or get_settings()
    level_name = settings.LOG_LEVEL.upper()
    level = logging.getLevelNamesMapping().get(level_name, logging.INFO)

    redactor = Redactor(settings.secret_values())
    handler = logging.StreamHandler(sys.stdout)
    handler.set_name(HANDLER_NAME)
    handler.setFormatter(JsonFormatter(redactor))
    handler.addFilter(SecretRedactionFilter(redactor))

    root = logging.getLogger()
    for existing in list(root.handlers):
        if existing.get_name() == HANDLER_NAME:
            root.removeHandler(existing)
            existing.close()
    root.addHandler(handler)
    root.setLevel(level)

    # Route server/worker loggers through the JSON handler.
    for name in ("uvicorn", "uvicorn.error", "celery", "alembic"):
        server_logger = logging.getLogger(name)
        server_logger.handlers.clear()
        server_logger.propagate = True
    # HTTP access logs are written by RequestContextMiddleware (path without query string).
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.addHandler(logging.NullHandler())
    access_logger.propagate = False
    # Third-party HTTP clients log full URLs at INFO; keep them quiet.
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    message: str | None = None,
    exc_info: Any = None,
    **fields: Any,
) -> None:
    """Log a structured event (``event=...`` + fields). Reserved LogRecord names get a ``_`` suffix."""
    extra: dict[str, Any] = {"event": event}
    for key, value in fields.items():
        extra[f"{key}_" if key in _RESERVED_RECORD_ATTRS else key] = value
    logger.log(level, message or event, extra=extra, exc_info=exc_info)
