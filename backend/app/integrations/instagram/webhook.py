"""Instagram webhooks: subscription verification, signature check, payload parsing (06-integrations.md §1).

See docs/research/instagram.md §4. The HTTP route stays thin: ``verify_signature`` over the raw
request bytes, ``parse_webhook`` on the decoded JSON, one Celery task per returned event, 200.
"""

import hashlib
import hmac
import logging
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from app.core.config import Settings
from app.core.logging import get_logger, log_event
from app.integrations.instagram.attachments import classify_attachment
from app.integrations.instagram.schemas import InstagramAttachment, InstagramEvent

__all__ = [
    "SIGNATURE_HEADER",
    "classify_attachment",
    "parse_webhook",
    "signature_secrets",
    "verify_signature",
    "verify_subscription",
]

logger = get_logger(__name__)

SIGNATURE_HEADER = "X-Hub-Signature-256"
WEBHOOK_OBJECT = "instagram"
MAX_CHALLENGE_LENGTH = 256
# Instagram ids are ~17 digits. The cap keeps a parsed event storable: ``customers.instagram_user_id``
# is String(64) and ``conversations.instagram_conversation_id`` ("{account_id}:{igsid}") String(128),
# so an implausibly long id must be rejected here instead of failing an INSERT inside a Celery task.
MAX_ID_LENGTH = 63
# ``messages.instagram_message_id`` (the idempotency key) is String(255).
MAX_MID_LENGTH = 255

_SIGNATURE_HEX_RE = re.compile(r"[0-9a-f]{64}")
# Media is fetched over HTTPS from Meta's CDN; any other scheme is unusable and must not be stored
# or rendered as a link in the admin UI.
_ALLOWED_ATTACHMENT_URL_SCHEMES = frozenset({"http", "https"})
# Values below this are seconds rather than milliseconds (1e11 ms is 1973; 1e11 s is year 5138).
_MILLISECONDS_THRESHOLD = 100_000_000_000
# ``messaging[]`` items without ``message`` that are skipped on purpose (not malformed).
_NON_MESSAGE_EVENT_KEYS = (
    "read",
    "seen",
    "reaction",
    "postback",
    "referral",
    "message_edit",
    "optin",
    "delivery",
    "pass_thread_control",
    "take_thread_control",
    "request_thread_control",
)


# --------------------------------------------------------------------------- verification


def verify_subscription(mode: str | None, token: str | None, challenge: str | None, settings: Settings) -> str | None:
    """GET ``hub.mode=subscribe`` + ``hub.verify_token`` → the ``hub.challenge`` to echo, else ``None`` (403)."""
    expected = settings.INSTAGRAM_VERIFY_TOKEN
    if not expected:
        reason = "verify_token_not_configured"
    elif mode != "subscribe":
        reason = "invalid_mode"
    elif not isinstance(token, str) or not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
        reason = "token_mismatch"
    elif not isinstance(challenge, str) or not challenge or len(challenge) > MAX_CHALLENGE_LENGTH:
        reason = "invalid_challenge"
    else:
        log_event(logger, "instagram.webhook_verified")
        return challenge
    log_event(logger, "instagram.webhook_verification_failed", level=logging.WARNING, reason=reason)
    return None


def signature_secrets(settings: Settings) -> list[str]:
    """App secrets to try for ``X-Hub-Signature-256``: ``INSTAGRAM_APP_SECRET``, then ``META_APP_SECRET``.

    Which secret Meta signs with is not confirmed by the docs, so both are accepted. Blank values are
    dropped, duplicates removed, order kept.
    """
    secrets: list[str] = []
    for value in (settings.INSTAGRAM_APP_SECRET, settings.META_APP_SECRET):
        secret = (value or "").strip()
        if secret and secret not in secrets:
            secrets.append(secret)
    return secrets


def verify_signature(raw_body: bytes, header: str | None, secrets: Iterable[str]) -> bool:
    """``X-Hub-Signature-256: sha256=<hex>`` == HMAC-SHA256(secret, raw body) for any of ``secrets``.

    Compute over the raw request bytes (the payload contains escaped unicode; never re-serialize).
    A missing/garbled header, a header without the ``sha256=`` prefix, or no secrets → ``False``.

    ``secrets`` must be a collection (``signature_secrets(settings)``). Passing a single secret
    string would iterate its characters and silently reject every webhook, so that is a ``TypeError``.
    """
    if not isinstance(raw_body, (bytes, bytearray, memoryview)):
        raise TypeError("raw_body must be the raw request bytes")
    if isinstance(secrets, (str, bytes, bytearray)):
        raise TypeError("secrets must be a collection of secrets, not a single secret")
    if not isinstance(header, str):
        return False
    algorithm, separator, digest = header.strip().partition("=")
    if not separator or algorithm.strip().lower() != "sha256":
        return False
    digest = digest.strip().lower()
    if not _SIGNATURE_HEX_RE.fullmatch(digest):
        return False

    body = bytes(raw_body)
    matched = False
    for secret in secrets:
        if not secret:
            continue
        key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        expected = hmac.new(key, body, hashlib.sha256).hexdigest()
        # No short circuit: every secret is compared, in constant time, before returning.
        matched |= hmac.compare_digest(expected, digest)
    return matched


# --------------------------------------------------------------------------- parsing


def _as_id(value: object, limit: int = MAX_ID_LENGTH) -> str | None:
    """Identifier as a string; ``None`` when it is blank or implausibly long (see ``MAX_ID_LENGTH``)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        value = str(value)
    if isinstance(value, str) and value.strip():
        identifier = value.strip()
        return identifier if len(identifier) <= limit else None
    return None


def _nested_id(container: Mapping[str, Any], key: str) -> str | None:
    value = container.get(key)
    return _as_id(value.get("id")) if isinstance(value, Mapping) else None


def _attachment_url(payload: object) -> str | None:
    """``payload.url`` when it is an http(s) URL; missing, blank or foreign schemes → ``None``."""
    url = payload.get("url") if isinstance(payload, Mapping) else None
    if not isinstance(url, str) or not url.strip():
        return None
    url = url.strip()
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:  # e.g. "https://[oops"
        return None
    return url if scheme in _ALLOWED_ATTACHMENT_URL_SCHEMES else None


def _as_flag(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _to_datetime(value: object) -> datetime | None:
    """Unix time in milliseconds (seconds tolerated) → aware UTC datetime."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    seconds = value / 1000 if abs(value) >= _MILLISECONDS_THRESHOLD else value
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _log_malformed(reason: str, **fields: Any) -> None:
    log_event(logger, "instagram.webhook_malformed", level=logging.WARNING, reason=reason, **fields)


def _parse_attachments(raw: object, *, entry_index: int, item_index: int) -> list[InstagramAttachment]:
    if raw is None:
        return []
    # The docs show ``attachments`` both as an array and as a single object.
    items = [raw] if isinstance(raw, Mapping) else raw
    if not isinstance(items, list):
        _log_malformed("attachments_not_list", entry_index=entry_index, item_index=item_index)
        return []
    attachments: list[InstagramAttachment] = []
    for attachment_index, item in enumerate(items):
        if not isinstance(item, Mapping):
            _log_malformed(
                "attachment_not_object",
                entry_index=entry_index,
                item_index=item_index,
                attachment_index=attachment_index,
            )
            continue
        attachments.append(InstagramAttachment(type=item.get("type"), url=_attachment_url(item.get("payload"))))
    return attachments


def _raw_type(*, is_deleted: bool, text: str | None, attachments: list[InstagramAttachment], unsupported: bool) -> str:
    if is_deleted:
        return "deleted"
    if attachments:
        return attachments[0].type
    if text:
        return "text"
    return "unsupported" if unsupported else "empty"


def _parse_messaging_item(
    item: Mapping[str, Any],
    *,
    entry_id: str | None,
    entry_time: datetime | None,
    entry_index: int,
    item_index: int,
) -> InstagramEvent | None:
    message = item.get("message")
    if not isinstance(message, Mapping):
        location = {"entry_index": entry_index, "item_index": item_index}
        if message is not None:
            _log_malformed("message_not_object", **location)
        else:
            kind = next((key for key in _NON_MESSAGE_EVENT_KEYS if key in item), "unknown")
            log_event(logger, "instagram.webhook_event_skipped", level=logging.DEBUG, kind=kind, **location)
        return None

    sender_id = _nested_id(item, "sender")
    recipient_id = _nested_id(item, "recipient")
    mid = _as_id(message.get("mid"), MAX_MID_LENGTH)
    missing = [name for name, value in (("sender", sender_id), ("recipient", recipient_id), ("mid", mid)) if not value]
    if missing or sender_id is None or recipient_id is None or mid is None:
        _log_malformed("missing_fields", entry_index=entry_index, item_index=item_index, missing=missing)
        return None

    is_echo = _as_flag(message.get("is_echo"))
    account_id = entry_id
    if account_id is None:
        # entry.id is the business account; for incoming messages that is the recipient.
        account_id = sender_id if is_echo else recipient_id
        _log_malformed("entry_id_missing", entry_index=entry_index, item_index=item_index)

    timestamp = _to_datetime(item.get("timestamp")) or entry_time
    if timestamp is None:
        _log_malformed("timestamp_missing", entry_index=entry_index, item_index=item_index)
        timestamp = datetime.now(UTC)

    text = message.get("text")
    text = text if isinstance(text, str) and text else None
    attachments = _parse_attachments(message.get("attachments"), entry_index=entry_index, item_index=item_index)
    is_deleted = _as_flag(message.get("is_deleted"))
    is_unsupported = _as_flag(message.get("is_unsupported"))
    is_self = (
        _as_flag(message.get("is_self"))
        or sender_id == recipient_id
        or (sender_id == account_id and not is_echo)
    )
    reply_to = message.get("reply_to")
    reply_to_mid = _as_id(reply_to.get("mid"), MAX_MID_LENGTH) if isinstance(reply_to, Mapping) else None

    return InstagramEvent(
        account_id=account_id,
        sender_id=sender_id,
        recipient_id=recipient_id,
        timestamp=timestamp,
        mid=mid,
        text=text,
        attachments=attachments,
        is_echo=is_echo,
        is_deleted=is_deleted,
        is_self=is_self,
        is_unsupported=is_unsupported,
        reply_to_mid=reply_to_mid,
        raw_type=_raw_type(is_deleted=is_deleted, text=text, attachments=attachments, unsupported=is_unsupported),
    )


def parse_webhook(payload: object) -> list[InstagramEvent]:
    """Instagram webhook POST body → message events in delivery order. Never raises.

    Only ``object == "instagram"`` payloads are read; every ``entry[].messaging[]`` item is visited
    (batches hold up to 1000 updates). Items without ``message`` (read/seen, reaction, postback,
    referral, message_edit, ...) are skipped. Echo and self messages are returned with ``is_echo`` /
    ``is_self`` set so the caller decides. Malformed items are logged as
    ``instagram.webhook_malformed`` and skipped.
    """
    if not isinstance(payload, Mapping):
        _log_malformed("payload_not_object")
        return []
    if payload.get("object") != WEBHOOK_OBJECT:
        log_event(
            logger,
            "instagram.webhook_ignored",
            level=logging.WARNING,
            reason="unexpected_object",
            object=str(payload.get("object"))[:32],
        )
        return []
    entries = payload.get("entry")
    if not isinstance(entries, list):
        _log_malformed("entry_not_list")
        return []

    events: list[InstagramEvent] = []
    items_seen = 0
    for entry_index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            _log_malformed("entry_not_object", entry_index=entry_index)
            continue
        messaging = entry.get("messaging")
        if messaging is None:
            # e.g. ``changes`` (comments) or ``standby`` (handover) — not handled by the bot.
            log_event(logger, "instagram.webhook_entry_skipped", level=logging.DEBUG, entry_index=entry_index)
            continue
        if not isinstance(messaging, list):
            _log_malformed("messaging_not_list", entry_index=entry_index)
            continue
        entry_id = _as_id(entry.get("id"))
        entry_time = _to_datetime(entry.get("time"))
        for item_index, item in enumerate(messaging):
            items_seen += 1
            if not isinstance(item, Mapping):
                _log_malformed("messaging_item_not_object", entry_index=entry_index, item_index=item_index)
                continue
            try:
                event = _parse_messaging_item(
                    item, entry_id=entry_id, entry_time=entry_time, entry_index=entry_index, item_index=item_index
                )
            except Exception as exc:  # defensive: one bad item must not drop the whole batch
                _log_malformed(
                    "unexpected_error", entry_index=entry_index, item_index=item_index, error=type(exc).__name__
                )
                continue
            if event is not None:
                events.append(event)

    log_event(
        logger,
        "instagram.webhook_parsed",
        entries=len(entries),
        items=items_seen,
        events=len(events),
        echoes=sum(1 for event in events if event.is_echo),
    )
    return events
