"""AI tools (SPEC §39, 05-ai.md §7): read-only for the model, ownership and "Да" enforced for writes."""

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.ai.confirmation import (
    ConfirmationDecision,
    classify_cancel_confirmation,
    is_hesitation,
    mentions_cancellation,
)
from app.ai.tools import (
    READ_TOOL_NAMES,
    WRITE_TOOL_NAMES,
    ToolContext,
    ToolNotAllowedError,
    ToolRegistry,
    tool_definitions,
)
from app.models.enums import OrderStatus
from app.services.settings_service import SettingsService


def _registry(db: Session, customer) -> ToolRegistry:
    return ToolRegistry(db, ToolContext(customer=customer))


def test_definitions_are_strict_read_tools_only() -> None:
    definitions = tool_definitions()
    assert [tool["name"] for tool in definitions] == list(READ_TOOL_NAMES)
    assert len(READ_TOOL_NAMES) + len(WRITE_TOOL_NAMES) == 12  # SPEC §39
    for tool in definitions:
        assert tool["strict"] is True
        schema = tool["input_schema"]
        assert schema["additionalProperties"] is False and schema["required"] == list(schema["properties"])


def test_catalog_and_totals_come_from_the_database(db: Session, make_customer, make_product) -> None:
    honey = make_product("Медовик", "120.50")
    hidden = make_product("Старый торт", "10", is_active=False)
    registry = _registry(db, make_customer())

    assert [product["name"] for product in registry.execute("get_products", {})["products"]] == ["Медовик"]
    assert registry.execute("get_product", {"product_id": hidden.id}) == {"error": "not_found"}
    total = registry.execute(
        "calculate_order_total",
        {"items": [{"product_id": honey.id, "quantity": 3}, {"product_id": hidden.id, "quantity": 1}]},
    )
    assert total["total"] == "361.50" and total["unavailable"] == [hidden.id]


def test_orders_of_other_customers_are_invisible(db: Session, make_customer, make_order) -> None:
    me, other = make_customer("Я"), make_customer("Другой")
    mine = make_order(customer=me)
    foreign = make_order(customer=other)
    registry = _registry(db, me)

    assert registry.execute("get_order", {"order_id": mine.id})["order_id"] == mine.id
    assert registry.execute("get_order", {"order_id": foreign.id}) == {"error": "not_found"}
    assert [order["order_id"] for order in registry.execute("get_customer_orders", {})["orders"]] == [mine.id]
    assert registry.execute("get_customer", {})["name"] == "Я"


def test_model_cannot_run_write_tools_or_unknown_tools(db: Session, make_customer) -> None:
    registry = _registry(db, make_customer())
    for name in WRITE_TOOL_NAMES:
        assert registry.execute(name, {})["error"] == "not_allowed"
    assert registry.execute("drop_database", {}) == {"error": "unknown_tool"}
    assert registry.execute("get_order", {"order_id": "x"}) == {"error": "invalid_arguments"}


def test_delivery_info_uses_the_admin_settings(db: Session, make_customer) -> None:
    SettingsService(db).update({"pickup_address": "ул. Айни, 12", "payment_methods_text": "Наличные, перевод"})
    info = _registry(db, make_customer()).execute("get_delivery_info", {})
    assert info["pickup_address"] == "ул. Айни, 12" and info["payment_methods"] == "Наличные, перевод"
    assert info["min_lead_time_hours"] == 24


@pytest.mark.parametrize(
    "decision", [ConfirmationDecision.UNCERTAIN, ConfirmationDecision.NO, ConfirmationDecision.CHANGE]
)
def test_confirm_and_cancel_require_an_explicit_yes(db: Session, make_customer, make_order, decision) -> None:
    customer = make_customer(phone="+992901234567")
    order = make_order(customer=customer, status=OrderStatus.WAITING_CONFIRMATION)
    registry = _registry(db, customer)

    with pytest.raises(ToolNotAllowedError):
        registry.confirm_order(order, decision)
    with pytest.raises(ToolNotAllowedError):
        registry.cancel_order(order, decision)
    assert order.status == OrderStatus.WAITING_CONFIRMATION


def test_write_tools_refuse_foreign_orders(db: Session, make_customer, make_order) -> None:
    foreign = make_order(
        customer=make_customer("Другой", phone="+992900000000"), status=OrderStatus.WAITING_CONFIRMATION
    )
    registry = _registry(db, make_customer("Я"))
    with pytest.raises(ToolNotAllowedError):
        registry.confirm_order(foreign, ConfirmationDecision.YES)
    with pytest.raises(ToolNotAllowedError):
        registry.update_order_draft(foreign, comment="чужой")


def test_confirm_with_yes_confirms(db: Session, make_customer, make_order) -> None:
    customer = make_customer(phone="+992901234567")
    order = make_order(customer=customer, status=OrderStatus.WAITING_CONFIRMATION)
    _registry(db, customer).confirm_order(order, ConfirmationDecision.YES)
    assert order.status == OrderStatus.CONFIRMED and order.total_amount == Decimal("100.00")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Да", ConfirmationDecision.YES),
        ("да, отмените", ConfirmationDecision.YES),
        ("Отмените пожалуйста", ConfirmationDecision.YES),
        ("бекор кунед", ConfirmationDecision.YES),
        ("Нет", ConfirmationDecision.NO),
        ("нет, не надо", ConfirmationDecision.NO),
        ("нет, отмена", ConfirmationDecision.UNCERTAIN),
        ("наверное отменить", ConfirmationDecision.UNCERTAIN),
        ("ну не знаю", ConfirmationDecision.UNCERTAIN),
    ],
)
def test_cancel_confirmation_classifier(text: str, expected: ConfirmationDecision) -> None:
    assert classify_cancel_confirmation(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ну вроде", True),
        ("думаю да", True),
        ("ок", True),
        ("👍", True),
        ("да да все верно спасибо вам большое за помощь", True),
        ("А как оплатить?", False),
        ("Сколько стоит доставка", False),
    ],
)
def test_hesitation_is_told_apart_from_other_questions(text: str, expected: bool) -> None:
    assert is_hesitation(text) is expected


def test_mentions_cancellation() -> None:
    assert mentions_cancellation("Отмените заказ")
    assert mentions_cancellation("бекор кунед")
    assert not mentions_cancellation("Нет, время другое")
