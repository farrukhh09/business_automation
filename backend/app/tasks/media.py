"""Media housekeeping (docs/architecture/06-integrations.md §3, §5).

``cleanup_media()`` — beat, once a day: synthesized voice replies older than 7 days are deleted
(Instagram has fetched them long before). Media of incoming messages is kept for the conversation
history.
"""

from app.core.config import get_settings
from app.services.media_storage import TTS_PREFIX, TTS_RETENTION, MediaStorage
from app.tasks.celery_app import celery_app


def run_cleanup_media(storage: MediaStorage | None = None) -> int:
    return (storage or MediaStorage(get_settings().media_root)).cleanup(prefix=TTS_PREFIX, older_than=TTS_RETENTION)


@celery_app.task(name="app.tasks.media.cleanup_media")
def cleanup_media() -> int:
    return run_cleanup_media()
