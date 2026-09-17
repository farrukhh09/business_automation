"""HTTP middleware: request id + access log (docs/architecture/01-overview.md §4, SPEC §41).

The access log contains method, path (without query string; capability tokens such as
``/api/public/location/{token}`` masked), status and duration_ms. Request/response bodies and
headers (incl. ``Authorization``) are never logged. Unhandled exceptions are logged here (with the
request id) because Starlette's error middleware runs outside this one.
"""

import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.exceptions import mark_exception_logged
from app.core.logging import get_logger, request_id_ctx

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")
# Path segments that are credentials (capability tokens) and must not reach the logs.
_SENSITIVE_PATH_RE = re.compile(r"^(/api/public/location/)[^/]+")
MASK = "***"
# Probe endpoints are logged at DEBUG to keep logs readable.
_QUIET_PATHS = frozenset({"/api/health", "/api/health/ready"})

logger = get_logger("app.http")


def loggable_path(path: str) -> str:
    """Request path for logs: no query string (never passed here) and no capability tokens."""
    return _SENSITIVE_PATH_RE.sub(rf"\g<1>{MASK}", path)


def _incoming_request_id(scope: Scope) -> str | None:
    header = REQUEST_ID_HEADER.lower().encode("latin-1")
    for name, value in scope.get("headers", []):
        if name.lower() == header:
            candidate = value.decode("latin-1").strip()
            return candidate if _REQUEST_ID_RE.match(candidate) else None
    return None


class RequestContextMiddleware:
    """Pure ASGI middleware (safe for streaming responses)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        method = scope.get("method", "")
        path = loggable_path(scope.get("path", ""))
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            # Starlette's ServerErrorMiddleware (and the app's ``Exception`` handler) run OUTSIDE this
            # middleware, after request_id_ctx is reset: log the traceback here, with the request id.
            logger.error(
                "unhandled error",
                exc_info=exc,
                extra={"event": "http.unhandled_error", "method": method, "path": path},
            )
            mark_exception_logged(exc)
            raise
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            if status_code >= 500:
                level = logging.ERROR
            elif path in _QUIET_PATHS and status_code < 400:
                level = logging.DEBUG
            else:
                level = logging.INFO
            logger.log(
                level,
                "%s %s %s %.2fms",
                method,
                path,
                status_code,
                duration_ms,
                extra={
                    "event": "http.request",
                    "method": method,
                    "path": path,
                    "status": status_code,
                    "duration_ms": duration_ms,
                },
            )
            request_id_ctx.reset(token)
