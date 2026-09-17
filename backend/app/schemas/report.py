"""Daily report schemas (04-api.md §8, SPEC §22).

``DailyReportOut = {date, generated_at, text, data: {finance, customers, delivery, production}}``;
``GET /reports/daily/history`` returns the same object **without** ``data``
(``frontend/types/api.ts``: ``DailyReportHistoryItem = Omit<DailyReportOut, "data">``).
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models.report import DailyReport
from app.schemas.production import ProductionSummaryOut
from app.schemas.statistics import CustomerStats, DeliveryStats, FinanceStats


class DailyReportData(BaseModel):
    """Stored in ``daily_reports.data`` (JSON) and returned as ``DailyReportOut.data``."""

    model_config = ConfigDict(extra="ignore")

    finance: FinanceStats
    customers: CustomerStats
    delivery: DeliveryStats
    production: ProductionSummaryOut


class DailyReportHistoryItem(BaseModel):
    """History row: the report without its ``data`` block."""

    model_config = ConfigDict(extra="ignore")

    date: date
    generated_at: datetime
    text: str


class DailyReportOut(DailyReportHistoryItem):
    data: DailyReportData


class DailyReportGenerateRequest(BaseModel):
    """``POST /reports/daily/generate`` body."""

    model_config = ConfigDict(extra="ignore")

    date: date


def build_daily_report_history_item(report: DailyReport) -> DailyReportHistoryItem:
    return DailyReportHistoryItem(date=report.report_date, generated_at=report.generated_at, text=report.text)


def build_daily_report_out(report: DailyReport, data: DailyReportData | None = None) -> DailyReportOut:
    """``DailyReport`` row → API object. ``data`` may be passed to avoid re-parsing the stored JSON."""
    return DailyReportOut(
        date=report.report_date,
        generated_at=report.generated_at,
        text=report.text,
        data=data if data is not None else DailyReportData.model_validate(report.data or {}),
    )
