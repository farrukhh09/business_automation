"""``order_pricing.py`` — item-set algebra and totals (03-business-rules.md §1.4)."""

from decimal import Decimal

import pytest

from app.core.exceptions import BadRequestError
from app.models.order import Order, OrderItem
from app.services.order_pricing import ItemSpec, OrderPricing, items_text, money


def test_money_rounds_half_up() -> None:
    assert money(Decimal("10.005")) == Decimal("10.01")
    assert money(10) == Decimal("10.00")
    assert money("33.336") == Decimal("33.34")


def test_line_total() -> None:
    assert OrderPricing.line_total(Decimal("33.33"), 3) == Decimal("99.99")


def test_new_item_snapshots_price_and_name(make_product) -> None:
    product = make_product("Медовик", price="150.00")
    item = OrderPricing.new_item(product, 2)
    assert item.product_id == product.id
    assert item.product_name == "Медовик"
    assert item.unit_price == Decimal("150.00")
    assert item.total_price == Decimal("300.00")


def test_new_item_keeps_a_given_unit_price(make_product) -> None:
    product = make_product("Торт", price="100.00")
    item = OrderPricing.new_item(product, 1, unit_price=Decimal("80.00"))
    assert item.unit_price == Decimal("80.00")


def test_apply_add_merges_same_product_and_comment(make_product) -> None:
    product = make_product("Чизкейк", price="50.00")
    order = Order(items=[], total_amount=Decimal("0.00"))

    assert OrderPricing.apply(order, [ItemSpec(product.id, 2)], {product.id: product}, mode="add")
    assert len(order.items) == 1 and order.items[0].quantity == 2

    assert OrderPricing.apply(order, [ItemSpec(product.id, 3)], {product.id: product}, mode="add")
    assert len(order.items) == 1  # same product, same (empty) comment -> merged
    assert order.items[0].quantity == 5
    assert order.total_amount == Decimal("250.00")


def test_apply_add_keeps_separate_positions_for_different_comments(make_product) -> None:
    product = make_product("Торт с надписью", price="200.00")
    order = Order(items=[], total_amount=Decimal("0.00"))
    OrderPricing.apply(order, [ItemSpec(product.id, 1, "С Днём Рождения")], {product.id: product}, mode="add")
    OrderPricing.apply(order, [ItemSpec(product.id, 1, "Поздравляем")], {product.id: product}, mode="add")
    assert len(order.items) == 2


def test_apply_replace_keeps_price_of_a_position_already_in_the_order(make_product) -> None:
    product = make_product("Капкейк", price="20.00")
    order = Order(items=[], total_amount=Decimal("0.00"))
    OrderPricing.apply(order, [ItemSpec(product.id, 4)], {product.id: product}, mode="add")
    original_price = order.items[0].unit_price

    product.price = Decimal("30.00")  # price rises after the position was added
    OrderPricing.apply(order, [ItemSpec(product.id, 6)], {product.id: product}, mode="replace", keep_prices=True)
    assert order.items[0].unit_price == original_price  # 03 §1.4: never follows a later price change
    assert order.items[0].quantity == 6
    assert order.total_amount == original_price * 6


def test_apply_replace_without_keep_prices_uses_the_current_price(make_product) -> None:
    product = make_product("Пирожное", price="20.00")
    order = Order(items=[], total_amount=Decimal("0.00"))
    OrderPricing.apply(order, [ItemSpec(product.id, 1)], {product.id: product}, mode="add")

    product.price = Decimal("35.00")
    OrderPricing.apply(order, [ItemSpec(product.id, 1)], {product.id: product}, mode="replace", keep_prices=False)
    assert order.items[0].unit_price == Decimal("35.00")


def test_apply_replace_drops_positions_not_in_the_new_set(make_product) -> None:
    first = make_product("Штрудель", price="15.00")
    second = make_product("Круассан", price="10.00")
    order = Order(items=[], total_amount=Decimal("0.00"))
    OrderPricing.apply(order, [ItemSpec(first.id, 1), ItemSpec(second.id, 1)], {first.id: first, second.id: second}, mode="add")
    OrderPricing.apply(order, [ItemSpec(second.id, 1)], {second.id: second}, mode="replace")
    assert [item.product_id for item in order.items] == [second.id]


def test_apply_set_changes_only_the_named_product(make_product) -> None:
    """ "Шоколадных не 2, а 3": the position takes the new quantity, other products stay."""
    chocolate = make_product("Шоколадные", price="100.00")
    classic = make_product("Классические", price="100.00")
    order = Order(items=[], total_amount=Decimal("0.00"))
    products = {chocolate.id: chocolate, classic.id: classic}
    OrderPricing.apply(order, [ItemSpec(chocolate.id, 2), ItemSpec(classic.id, 3)], products, mode="add")
    chocolate.price = Decimal("120.00")  # the snapshot of the existing position must survive

    assert OrderPricing.apply(order, [ItemSpec(chocolate.id, 3)], products, mode="set")

    lines = sorted((item.product_name, item.quantity) for item in order.items)
    assert lines == [("Классические", 3), ("Шоколадные", 3)]
    assert next(item for item in order.items if item.product_id == chocolate.id).unit_price == Decimal("100.00")
    assert order.total_amount == Decimal("600.00")

    # a product not in the order yet is added; 0 removes one
    other = make_product("Ягодные", price="100.00")
    specs = [ItemSpec(other.id, 1), ItemSpec(classic.id, 0)]
    OrderPricing.apply(order, specs, {**products, other.id: other}, mode="set")
    assert sorted((item.product_name, item.quantity) for item in order.items) == [("Шоколадные", 3), ("Ягодные", 1)]


def test_apply_remove_partial_quantity(make_product) -> None:
    product = make_product("Круассан", price="15.00")
    order = Order(items=[], total_amount=Decimal("0.00"))
    OrderPricing.apply(order, [ItemSpec(product.id, 5)], {product.id: product}, mode="add")
    OrderPricing.apply(order, [ItemSpec(product.id, 2)], {}, mode="remove")
    assert order.items[0].quantity == 3


def test_apply_remove_all_when_quantity_not_given(make_product) -> None:
    product = make_product("Штрудель", price="15.00")
    order = Order(items=[], total_amount=Decimal("0.00"))
    OrderPricing.apply(order, [ItemSpec(product.id, 5)], {product.id: product}, mode="add")
    OrderPricing.apply(order, [ItemSpec(product.id)], {}, mode="remove")
    assert order.items == []
    assert order.total_amount == Decimal("0.00")


def test_apply_returns_false_when_nothing_changed(make_product) -> None:
    product = make_product("Ватрушка", price="15.00")
    order = Order(items=[], total_amount=Decimal("0.00"))
    assert OrderPricing.apply(order, [ItemSpec(product.id)], {}, mode="remove") is False


def test_apply_unknown_mode_raises() -> None:
    order = Order(items=[], total_amount=Decimal("0.00"))
    with pytest.raises(BadRequestError):
        OrderPricing.apply(order, [], {}, mode="bogus")


def test_item_spec_coerce_rejects_missing_product() -> None:
    with pytest.raises(BadRequestError):
        ItemSpec.coerce({"quantity": 2})


def test_items_text_matches_order_list_item_format() -> None:
    order = Order(
        items=[
            OrderItem(product_name="Медовик", quantity=2, unit_price=Decimal("1"), total_price=Decimal("2")),
            OrderItem(product_name="Чизкейк", quantity=1, unit_price=Decimal("1"), total_price=Decimal("1")),
        ]
    )
    assert items_text(order.items) == "Медовик ×2, Чизкейк ×1"
