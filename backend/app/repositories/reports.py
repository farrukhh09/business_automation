"""Stored daily reports (02-data-model.md: daily_reports; 04-api.md §8)."""

from datetime import date

from sqlalchemy import select

from app.models.report import DailyReport
from app.repositories.base import BaseRepository

DEFAULT_HISTORY_LIMIT = 30


class DailyReportRepository(BaseRepository[DailyReport]):
    model = DailyReport
    not_found_detail = "Отчёт не найден"

    def get_by_date(self, report_date: date) -> DailyReport | None:
        return self.db.scalars(select(DailyReport).where(DailyReport.report_date == report_date)).first()

    def history(self, limit: int = DEFAULT_HISTORY_LIMIT) -> list[DailyReport]:
        """Newest report dates first."""
        stmt = select(DailyReport).order_by(DailyReport.report_date.desc()).limit(max(int(limit), 1))
        return list(self.db.scalars(stmt).all())
