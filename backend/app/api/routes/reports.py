"""Daily report routes — docs/architecture/04-api.md §8 "Reports".

- ``GET  /api/reports/daily``           STAFF  ``date`` → ``DailyReportOut`` (built and stored if missing)
- ``POST /api/reports/daily/generate``  ADMIN  ``{date}`` → ``DailyReportOut`` (rebuild)
- ``GET  /api/reports/daily/history``   STAFF  ``limit`` (30) → ``DailyReportOut[]`` without ``data``
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, DbSession, StaffUser
from app.repositories.reports import DEFAULT_HISTORY_LIMIT
from app.schemas.report import (
    DailyReportGenerateRequest,
    DailyReportHistoryItem,
    DailyReportOut,
    build_daily_report_history_item,
    build_daily_report_out,
)
from app.services.report_service import MAX_HISTORY_LIMIT, ReportService

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/daily", response_model=DailyReportOut, summary="Ежедневный отчёт за дату")
def get_daily_report(
    db: DbSession,
    _user: StaffUser,
    report_date: Annotated[
        date | None,
        Query(alias="date", description="Дата отчёта; по умолчанию вчера до времени отчёта, иначе сегодня"),
    ] = None,
) -> DailyReportOut:
    return build_daily_report_out(ReportService(db).daily(report_date))


@router.post("/daily/generate", response_model=DailyReportOut, summary="Перестроить отчёт за дату")
def generate_daily_report(db: DbSession, _user: AdminUser, body: DailyReportGenerateRequest) -> DailyReportOut:
    return build_daily_report_out(ReportService(db).regenerate(body.date))


@router.get("/daily/history", response_model=list[DailyReportHistoryItem], summary="История отчётов")
def get_daily_report_history(
    db: DbSession,
    _user: StaffUser,
    limit: Annotated[int, Query(ge=1, le=MAX_HISTORY_LIMIT, description="Сколько отчётов вернуть")] = (
        DEFAULT_HISTORY_LIMIT
    ),
) -> list[DailyReportHistoryItem]:
    return [build_daily_report_history_item(report) for report in ReportService(db).history(limit)]
