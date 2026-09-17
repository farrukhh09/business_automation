"""Production summary for a date (03-business-rules.md §4, 04-api.md §6, SPEC §19).

VALID orders with ``delivery_date = D``; their items are grouped by ``product_id`` (by the
``product_name`` snapshot when the product reference is gone), quantities summed, sorted by
``Product.sort_order`` and then by name::

    Заказы на 16 сентября:

    Красный бархат — 5 шт.
    Медовик — 4 шт.

Read-only service: it never writes, so it never commits.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.core.time import business_today
from app.models.order import Order
from app.models.product import Product
from app.repositories.orders import OrderRepository
from app.repositories.products import ProductRepository
from app.schemas.production import ProductionItem, ProductionSummaryOut
from app.services.formatting import DEFAULT_UNIT, format_day_month_ru, format_quantity

DELIVERY_BASIS = "delivery"
#: Products whose row is gone sort next to ``sort_order = 0`` products, then by name.
FALLBACK_SORT_ORDER = 0


@dataclass(frozen=True, slots=True)
class _Group:
    """Accumulator for one production line before products are looked up."""

    product_id: int | None
    fallback_name: str
    quantity: int


def _group_items(orders: Iterable[Order]) -> list[_Group]:
    """Sum quantities per ``product_id`` (or per ``product_name`` when there is no product)."""
    totals: dict[tuple[str, object], int] = {}
    names: dict[tuple[str, object], str] = {}
    for order in orders:
        for item in order.items:
            key: tuple[str, object] = (
                ("id", item.product_id) if item.product_id is not None else ("name", item.product_name)
            )
            totals[key] = totals.get(key, 0) + int(item.quantity)
            names.setdefault(key, item.product_name)
    return [
        _Group(
            product_id=int(value) if kind == "id" else None,
            fallback_name=names[(kind, value)],
            quantity=quantity,
        )
        for (kind, value), quantity in totals.items()
    ]


def production_text(summary_date: date, items: Sequence[ProductionItem]) -> str:
    """SPEC §19 text. Without items only the header line is produced."""
    lines = [f"Заказы на {format_day_month_ru(summary_date)}:"]
    if items:
        lines.append("")
        lines.extend(f"{item.product_name} — {format_quantity(item.quantity, item.unit)}" for item in items)
    return "\n".join(lines)


class ProductionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.orders = OrderRepository(db)
        self.products = ProductRepository(db)

    def summary(self, summary_date: date | None = None) -> ProductionSummaryOut:
        """04 §6 ``GET /production``. ``summary_date`` defaults to the current business date."""
        day = summary_date or business_today()
        orders = self.orders.valid_orders_for_period(day, day, DELIVERY_BASIS)
        items = self._items(orders)
        return ProductionSummaryOut(
            date=day,
            orders_count=len(orders),
            items=items,
            text=production_text(day, items),
        )

    def _items(self, orders: Sequence[Order]) -> list[ProductionItem]:
        groups = _group_items(orders)
        products = self.products.get_many(
            [group.product_id for group in groups if group.product_id is not None]
        )
        rows: list[tuple[int, str, int, ProductionItem]] = []
        for group in groups:
            product: Product | None = products.get(group.product_id) if group.product_id is not None else None
            # The current catalog name is what the kitchen bakes today; snapshots may differ per order.
            name = product.name if product is not None else group.fallback_name
            unit = (product.unit if product is not None else DEFAULT_UNIT) or DEFAULT_UNIT
            sort_order = product.sort_order if product is not None else FALLBACK_SORT_ORDER
            rows.append(
                (
                    sort_order,
                    name,
                    group.product_id or 0,
                    ProductionItem(
                        product_id=group.product_id,
                        product_name=name,
                        quantity=group.quantity,
                        unit=unit,
                    ),
                )
            )
        rows.sort(key=lambda row: (row[0], row[1], row[2]))
        return [row[3] for row in rows]
