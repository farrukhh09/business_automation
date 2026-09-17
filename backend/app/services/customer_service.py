"""Customers: profile, new/regular status, statistics (03-business-rules.md §2; 04-api.md §3).

Rules implemented here:

- a customer is **regular** (``is_new=False``) as soon as one of their orders is ``COMPLETED``;
  ``recalculate_status`` runs on every entry into and exit from ``COMPLETED`` (called by
  ``OrderService``), so reverting ``COMPLETED → READY`` makes the customer new again;
- ``last_order_at`` = max ``confirmed_at`` over the customer's non-cancelled orders;
- phones are normalized by ``app.services.phone`` — an unusable number is rejected (the bot asks
  again, staff get 422 ``invalid_phone``);
- an Instagram customer is created on the first incoming message
  (``get_or_create_by_instagram_id``, idempotent and safe against a concurrent webhook).

Transactions: the public entry points commit; ``sync_status``/``set_profile(commit=False)`` only
flush so ``OrderService`` keeps one commit per operation.
"""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessRuleError, ValidationError
from app.core.logging import get_logger, log_event
from app.core.time import ensure_utc
from app.models.customer import Customer
from app.models.enums import Language
from app.models.user import User
from app.repositories.conversations import ConversationRepository
from app.repositories.customers import CustomerRepository
from app.repositories.orders import OrderRepository
from app.schemas.common import Page
from app.schemas.customer import (
    CustomerCreate,
    CustomerDetail,
    CustomerListItem,
    CustomerUpdate,
    build_customer_detail,
    build_customer_list_item,
)
from app.schemas.order import OrderListItem, build_order_list_item
from app.services.phone import normalize_phone

logger = get_logger(__name__)

PROFILE_FIELDS: tuple[str, ...] = ("name", "phone", "username", "language", "notes", "is_blocked")
#: NOT NULL columns: an explicit ``null`` in a PATCH body means "leave unchanged", not "clear".
NON_NULLABLE_PROFILE_FIELDS: frozenset[str] = frozenset({"name", "language", "is_blocked"})


def _invalid_phone(value: str) -> BusinessRuleError:
    return BusinessRuleError(
        "invalid_phone",
        "Некорректный номер телефона: укажите 9 цифр или номер в международном формате",
        field="phone",
        value=value,
    )


class CustomerService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = CustomerRepository(db)
        self.orders = OrderRepository(db)
        self.conversations = ConversationRepository(db)

    # ------------------------------------------------------------------ reads

    def get(self, customer_id: int) -> Customer:
        return self.repository.get_or_raise(customer_id)

    def list(
        self,
        search: str | None = None,
        customer_type: str | None = None,
        sort: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[CustomerListItem]:
        rows, total = self.repository.search_with_stats(
            search=search, customer_type=customer_type, sort=sort, page=page, page_size=page_size
        )
        return Page[CustomerListItem](
            items=[
                build_customer_list_item(row.customer, row.orders_count, row.total_spent) for row in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

    def detail(self, customer_id: int) -> CustomerDetail:
        """``GET /customers/{id}``: stats over VALID orders + the full order history (newest first)."""
        customer = self.repository.get_or_raise(customer_id)
        orders_count, total_spent = self.repository.get_stats(customer.id)
        conversation = self.conversations.latest_for_customer(customer.id)
        orders: list[OrderListItem] = [
            build_order_list_item(order) for order in self.orders.list_for_customer(customer.id)
        ]
        return build_customer_detail(
            customer,
            orders_count=orders_count,
            total_spent=total_spent,
            conversation_id=conversation.id if conversation else None,
            orders=orders,
        )

    # ------------------------------------------------------------------ writes

    def create(self, data: CustomerCreate | Mapping[str, Any], user: User | None = None) -> Customer:
        """``POST /customers`` (STAFF). Commits."""
        payload = self._validate(CustomerCreate, data)
        customer = Customer(
            name=payload.name,
            username=payload.username,
            language=payload.language,
            notes=payload.notes,
            is_new=True,
        )
        if payload.phone:
            customer.phone = self._normalized_phone(payload.phone)
        self.repository.add(customer)
        self.db.commit()
        log_event(logger, "customer.created", customer_id=customer.id, user_id=user.id if user else None)
        return customer

    def update(
        self,
        customer_id: int,
        data: CustomerUpdate | Mapping[str, Any],
        user: User | None = None,
    ) -> Customer:
        """``PATCH /customers/{id}`` (STAFF): only the sent fields change. Commits."""
        customer = self.repository.get_or_raise(customer_id)
        payload = self._validate(CustomerUpdate, data)
        changes = self._apply_profile(customer, payload.model_dump(exclude_unset=True))
        if not changes:
            return customer
        self.db.flush()
        self.db.commit()
        log_event(
            logger,
            "customer.updated",
            customer_id=customer.id,
            fields=sorted(changes),
            user_id=user.id if user else None,
        )
        return customer

    def set_profile(
        self,
        customer: Customer,
        *,
        commit: bool = True,
        **fields: Any,
    ) -> dict[str, list[Any]]:
        """Profile update from the dialog (``name``/``phone``/``username``/``language``/``notes``).

        Returns the diff ``{"field": [old, new]}``; an unusable phone raises ``invalid_phone``.
        """
        unknown = sorted(set(fields) - set(PROFILE_FIELDS))
        if unknown:
            raise BusinessRuleError("unknown_customer_field", f"Неизвестные поля клиента: {', '.join(unknown)}")
        changes = self._apply_profile(customer, fields)
        if changes:
            self.db.flush()
            if commit:
                self.db.commit()
            log_event(logger, "customer.updated", customer_id=customer.id, fields=sorted(changes))
        return changes

    def get_or_create_by_instagram_id(
        self,
        instagram_user_id: str,
        name: str | None = None,
        username: str | None = None,
    ) -> Customer:
        """Customer of an Instagram thread (03 §2). Idempotent: the same IGSID returns the same row.

        A known customer only gets ``name``/``username`` filled in when they are still empty — the
        Graph API profile never overwrites what staff typed.
        """
        igsid = (instagram_user_id or "").strip()
        if not igsid:
            raise BusinessRuleError("instagram_user_id_required", "Не указан идентификатор клиента Instagram")

        customer = self.repository.get_by_instagram_id(igsid)
        if customer is not None:
            changes: dict[str, Any] = {}
            if name and not (customer.name or "").strip():
                changes["name"] = name.strip()
            if username and not (customer.username or "").strip():
                changes["username"] = username.strip()
            if changes:
                self._apply_profile(customer, changes)
                self.db.commit()
                log_event(logger, "customer.updated", customer_id=customer.id, fields=sorted(changes))
            return customer

        customer = Customer(
            instagram_user_id=igsid,
            name=(name or "").strip() or None,
            username=(username or "").strip() or None,
            language=Language.RU,
            is_new=True,
        )
        self.db.add(customer)
        try:
            self.db.commit()
        except IntegrityError:  # a concurrent webhook created the same customer first
            self.db.rollback()
            existing = self.repository.get_by_instagram_id(igsid)
            if existing is None:
                raise
            log_event(logger, "customer.create_conflict", instagram_user_id=igsid, customer_id=existing.id)
            return existing
        log_event(logger, "customer.created", customer_id=customer.id, source="instagram")
        return customer

    # ------------------------------------------------------------------ new / regular status

    def sync_status(self, customer: Customer) -> dict[str, list[Any]]:
        """Recompute ``is_new`` and ``last_order_at`` from the orders (flush only, no commit)."""
        changes: dict[str, list[Any]] = {}

        is_new = not self.orders.customer_has_completed_orders(customer.id)
        if customer.is_new != is_new:
            changes["is_new"] = [customer.is_new, is_new]
            customer.is_new = is_new

        confirmed_at = self.orders.max_confirmed_at_for_customer(customer.id)
        last_order_at = ensure_utc(confirmed_at) if confirmed_at is not None else None
        current = ensure_utc(customer.last_order_at) if customer.last_order_at is not None else None
        if current != last_order_at:
            changes["last_order_at"] = [
                current.isoformat() if current else None,
                last_order_at.isoformat() if last_order_at else None,
            ]
            customer.last_order_at = last_order_at

        if changes:
            self.db.flush()
            log_event(
                logger,
                "customer.status_changed",
                customer_id=customer.id,
                is_new=customer.is_new,
                fields=sorted(changes),
            )
        return changes

    def recalculate_status(self, customer_id: int) -> Customer:
        """03 §2: called on every entry into / exit from ``COMPLETED``. Commits."""
        customer = self.repository.get_or_raise(customer_id)
        if self.sync_status(customer):
            self.db.commit()
        return customer

    def stats(self, customer_id: int) -> tuple[int, Decimal]:
        """``(orders_count, total_spent)`` over VALID orders (03 §4)."""
        return self.repository.get_stats(customer_id)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _validate(schema: type, data: Any) -> Any:
        if isinstance(data, schema):
            return data
        try:
            return schema.model_validate(dict(data))
        except PydanticValidationError as exc:
            raise ValidationError(detail="Некорректные данные клиента") from exc

    def _normalized_phone(self, raw: str) -> str:
        normalized = normalize_phone(raw)
        if normalized is None:
            raise _invalid_phone(raw)
        return normalized

    def _apply_profile(self, customer: Customer, values: Mapping[str, Any]) -> dict[str, list[Any]]:
        """Set the given profile fields; returns ``{"field": [old, new]}`` for what really changed."""
        changes: dict[str, list[Any]] = {}
        for field in PROFILE_FIELDS:
            if field not in values:
                continue
            value = values[field]
            if isinstance(value, str):
                value = value.strip() or None
            if value is None and field in NON_NULLABLE_PROFILE_FIELDS:
                continue
            if field == "phone" and value is not None:
                value = self._normalized_phone(value)
            if field == "language" and value is not None:
                value = Language(value)
            old = getattr(customer, field)
            if old == value:
                continue
            setattr(customer, field, value)
            changes[field] = [old.value if isinstance(old, Language) else old, getattr(value, "value", value)]
        return changes
