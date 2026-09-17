"""Domain exceptions and their HTTP mapping (docs/architecture/01-overview.md §4, 04-api.md §0).

Every error response body is ``{"detail": str, "code": str}``; extra keyword details passed to an
exception (e.g. ``BusinessRuleError(code="order_incomplete", missing=[...])``) are added as
additional top-level keys. Request validation errors keep FastAPI's standard ``detail`` list and
add ``"code": "validation_error"``.
"""

import re
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for errors that map to an HTTP response."""

    status_code: int = 400
    code: str = "bad_request"
    default_detail: str = "Некорректный запрос"
    headers: Mapping[str, str] | None = None

    def __init__(
        self,
        detail: str | None = None,
        *,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        if code:
            self.code = code
        self.detail = detail or self.default_detail
        self.details: dict[str, Any] = {**(details or {}), **extra}
        super().__init__(self.detail)

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"detail": self.detail, "code": self.code}
        for key, value in self.details.items():
            body.setdefault(key, value)
        return body

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, detail={self.detail!r}, details={self.details!r})"


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"
    default_detail = "Некорректный запрос"


class AuthenticationError(AppError):
    status_code = 401
    code = "not_authenticated"
    default_detail = "Требуется авторизация"
    headers = {"WWW-Authenticate": "Bearer"}


class InvalidTokenError(AuthenticationError):
    code = "invalid_token"
    default_detail = "Недействительный токен"


class TokenExpiredError(InvalidTokenError):
    default_detail = "Срок действия токена истёк"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "forbidden"
    default_detail = "Недостаточно прав"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    default_detail = "Объект не найден"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    default_detail = "Конфликт данных"


class GoneError(AppError):
    status_code = 410
    code = "gone"
    default_detail = "Ресурс больше недоступен"


_BUSINESS_RULE_DETAILS: dict[str, str] = {
    "order_incomplete": "Не заполнены обязательные данные заказа",
    "invalid_status_transition": "Недопустимая смена статуса заказа",
}


_MACHINE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class BusinessRuleError(AppError):
    """Violation of a business rule (03-business-rules.md) → 422 with a machine code.

    ``BusinessRuleError("invalid_status_transition")``
    ``BusinessRuleError(code="order_incomplete", missing=["delivery_time"])``
    ``BusinessRuleError("order_incomplete", "Нет даты", {"missing": ["delivery_date"]})``
    ``BusinessRuleError("Нельзя изменить заказ в производстве")`` — a first positional argument
    that is not a snake_case machine code is the human-readable ``detail`` (the class code is kept),
    so a message is never sent to the frontend as ``code``.
    """

    status_code = 422
    code = "business_rule_violation"
    default_detail = "Операция нарушает бизнес-правила"

    def __init__(
        self,
        code: str | None = None,
        detail: str | None = None,
        details: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        class_code = type(self).code
        if code is not None and detail is None and not _MACHINE_CODE_RE.match(code):
            code, detail = None, code
        resolved_code = code or class_code
        super().__init__(
            detail or _BUSINESS_RULE_DETAILS.get(resolved_code), code=resolved_code, details=details, **extra
        )


class ValidationError(BusinessRuleError):
    """Invalid input that passed schema validation but fails a domain check → 422 ``validation_error``.

    01-overview.md §4 names "ValidationError/BusinessRuleError": it is a ``BusinessRuleError``
    (same 422 handling, ``except BusinessRuleError`` catches it) with its own default code.
    ``ValidationError("Дата доставки в прошлом")`` → ``{"detail": "...", "code": "validation_error"}``;
    ``ValidationError("order_incomplete", missing=[...])`` → code ``order_incomplete``.
    Not to be confused with ``pydantic.ValidationError``.
    """

    code = "validation_error"
    default_detail = "Ошибка валидации"


class IntegrationError(AppError):
    """External provider failed (Instagram, LLM, STT/TTS, geocoder, ...)."""

    status_code = 502
    code = "integration_error"
    default_detail = "Ошибка внешнего сервиса"


class IntegrationNotConfiguredError(IntegrationError):
    """Provider is not configured (missing key/URL) — the system degrades gracefully."""

    status_code = 503
    code = "integration_not_configured"
    default_detail = "Интеграция не настроена"


_HTTP_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "not_authenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    409: "conflict",
    410: "gone",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "integration_error",
    503: "service_unavailable",
}

_HTTP_STATUS_DETAILS: dict[int, str] = {
    400: "Некорректный запрос",
    401: "Требуется авторизация",
    403: "Недостаточно прав",
    404: "Не найдено",
    405: "Метод не поддерживается",
    406: "Неприемлемый формат ответа",
    409: "Конфликт данных",
    410: "Ресурс больше недоступен",
    413: "Слишком большой запрос",
    415: "Неподдерживаемый тип содержимого",
    422: "Ошибка валидации",
    429: "Слишком много запросов, попробуйте позже",
    500: "Внутренняя ошибка сервера",
    502: "Ошибка внешнего сервиса",
    503: "Сервис временно недоступен",
}


def error_body(detail: Any, code: str) -> dict[str, Any]:
    return {"detail": detail, "code": code}


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    if exc.status_code >= 500:
        logger.warning(
            "request failed: %s",
            exc.code,
            extra={"event": "http.app_error", "code": exc.code, "status": exc.status_code, "path": request.url.path},
        )
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder(exc.to_dict()),
        headers=dict(exc.headers) if exc.headers else None,
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    status_code = exc.status_code
    code = _HTTP_STATUS_CODES.get(status_code, "http_error")
    detail: Any = exc.detail
    try:
        standard_phrase = HTTPStatus(status_code).phrase
    except ValueError:
        standard_phrase = None
    if not detail or detail == standard_phrase:
        detail = _HTTP_STATUS_DETAILS.get(status_code, standard_phrase or "Ошибка")
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(error_body(detail, code)),
        headers=getattr(exc, "headers", None),
    )


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors()), "code": "validation_error"},
    )


_LOGGED_MARKER = "_bakery_unhandled_logged"


def mark_exception_logged(exc: BaseException) -> None:
    """Called by ``RequestContextMiddleware`` after logging ``exc`` with the request id."""
    try:
        setattr(exc, _LOGGED_MARKER, True)
    except (AttributeError, TypeError):  # exotic exception types without __dict__
        pass


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if not getattr(exc, _LOGGED_MARKER, False):
        logger.error(
            "unhandled error",
            exc_info=exc,
            extra={"event": "http.unhandled_error", "path": request.url.path, "method": request.method},
        )
    return JSONResponse(status_code=500, content=error_body(_HTTP_STATUS_DETAILS[500], "internal_error"))


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
