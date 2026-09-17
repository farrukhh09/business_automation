"""Instagram API with Instagram Login: webhook parsing/signature, Graph API client (06 §1).

- ``webhook``: ``verify_subscription``, ``verify_signature``, ``signature_secrets``, ``parse_webhook``,
  ``classify_attachment``;
- ``schemas``: ``InstagramEvent``, ``InstagramAttachment``;
- ``client``: ``InstagramClient``, ``InstagramAPIError``, ``InstagramNotConfiguredError``;
- ``window``: ``messaging_window_open`` (24-hour rule);
- ``text``: ``split_text`` (1000-byte Send API limit).
"""

from app.integrations.instagram.attachments import attachment_label, classify_attachment, should_store_media
from app.integrations.instagram.client import (
    InstagramAPIError,
    InstagramClient,
    InstagramMediaTooLargeError,
    InstagramMessenger,
    InstagramNotConfiguredError,
    get_instagram_client,
)
from app.integrations.instagram.schemas import InstagramAttachment, InstagramEvent
from app.integrations.instagram.text import MAX_TEXT_BYTES, split_text
from app.integrations.instagram.webhook import parse_webhook, signature_secrets, verify_signature, verify_subscription
from app.integrations.instagram.window import MESSAGING_WINDOW, messaging_window_expires_at, messaging_window_open

__all__ = [
    "MAX_TEXT_BYTES",
    "MESSAGING_WINDOW",
    "InstagramAPIError",
    "InstagramAttachment",
    "InstagramClient",
    "InstagramEvent",
    "InstagramMediaTooLargeError",
    "InstagramMessenger",
    "InstagramNotConfiguredError",
    "attachment_label",
    "classify_attachment",
    "get_instagram_client",
    "messaging_window_expires_at",
    "messaging_window_open",
    "parse_webhook",
    "should_store_media",
    "signature_secrets",
    "split_text",
    "verify_signature",
    "verify_subscription",
]
