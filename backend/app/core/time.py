"""Time helpers (docs/architecture/01-overview.md §4).

- All ``DateTime(timezone=True)`` values are UTC (``now_utc()``).
- Business dates/times ("today", delivery date/time, statistics periods, daily report) use
  ``BUSINESS_TIMEZONE`` (default ``Asia/Dushanbe``).

Call these through the module (``from app.core import time as app_time``) or import the functions;
tests may monkeypatch ``app.core.time.now_utc`` — all other helpers derive from it.
"""

from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from functools import lru_cache
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def now_utc() -> datetime:
    """Current time, timezone-aware UTC."""
    return datetime.now(UTC)


@lru_cache(maxsize=8)
def _zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def business_tz() -> ZoneInfo:
    return _zone(get_settings().BUSINESS_TIMEZONE)


# 01-overview.md §2 ("BUSINESS_TZ helpers"): the business timezone resolved from settings at import.
# Prefer business_tz() in code that must follow settings replaced at runtime (tests).
BUSINESS_TZ: ZoneInfo = business_tz()


def ensure_utc(value: datetime) -> datetime:
    """Naive datetimes are treated as UTC (SQLite returns naive values)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_business(value: datetime) -> datetime:
    """Convert a UTC (or naive-UTC) datetime to the business timezone."""
    return ensure_utc(value).astimezone(business_tz())


def business_now() -> datetime:
    return now_utc().astimezone(business_tz())


def business_today() -> date:
    return business_now().date()


def business_date_of(value: datetime) -> date:
    """Business calendar date of a UTC instant."""
    return to_business(value).date()


def combine_business(day: date, at: dt_time) -> datetime:
    """Aware datetime for a business-local date and time."""
    return datetime.combine(day, at.replace(tzinfo=None), tzinfo=business_tz())


def business_day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """Half-open UTC interval ``[start, end)`` covering the business-local calendar day."""
    tz = business_tz()
    start = datetime.combine(day, dt_time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), dt_time.min, tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)
