"""Payments and payment status (03-business-rules.md §3).

Manual status changes by staff and registered payments go through the **same** service so that
``paid_amount``, ``payments`` and ``payment_status`` can never drift apart:

- ``POST /orders/{id}/payments {kind, amount, method}`` → a ``Payment`` row → recalculation;
- ``payment_status=PAID`` → ``Payment(PAYMENT)`` for ``total − paid`` (when > 0);
- ``payment_status=UNPAID`` → correcting ``Payment(REFUND)`` for ``paid`` (when > 0), note
  "Ручная корректировка"; allowed for ADMIN and OPERATOR;
- ``payment_status=PARTIALLY_PAID`` requires ``paid_amount`` (0 < x < total) → a PAYMENT or REFUND
  for the difference;
- ``payment_status=REFUNDED`` → ``Payment(REFUND)`` for the whole ``paid``, the status stays REFUNDED.

Recalculation: ``paid = Σ PAYMENT − Σ REFUND``; a refund exists and ``paid == 0`` → ``REFUNDED``;
``paid == 0`` → ``UNPAID``; ``0 < paid < total`` → ``PARTIALLY_PAID``; otherwise ``PAID``. The item
set changing the total re-runs exactly the same recalculation (``apply_total_change``).

Every change writes ``OrderEvent(PAYMENT_CHANGED)`` and logs ``order.payment_changed``.

``record_order_event`` / ``json_changes`` live here (and not in ``order_service``) because
``order_service`` imports this module, not the other way around.
"""

from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import BusinessRuleError
from app.core.logging import get_logger, log_event
from app.core.time import now_utc
from app.models.enums import ActorType, PaymentKind, PaymentMethod, PaymentStatus
from app.models.order import Order, OrderEvent, Payment
from app.models.user import User
from app.repositories.orders import PaymentRepository
from app.services.order_pricing import ZERO, money

logger = get_logger(__name__)

# 02-data-model.md: order_events.event_type
EVENT_CREATED = "CREATED"
EVENT_UPDATED = "UPDATED"
EVENT_ITEMS_CHANGED = "ITEMS_CHANGED"
EVENT_STATUS_CHANGED = "STATUS_CHANGED"
EVENT_PAYMENT_CHANGED = "PAYMENT_CHANGED"
EVENT_DELIVERY_CHANGED = "DELIVERY_CHANGED"
EVENT_CONFIRMED = "CONFIRMED"
EVENT_CANCELLED = "CANCELLED"

MANUAL_CORRECTION_NOTE = "Ручная корректировка"
TOTAL_CHANGED_COMMENT = "Пересчёт оплаты после изменения суммы заказа"


def json_value(value: Any) -> Any:
    """JSON-serializable form of a diff value (``OrderEvent.changes`` is a JSON column)."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_value(item) for item in value]
    return value


def json_changes(changes: Mapping[str, Any] | None) -> dict[str, Any]:
    return {str(field): json_value(value) for field, value in (changes or {}).items()}


def record_order_event(
    db: Session,
    order: Order,
    event_type: str,
    changes: Mapping[str, Any] | None = None,
    comment: str | None = None,
    actor_type: ActorType | str = ActorType.USER,
    user: User | None = None,
) -> OrderEvent:
    """Append one journal row (03 §1.5). Flush only — the calling service commits."""
    event = OrderEvent(
        order=order,
        actor_type=ActorType(actor_type),
        actor_user_id=user.id if user is not None else None,
        event_type=event_type,
        changes=json_changes(changes),
        comment=comment,
    )
    db.add(event)
    db.flush()
    return event


def compute_payment_status(paid: Decimal, total: Decimal, has_refund: bool) -> PaymentStatus:
    """03 §3 recalculation table (``paid`` ≥ ``total`` with a zero total counts as PAID)."""
    if paid <= ZERO:
        return PaymentStatus.REFUNDED if has_refund else PaymentStatus.UNPAID
    if total > ZERO and paid < total:
        return PaymentStatus.PARTIALLY_PAID
    return PaymentStatus.PAID


class PaymentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = PaymentRepository(db)

    # ------------------------------------------------------------------ ledger

    def ledger(self, order: Order) -> tuple[Decimal, bool]:
        """``(paid, has_refund)`` straight from the payments of the order."""
        sums = self.repository.sums_by_kind(order.id)
        paid = money(sums[PaymentKind.PAYMENT] - sums[PaymentKind.REFUND])
        return paid, sums[PaymentKind.REFUND] > ZERO

    def recalculate(self, order: Order) -> dict[str, list[Any]]:
        """Recompute ``paid_amount`` and ``payment_status`` from the ledger (flush only).

        Returns the diff ``{"field": [old, new]}`` — empty when nothing changed.
        """
        paid, has_refund = self.ledger(order)
        status = compute_payment_status(paid, money(order.total_amount), has_refund)
        changes: dict[str, list[Any]] = {}
        if money(order.paid_amount) != paid:
            changes["paid_amount"] = [money(order.paid_amount), paid]
            order.paid_amount = paid
        if order.payment_status != status:
            changes["payment_status"] = [order.payment_status, status]
            order.payment_status = status
        if changes:
            self.db.flush()
        return changes

    def add_payment_row(
        self,
        order: Order,
        kind: PaymentKind | str,
        amount: Decimal,
        method: PaymentMethod | str | None = None,
        note: str | None = None,
        user: User | None = None,
        paid_at: datetime | None = None,
    ) -> Payment:
        """Insert one ledger row (flush only, no recalculation)."""
        payment = Payment(
            order=order,
            kind=PaymentKind(kind),
            amount=money(amount),
            method=PaymentMethod(method) if method is not None else None,
            note=note,
            created_by_user_id=user.id if user is not None else None,
            paid_at=paid_at or now_utc(),
        )
        self.db.add(payment)
        self.db.flush()
        return payment

    # ------------------------------------------------------------------ operations

    def register_payment(
        self,
        order: Order,
        kind: PaymentKind | str,
        amount: Decimal | int | float | str,
        method: PaymentMethod | str | None = None,
        note: str | None = None,
        user: User | None = None,
        actor_type: ActorType | str = ActorType.USER,
        paid_at: datetime | None = None,
        commit: bool = True,
    ) -> Payment:
        """``POST /orders/{id}/payments``: one ledger row + recalculation + journal."""
        kind = PaymentKind(kind)
        value = money(amount)
        if value <= ZERO:
            raise BusinessRuleError("invalid_payment_amount", "Сумма платежа должна быть больше нуля")
        paid, _ = self.ledger(order)
        if kind == PaymentKind.REFUND and value > paid:
            raise BusinessRuleError(
                "refund_exceeds_paid",
                "Возврат больше оплаченной суммы",
                paid_amount=float(paid),
                amount=float(value),
            )

        payment = self.add_payment_row(order, kind, value, method=method, note=note, user=user, paid_at=paid_at)
        changes: dict[str, Any] = {
            "payment": [
                None,
                {
                    "kind": kind.value,
                    "amount": float(value),
                    "method": PaymentMethod(method).value if method is not None else None,
                },
            ]
        }
        changes.update(self.recalculate(order))
        self._journal(order, changes, comment=note, actor_type=actor_type, user=user, commit=commit)
        return payment

    def set_status(
        self,
        order: Order,
        status: PaymentStatus | str,
        paid_amount: Decimal | int | float | str | None = None,
        method: PaymentMethod | str | None = None,
        user: User | None = None,
        actor_type: ActorType | str = ActorType.USER,
        commit: bool = True,
    ) -> dict[str, list[Any]]:
        """Manual ``payment_status`` change (03 §3): creates the correcting ledger rows.

        Returns the diff of the order fields; empty when the order was already in that state.
        """
        target = PaymentStatus(status)
        total = money(order.total_amount)
        paid, has_refund = self.ledger(order)

        if target == PaymentStatus.PAID:
            delta = money(total - paid)
            if delta > ZERO:
                self.add_payment_row(order, PaymentKind.PAYMENT, delta, method=method, user=user)
            elif total <= ZERO and order.payment_status != PaymentStatus.PAID:
                raise BusinessRuleError(
                    "nothing_to_pay",
                    "Нельзя отметить оплату: сумма заказа равна нулю",
                    total_amount=float(total),
                )
        elif target == PaymentStatus.UNPAID:
            if paid > ZERO:
                self.add_payment_row(
                    order, PaymentKind.REFUND, paid, method=method, note=MANUAL_CORRECTION_NOTE, user=user
                )
        elif target == PaymentStatus.PARTIALLY_PAID:
            if paid_amount is None:
                raise BusinessRuleError(
                    "paid_amount_required",
                    "Для статуса «Частично оплачен» укажите оплаченную сумму",
                )
            wanted = money(paid_amount)
            if wanted <= ZERO or wanted >= total:
                raise BusinessRuleError(
                    "invalid_paid_amount",
                    "Оплаченная сумма должна быть больше нуля и меньше суммы заказа",
                    total_amount=float(total),
                    paid_amount=float(wanted),
                )
            delta = money(wanted - paid)
            if delta > ZERO:
                self.add_payment_row(order, PaymentKind.PAYMENT, delta, method=method, user=user)
            elif delta < ZERO:
                self.add_payment_row(
                    order, PaymentKind.REFUND, -delta, method=method, note=MANUAL_CORRECTION_NOTE, user=user
                )
        elif target == PaymentStatus.REFUNDED:
            if paid > ZERO:
                self.add_payment_row(
                    order, PaymentKind.REFUND, paid, method=method, note=MANUAL_CORRECTION_NOTE, user=user
                )
            elif not has_refund:
                raise BusinessRuleError("nothing_to_refund", "По заказу нет оплаты для возврата")

        changes = self.recalculate(order)
        if changes:
            self._journal(order, changes, actor_type=actor_type, user=user, commit=commit)
        return changes

    def apply_total_change(
        self,
        order: Order,
        user: User | None = None,
        actor_type: ActorType | str = ActorType.USER,
        commit: bool = True,
    ) -> dict[str, list[Any]]:
        """03 §3: the item set changed the total → re-evaluate the payment status."""
        changes = self.recalculate(order)
        if changes:
            self._journal(
                order, changes, comment=TOTAL_CHANGED_COMMENT, actor_type=actor_type, user=user, commit=commit
            )
        return changes

    # ------------------------------------------------------------------ helpers

    def _journal(
        self,
        order: Order,
        changes: Mapping[str, Any],
        comment: str | None = None,
        actor_type: ActorType | str = ActorType.USER,
        user: User | None = None,
        commit: bool = True,
    ) -> None:
        record_order_event(
            self.db,
            order,
            EVENT_PAYMENT_CHANGED,
            changes,
            comment=comment,
            actor_type=actor_type,
            user=user,
        )
        if commit:
            self.db.commit()
        log_event(
            logger,
            "order.payment_changed",
            order_id=order.id,
            actor=ActorType(actor_type).value,
            actor_user_id=user.id if user is not None else None,
            payment_status=order.payment_status.value,
        )
