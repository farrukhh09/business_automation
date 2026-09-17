"""Token rotation, the per-conversation lock and the Celery task wrappers (06-integrations.md §1, §5)."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

import pytest
import redis
from celery.exceptions import Retry
from sqlalchemy.orm import Session

from app.core import locks
from app.core.config import Settings
from app.core.exceptions import IntegrationError
from app.core.locks import ConversationBusyError, conversation_lock
from app.core.time import now_utc
from app.repositories.app_settings import AppSettingRepository
from app.services.instagram_token_service import INSTAGRAM_TOKEN_KEY, InstagramTokenService
from app.services.messaging_service import RetryableDeliveryError
from app.services.task_queue import CeleryTaskQueue
from app.tasks import instagram as instagram_tasks
from app.tasks import speech as speech_tasks
from tests.bot_fakes import FakeMessenger


def _settings(**values: Any) -> Settings:
    return Settings(_env_file=None, APP_ENV="development", **values)


# --------------------------------------------------------------------------- Instagram token


def test_refreshed_token_replaces_the_env_token_until_the_env_changes(db: Session) -> None:
    service = InstagramTokenService(db, _settings(INSTAGRAM_ACCESS_TOKEN="env-token-1"))
    assert service.active_token() == "env-token-1"

    assert service.refresh(FakeMessenger()) is True
    assert service.active_token() == "refreshed-token"
    stored = AppSettingRepository(db).get_value(INSTAGRAM_TOKEN_KEY)
    assert "env-token-1" not in str(stored)  # only a fingerprint of the env token is kept

    rotated_env = InstagramTokenService(db, _settings(INSTAGRAM_ACCESS_TOKEN="env-token-2"))
    assert rotated_env.active_token() == "env-token-2"


def test_expired_stored_token_is_not_used(db: Session) -> None:
    service = InstagramTokenService(db, _settings(INSTAGRAM_ACCESS_TOKEN="env-token"))
    service.refresh(FakeMessenger())
    stored = AppSettingRepository(db).get_value(INSTAGRAM_TOKEN_KEY)
    stored["expires_at"] = (now_utc() - timedelta(minutes=1)).isoformat()
    AppSettingRepository(db).set_value(INSTAGRAM_TOKEN_KEY, stored)
    db.commit()

    assert service.active_token() == "env-token"


def test_refresh_without_token_or_on_api_error(db: Session) -> None:
    assert InstagramTokenService(db, _settings()).refresh(FakeMessenger()) is False

    class Failing(FakeMessenger):
        def refresh_long_lived_token(self) -> tuple[str, int]:
            raise IntegrationError("expired")

    service = InstagramTokenService(db, _settings(INSTAGRAM_ACCESS_TOKEN="env-token"))
    assert service.refresh(Failing()) is False
    assert AppSettingRepository(db).get_value(INSTAGRAM_TOKEN_KEY) is None


def test_messenger_uses_the_active_token(db: Session) -> None:
    service = InstagramTokenService(db, _settings(INSTAGRAM_ACCESS_TOKEN="env-token", INSTAGRAM_ACCOUNT_ID="1"))
    service.refresh(FakeMessenger())
    client = service.messenger()
    try:
        assert client._access_token == "refreshed-token"
    finally:
        client.close()


# --------------------------------------------------------------------------- conversation lock


class FakeLock:
    def __init__(self, acquired: bool | Exception) -> None:
        self.acquired = acquired
        self.released = False

    def acquire(self) -> bool:
        if isinstance(self.acquired, Exception):
            raise self.acquired
        return self.acquired

    def release(self) -> None:
        self.released = True


class FakeRedis:
    def __init__(self, lock: FakeLock) -> None:
        self._lock = lock
        self.closed = False
        self.names: list[str] = []

    def lock(self, name: str, timeout: int, blocking_timeout: int) -> FakeLock:
        self.names.append(name)
        return self._lock

    def close(self) -> None:
        self.closed = True


def _with_redis(monkeypatch: pytest.MonkeyPatch, lock: FakeLock) -> FakeRedis:
    client = FakeRedis(lock)
    monkeypatch.setattr(locks.redis.Redis, "from_url", staticmethod(lambda url: client))
    return client


def test_lock_is_a_no_op_without_redis_or_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _with_redis(monkeypatch, FakeLock(True))
    with conversation_lock("k", _settings(REDIS_URL="")):
        pass
    with conversation_lock("k", Settings(_env_file=None, APP_ENV="test", REDIS_URL="redis://x")):
        pass
    assert client.names == []


def test_lock_is_held_and_released(monkeypatch: pytest.MonkeyPatch) -> None:
    lock = FakeLock(True)
    client = _with_redis(monkeypatch, lock)
    with conversation_lock("acc:igsid", _settings(REDIS_URL="redis://redis:6379/0")):
        assert not lock.released
    assert lock.released and client.closed
    assert client.names == ["bakery:conversation-lock:acc:igsid"]


def test_busy_conversation_raises_for_a_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_redis(monkeypatch, FakeLock(False))
    with pytest.raises(ConversationBusyError), conversation_lock("k", _settings(REDIS_URL="redis://redis")):
        pytest.fail("the body must not run")


def test_redis_outage_processes_without_the_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_redis(monkeypatch, FakeLock(redis.ConnectionError("down")))
    ran = []
    with conversation_lock("k", _settings(REDIS_URL="redis://redis")):
        ran.append(True)
    assert ran == [True]


# --------------------------------------------------------------------------- task wrappers


@contextmanager
def _no_session() -> Iterator[None]:
    yield None


def test_send_task_retries_temporary_failures_and_gives_up_on_the_last_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[bool] = []

    def fake_run(db: Any, message_id: int, *, final_attempt: bool = True, **deps: Any) -> None:
        attempts.append(final_attempt)
        if not final_attempt:
            raise RetryableDeliveryError()

    monkeypatch.setattr(instagram_tasks, "run_send_instagram_message", fake_run)
    monkeypatch.setattr(instagram_tasks, "session_scope", _no_session)
    task = instagram_tasks.send_instagram_message

    with pytest.raises(Retry):
        task.apply(args=[7], retries=0).get()
    task.apply(args=[7], retries=instagram_tasks.SEND_MAX_RETRIES).get()  # the last attempt marks FAILED itself

    assert attempts == [False, True]


def test_process_task_retries_a_busy_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    @contextmanager
    def busy_once(key: str, settings: Any = None) -> Iterator[None]:
        calls.append(key)
        if len(calls) == 1:
            raise ConversationBusyError(key)
        yield

    monkeypatch.setattr(instagram_tasks, "conversation_lock", busy_once)
    monkeypatch.setattr(instagram_tasks, "session_scope", _no_session)
    monkeypatch.setattr(
        instagram_tasks,
        "run_process_instagram_event",
        lambda db, event: instagram_tasks.InboundResult("processed", 1, [2]),
    )
    event = {
        "account_id": "acc",
        "sender_id": "igsid",
        "recipient_id": "acc",
        "timestamp": now_utc().isoformat(),
        "mid": "mid.1",
        "text": "Привет",
    }
    task = instagram_tasks.process_instagram_event

    with pytest.raises(Retry):
        task.apply(args=[event]).get()
    result = task.apply(args=[event], retries=1).get()

    assert calls == ["acc:igsid", "acc:igsid"]
    assert result == {"status": "processed", "message_id": 1, "replies": [2]}


def test_transcription_task_skips_unknown_messages_and_retries_provider_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.integrations.speech.base import SpeechProviderError

    monkeypatch.setattr(speech_tasks, "_conversation_key", lambda message_id: None)
    assert speech_tasks.transcribe_voice_message.apply(args=[1]).get() is None

    attempts: list[bool] = []

    def fake_run(db: Any, message_id: int, *, final_attempt: bool = True, **deps: Any) -> Any:
        attempts.append(final_attempt)
        if not final_attempt:
            raise SpeechProviderError(provider="fake", status=503)
        return instagram_tasks.InboundResult("processed", message_id, [])

    monkeypatch.setattr(speech_tasks, "_conversation_key", lambda message_id: "acc:igsid")
    monkeypatch.setattr(speech_tasks, "run_transcribe_voice_message", fake_run)
    monkeypatch.setattr(speech_tasks, "session_scope", _no_session)
    task = speech_tasks.transcribe_voice_message

    with pytest.raises(Retry):
        task.apply(args=[5]).get()
    result = task.apply(args=[5], retries=speech_tasks.TRANSCRIBE_MAX_RETRIES).get()

    assert attempts == [False, True]
    assert result == {"status": "processed", "message_id": 5, "replies": []}


def test_celery_queue_reports_a_broker_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from kombu.exceptions import OperationalError

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise OperationalError("broker down")

    monkeypatch.setattr(instagram_tasks.send_instagram_message, "delay", refuse)
    sent: list[int] = []
    monkeypatch.setattr(instagram_tasks.continue_dialog_after_location, "delay", lambda order_id: sent.append(order_id))

    queue = CeleryTaskQueue()
    assert queue.send_message(1) is False
    assert queue.continue_after_location(9) is True and sent == [9]
