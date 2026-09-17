"""Production summary schemas (04-api.md §6, SPEC §19).

``GET /production?date=YYYY-MM-DD`` →
``{date, orders_count, items: [{product_id, product_name, quantity, unit}], text}``.
The same object is embedded in the daily report as ``DailyReportData.production``.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class ProductionItem(BaseModel):
    """One catalog position summed over the day's VALID orders (03 §4).

    ``product_id`` is ``null`` when the order item no longer references a product row and the
    position could only be grouped by its ``product_name`` snapshot.
    """

    model_config = ConfigDict(extra="ignore")

    product_id: int | None = None
    product_name: str
    quantity: int
    unit: str


class ProductionSummaryOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    date: date
    orders_count: int
    items: list[ProductionItem] = Field(default_factory=list)
    text: str
