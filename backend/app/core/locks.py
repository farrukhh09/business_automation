"""Per-conversation lock for background processing (docs/architecture/06-integrations.md §5).

Customers often send several short messages in a row ("Здравствуйте" / "хочу торт" / "на завтра").
Two workers handling them at once would read the same dialog state and overwrite each other's draft,
so every task that runs the dialog of a conversation holds a Redis lock on its thread key.

Without Redis (``REDIS_URL`` empty, or ``APP_ENV=test`` where tasks run inline) the lock is a no-op.
A Redis outage degrades to processing without the lock (logged) rather than dropping messages; a
lock that cannot be acquired in time raises ``ConversationBusyError`` so the task is retried.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import redis

from app.core.config import Settings, get_settings
from app.core.logging import get_logger, log_event

logger = get_logger(__name__)

__all__ = ["LOCK_BLOCKING_SECONDS", "LOCK_TIMEOUT_SECONDS", "ConversationBusyError", "conversation_lock"]

#: Upper bound of one dialog turn (LLM understanding + reply + geocoding), after which the lock expires.
LOCK_TIMEOUT_SECONDS = 180
#: How long a task waits for the previous message of the same conversation.
LOCK_BLOCKING_SECONDS = 120
LOCK_PREFIX = "bakery:conversation-lock:"


class ConversationBusyError(RuntimeError):
    """Another task still processes this conversation; the Celery task should retry."""


def _acquire(key: str, settings: Settings) -> tuple[Any, Any] | None:
    redis_url = settings.REDIS_URL.strip()
    if settings.is_test or not redis_url:
        return None
    client = redis.Redis.from_url(redis_url)
    lock = client.lock(f"{LOCK_PREFIX}{key}", timeout=LOCK_TIMEOUT_SECONDS, blocking_timeout=LOCK_BLOCKING_SECONDS)
    try:
        acquired = lock.acquire()
    except redis.RedisError as exc:
        log_event(logger, "lock.unavailable", level=logging.WARNING, error=type(exc).__name__)
        client.close()
        return None
    if not acquired:
        client.close()
        raise ConversationBusyError(key)
    return client, lock


def _release(handle: tuple[Any, Any]) -> None:
    client, lock = handle
    try:
        lock.release()
    except redis.RedisError as exc:  # expired meanwhile or Redis is gone: nothing left to release
        log_event(logger, "lock.release_failed", level=logging.WARNING, error=type(exc).__name__)
    finally:
        client.close()


@contextmanager
def conversation_lock(key: str, settings: Settings | None = None) -> Iterator[None]:
    handle = _acquire(key, settings or get_settings())
    try:
        yield
    finally:
        if handle is not None:
            _release(handle)
