"""Orders: lifecycle, item set, journal (03-business-rules.md §1; 04-api.md §5).

One service for both callers:

- the **admin panel** — ``create_admin_order`` / ``update_order`` / ``change_status`` /
  ``register_payment`` / ``cancel`` with the role limits of 04 §5;
- the **bot** — ``create_draft`` / ``set_items`` / ``update_fields`` / ``confirm`` / ``cancel``
  with ``ActorType.AI``; it may only touch drafts (NEW / WAITING_CONFIRMATION, 03 §1.5) and never
  decides on its own whether an order is confirmed (``classify_confirmation`` does, 03 §5).

Everything that changes an order writes an ``OrderEvent`` with a ``{"field": [old, new]}`` diff and
logs the matching domain event (``order.created``, ``order.updated``, ``order.status_changed``,
``order.payment_changed``, ``order.confirmed``, ``order.cancelled``).

Transactions: one commit per public operation. ``DeliveryService.request_geocoding`` is called
**after** the commit (the Celery task reads the delivery in its own session).
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, BusinessRuleError, PermissionDeniedError
from app.core.logging import get_logger, log_event
from app.core.time import now_utc
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.delivery import Delivery
from app.models.enums import (
    ActorType,
    DeliveryType,
    OrderSource,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    UserRole,
)
from app.models.order import Order
from app.models.product import Product
from app.models.user import User
from app.repositories.customers import CustomerRepository
from app.repositories.orders import OrderEventRepository, OrderRepository
from app.repositories.products import ProductRepository
from app.schemas.common import Page
from app.schemas.delivery import DeliveryIn
from app.schemas.order import (
    OrderCreate,
    OrderDetail,
    OrderEventOut,
    OrderListItem,
    OrderUpdate,
    PaymentCreate,
    build_order_detail,
    build_order_event_out,
    build_order_list_item,
)
from app.schemas.settings import BusinessSettings
from app.services import order_rules
from app.services.constants import DRAFT_ORDER_STATUSES
from app.services.customer_service import CustomerService
from app.services.delivery_service import DeliveryService
from app.services.order_pricing import ADD, REPLACE, ZERO, ItemSpec, OrderPricing, items_text, money
from app.services.order_validator import OrderValidator
from app.services.payment_service import (
    EVENT_CANCELLED,
    EVENT_CONFIRMED,
    EVENT_CREATED,
    EVENT_DELIVERY_CHANGED,
    EVENT_ITEMS_CHANGED,
    EVENT_STATUS_CHANGED,
    EVENT_UPDATED,
    PaymentService,
    record_order_event,
)
from app.services.settings_service import SettingsService

logger = get_logger(__name__)

#: Scalar order fields an update may set (the amounts are never accepted, 03 §1.4).
ORDER_FIELDS: tuple[str, ...] = (
    "delivery_type",
    "delivery_date",
    "delivery_time",
    "comment",
    "payment_method",
)
#: 04 §5: an OPERATOR may not touch these through ``PATCH /orders/{id}``.
OPERATOR_FORBIDDEN_FIELDS: frozenset[str] = frozenset(
    {"items", "delivery_date", "delivery_time", "delivery_type", "customer_id"}
)
#: Actors that are not staff: the bot may only change drafts (03 §1.5).
BOT_ACTOR_TYPES: frozenset[ActorType] = frozenset({ActorType.AI, ActorType.CUSTOMER})
#: Statuses the bot may still cancel from (later ones need an operator, 03 §1.5).
BOT_CANCELLABLE_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.NEW, OrderStatus.WAITING_CONFIRMATION, OrderStatus.CONFIRMED}
)
CHANGE_REQUEST_PREFIX = "Клиент просит изменить: "


def resolve_actor(
    actor: ActorType | User | str | None,
    user: User | None = None,
) -> tuple[ActorType, User | None]:
    """``actor`` may be a ``User`` (staff), an ``ActorType`` or ``None``."""
    if isinstance(actor, User):
        return ActorType.USER, actor
    if actor is None:
        return (ActorType.USER, user) if user is not None else (ActorType.SYSTEM, None)
    return ActorType(actor), user


def _coerce_date(value: Any) -> date | None:
    if value is None or isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise BadRequestError("Некорректная дата доставки: ожидается ГГГГ-ММ-ДД") from exc


def _coerce_time(value: Any) -> time | None:
    if value is None:
        return value
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    text = str(value).strip()
    try:
        parsed = time.fromisoformat(text)
    except ValueError as exc:
        raise BadRequestError("Некорректное время доставки: ожидается ЧЧ:ММ") from exc
    return parsed.replace(second=0, microsecond=0)


def _coerce_field(field: str, value: Any) -> Any:
    if value is None:
        return None
    if field == "delivery_type":
        try:
            return DeliveryType(value)
        except ValueError as exc:
            raise BadRequestError("Недопустимый тип получения заказа") from exc
    if field == "payment_method":
        try:
            return PaymentMethod(value)
        except ValueError as exc:
            raise BadRequestError("Недопустимый способ оплаты") from exc
    if field == "delivery_date":
        return _coerce_date(value)
    if field == "delivery_time":
        return _coerce_time(value)
    if isinstance(value, str):
        return value.strip() or None
    return value


class OrderService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.orders = OrderRepository(db)
        self.event_repository = OrderEventRepository(db)
        self.products = ProductRepository(db)
        self.customers = CustomerRepository(db)
        self.customer_service = CustomerService(db)
        self.payments = PaymentService(db)
        self.deliveries = DeliveryService(db)

    # ------------------------------------------------------------------ reads

    def get(self, order_id: int) -> Order:
        """Order with items, customer, delivery, payments and events loaded (404 when missing)."""
        return self.orders.get_detail_or_raise(order_id)

    def missing_fields(self, order: Order) -> list[str]:
        """03 §1.3, in the order the bot asks about them."""
        return OrderValidator.missing_fields(order)

    def timing_problems(
        self,
        order: Order,
        business_settings: BusinessSettings | None = None,
        now: datetime | None = None,
    ) -> list[str]:
        """03 §1.3 lead time / horizon. Never blocks a manual order — the bot decides what to say."""
        settings = business_settings or SettingsService(self.db).get()
        return OrderValidator.timing_problems(order, settings, now)

    def allowed_transitions(
        self,
        order: Order,
        user: User | None = None,
        actor_type: ActorType | str = ActorType.USER,
    ) -> list[OrderStatus]:
        return order_rules.allowed_transitions(
            order, role=user.role if user is not None else None, actor_type=actor_type
        )

    def detail(self, order: Order, user: User | None = None) -> OrderDetail:
        """``OrderDetail`` with ``allowed_transitions`` (role + delivery type) and ``missing_fields``."""
        return build_order_detail(
            order,
            allowed_transitions=self.allowed_transitions(order, user),
            missing_fields=self.missing_fields(order),
        )

    def get_detail(self, order_id: int, user: User | None = None) -> OrderDetail:
        return self.detail(self.get(order_id), user)

    def list_orders(
        self,
        *,
        statuses: Sequence[OrderStatus | str] | None = None,
        payment_status: PaymentStatus | str | None = None,
        delivery_type: DeliveryType | str | None = None,
        customer_id: int | None = None,
        delivery_date: date | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        search: str | None = None,
        sort: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[OrderListItem]:
        orders, total = self.orders.list_filtered(
            statuses=statuses,
            payment_status=payment_status,
            delivery_type=delivery_type,
            customer_id=customer_id,
            delivery_date=delivery_date,
            date_from=date_from,
            date_to=date_to,
            search=search,
            sort=sort,
            page=page,
            page_size=page_size,
        )
        return Page[OrderListItem](
            items=[build_order_list_item(order) for order in orders],
            total=total,
            page=page,
            page_size=page_size,
        )

    def list_events(self, order_id: int) -> list[OrderEventOut]:
        """``GET /orders/{id}/events`` — the change journal, oldest first."""
        self.orders.get_or_raise(order_id)
        return [build_order_event_out(event) for event in self.event_repository.list_for_order(order_id)]

    # ------------------------------------------------------------------ drafts (bot)

    def create_draft(
        self,
        customer: Customer,
        conversation: Conversation | int | None = None,
        source: OrderSource | str = OrderSource.INSTAGRAM,
        actor: ActorType | User | str | None = None,
    ) -> Order:
        """Empty NEW order the bot fills in step by step (05-ai.md §5.7). Commits."""
        order_source = OrderSource(source)
        if actor is None:
            actor = ActorType.USER if order_source == OrderSource.ADMIN else ActorType.AI
        actor_type, user = resolve_actor(actor)
        conversation_id = conversation.id if isinstance(conversation, Conversation) else conversation

        order = Order(
            customer=customer,
            conversation_id=conversation_id,
            source=order_source,
            status=OrderStatus.NEW,
            payment_status=PaymentStatus.UNPAID,
            total_amount=ZERO,
            paid_amount=ZERO,
        )
        self.db.add(order)
        self.db.flush()
        record_order_event(
            self.db,
            order,
            EVENT_CREATED,
            {"status": [None, OrderStatus.NEW], "source": [None, order_source]},
            actor_type=actor_type,
            user=user,
        )
        self.db.commit()
        self._log("order.created", order, actor_type, user, source=order_source.value)
        return order

    def set_items(
        self,
        order: Order,
        items: Iterable[Any],
        mode: str = ADD,
        actor: ActorType | User | str | None = None,
        keep_prices: bool = True,
        user: User | None = None,
    ) -> Order:
        """Change the item set (``add`` / ``replace`` / ``remove``). Prices come from the DB (03 §1.4).

        ``keep_prices`` (default) lets positions that were already in the order keep their
        ``unit_price`` when the set is replaced.
        """
        actor_type, actor_user = resolve_actor(actor, user)
        self._ensure_editable(order, actor_type)
        changes = self._apply_items(order, items, mode, keep_prices)
        if not changes:
            return order
        record_order_event(
            self.db, order, EVENT_ITEMS_CHANGED, changes, actor_type=actor_type, user=actor_user
        )
        self._refresh_draft_status(order, actor_type, actor_user)
        self.payments.apply_total_change(order, user=actor_user, actor_type=actor_type, commit=False)
        self.db.commit()
        self._log("order.updated", order, actor_type, actor_user, fields=sorted(changes))
        return order

    def update_fields(
        self,
        order: Order,
        actor: ActorType | User | str | None = None,
        user: User | None = None,
        request_geocoding: bool = True,
        **fields: Any,
    ) -> Order:
        """Set order fields (``delivery_type``, ``delivery_date``, ``delivery_time``, ``comment``,
        ``payment_method``) and/or the ``delivery`` block. Commits.

        Switching to ``PICKUP`` removes the delivery; an address without coordinates schedules
        geocoding after the commit (03 §7). The bot passes ``request_geocoding=False`` because it
        geocodes synchronously to answer the customer in the same turn (05 §5.7.3).
        """
        actor_type, actor_user = resolve_actor(actor, user)
        self._ensure_editable(order, actor_type)
        unknown = sorted(set(fields) - set(ORDER_FIELDS) - {"delivery"})
        if unknown:
            raise BadRequestError(f"Недопустимые поля заказа: {', '.join(unknown)}")

        previous_type = order.delivery_type
        changes = self._apply_scalar_fields(order, fields)
        if changes:
            record_order_event(self.db, order, EVENT_UPDATED, changes, actor_type=actor_type, user=actor_user)
        delivery, delivery_changes = self._apply_delivery(
            order, fields.get("delivery"), previous_type, actor_user
        )
        if delivery_changes:
            record_order_event(
                self.db, order, EVENT_DELIVERY_CHANGED, delivery_changes, actor_type=actor_type, user=actor_user
            )
        if not changes and not delivery_changes:
            return order
        self._refresh_draft_status(order, actor_type, actor_user)
        self.db.commit()
        if request_geocoding:
            self._request_geocoding(delivery)
        self._log(
            "order.updated",
            order,
            actor_type,
            actor_user,
            fields=sorted(set(changes) | {f"delivery.{field}" for field in delivery_changes}),
        )
        return order

    def update_draft(
        self,
        order: Order,
        items: Iterable[Any] | None = None,
        items_mode: str = ADD,
        actor: ActorType | User | str | None = None,
        request_geocoding: bool = True,
        **fields: Any,
    ) -> Order:
        """Items + fields in one bot step (05-ai.md §5.7): items first, then the other fields."""
        if items is not None:
            self.set_items(order, items, items_mode, actor)
        if fields:
            self.update_fields(order, actor, request_geocoding=request_geocoding, **fields)
        return order

    def record_change_request(
        self,
        order: Order,
        text: str,
        actor_type: ActorType | str = ActorType.AI,
        user: User | None = None,
    ) -> Order:
        """03 §1.5: a confirmed order is never changed by the bot — the request is journaled instead
        (the dialog is handed over to an operator by ``DialogService``)."""
        record_order_event(
            self.db,
            order,
            EVENT_UPDATED,
            {},
            comment=f"{CHANGE_REQUEST_PREFIX}{(text or '').strip()}",
            actor_type=actor_type,
            user=user,
        )
        self.db.commit()
        self._log("order.updated", order, ActorType(actor_type), user, change_request=True)
        return order

    def record_receipt(self, order: Order, note: str, actor_type: ActorType | str = ActorType.AI) -> Order:
        """03 §3: a payment receipt the customer sent is journaled for the operator ("Чек из Instagram: …")."""
        record_order_event(self.db, order, EVENT_UPDATED, {}, comment=(note or "").strip(), actor_type=actor_type)
        self.db.commit()
        self._log("order.receipt_received", order, ActorType(actor_type), None)
        return order

    # ------------------------------------------------------------------ admin API

    def create_admin_order(self, data: OrderCreate, user: User) -> Order:
        """``POST /orders`` (ADMIN). ``confirm=true`` → CONFIRMED when complete, otherwise NEW.

        Timing checks (03 §1.3) never block a manual order.
        """
        customer = self.customers.get_or_raise(data.customer_id)
        specs = [ItemSpec.coerce(item) for item in data.items]
        products = self._resolve_products(specs)

        order = Order(
            customer=customer,
            source=OrderSource.ADMIN,
            status=OrderStatus.NEW,
            payment_status=PaymentStatus.UNPAID,
            total_amount=ZERO,
            paid_amount=ZERO,
            delivery_type=data.delivery_type,
            delivery_date=data.delivery_date,
            delivery_time=data.delivery_time,
            comment=data.comment,
            payment_method=data.payment_method,
        )
        self.db.add(order)
        self.db.flush()

        OrderPricing.apply(order, specs, products, mode=REPLACE, keep_prices=False)
        delivery: Delivery | None = None
        if data.delivery is not None:
            delivery = self.deliveries.upsert_for_order(order, data.delivery, actor_user=user)
        self.db.flush()

        record_order_event(
            self.db,
            order,
            EVENT_CREATED,
            {
                "status": [None, order.status],
                "source": [None, order.source],
                "items": [None, items_text(order.items)],
                "total_amount": [None, order.total_amount],
                "delivery_type": [None, order.delivery_type],
                "delivery_date": [None, order.delivery_date],
                "delivery_time": [None, order.delivery_time],
            },
            actor_type=ActorType.USER,
            user=user,
        )
        if data.confirm and not self.missing_fields(order):
            self._change_status(order, OrderStatus.CONFIRMED, ActorType.USER, user, commit=False)
        self.db.commit()
        self._request_geocoding(delivery)
        self._log("order.created", order, ActorType.USER, user, source=OrderSource.ADMIN.value)
        return order

    def update_order(self, order_id: int, data: OrderUpdate, user: User) -> Order:
        """``PATCH /orders/{id}``. OPERATOR may only send the fields of 04 §5, else 403."""
        order = self.get(order_id)
        values = data.model_dump(exclude_unset=True)
        if user.role == UserRole.OPERATOR:
            forbidden = sorted(set(values) & OPERATOR_FORBIDDEN_FIELDS)
            if forbidden:
                raise PermissionDeniedError(
                    "Оператор не может изменять эти поля заказа: " + ", ".join(forbidden),
                    fields=forbidden,
                )

        previous_type = order.delivery_type
        touched: set[str] = set()

        field_changes = self._apply_scalar_fields(order, values)
        if field_changes:
            record_order_event(self.db, order, EVENT_UPDATED, field_changes, actor_type=ActorType.USER, user=user)
            touched |= set(field_changes)

        if values.get("items") is not None:
            item_changes = self._apply_items(order, data.items or [], REPLACE, keep_prices=True)
            if item_changes:
                record_order_event(
                    self.db, order, EVENT_ITEMS_CHANGED, item_changes, actor_type=ActorType.USER, user=user
                )
                touched |= set(item_changes)

        delivery, delivery_changes = self._apply_delivery(order, values.get("delivery"), previous_type, user)
        if delivery_changes:
            record_order_event(
                self.db, order, EVENT_DELIVERY_CHANGED, delivery_changes, actor_type=ActorType.USER, user=user
            )
            touched |= {f"delivery.{field}" for field in delivery_changes}

        self._refresh_draft_status(order, ActorType.USER, user)
        if "items" in touched:
            self.payments.apply_total_change(order, user=user, commit=False)
        if self._apply_payment(order, values, user):
            touched |= {"payment_status", "paid_amount"} & set(values)

        if values.get("status") is not None:
            self._change_status(order, data.status, ActorType.USER, user, commit=False)
            touched.add("status")

        self.db.commit()
        self._request_geocoding(delivery)
        if touched:
            self._log("order.updated", order, ActorType.USER, user, fields=sorted(touched))
        return order

    def register_payment(
        self,
        order_id: int,
        data: PaymentCreate | Mapping[str, Any],
        user: User | None = None,
        actor_type: ActorType | str = ActorType.USER,
    ) -> Order:
        """``POST /orders/{id}/payments`` — one ledger row + recalculation (03 §3). Commits."""
        order = self.get(order_id)
        payload = data if isinstance(data, PaymentCreate) else PaymentCreate.model_validate(dict(data))
        self.payments.register_payment(
            order,
            payload.kind,
            payload.amount,
            method=payload.method,
            note=payload.note,
            user=user,
            actor_type=actor_type,
            commit=True,
        )
        return order

    # ------------------------------------------------------------------ status

    def change_status(
        self,
        order: Order,
        new_status: OrderStatus | str,
        actor_type: ActorType | str = ActorType.USER,
        user: User | None = None,
        comment: str | None = None,
    ) -> Order:
        """Status transition with all the rules of 03 §1.2. Commits.

        Setting the status the order already has is a no-op (no event, no error).
        """
        self._change_status(order, new_status, actor_type, user, comment=comment, commit=True)
        return order

    def confirm(
        self,
        order: Order,
        actor_type: ActorType | str = ActorType.USER,
        user: User | None = None,
        comment: str | None = None,
    ) -> Order:
        """``→ CONFIRMED``: requires complete data; snapshots ``is_repeat_customer`` (03 §2)."""
        return self.change_status(order, OrderStatus.CONFIRMED, actor_type, user, comment)

    def cancel(
        self,
        order: Order,
        reason: str | None = None,
        actor_type: ActorType | str = ActorType.USER,
        user: User | None = None,
    ) -> Order:
        """``→ CANCELLED`` with a reason. The bot may only cancel up to CONFIRMED (03 §1.5)."""
        actor = ActorType(actor_type)
        if actor in BOT_ACTOR_TYPES and order.status not in BOT_CANCELLABLE_STATUSES:
            raise BusinessRuleError(
                "cancel_requires_operator",
                "Отменить заказ в производстве может только сотрудник",
                order_id=order.id,
                status=order.status.value,
            )
        text = (reason or "").strip() or None
        if text and order.cancel_reason != text:
            order.cancel_reason = text
        self._change_status(order, OrderStatus.CANCELLED, actor, user, comment=text, commit=True)
        return order

    # ------------------------------------------------------------------ internals

    def _change_status(
        self,
        order: Order,
        new_status: OrderStatus | str,
        actor_type: ActorType | str,
        user: User | None,
        comment: str | None = None,
        commit: bool = True,
    ) -> bool:
        try:
            target = OrderStatus(new_status)
        except ValueError as exc:
            raise BadRequestError("Недопустимый статус заказа") from exc
        actor = ActorType(actor_type)
        if order.status == target:
            return False

        order_rules.ensure_transition(
            order, target, actor_type=actor, role=user.role if user is not None else None
        )
        if order_rules.requires_completeness(target):
            missing = self.missing_fields(order)
            if missing:
                raise BusinessRuleError(
                    "order_incomplete",
                    "Не заполнены обязательные данные заказа",
                    missing=missing,
                )

        previous = order.status
        moment = now_utc()
        order.status = target
        if target == OrderStatus.CONFIRMED and order.confirmed_at is None:
            order.confirmed_at = moment
            # 03 §2: snapshot taken at the first confirmation, used by the statistics.
            order.is_repeat_customer = not order.customer.is_new
        if target == OrderStatus.COMPLETED:
            order.completed_at = order.completed_at or moment
        elif previous == OrderStatus.COMPLETED:
            order.completed_at = None  # the order was reverted: it is not completed any more
        if target == OrderStatus.CANCELLED:
            order.cancelled_at = order.cancelled_at or moment
        self.db.flush()

        # 03 §2: entering/leaving COMPLETED and CONFIRMED changes is_new / last_order_at.
        self.customer_service.sync_status(order.customer)

        event_type = EVENT_STATUS_CHANGED
        if target == OrderStatus.CONFIRMED:
            event_type = EVENT_CONFIRMED
        elif target == OrderStatus.CANCELLED:
            event_type = EVENT_CANCELLED
        record_order_event(
            self.db,
            order,
            event_type,
            {"status": [previous, target]},
            comment=comment,
            actor_type=actor,
            user=user,
        )
        if commit:
            self.db.commit()

        self._log(
            "order.status_changed", order, actor, user, old_status=previous.value, new_status=target.value
        )
        if target == OrderStatus.CONFIRMED:
            self._log("order.confirmed", order, actor, user, is_repeat_customer=order.is_repeat_customer)
        elif target == OrderStatus.CANCELLED:
            self._log("order.cancelled", order, actor, user)
        return True

    def _refresh_draft_status(self, order: Order, actor_type: ActorType, user: User | None) -> None:
        """03 §1.5: a draft that lost required data goes back to NEW (confirmation is asked again)."""
        if order.status != OrderStatus.WAITING_CONFIRMATION or not self.missing_fields(order):
            return
        order.status = OrderStatus.NEW
        self.db.flush()
        record_order_event(
            self.db,
            order,
            EVENT_STATUS_CHANGED,
            {"status": [OrderStatus.WAITING_CONFIRMATION, OrderStatus.NEW]},
            actor_type=actor_type,
            user=user,
        )
        self._log(
            "order.status_changed",
            order,
            actor_type,
            user,
            old_status=OrderStatus.WAITING_CONFIRMATION.value,
            new_status=OrderStatus.NEW.value,
        )

    def _ensure_editable(self, order: Order, actor_type: ActorType) -> None:
        """03 §1.5: the bot only edits drafts; a confirmed order is changed by staff."""
        if actor_type in BOT_ACTOR_TYPES and order.status not in DRAFT_ORDER_STATUSES:
            raise BusinessRuleError(
                "order_not_editable",
                "Заказ уже подтверждён — изменения вносит менеджер",
                order_id=order.id,
                status=order.status.value,
            )

    def _resolve_products(self, specs: Sequence[ItemSpec]) -> dict[int, Product]:
        """Every ``product_id`` must exist, be active and not deleted (03 §1.3)."""
        wanted = {spec.product_id for spec in specs}
        found = self.products.get_many(wanted)
        unavailable = sorted(
            product_id
            for product_id in wanted
            if product_id not in found
            or not found[product_id].is_active
            or found[product_id].deleted_at is not None
        )
        if unavailable:
            raise BusinessRuleError(
                "product_unavailable",
                "Товар недоступен для заказа: " + ", ".join(str(product_id) for product_id in unavailable),
                product_ids=unavailable,
            )
        return found

    def _apply_items(
        self,
        order: Order,
        items: Iterable[Any],
        mode: str,
        keep_prices: bool,
    ) -> dict[str, list[Any]]:
        specs = [ItemSpec.coerce(item) for item in items]
        products: dict[int, Product] = {} if mode == "remove" else self._resolve_products(specs)
        before_items = items_text(order.items)
        before_total = money(order.total_amount)
        if not OrderPricing.apply(order, specs, products, mode=mode, keep_prices=keep_prices):
            return {}
        self.db.flush()
        changes: dict[str, list[Any]] = {"items": [before_items, items_text(order.items)]}
        if money(order.total_amount) != before_total:
            changes["total_amount"] = [before_total, money(order.total_amount)]
        return changes

    def _apply_scalar_fields(self, order: Order, values: Mapping[str, Any]) -> dict[str, list[Any]]:
        changes: dict[str, list[Any]] = {}
        for field in ORDER_FIELDS:
            if field not in values:
                continue
            value = _coerce_field(field, values[field])
            old = getattr(order, field)
            if old == value:
                continue
            setattr(order, field, value)
            changes[field] = [old, value]
        if changes:
            self.db.flush()
        return changes

    def _apply_delivery(
        self,
        order: Order,
        data: DeliveryIn | Mapping[str, Any] | None,
        previous_type: DeliveryType | None,
        user: User | None,
    ) -> tuple[Delivery | None, dict[str, list[Any]]]:
        """Delivery block of the order (03 §7): upsert for DELIVERY, removal on the switch to PICKUP."""
        if order.delivery_type != DeliveryType.DELIVERY:
            if data is not None and order.delivery_type is not None:
                raise BusinessRuleError(
                    "delivery_not_applicable",
                    "Адрес доставки можно указать только для заказа с доставкой",
                )
            if previous_type == DeliveryType.DELIVERY and order.delivery is not None:
                address = order.delivery.address_raw
                self.deliveries.remove_for_order(order)
                return None, {"delivery": [address, None]}
            return None, {}
        if data is None:
            return order.delivery, {}
        delivery = self.deliveries.upsert_for_order(order, data, actor_user=user)
        return delivery, dict(self.deliveries.last_changes)

    def _apply_payment(self, order: Order, values: Mapping[str, Any], user: User) -> bool:
        """``payment_status`` / ``paid_amount`` of ``PATCH /orders/{id}`` (03 §3)."""
        if "payment_status" not in values and "paid_amount" not in values:
            return False
        status = values.get("payment_status")
        amount: Decimal | None = None
        if values.get("paid_amount") is not None:
            amount = money(values["paid_amount"])
        if status is None:
            if amount is None:
                return False
            total = money(order.total_amount)
            if amount <= ZERO:
                status = PaymentStatus.UNPAID
            elif total > ZERO and amount >= total:
                status = PaymentStatus.PAID
            else:
                status = PaymentStatus.PARTIALLY_PAID
        return bool(
            self.payments.set_status(
                order,
                status,
                paid_amount=amount,
                method=order.payment_method,
                user=user,
                commit=False,
            )
        )

    def _request_geocoding(self, delivery: Delivery | None) -> None:
        """Called after the commit: the Celery task reads the delivery in another session (03 §7)."""
        if delivery is not None and DeliveryService.needs_geocoding(delivery):
            self.deliveries.request_geocoding(delivery)

    def _log(
        self,
        event: str,
        order: Order,
        actor_type: ActorType,
        user: User | None,
        **fields: Any,
    ) -> None:
        log_event(
            logger,
            event,
            order_id=order.id,
            actor=ActorType(actor_type).value,
            actor_user_id=user.id if user is not None else None,
            **fields,
        )
