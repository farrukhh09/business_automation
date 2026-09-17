"""Background work requested by services and routes (docs/architecture/06-integrations.md §5).

Services never import Celery tasks directly: they receive a ``TaskQueue``. Production uses
``CeleryTaskQueue`` (``.delay`` on the task, imported lazily so ``app.services`` does not import the
task modules at import time); tests inject a queue that records or runs the work inline.

Enqueueing happens **after** the commit that produced the row the task reads.
"""

import logging
from importlib import import_module
from typing import Any, Protocol, runtime_checkable

from kombu.exceptions import OperationalError as BrokerOperationalError

from app.core.logging import get_logger, log_event

logger = get_logger(__name__)

__all__ = ["CeleryTaskQueue", "TaskQueue", "get_task_queue"]


@runtime_checkable
class TaskQueue(Protocol):
    def process_instagram_event(self, event: dict[str, Any]) -> bool: ...

    def send_message(self, message_id: int) -> bool: ...

    def transcribe_voice(self, message_id: int) -> bool: ...

    def synthesize_voice_reply(self, message_id: int) -> bool: ...

    def continue_after_location(self, order_id: int) -> bool: ...


class CeleryTaskQueue:
    """``TaskQueue`` over Celery. ``False`` means the broker refused the task (logged)."""

    def process_instagram_event(self, event: dict[str, Any]) -> bool:
        return self._enqueue("app.tasks.instagram", "process_instagram_event", event, ref=event.get("mid"))

    def send_message(self, message_id: int) -> bool:
        return self._enqueue("app.tasks.instagram", "send_instagram_message", message_id, ref=message_id)

    def transcribe_voice(self, message_id: int) -> bool:
        return self._enqueue("app.tasks.speech", "transcribe_voice_message", message_id, ref=message_id)

    def synthesize_voice_reply(self, message_id: int) -> bool:
        return self._enqueue("app.tasks.speech", "synthesize_voice_reply", message_id, ref=message_id)

    def continue_after_location(self, order_id: int) -> bool:
        return self._enqueue("app.tasks.instagram", "continue_dialog_after_location", order_id, ref=order_id)

    @staticmethod
    def _enqueue(module: str, name: str, *args: Any, ref: Any = None) -> bool:
        task = getattr(import_module(module), name)
        try:
            task.delay(*args)
        except BrokerOperationalError as exc:
            log_event(
                logger,
                "task.enqueue_failed",
                level=logging.ERROR,
                task=f"{module}.{name}",
                ref=ref,
                error=type(exc).__name__,
            )
            return False
        return True


def get_task_queue() -> TaskQueue:
    """FastAPI dependency / default for services (tests override it)."""
    return CeleryTaskQueue()
