"""Instagram 24-hour messaging window (06-integrations.md §1, docs/research/instagram.md §6).

"Your app has 24 hours to respond to any message sent from an Instagram user to your app user."
Outgoing bot and operator messages are allowed while ``now - last_customer_message_at < 24h``.
The HUMAN_AGENT tag (7 days) is not used in the MVP: it requires App Review.
"""

from datetime import datetime, timedelta

from app.core import time as app_time

MESSAGING_WINDOW = timedelta(hours=24)


def messaging_window_expires_at(last_customer_message_at: datetime | None) -> datetime | None:
    """UTC instant when the window closes, or ``None`` if the customer never wrote."""
    if last_customer_message_at is None:
        return None
    return app_time.ensure_utc(last_customer_message_at) + MESSAGING_WINDOW


def messaging_window_open(last_customer_message_at: datetime | None, now: datetime | None = None) -> bool:
    """True while less than 24 hours have passed since the customer's last message.

    Naive datetimes are treated as UTC (SQLite). A customer timestamp in the future (clock skew)
    counts as an open window.
    """
    expires_at = messaging_window_expires_at(last_customer_message_at)
    if expires_at is None:
        return False
    current = app_time.ensure_utc(now) if now is not None else app_time.now_utc()
    return current < expires_at
