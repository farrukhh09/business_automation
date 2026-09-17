"""Statistics, dashboard and time-series schemas (04-api.md §7).

Field names are fixed by the contract (``frontend/types/api.ts``: ``StatisticsOut``, ``DashboardOut``,
``TimeseriesPoint``). All money fields serialize as JSON numbers with 2 decimals (``Money``).
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Money
from app.schemas.order import OrderListItem

PeriodName = Literal["today", "yesterday", "week", "month", "custom"]
DateBasis = Literal["delivery", "created"]

DEFAULT_PERIOD: PeriodName = "today"
DEFAULT_DATE_BASIS: DateBasis = "delivery"


class StatisticsPeriodInfo(BaseModel):
    """``StatisticsOut.period`` — the resolved period and the date column it was computed on."""

    model_config = ConfigDict(extra="ignore")

    name: str
    date_from: date
    date_to: date
    date_basis: DateBasis


class FinanceStats(BaseModel):
    """03-business-rules.md §4 "Финансы" over VALID orders of the period."""

    model_config = ConfigDict(extra="ignore")

    revenue: Money
    orders_count: int
    paid_orders_count: int
    partially_paid_orders_count: int
    unpaid_orders_count: int
    paid_amount: Money
    unpaid_amount: Money
    average_check: Money


class CustomerStats(BaseModel):
    """03-business-rules.md §4 "Клиенты" (SPEC §21)."""

    model_config = ConfigDict(extra="ignore")

    new_customers: int
    regular_customers: int
    total_customers: int
    new_customer_orders: int
    regular_customer_orders: int
    customers_registered: int


class DeliveryStats(BaseModel):
    """03-business-rules.md §4 "Доставка"."""

    model_config = ConfigDict(extra="ignore")

    delivery_orders: int
    pickup_orders: int


class StatisticsOut(BaseModel):
    period: StatisticsPeriodInfo
    finance: FinanceStats
    customers: CustomerStats
    delivery: DeliveryStats


class DashboardOut(BaseModel):
    """``GET /statistics/dashboard`` (SPEC §32). "Today" is the business-timezone ``delivery_date``."""

    date: date
    orders_today: int
    revenue_today: Money
    unpaid_orders_count: int
    new_customers_today: int
    regular_customers_today: int
    delivery_orders_today: int
    pickup_orders_today: int
    waiting_confirmation_count: int
    conversations_needing_attention: int
    recent_orders: list[OrderListItem] = Field(default_factory=list)


class TimeseriesPoint(BaseModel):
    """One day of ``GET /statistics/timeseries`` (days without orders are present with zeros)."""

    date: date
    revenue: Money
    orders_count: int
    paid_amount: Money
