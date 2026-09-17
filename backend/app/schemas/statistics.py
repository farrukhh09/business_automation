"""Statistics, dashboard and time-series schemas (04-api.md §7).

Field names are fixed by the contract (``frontend/types/api.ts``: ``StatisticsOut``, ``DashboardOut``,
``TimeseriesPoint``). All money fields serialize as JSON numbers with 2 decimals (``Money``).
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ExpenseCategory
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


class ExpenseCategoryTotal(BaseModel):
    category: ExpenseCategory
    amount: Money


class ProfitAndLossStats(BaseModel):
    """03-business-rules.md §4 "Расходы и прибыль". Kept out of ``FinanceStats``: the daily report
    stores ``FinanceStats`` snapshots and its text (SPEC §22) has no expenses."""

    model_config = ConfigDict(extra="ignore")

    revenue: Money
    expenses: Money
    profit: Money
    #: ``profit / revenue × 100`` rounded to 0.1; ``null`` when there is no revenue.
    margin_percent: float | None = None
    #: Categories with a non-zero total, largest first.
    expenses_by_category: list[ExpenseCategoryTotal] = Field(default_factory=list)


class StatisticsOut(BaseModel):
    period: StatisticsPeriodInfo
    finance: FinanceStats
    customers: CustomerStats
    delivery: DeliveryStats
    profit_and_loss: ProfitAndLossStats


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
    #: Expenses dated that day (``expense_date``, independent of ``date_basis``).
    expenses: Money
    profit: Money
