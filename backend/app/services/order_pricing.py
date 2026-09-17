"""Order totals and price snapshots (03-business-rules.md §1.4).

- the price of a position is taken **from the database** (``Product.price``) when the position is
  added and stored in ``OrderItem.unit_price``; ``product_name`` is a snapshot too;
- ``total_price = quantity × unit_price`` (``Decimal``, ROUND_HALF_UP, 2 places),
  ``Order.total_amount = Σ total_price``;
- the price of an existing position never follows a later price change of the product. When an
  admin replaces the item set, new positions take the current price while positions with a
  ``product_id`` that was already in the order keep their ``unit_price`` if ``keep_prices=True``;
- amounts are never accepted from the client/LLM — only ``product_id``, ``quantity``, ``comment``.

The module is pure: it mutates the passed ``Order`` (and its ``items`` collection) and never
queries or commits. ``OrderService`` resolves products and owns the transaction.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.core.exceptions import BadRequestError
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.schemas.order import ITEMS_SUMMARY_SEPARATOR

MONEY_QUANT = Decimal("0.01")
ZERO = Decimal("0.00")

ADD = "add"
REPLACE = "replace"
REMOVE = "remove"
#: "Шоколадных не 2, а 3": the named positions get exactly these quantities, every other position stays.
SET = "set"
ITEMS_MODES: tuple[str, ...] = (ADD, REPLACE, REMOVE, SET)


def money(value: Decimal | int | float | str) -> Decimal:
    """2 decimal places, ROUND_HALF_UP (01-overview.md §4)."""
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
        return number.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise BadRequestError("Некорректная сумма") from exc


@dataclass(frozen=True, slots=True)
class ItemSpec:
    """One requested position: ``(product_id, quantity, comment)`` — never a price."""

    product_id: int
    quantity: int | None = None
    comment: str | None = None

    @classmethod
    def coerce(cls, value: Any) -> "ItemSpec":
        """Accepts ``ItemSpec``, ``(product_id[, quantity[, comment]])``, a mapping or ``OrderItemIn``."""
        if isinstance(value, ItemSpec):
            return value
        if isinstance(value, Mapping):
            product_id = value.get("product_id")
            quantity = value.get("quantity")
            comment = value.get("comment")
        elif isinstance(value, (tuple, list)):
            parts: Sequence[Any] = value
            if not parts:
                raise BadRequestError("Некорректная позиция заказа")
            product_id = parts[0]
            quantity = parts[1] if len(parts) > 1 else None
            comment = parts[2] if len(parts) > 2 else None
        else:
            product_id = getattr(value, "product_id", None)
            quantity = getattr(value, "quantity", None)
            comment = getattr(value, "comment", None)
        if product_id is None:
            raise BadRequestError("Не указан товар позиции заказа")
        try:
            resolved_id = int(product_id)
            resolved_quantity = None if quantity is None else int(quantity)
        except (TypeError, ValueError) as exc:
            raise BadRequestError("Некорректная позиция заказа") from exc
        text = comment.strip() if isinstance(comment, str) else comment
        return cls(product_id=resolved_id, quantity=resolved_quantity, comment=text or None)


def items_text(items: Iterable[OrderItem]) -> str:
    """``"Медовик ×2, Чизкейк ×1"`` — the same shape as ``OrderListItem.items_summary``."""
    return ITEMS_SUMMARY_SEPARATOR.join(f"{item.product_name} ×{item.quantity}" for item in items)


def items_snapshot(order: Order) -> list[tuple[int | None, str, int, Decimal, str | None]]:
    """Comparable snapshot of the item set (used to detect "nothing changed")."""
    return [
        (item.product_id, item.product_name, item.quantity, money(item.unit_price), item.comment)
        for item in order.items
    ]


def _same_comment(left: str | None, right: str | None) -> bool:
    return (left or "").strip() == (right or "").strip()


class OrderPricing:
    """Item-set algebra and totals. Every method only mutates the given order."""

    @staticmethod
    def line_total(unit_price: Decimal, quantity: int) -> Decimal:
        return money(money(unit_price) * int(quantity))

    @classmethod
    def new_item(
        cls,
        product: Product,
        quantity: int,
        comment: str | None = None,
        unit_price: Decimal | None = None,
    ) -> OrderItem:
        """Position with the price snapshot taken from the database (or ``unit_price`` when kept)."""
        price = money(product.price if unit_price is None else unit_price)
        return OrderItem(
            product=product,
            product_id=product.id,
            product_name=product.name,
            quantity=int(quantity),
            unit_price=price,
            total_price=cls.line_total(price, quantity),
            comment=comment,
        )

    @classmethod
    def set_quantity(cls, item: OrderItem, quantity: int) -> None:
        item.quantity = int(quantity)
        item.total_price = cls.line_total(item.unit_price, item.quantity)

    @classmethod
    def recalculate_total(cls, order: Order) -> Decimal:
        """``Order.total_amount = Σ total_price`` (also refreshes each ``total_price``)."""
        total = ZERO
        for item in order.items:
            item.total_price = cls.line_total(item.unit_price, item.quantity)
            total += item.total_price
        order.total_amount = money(total)
        return order.total_amount

    # ------------------------------------------------------------------ item set

    @classmethod
    def apply(
        cls,
        order: Order,
        specs: Iterable[ItemSpec],
        products: Mapping[int, Product],
        mode: str = ADD,
        keep_prices: bool = True,
    ) -> bool:
        """Apply ``specs`` in ``mode`` (``add`` / ``replace`` / ``remove`` / ``set``); returns "something changed".

        ``products`` must contain every ``product_id`` of ``specs`` for ``add``/``replace``/``set``
        (``OrderService`` validates that they exist, are active and not deleted beforehand).
        """
        resolved_mode = (mode or ADD).strip().lower()
        if resolved_mode not in ITEMS_MODES:
            raise BadRequestError("Недопустимый режим изменения позиций: ожидается add, replace, remove или set")
        before = items_snapshot(order)
        spec_list = [ItemSpec.coerce(spec) for spec in specs]

        if resolved_mode == REPLACE:
            cls._replace(order, spec_list, products, keep_prices)
        elif resolved_mode == ADD:
            cls._add(order, spec_list, products)
        elif resolved_mode == SET:
            cls._set(order, spec_list, products)
        else:
            cls._remove(order, spec_list)

        cls.recalculate_total(order)
        return items_snapshot(order) != before

    @classmethod
    def _replace(
        cls,
        order: Order,
        specs: Sequence[ItemSpec],
        products: Mapping[int, Product],
        keep_prices: bool,
    ) -> None:
        reusable: dict[int, list[OrderItem]] = {}
        if keep_prices:
            for item in order.items:
                if item.product_id is not None:
                    reusable.setdefault(item.product_id, []).append(item)

        kept: list[OrderItem] = []
        for spec in specs:
            product = products[spec.product_id]
            quantity = spec.quantity if spec.quantity is not None else 1
            candidates = reusable.get(spec.product_id)
            if candidates:
                item = candidates.pop(0)  # keeps its unit_price snapshot (03 §1.4)
                item.comment = spec.comment
                cls.set_quantity(item, quantity)
            else:
                item = cls.new_item(product, quantity, spec.comment)
            kept.append(item)
        order.items[:] = kept  # delete-orphan removes the positions that are gone

    @classmethod
    def _add(cls, order: Order, specs: Sequence[ItemSpec], products: Mapping[int, Product]) -> None:
        for spec in specs:
            product = products[spec.product_id]
            quantity = spec.quantity if spec.quantity is not None else 1
            if quantity < 1:
                continue
            existing = next(
                (
                    item
                    for item in order.items
                    if item.product_id == spec.product_id and _same_comment(item.comment, spec.comment)
                ),
                None,
            )
            if existing is not None:
                cls.set_quantity(existing, existing.quantity + quantity)
            else:
                order.items.append(cls.new_item(product, quantity, spec.comment))

    @classmethod
    def _set(cls, order: Order, specs: Sequence[ItemSpec], products: Mapping[int, Product]) -> None:
        """A corrected quantity ("шоколадных не 2, а 3"): the position takes exactly the new quantity (its
        price snapshot stays), a product not in the order yet is added, ``0`` removes it; positions of
        other products are untouched. Several positions of one product (different comments) are folded
        into the first one — the customer named one total."""
        for spec in specs:
            quantity = spec.quantity if spec.quantity is not None else 1
            matches = [item for item in order.items if item.product_id == spec.product_id]
            if not matches:
                if quantity >= 1:
                    order.items.append(cls.new_item(products[spec.product_id], quantity, spec.comment))
                continue
            first, *rest = matches
            for item in rest:
                order.items.remove(item)
            if quantity < 1:
                order.items.remove(first)
                continue
            cls.set_quantity(first, quantity)
            if spec.comment:
                first.comment = spec.comment

    @classmethod
    def _remove(cls, order: Order, specs: Sequence[ItemSpec]) -> None:
        for spec in specs:
            matches = [item for item in order.items if item.product_id == spec.product_id]
            if not matches:
                continue
            if spec.quantity is None or spec.quantity <= 0:
                for item in matches:
                    order.items.remove(item)
                continue
            remaining = spec.quantity
            for item in reversed(matches):
                if remaining <= 0:
                    break
                taken = min(item.quantity, remaining)
                remaining -= taken
                if item.quantity - taken <= 0:
                    order.items.remove(item)
                else:
                    cls.set_quantity(item, item.quantity - taken)
