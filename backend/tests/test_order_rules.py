"""``order_rules.py`` — status transition table (03-business-rules.md §1.1-§1.2)."""

import pytest

from app.core.exceptions import BusinessRuleError, PermissionDeniedError
from app.models.enums import ActorType, DeliveryType, OrderStatus, UserRole
from app.services import order_rules


class _FakeOrder:
    """``order_rules`` only reads ``status``/``delivery_type`` — no DB needed."""

    def __init__(self, status: OrderStatus, delivery_type: DeliveryType | None = DeliveryType.PICKUP) -> None:
        self.status = status
        self.delivery_type = delivery_type


ALLOWED = [
    (OrderStatus.NEW, OrderStatus.WAITING_CONFIRMATION),
    (OrderStatus.NEW, OrderStatus.CANCELLED),
    (OrderStatus.WAITING_CONFIRMATION, OrderStatus.CONFIRMED),
    (OrderStatus.WAITING_CONFIRMATION, OrderStatus.NEW),
    (OrderStatus.CONFIRMED, OrderStatus.PREPARING),
    (OrderStatus.PREPARING, OrderStatus.READY),
    (OrderStatus.PREPARING, OrderStatus.CONFIRMED),
    (OrderStatus.READY, OrderStatus.COMPLETED),
    (OrderStatus.HANDED_TO_COURIER, OrderStatus.COMPLETED),
    (OrderStatus.HANDED_TO_COURIER, OrderStatus.READY),
]

FORBIDDEN = [
    (OrderStatus.NEW, OrderStatus.READY),
    (OrderStatus.NEW, OrderStatus.COMPLETED),
    (OrderStatus.COMPLETED, OrderStatus.CANCELLED),
    (OrderStatus.CANCELLED, OrderStatus.NEW),
    (OrderStatus.CONFIRMED, OrderStatus.HANDED_TO_COURIER),  # delivery-type rule, not the table itself
]


@pytest.mark.parametrize("current,new", ALLOWED)
def test_is_allowed_true(current: OrderStatus, new: OrderStatus) -> None:
    assert order_rules.is_allowed(current, new)


@pytest.mark.parametrize("current,new", FORBIDDEN)
def test_is_allowed_false(current: OrderStatus, new: OrderStatus) -> None:
    assert not order_rules.is_allowed(current, new)


def test_same_status_is_not_a_transition() -> None:
    assert not order_rules.is_allowed(OrderStatus.CONFIRMED, OrderStatus.CONFIRMED)


def test_cancelled_has_no_outgoing_transitions() -> None:
    assert order_rules.transitions_from(OrderStatus.CANCELLED) == frozenset()


def test_handed_to_courier_only_for_delivery_orders() -> None:
    order = _FakeOrder(OrderStatus.READY, DeliveryType.PICKUP)
    with pytest.raises(BusinessRuleError) as excinfo:
        order_rules.ensure_transition(order, OrderStatus.HANDED_TO_COURIER)
    assert excinfo.value.code == "invalid_status_transition"

    order.delivery_type = DeliveryType.DELIVERY
    order_rules.ensure_transition(order, OrderStatus.HANDED_TO_COURIER)  # no error


def test_new_to_confirmed_is_staff_only() -> None:
    order = _FakeOrder(OrderStatus.NEW)
    with pytest.raises(BusinessRuleError) as excinfo:
        order_rules.ensure_transition(order, OrderStatus.CONFIRMED, actor_type=ActorType.AI)
    assert excinfo.value.code == "invalid_status_transition"
    order_rules.ensure_transition(order, OrderStatus.CONFIRMED, actor_type=ActorType.USER)  # no error


def test_completed_to_ready_is_admin_only() -> None:
    order = _FakeOrder(OrderStatus.COMPLETED)
    with pytest.raises(PermissionDeniedError):
        order_rules.ensure_transition(order, OrderStatus.READY, role=UserRole.OPERATOR)
    order_rules.ensure_transition(order, OrderStatus.READY, role=UserRole.ADMIN)  # no error


def test_allowed_transitions_filters_by_delivery_type() -> None:
    pickup = _FakeOrder(OrderStatus.READY, DeliveryType.PICKUP)
    delivery = _FakeOrder(OrderStatus.READY, DeliveryType.DELIVERY)
    assert OrderStatus.HANDED_TO_COURIER not in order_rules.allowed_transitions(pickup)
    assert OrderStatus.HANDED_TO_COURIER in order_rules.allowed_transitions(delivery)


def test_allowed_transitions_filters_by_role() -> None:
    completed = _FakeOrder(OrderStatus.COMPLETED)
    assert OrderStatus.READY not in order_rules.allowed_transitions(completed, role=UserRole.OPERATOR)
    assert OrderStatus.READY in order_rules.allowed_transitions(completed, role=UserRole.ADMIN)
    # No role given at all means "the check does not apply here" (mirrors ensure_transition), not
    # "deny" — the admin-only row is visible, same as passing role=ADMIN.
    assert OrderStatus.READY in order_rules.allowed_transitions(completed, role=None)


def test_allowed_transitions_result_is_sorted_by_declaration_order() -> None:
    order = _FakeOrder(OrderStatus.READY, DeliveryType.DELIVERY)
    options = order_rules.allowed_transitions(order, role=UserRole.ADMIN)
    assert options == sorted(options, key=order_rules.status_rank)
