"""Celery application (docs/architecture/06-integrations.md §5).

Worker: ``celery -A app.tasks.celery_app:celery_app worker -l info``
Beat:   ``celery -A app.tasks.celery_app:celery_app beat -l info``

Task modules are included only if they exist, so this module imports cleanly before the task
modules are written. Planned modules are listed in ``TASK_MODULES``; any other module placed in
``app/tasks/`` is picked up automatically. Beat entries are installed only when their task
module exists (task names are Celery defaults: ``<module>.<function>``).
"""

import importlib.util
import pkgutil
from pathlib import Path
from typing import Any

from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging, worker_process_init

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging

TASKS_PACKAGE = "app.tasks"

TASK_MODULES: tuple[str, ...] = (
    "app.tasks.instagram",  # process_instagram_event, send_instagram_message, refresh_instagram_token
    "app.tasks.speech",  # transcribe_voice_message, synthesize_voice_reply
    "app.tasks.media",  # cleanup_media
    "app.tasks.delivery",  # geocode_delivery, optimize_routes, sync_delivery_statuses
    "app.tasks.reports",  # generate_daily_report, maybe_generate_daily_report
)

_NOT_TASK_MODULES = frozenset({"celery_app"})


def _module_exists(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def discover_task_modules() -> list[str]:
    modules = [name for name in TASK_MODULES if _module_exists(name)]
    package_dir = Path(__file__).resolve().parent
    for info in pkgutil.iter_modules([str(package_dir)]):
        name = f"{TASKS_PACKAGE}.{info.name}"
        if info.name.startswith("_") or info.name in _NOT_TASK_MODULES or name in modules:
            continue
        modules.append(name)
    return modules


def build_beat_schedule(settings: Settings, modules: list[str]) -> dict[str, dict[str, Any]]:
    # 06 §5: the report time is BusinessSettings.daily_report_time (editable in the admin panel;
    # env DAILY_REPORT_TIME is only its default), so beat polls every 15 minutes and the task
    # decides whether today's report is due — a crontab fixed at startup would ignore the setting.
    entries: dict[str, dict[str, Any]] = {
        "maybe-generate-daily-report": {
            "task": "app.tasks.reports.maybe_generate_daily_report",
            "schedule": crontab(minute="*/15"),
        },
        "sync-delivery-statuses": {
            "task": "app.tasks.delivery.sync_delivery_statuses",
            "schedule": crontab(minute="*/15"),
        },
        "refresh-instagram-token": {
            "task": "app.tasks.instagram.refresh_instagram_token",
            "schedule": crontab(hour=4, minute=0),
        },
        "cleanup-media": {
            "task": "app.tasks.media.cleanup_media",
            "schedule": crontab(hour=4, minute=30),
        },
    }
    available = set(modules)
    return {name: entry for name, entry in entries.items() if entry["task"].rsplit(".", 1)[0] in available}


def create_celery_app(settings: Settings | None = None) -> Celery:
    settings = settings or get_settings()
    modules = discover_task_modules()
    redis_url = settings.REDIS_URL.strip()
    eager = settings.celery_task_always_eager

    application = Celery("bakery", include=modules)
    application.conf.update(
        broker_url=redis_url or "memory://",
        result_backend=redis_url or "cache+memory://",
        timezone=settings.BUSINESS_TIMEZONE,
        enable_utc=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        result_expires=24 * 3600,
        task_track_started=True,
        task_time_limit=600,
        task_soft_time_limit=540,
        broker_connection_retry_on_startup=True,
        worker_hijack_root_logger=False,
        task_always_eager=eager,
        task_eager_propagates=eager,
        beat_schedule=build_beat_schedule(settings, modules),
    )
    return application


celery_app: Celery = create_celery_app()
celery = celery_app  # conventional name for `celery -A app.tasks.celery_app`


@setup_logging.connect
def _configure_worker_logging(**_: Any) -> None:
    configure_logging()


@worker_process_init.connect
def _reset_db_pool_after_fork(**_: Any) -> None:
    from app.core.database import engine

    engine.dispose(close=False)
