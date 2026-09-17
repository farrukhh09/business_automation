"""Daily report tasks (docs/architecture/06-integrations.md §5).

| task | when |
|---|---|
| ``generate_daily_report(date_iso=None)`` | on request: (re)build the report for a date |
| ``maybe_generate_daily_report()`` | beat every 15 min: build today's report once the business
  clock passes ``BusinessSettings.daily_report_time`` |

``maybe_generate_daily_report`` is idempotent: it does nothing before the report time and nothing
when today's report was already generated **after** that time. A report stored earlier the same day
(e.g. opened in the admin panel at noon) is regenerated, so the evening report covers the whole day.

The tasks open their own session (``session_scope``); the ``run_*`` functions take a session so that
they can be called from tests and from other services.
"""

from datetime import date, datetime

from sqlalchemy.orm import Session

from app.core.database import session_scope
from app.core.logging import get_logger, log_event
from app.core.time import business_now, combine_business, ensure_utc
from app.repositories.reports import DailyReportRepository
from app.services.report_service import ReportService
from app.services.settings_service import SettingsService
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)

SKIP_BEFORE_REPORT_TIME = "before_report_time"
SKIP_ALREADY_GENERATED = "already_generated"


def run_generate_daily_report(db: Session, report_date: date | None = None) -> date:
    """Build (or rebuild) and store the report for ``report_date``; returns the stored date."""
    service = ReportService(db)
    day = report_date if report_date is not None else service.default_report_date()
    report = service.regenerate(day)
    log_event(logger, "task.daily_report_generated", report_date=report.report_date.isoformat())
    return report.report_date


def run_maybe_generate_daily_report(db: Session, now: datetime | None = None) -> date | None:
    """06 §5: generate today's report when it is due. Returns the date, or ``None`` when skipped."""
    moment = now or business_now()
    today = moment.date()
    report_time = SettingsService(db).get().daily_report_time
    if moment.time() < report_time:
        log_event(logger, "task.daily_report_skipped", report_date=today.isoformat(), reason=SKIP_BEFORE_REPORT_TIME)
        return None

    due_at = ensure_utc(combine_business(today, report_time))
    existing = DailyReportRepository(db).get_by_date(today)
    if existing is not None and ensure_utc(existing.generated_at) >= due_at:
        log_event(logger, "task.daily_report_skipped", report_date=today.isoformat(), reason=SKIP_ALREADY_GENERATED)
        return None
    return run_generate_daily_report(db, today)


@celery_app.task(name="app.tasks.reports.generate_daily_report")
def generate_daily_report(date_iso: str | None = None) -> str:
    """``date_iso`` is ``"YYYY-MM-DD"``; without it the report date follows the 04 §8 default rule."""
    report_date = date.fromisoformat(date_iso) if date_iso else None
    with session_scope() as db:
        return run_generate_daily_report(db, report_date).isoformat()


@celery_app.task(name="app.tasks.reports.maybe_generate_daily_report")
def maybe_generate_daily_report() -> str | None:
    with session_scope() as db:
        generated = run_maybe_generate_daily_report(db)
        return generated.isoformat() if generated is not None else None
