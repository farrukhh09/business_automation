"""Statistics routes — docs/architecture/04-api.md §7 "Statistics".

STAFF:
- ``GET /api/statistics``             ``period``, ``date_from``, ``date_to``, ``date_basis`` → ``StatisticsOut``
- ``GET /api/statistics/dashboard``   → ``DashboardOut``
- ``GET /api/statistics/timeseries``  ``date_from``, ``date_to``, ``date_basis`` → ``[{date, revenue, orders_count, paid_amount}]``
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession, StaffUser
from app.schemas.statistics import (
    DEFAULT_DATE_BASIS,
    DEFAULT_PERIOD,
    DashboardOut,
    DateBasis,
    PeriodName,
    StatisticsOut,
    TimeseriesPoint,
)
from app.services.statistics_service import StatisticsService

router = APIRouter(prefix="/statistics", tags=["statistics"])

PeriodQuery = Annotated[PeriodName, Query(description="Период: today/yesterday/week/month/custom")]
DateBasisQuery = Annotated[DateBasis, Query(description="База даты: delivery (дата выдачи) или created (подтверждение)")]


@router.get("", response_model=StatisticsOut, summary="Статистика за период")
def get_statistics(
    db: DbSession,
    _user: StaffUser,
    period: PeriodQuery = DEFAULT_PERIOD,
    date_from: Annotated[date | None, Query(description="Начало периода (для custom)")] = None,
    date_to: Annotated[date | None, Query(description="Конец периода (для custom)")] = None,
    date_basis: DateBasisQuery = DEFAULT_DATE_BASIS,
) -> StatisticsOut:
    return StatisticsService(db).statistics(period, date_basis, date_from=date_from, date_to=date_to)


@router.get("/dashboard", response_model=DashboardOut, summary="Сводка за сегодня")
def get_dashboard(db: DbSession, _user: StaffUser) -> DashboardOut:
    return StatisticsService(db).dashboard()


@router.get("/timeseries", response_model=list[TimeseriesPoint], summary="Выручка по дням")
def get_timeseries(
    db: DbSession,
    _user: StaffUser,
    date_from: Annotated[date | None, Query(description="Начало диапазона; по умолчанию 30 дней назад")] = None,
    date_to: Annotated[date | None, Query(description="Конец диапазона; по умолчанию сегодня")] = None,
    date_basis: DateBasisQuery = DEFAULT_DATE_BASIS,
) -> list[TimeseriesPoint]:
    return StatisticsService(db).timeseries(date_from, date_to, date_basis)
