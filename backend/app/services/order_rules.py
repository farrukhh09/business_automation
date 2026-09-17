"""Order lifecycle: allowed status transitions (03-business-rules.md §1.1, §1.2).

Pure rules — no database access, no session. ``OrderService`` calls :func:`ensure_transition`
before every status change and exposes :func:`allowed_transitions` in ``OrderDetail`` so the admin
panel only offers buttons the backend would accept.

The table (03 §1.2)::

    NEW                  → WAITING_CONFIRMATION, CONFIRMED*, CANCELLED
    WAITING_CONFIRMATION → CONFIRMED, NEW, CANCELLED
    CONFIRMED            → PREPARING, READY, CANCELLED
    PREPARING            → READY, CONFIRMED, CANCELLED
    READY                → HANDED_TO_COURIER (DELIVERY only), COMPLETED, PREPARING, CANCELLED
    HANDED_TO_COURIER    → COMPLETED, READY, CANCELLED
    COMPLETED            → READY**
    CANCELLED            → —

``*``  ``NEW → CONFIRMED`` — staff only (``ActorType.USER``), and only when the order is complete.
``**`` ``COMPLETED → READY`` — ADMIN only (fixing a mistake); recalculates the customer status.
Entering ``WAITING_CONFIRMATION`` or ``CONFIRMED`` always requires complete data (§1.3).
"""

from app.core.exceptions import BusinessRuleError, PermissionDeniedError
from app.models.enums import ActorType, DeliveryType, OrderStatus, UserRole
from app.models.order import Order

ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.NEW: frozenset(
        {OrderStatus.WAITING_CONFIRMATION, OrderStatus.CONFIRMED, OrderStatus.CANCELLED}
    ),
    OrderStatus.WAITING_CONFIRMATION: frozenset(
        {OrderStatus.CONFIRMED, OrderStatus.NEW, OrderStatus.CANCELLED}
    ),
    OrderStatus.CONFIRMED: frozenset({OrderStatus.PREPARING, OrderStatus.READY, OrderStatus.CANCELLED}),
    OrderStatus.PREPARING: frozenset({OrderStatus.READY, OrderStatus.CONFIRMED, OrderStatus.CANCELLED}),
    OrderStatus.READY: frozenset(
        {
            OrderStatus.HANDED_TO_COURIER,
            OrderStatus.COMPLETED,
            OrderStatus.PREPARING,
            OrderStatus.CANCELLED,
        }
    ),
    OrderStatus.HANDED_TO_COURIER: frozenset(
        {OrderStatus.COMPLETED, OrderStatus.READY, OrderStatus.CANCELLED}
    ),
    OrderStatus.COMPLETED: frozenset({OrderStatus.READY}),
    OrderStatus.CANCELLED: frozenset(),
}

#: Entering these statuses requires ``OrderValidator.missing_fields(order) == []`` (03 §1.2).
STATUSES_REQUIRING_COMPLETENESS: frozenset[OrderStatus] = frozenset(
    {OrderStatus.WAITING_CONFIRMATION, OrderStatus.CONFIRMED}
)
#: Only for ``delivery_type=DELIVERY`` orders.
DELIVERY_ONLY_STATUSES: frozenset[OrderStatus] = frozenset({OrderStatus.HANDED_TO_COURIER})
#: ``*`` — only a staff member (``ActorType.USER``) may confirm a draft straight from NEW.
STAFF_ONLY_TRANSITIONS: frozenset[tuple[OrderStatus, OrderStatus]] = frozenset(
    {(OrderStatus.NEW, OrderStatus.CONFIRMED)}
)
#: ``**`` — only ADMIN may revert a completed order.
ADMIN_ONLY_TRANSITIONS: frozenset[tuple[OrderStatus, OrderStatus]] = frozenset(
    {(OrderStatus.COMPLETED, OrderStatus.READY)}
)
#: Actors that count as staff (the admin panel); the bot is ``AI``/``CUSTOMER``.
STAFF_ACTOR_TYPES: frozenset[ActorType] = frozenset({ActorType.USER})
#: Canonical display order of statuses (declaration order of the enum).
STATUS_ORDER: tuple[OrderStatus, ...] = tuple(OrderStatus)

INVALID_TRANSITION_CODE = "invalid_status_transition"


def status_rank(status: OrderStatus) -> int:
    return STATUS_ORDER.index(status)


def transitions_from(status: OrderStatus) -> frozenset[OrderStatus]:
    """Raw table row (no role / delivery-type filtering)."""
    return ALLOWED_TRANSITIONS.get(OrderStatus(status), frozenset())


def is_allowed(current: OrderStatus, new: OrderStatus) -> bool:
    """Table lookup only. ``current == new`` is not a transition and returns ``False``."""
    return OrderStatus(new) in transitions_from(current)


def requires_completeness(new_status: OrderStatus) -> bool:
    return OrderStatus(new_status) in STATUSES_REQUIRING_COMPLETENESS


def is_delivery_order(order: Order) -> bool:
    return order.delivery_type == DeliveryType.DELIVERY


def allowed_transitions(
    order: Order,
    role: UserRole | str | None = None,
    actor_type: ActorType | str = ActorType.USER,
) -> list[OrderStatus]:
    """Statuses this actor may set on ``order`` right now (04 §5 ``OrderDetail.allowed_transitions``).

    Filtered by delivery type (``HANDED_TO_COURIER`` only for DELIVERY) and by role
    (``COMPLETED → READY`` only for ADMIN; ``NEW → CONFIRMED`` only for staff). Completeness is
    **not** applied here: the panel shows ``missing_fields`` next to the buttons and an incomplete
    confirmation is answered with 422 ``order_incomplete``.
    """
    actor = ActorType(actor_type)
    user_role = UserRole(role) if role is not None else None
    current = order.status
    result: list[OrderStatus] = []
    for status in sorted(transitions_from(current), key=status_rank):
        if status in DELIVERY_ONLY_STATUSES and not is_delivery_order(order):
            continue
        if (current, status) in ADMIN_ONLY_TRANSITIONS and user_role is not None and user_role != UserRole.ADMIN:
            continue
        if (current, status) in STAFF_ONLY_TRANSITIONS and actor not in STAFF_ACTOR_TYPES:
            continue
        result.append(status)
    return result


def transition_error(current: OrderStatus, new: OrderStatus, detail: str | None = None) -> BusinessRuleError:
    return BusinessRuleError(
        INVALID_TRANSITION_CODE,
        detail or f"Недопустимая смена статуса: {current.value} → {new.value}",
        from_status=current.value,
        to_status=new.value,
    )


def ensure_transition(
    order: Order,
    new_status: OrderStatus,
    *,
    actor_type: ActorType | str = ActorType.USER,
    role: UserRole | str | None = None,
) -> None:
    """Raise unless ``order.status → new_status`` is allowed for this actor.

    ``BusinessRuleError("invalid_status_transition")`` (422) for the table, the delivery-type rule
    and the staff-only rule; ``PermissionDeniedError`` (403) when only the role is missing
    (``COMPLETED → READY`` by an operator).
    """
    current = order.status
    new_status = OrderStatus(new_status)
    actor = ActorType(actor_type)
    user_role = UserRole(role) if role is not None else None

    if not is_allowed(current, new_status):
        raise transition_error(current, new_status)
    if new_status in DELIVERY_ONLY_STATUSES and not is_delivery_order(order):
        raise transition_error(
            current,
            new_status,
            "Статус «Передан курьеру» доступен только для заказов с доставкой",
        )
    if (current, new_status) in STAFF_ONLY_TRANSITIONS and actor not in STAFF_ACTOR_TYPES:
        raise transition_error(
            current,
            new_status,
            "Подтвердить заказ без запроса подтверждения может только сотрудник",
        )
    if (current, new_status) in ADMIN_ONLY_TRANSITIONS and user_role is not None and user_role != UserRole.ADMIN:
        raise PermissionDeniedError("Вернуть завершённый заказ может только администратор")
