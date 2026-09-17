"""Instagram Graph API client — "Instagram API with Instagram Login" (06-integrations.md §1).

Host ``INSTAGRAM_GRAPH_URL`` (``https://graph.instagram.com``), version ``INSTAGRAM_API_VERSION``,
account ``INSTAGRAM_ACCOUNT_ID`` (``user_id`` from ``/me?fields=user_id``), Instagram User token
``INSTAGRAM_ACCESS_TOKEN`` (or a fresher refreshed token passed as ``access_token``).

The token travels only in the ``Authorization: Bearer`` header, except for
``refresh_access_token`` where Meta documents it as a query parameter. Media CDN downloads are sent
without credentials. Nothing here logs tokens, full URLs or message bodies.
"""

import logging
import time
from typing import Any, Protocol, Self, runtime_checkable
from urllib.parse import SplitResult, quote, urlsplit

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import IntegrationError, IntegrationNotConfiguredError
from app.core.logging import MASK, get_logger, log_event
from app.integrations.instagram.text import MAX_TEXT_BYTES, split_text, utf8_len

__all__ = [
    "API_TIMEOUT",
    "DOWNLOAD_TIMEOUT",
    "MAX_MEDIA_BYTES",
    "MAX_TEXT_BYTES",
    "RETRYABLE_ERROR_CODES",
    "InstagramAPIError",
    "InstagramClient",
    "InstagramMediaTooLargeError",
    "InstagramMessenger",
    "InstagramNotConfiguredError",
    "get_instagram_client",
    "split_text",
]

logger = get_logger(__name__)

MAX_MEDIA_BYTES = 25 * 1024 * 1024  # audio/video/file attachments: 25MB
API_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
# Graph API throttling / temporary errors: 2 service unavailable, 4 app rate limit, 17 user rate limit,
# 32 page rate limit, 613 custom rate limit, 80002 Instagram messaging throttling.
RETRYABLE_ERROR_CODES: frozenset[int] = frozenset({2, 4, 17, 32, 613, 80002})
USER_AGENT = "bakery-backend/1.0 (+instagram)"

_MAX_ERROR_MESSAGE_LENGTH = 300
_DOWNLOAD_CHUNK_SIZE = 64 * 1024


# --------------------------------------------------------------------------- errors


class InstagramAPIError(IntegrationError):
    """Graph API / transport failure.

    ``exc.code`` stays the machine code ``"instagram_api_error"`` used by the HTTP error handler;
    Meta's numeric ``error.code`` / ``error.error_subcode`` are ``exc.error_code`` / ``exc.error_subcode``.
    ``retryable`` is True for network errors, HTTP 5xx and 429, ``is_transient`` errors and the codes in
    ``RETRYABLE_ERROR_CODES``. ``partial_message_ids`` lists parts already delivered by ``send_text``.
    """

    code = "instagram_api_error"
    default_detail = "Ошибка Instagram API"

    def __init__(
        self,
        status: int | None = None,
        code: int | None = None,
        subcode: int | None = None,
        message: str | None = None,
        fbtrace_id: str | None = None,
        retryable: bool = False,
        *,
        error_type: str | None = None,
    ) -> None:
        self.status = status
        self.error_code = code
        self.error_subcode = subcode
        self.message = message or ""
        self.fbtrace_id = fbtrace_id
        self.retryable = retryable
        self.error_type = error_type
        self.partial_message_ids: list[str] = []
        super().__init__(details={"retryable": retryable})

    def __str__(self) -> str:
        return (
            f"Instagram API error (status={self.status}, code={self.error_code}, subcode={self.error_subcode}, "
            f"retryable={self.retryable}): {self.message}"
        )


class InstagramMediaTooLargeError(InstagramAPIError):
    code = "instagram_media_too_large"
    default_detail = "Вложение Instagram слишком большое"

    def __init__(self, size: int | None, max_bytes: int, status: int | None = 200) -> None:
        self.size = size
        self.max_bytes = max_bytes
        detail = f"media exceeds {max_bytes} bytes" + (f" (Content-Length {size})" if size is not None else "")
        super().__init__(status=status, message=detail, retryable=False)


class InstagramNotConfiguredError(IntegrationNotConfiguredError):
    """``INSTAGRAM_ACCESS_TOKEN`` or ``INSTAGRAM_ACCOUNT_ID`` is missing (names only, never values)."""

    default_detail = "Интеграция Instagram не настроена"

    def __init__(self, missing: list[str]) -> None:
        self.missing = list(missing)
        super().__init__(details={"missing": self.missing})


# --------------------------------------------------------------------------- protocol


@runtime_checkable
class InstagramMessenger(Protocol):
    """What services need from Instagram; tests provide fakes implementing it."""

    def send_text(self, recipient_id: str, text: str) -> list[str]: ...

    def send_audio(self, recipient_id: str, url: str) -> str: ...

    def get_user_profile(self, igsid: str) -> dict[str, str | None] | None: ...

    def download_attachment(self, url: str, max_bytes: int = MAX_MEDIA_BYTES) -> tuple[bytes, str]: ...

    def refresh_long_lived_token(self) -> tuple[str, int]: ...


# --------------------------------------------------------------------------- helpers


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _require_id(value: object, name: str) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty id")
    return value.strip()


def _split_url(value: object) -> SplitResult | None:
    """Parse a URL without ever raising.

    ``urlsplit`` raises ``ValueError`` on some malformed inputs (``https://[oops`` → "Invalid IPv6
    URL"). Media URLs come from the webhook payload, so a bad one must become a typed error, not an
    unexpected ``ValueError`` in a Celery task.
    """
    if not isinstance(value, str):
        return None
    try:
        return urlsplit(value.strip())
    except ValueError:
        return None


def _path_of(url: str) -> str:
    """URL path only — query strings (tokens, CDN signatures) are never logged."""
    parts = _split_url(url)
    return parts.path if parts is not None else ""


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


# --------------------------------------------------------------------------- client


class InstagramClient:
    """Synchronous Graph API client. Pass ``http`` to share a pooled ``httpx.Client`` (not closed here)."""

    def __init__(
        self,
        settings: Settings,
        http: httpx.Client | None = None,
        *,
        access_token: str | None = None,
    ) -> None:
        self._settings = settings
        self._access_token = (access_token or settings.INSTAGRAM_ACCESS_TOKEN or "").strip()
        self._account_id = (settings.INSTAGRAM_ACCOUNT_ID or "").strip()
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client(timeout=API_TIMEOUT, headers={"User-Agent": USER_AGENT})

    # ------------------------------------------------------------------ lifecycle

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------ configuration

    @property
    def is_configured(self) -> bool:
        return bool(self._access_token and self._account_id)

    def _require_configured(self, *, account: bool) -> None:
        missing = []
        if not self._access_token:
            missing.append("INSTAGRAM_ACCESS_TOKEN")
        if account and not self._account_id:
            missing.append("INSTAGRAM_ACCOUNT_ID")
        if missing:
            log_event(logger, "instagram.not_configured", level=logging.WARNING, missing=missing)
            raise InstagramNotConfiguredError(missing)

    def _graph_base(self) -> str:
        return self._settings.INSTAGRAM_GRAPH_URL.strip().rstrip("/")

    def _api_base(self) -> str:
        version = self._settings.INSTAGRAM_API_VERSION.strip().strip("/")
        return f"{self._graph_base()}/{version}" if version else self._graph_base()

    def _messages_url(self) -> str:
        return f"{self._api_base()}/{quote(self._account_id, safe='')}/messages"

    # ------------------------------------------------------------------ transport

    def _request(
        self,
        operation: str,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        bearer: bool = True,
    ) -> httpx.Response:
        headers = {"Accept": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {self._access_token}"
        log_event(
            logger,
            "instagram.request",
            level=logging.DEBUG,
            operation=operation,
            method=method,
            path=_path_of(url),
        )
        started = time.monotonic()
        try:
            response = self._http.request(method, url, json=json, params=params, headers=headers, timeout=API_TIMEOUT)
        except httpx.InvalidURL as exc:
            # A misconfigured INSTAGRAM_GRAPH_URL / account id — retrying cannot help.
            error = InstagramAPIError(message="invalid Graph API URL", retryable=False)
            self._log_error(operation, error, started)
            raise error from exc
        except httpx.HTTPError as exc:
            error = InstagramAPIError(message=f"network error: {type(exc).__name__}", retryable=True)
            self._log_error(operation, error, started)
            raise error from exc
        log_event(
            logger,
            "instagram.response",
            level=logging.DEBUG,
            operation=operation,
            status=response.status_code,
            duration_ms=_elapsed_ms(started),
        )
        return response

    def _error_from_response(self, response: httpx.Response) -> InstagramAPIError:
        status = response.status_code
        error: object = None
        try:
            body = response.json()
        except ValueError:  # not JSON (incl. undecodable bytes)
            body = None
        if isinstance(body, dict):
            error = body.get("error")
        code = subcode = None
        message = fbtrace_id = error_type = None
        is_transient = False
        if isinstance(error, dict):
            code = _as_int(error.get("code"))
            subcode = _as_int(error.get("error_subcode"))
            message = error.get("message") if isinstance(error.get("message"), str) else None
            fbtrace_id = error.get("fbtrace_id") if isinstance(error.get("fbtrace_id"), str) else None
            error_type = error.get("type") if isinstance(error.get("type"), str) else None
            is_transient = error.get("is_transient") is True
        message = self._sanitize(message or f"HTTP {status}")
        retryable = status >= 500 or status == 429 or is_transient or code in RETRYABLE_ERROR_CODES
        return InstagramAPIError(status, code, subcode, message, fbtrace_id, retryable, error_type=error_type)

    def _sanitize(self, message: str) -> str:
        if self._access_token:
            message = message.replace(self._access_token, MASK)
        return message[:_MAX_ERROR_MESSAGE_LENGTH]

    def _log_error(self, operation: str, error: InstagramAPIError, started: float) -> None:
        log_event(
            logger,
            "instagram.error",
            level=logging.WARNING,
            operation=operation,
            status=error.status,
            error_code=error.error_code,
            error_subcode=error.error_subcode,
            error_type=error.error_type,
            fbtrace_id=error.fbtrace_id,
            retryable=error.retryable,
            error_message=error.message,
            duration_ms=_elapsed_ms(started),
        )

    def _json_or_raise(self, operation: str, response: httpx.Response, started: float) -> dict[str, Any]:
        if not response.is_success:
            error = self._error_from_response(response)
            self._log_error(operation, error, started)
            raise error
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            error = InstagramAPIError(status=response.status_code, message="unexpected non-JSON response")
            self._log_error(operation, error, started)
            raise error
        return data

    # ------------------------------------------------------------------ Send API

    def _send_message(self, operation: str, recipient_id: str, message: dict[str, Any]) -> str:
        started = time.monotonic()
        body = {"recipient": {"id": recipient_id}, "message": message}
        response = self._request(operation, "POST", self._messages_url(), json=body)
        data = self._json_or_raise(operation, response, started)
        message_id = data.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            # The message was probably delivered: do not retry.
            error = InstagramAPIError(status=response.status_code, message="response has no message_id")
            self._log_error(operation, error, started)
            raise error
        return message_id

    def send_text(self, recipient_id: str, text: str) -> list[str]:
        """Send text, split into parts of ≤ 1000 UTF-8 bytes; returns message ids in order."""
        self._require_configured(account=True)
        recipient = _require_id(recipient_id, "recipient_id")
        chunks = split_text(text or "", MAX_TEXT_BYTES)
        if not chunks:
            raise ValueError("text must not be empty")
        message_ids: list[str] = []
        for chunk in chunks:
            try:
                message_ids.append(self._send_message("send_text", recipient, {"text": chunk}))
            except InstagramAPIError as exc:
                exc.partial_message_ids = list(message_ids)
                raise
        log_event(
            logger,
            "instagram.message_sent",
            kind="text",
            recipient_id=recipient,
            parts=len(message_ids),
            text_bytes=utf8_len(text),
        )
        return message_ids

    def send_audio(self, recipient_id: str, url: str) -> str:
        """Send an audio attachment by public URL (aac/m4a/wav/mp4, ≤ 25MB); returns the message id."""
        self._require_configured(account=True)
        recipient = _require_id(recipient_id, "recipient_id")
        parts = _split_url(url)
        if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("url must be a public http(s) URL")
        message = {"attachment": {"type": "audio", "payload": {"url": url.strip()}}}
        message_id = self._send_message("send_audio", recipient, message)
        log_event(logger, "instagram.message_sent", kind="audio", recipient_id=recipient, parts=1)
        return message_id

    # ------------------------------------------------------------------ User Profile API

    def get_user_profile(self, igsid: str) -> dict[str, str | None] | None:
        """``GET /{igsid}?fields=name,username`` → ``{"name", "username"}``; 4xx (no consent, blocked...) → None."""
        self._require_configured(account=False)
        user_id = _require_id(igsid, "igsid")
        started = time.monotonic()
        url = f"{self._api_base()}/{quote(user_id, safe='')}"
        response = self._request("get_user_profile", "GET", url, params={"fields": "name,username"})
        if 400 <= response.status_code < 500:
            error = self._error_from_response(response)
            log_event(
                logger,
                "instagram.profile_unavailable",
                level=logging.WARNING,
                igsid=user_id,
                status=error.status,
                error_code=error.error_code,
                error_subcode=error.error_subcode,
                fbtrace_id=error.fbtrace_id,
            )
            return None
        data = self._json_or_raise("get_user_profile", response, started)
        name = data.get("name")
        username = data.get("username")
        return {
            "name": name.strip() if isinstance(name, str) and name.strip() else None,
            "username": username.strip() if isinstance(username, str) and username.strip() else None,
        }

    # ------------------------------------------------------------------ media

    def download_attachment(self, url: str, max_bytes: int = MAX_MEDIA_BYTES) -> tuple[bytes, str]:
        """Download attachment media right away (CDN URLs expire); streamed with a size guard.

        Returns ``(content, content_type)``; the content type has parameters stripped and defaults to
        ``application/octet-stream``. No credentials are sent to the CDN.
        """
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        operation = "download_attachment"
        started = time.monotonic()
        parts = _split_url(url)
        if parts is None or parts.scheme != "https" or not parts.hostname:
            error = InstagramAPIError(message="media URL must be an https URL")
            self._log_error(operation, error, started)
            raise error
        host = parts.hostname
        try:
            with self._http.stream(
                "GET",
                url.strip(),
                headers={"Accept": "*/*"},
                timeout=DOWNLOAD_TIMEOUT,
                follow_redirects=True,
            ) as response:
                status = response.status_code
                if not response.is_success:
                    error = InstagramAPIError(
                        status=status,
                        message=f"media download failed: HTTP {status}",
                        retryable=status >= 500 or status == 429,
                    )
                    self._log_error(operation, error, started)
                    raise error
                declared = _as_int(response.headers.get("Content-Length"))
                if declared is not None and declared > max_bytes:
                    error = InstagramMediaTooLargeError(declared, max_bytes, status=status)
                    self._log_error(operation, error, started)
                    raise error
                buffer = bytearray()
                for chunk in response.iter_bytes(_DOWNLOAD_CHUNK_SIZE):
                    buffer.extend(chunk)
                    if len(buffer) > max_bytes:
                        error = InstagramMediaTooLargeError(None, max_bytes, status=status)
                        self._log_error(operation, error, started)
                        raise error
                raw_type = response.headers.get("Content-Type") or ""
        except httpx.InvalidURL as exc:
            # httpx rejects URLs that urlsplit accepts (control characters, bad escapes).
            error = InstagramAPIError(message="media URL is not a valid URL", retryable=False)
            self._log_error(operation, error, started)
            raise error from exc
        except httpx.HTTPError as exc:
            error = InstagramAPIError(message=f"network error: {type(exc).__name__}", retryable=True)
            self._log_error(operation, error, started)
            raise error from exc

        if not buffer:
            error = InstagramAPIError(status=status, message="media download returned an empty body")
            self._log_error(operation, error, started)
            raise error
        content_type = raw_type.split(";", 1)[0].strip().lower() or "application/octet-stream"
        log_event(
            logger,
            "instagram.media_downloaded",
            host=host,
            size_bytes=len(buffer),
            content_type=content_type,
            duration_ms=_elapsed_ms(started),
        )
        return bytes(buffer), content_type

    # ------------------------------------------------------------------ tokens

    def refresh_long_lived_token(self) -> tuple[str, int]:
        """``GET {graph}/refresh_access_token?grant_type=ig_refresh_token&access_token=...`` → ``(token, expires_in)``.

        The token must be long-lived, at least 24 hours old and not expired. The new token is returned,
        never logged.
        """
        self._require_configured(account=False)
        operation = "refresh_access_token"
        started = time.monotonic()
        response = self._request(
            operation,
            "GET",
            f"{self._graph_base()}/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": self._access_token},
            bearer=False,
        )
        data = self._json_or_raise(operation, response, started)
        token = data.get("access_token")
        expires_in = _as_int(data.get("expires_in"))
        if not isinstance(token, str) or not token.strip() or expires_in is None or expires_in <= 0:
            error = InstagramAPIError(status=response.status_code, message="unexpected refresh_access_token response")
            self._log_error(operation, error, started)
            raise error
        log_event(logger, "instagram.token_refreshed", expires_in=expires_in, duration_ms=_elapsed_ms(started))
        return token.strip(), expires_in


def get_instagram_client(
    settings: Settings | None = None,
    *,
    http: httpx.Client | None = None,
    access_token: str | None = None,
) -> InstagramClient:
    """Factory; ``access_token`` overrides the env token (e.g. a fresher one from ``app_settings``)."""
    return InstagramClient(settings or get_settings(), http, access_token=access_token)
