"""Stored daily reports (02-data-model.md: daily_reports)."""

from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import now_utc
from app.models.base import Base, TimestampMixin, UTCDateTime, json_dict_type


class DailyReport(TimestampMixin, Base):
    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    report_date: Mapped[date] = mapped_column(sa.Date, unique=True, nullable=False)
    # {"finance": {...}, "customers": {...}, "delivery": {...}, "production": {...}}
    data: Mapped[dict[str, Any]] = mapped_column(json_dict_type(), nullable=False, default=dict)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=now_utc, server_default=sa.func.now()
    )
