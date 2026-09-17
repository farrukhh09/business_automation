"""Daily report (03-business-rules.md §4, 04-api.md §8, SPEC §22).

``build_daily(D)`` computes the finance / customers / delivery blocks for ``D`` (``date_basis=delivery``)
plus the production summary, and renders the text **exactly** in the SPEC §22 layout::

    ОТЧЁТ ЗА 15.09.2026

    Заказов: 18

    Выручка: 12 500 сомони

    Оплачено: 10 000 сомони
    Не оплачено: 2 500 сомони

    Новые клиенты: 7
    Постоянные клиенты: 11

    Доставка: 13
    Самовывоз: 5

    ПРОИЗВОДСТВО:

    Красный бархат — 4
    Медовик — 5

``get_or_build`` / ``regenerate`` store the result in ``daily_reports`` (one row per date) and commit.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import time as app_time
from app.core.logging import get_logger, log_event
from app.models.report import DailyReport
from app.repositories.reports import DEFAULT_HISTORY_LIMIT, DailyReportRepository
from app.schemas.report import DailyReportData
from app.schemas.statistics import DEFAULT_DATE_BASIS
from app.services.formatting import format_date_ru, format_money
from app.services.production_service import ProductionService
from app.services.settings_service import SettingsService
from app.services.statistics_service import Period, StatisticsService

logger = get_logger(__name__)

REPORT_HEADER = "ОТЧЁТ ЗА"
PRODUCTION_HEADER = "ПРОИЗВОДСТВО:"
MAX_HISTORY_LIMIT = 365


@dataclass(frozen=True, slots=True)
class DailyReportContent:
    """What ``build_daily`` produces before it is stored: the JSON blocks and the rendered text."""

    report_date: date
    data: DailyReportData
    text: str


def daily_report_text(report_date: date, data: DailyReportData) -> str:
    """SPEC §22 layout. Production lines carry no unit ("Красный бархат — 4")."""
    lines = [
        f"{REPORT_HEADER} {format_date_ru(report_date)}",
        "",
        f"Заказов: {data.finance.orders_count}",
        "",
        f"Выручка: {format_money(data.finance.revenue)}",
        "",
        f"Оплачено: {format_money(data.finance.paid_amount)}",
        f"Не оплачено: {format_money(data.finance.unpaid_amount)}",
        "",
        f"Новые клиенты: {data.customers.new_customers}",
        f"Постоянные клиенты: {data.customers.regular_customers}",
        "",
        f"Доставка: {data.delivery.delivery_orders}",
        f"Самовывоз: {data.delivery.pickup_orders}",
        "",
        PRODUCTION_HEADER,
    ]
    if data.production.items:
        lines.append("")
        lines.extend(f"{item.product_name} — {item.quantity}" for item in data.production.items)
    return "\n".join(lines)


class ReportService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = DailyReportRepository(db)
        self.statistics = StatisticsService(db)
        self.production = ProductionService(db)
        self.settings = SettingsService(db)

    # ------------------------------------------------------------------ date defaults

    def default_report_date(self, now: datetime | None = None) -> date:
        """04 §8 ``GET /reports/daily``: yesterday before ``daily_report_time``, today after it."""
        moment = now or app_time.business_now()
        report_time = self.settings.get().daily_report_time
        return moment.date() - timedelta(days=1) if moment.time() < report_time else moment.date()

    # ------------------------------------------------------------------ build

    def build_daily(self, report_date: date) -> DailyReportContent:
        """Compute the report for ``report_date`` without touching ``daily_reports``."""
        statistics = self.statistics.statistics(Period("custom", report_date, report_date), DEFAULT_DATE_BASIS)
        data = DailyReportData(
            finance=statistics.finance,
            customers=statistics.customers,
            delivery=statistics.delivery,
            production=self.production.summary(report_date),
        )
        return DailyReportContent(report_date=report_date, data=data, text=daily_report_text(report_date, data))

    # ------------------------------------------------------------------ store

    def daily(self, report_date: date | None = None) -> DailyReport:
        """``GET /reports/daily``: the stored report, built and stored on the fly when missing.

        A day that is not over yet (today or later) is always recomputed: a snapshot taken at noon
        would otherwise be shown until the evening job — or forever, without a worker.
        """
        day = report_date if report_date is not None else self.default_report_date()
        if day >= app_time.business_today():
            return self.regenerate(day)
        return self.get_or_build(day)

    def get_or_build(self, report_date: date) -> DailyReport:
        existing = self.repository.get_by_date(report_date)
        if existing is not None:
            return existing
        return self._store(self.build_daily(report_date))

    def regenerate(self, report_date: date) -> DailyReport:
        """``POST /reports/daily/generate`` and the Celery task: always recompute and overwrite."""
        return self._store(self.build_daily(report_date))

    def history(self, limit: int = DEFAULT_HISTORY_LIMIT) -> list[DailyReport]:
        return self.repository.history(min(max(int(limit), 1), MAX_HISTORY_LIMIT))

    def _store(self, content: DailyReportContent) -> DailyReport:
        payload = content.data.model_dump(mode="json")
        report = self.repository.get_by_date(content.report_date)
        if report is None:
            report = DailyReport(
                report_date=content.report_date,
                data=payload,
                text=content.text,
                generated_at=app_time.now_utc(),
            )
            self.repository.add(report)
        else:
            report.data = payload
            report.text = content.text
            report.generated_at = app_time.now_utc()
            self.repository.flush()
        try:
            self.db.commit()
        except IntegrityError:
            # Another worker stored the same date first (``daily_reports.report_date`` is unique).
            self.db.rollback()
            stored = self.repository.get_by_date(content.report_date)
            if stored is None:  # pragma: no cover - the unique constraint is the only cause
                raise
            return stored
        self.db.refresh(report)
        log_event(
            logger,
            "report.generated",
            report_date=content.report_date.isoformat(),
            orders_count=content.data.finance.orders_count,
            revenue=float(content.data.finance.revenue),
        )
        return report
