"""Shared pytest fixtures (docs/architecture/01-overview.md §5).

The environment is configured BEFORE the application is imported:

- ``APP_ENV=test``: ``.env`` files ignored, rate-limit storage in memory, Celery eager.
- ``DATABASE_URL``: ``TEST_DATABASE_URL`` if set (e.g. PostgreSQL), else in-memory SQLite
  (``StaticPool``, one shared connection).

Fixtures: ``db``, ``app``, ``client``, ``make_user``, ``admin_user``, ``operator_user``,
``admin_headers``, ``operator_headers``, ``make_auth_headers``, factories ``make_product``,
``make_customer``, ``make_order`` (rows inserted directly via the ORM; totals per 03 §1.4).
"""
# ruff: noqa: E402

import os

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or "sqlite+pysqlite:///:memory:"
os.environ["REDIS_URL"] = ""
os.environ["JWT_SECRET"] = "test-jwt-secret-0123456789abcdef0123456789abcdef"
os.environ["BUSINESS_TIMEZONE"] = "Asia/Dushanbe"
os.environ.setdefault("LOG_LEVEL", "WARNING")

import itertools
from collections.abc import Callable, Iterator, Sequence
from datetime import date, time
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings

get_settings.cache_clear()

from app.core.database import SessionLocal, engine, get_db
from app.core.rate_limit import limiter
from app.core.security import create_access_token, hash_password
from app.core.time import business_today, now_utc
from app.main import create_app
from app.models import Base, Customer, Delivery, Order, OrderItem, Payment, Product, User
from app.models.enums import DeliveryType, OrderSource, OrderStatus, PaymentKind, PaymentStatus, UserRole

ADMIN_PASSWORD = "admin-password-123"
OPERATOR_PASSWORD = "operator-password-123"
DEFAULT_PASSWORD = "password-123"

# 03-business-rules.md §4: statuses counted in revenue/production.
VALID_ORDER_STATUSES = frozenset(
    {
        OrderStatus.CONFIRMED,
        OrderStatus.PREPARING,
        OrderStatus.READY,
        OrderStatus.HANDED_TO_COURIER,
        OrderStatus.COMPLETED,
    }
)

_MONEY_QUANT = Decimal("0.01")
_UNSET: Any = object()


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


@lru_cache(maxsize=32)
def _password_hash_for(password: str) -> str:
    # Argon2 is intentionally slow; reuse hashes across tests.
    return hash_password(password)


def _clear_database() -> None:
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
            connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        else:
            for table in reversed(Base.metadata.sorted_tables):
                connection.execute(table.delete())


# --------------------------------------------------------------------------- infrastructure


@pytest.fixture(scope="session", autouse=True)
def _database_schema() -> Iterator[None]:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> Iterator[None]:
    limiter.reset()
    yield


@pytest.fixture
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        _clear_database()


@pytest.fixture(name="app")
def app_fixture(db: Session) -> FastAPI:
    application = create_app()

    def _override_get_db() -> Iterator[Session]:
        yield db

    application.dependency_overrides[get_db] = _override_get_db
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------- users & auth


@pytest.fixture
def make_user(db: Session) -> Callable[..., User]:
    counter = itertools.count(1)

    def _make(
        username: str | None = None,
        *,
        password: str = DEFAULT_PASSWORD,
        role: UserRole = UserRole.OPERATOR,
        is_active: bool = True,
        full_name: str | None = None,
    ) -> User:
        user = User(
            username=username or f"user{next(counter)}",
            full_name=full_name,
            password_hash=_password_hash_for(password),
            role=role,
            is_active=is_active,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    return _make


@pytest.fixture
def admin_user(make_user: Callable[..., User]) -> User:
    return make_user("admin", password=ADMIN_PASSWORD, role=UserRole.ADMIN, full_name="Администратор")


@pytest.fixture
def operator_user(make_user: Callable[..., User]) -> User:
    return make_user("operator", password=OPERATOR_PASSWORD, role=UserRole.OPERATOR, full_name="Оператор")


def _auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, role=user.role)}"}


@pytest.fixture
def make_auth_headers() -> Callable[[User], dict[str, str]]:
    return _auth_headers


@pytest.fixture
def admin_headers(admin_user: User) -> dict[str, str]:
    return _auth_headers(admin_user)


@pytest.fixture
def operator_headers(operator_user: User) -> dict[str, str]:
    return _auth_headers(operator_user)


# --------------------------------------------------------------------------- factories


@pytest.fixture
def make_product(db: Session) -> Callable[..., Product]:
    counter = itertools.count(1)

    def _make(name: str | None = None, price: Decimal | int | float | str = "100.00", **fields: Any) -> Product:
        product = Product(name=name or f"Товар {next(counter)}", price=money(price), **fields)
        db.add(product)
        db.commit()
        db.refresh(product)
        return product

    return _make


@pytest.fixture
def make_customer(db: Session) -> Callable[..., Customer]:
    counter = itertools.count(1)

    def _make(name: str | None = None, **fields: Any) -> Customer:
        customer = Customer(name=name if name is not None else f"Клиент {next(counter)}", **fields)
        db.add(customer)
        db.commit()
        db.refresh(customer)
        return customer

    return _make


def _payment_status_for(paid: Decimal, total: Decimal) -> PaymentStatus:
    """03-business-rules.md §3 (without refunds)."""
    if paid <= 0:
        return PaymentStatus.UNPAID
    if total > 0 and paid >= total:
        return PaymentStatus.PAID
    return PaymentStatus.PARTIALLY_PAID


def _build_order_item(spec: Any) -> OrderItem:
    """Item spec: ``Product`` | ``(product, quantity)`` | ``(product, quantity, unit_price)`` |
    ``{"product", "quantity", "unit_price"?, "comment"?, "product_name"?}``.

    ``unit_price`` defaults to the product price from the DB (snapshot, 03 §1.4).
    """
    unit_price: Any = None
    comment: str | None = None
    product_name: str | None = None
    if isinstance(spec, Product):
        product, quantity = spec, 1
    elif isinstance(spec, tuple):
        product, quantity = spec[0], spec[1]
        if len(spec) > 2:
            unit_price = spec[2]
    elif isinstance(spec, dict):
        product = spec.get("product")
        quantity = spec.get("quantity", 1)
        unit_price = spec.get("unit_price")
        comment = spec.get("comment")
        product_name = spec.get("product_name")
    else:
        raise TypeError(f"Unsupported order item spec: {spec!r}")

    if product is None and (unit_price is None or product_name is None):
        raise ValueError("An item without a product needs product_name and unit_price")
    price = money(unit_price if unit_price is not None else product.price)
    return OrderItem(
        product=product,
        product_name=product_name or product.name,
        quantity=quantity,
        unit_price=price,
        total_price=money(price * quantity),
        comment=comment,
    )


@pytest.fixture
def make_order(
    db: Session,
    make_customer: Callable[..., Customer],
    make_product: Callable[..., Product],
) -> Callable[..., Order]:
    def _make(
        customer: Customer | None = None,
        items: Sequence[Any] | None = None,
        status: OrderStatus = OrderStatus.NEW,
        *,
        source: OrderSource = OrderSource.ADMIN,
        delivery_type: DeliveryType | None = DeliveryType.PICKUP,
        delivery_date: date | None = _UNSET,
        delivery_time: time | None = time(12, 0),
        paid_amount: Decimal | int | float | str = 0,
        payment_status: PaymentStatus | None = None,
        delivery: dict[str, Any] | None = None,
        **fields: Any,
    ) -> Order:
        # Create dependencies first: their commits must not see a half-built order.
        order_customer = customer or make_customer()
        item_specs = list(items) if items is not None else [(make_product(), 1)]

        order = Order(
            customer=order_customer,
            source=source,
            status=status,
            delivery_type=delivery_type,
            delivery_date=business_today() if delivery_date is _UNSET else delivery_date,
            delivery_time=delivery_time,
            **fields,
        )
        db.add(order)
        with db.no_autoflush:
            for spec in item_specs:
                order.items.append(_build_order_item(spec))
            total = money(sum((item.total_price for item in order.items), Decimal("0")))
            order.total_amount = total

            now = now_utc()
            if status in VALID_ORDER_STATUSES and "confirmed_at" not in fields:
                order.confirmed_at = now
            if status == OrderStatus.COMPLETED and "completed_at" not in fields:
                order.completed_at = now
            if status == OrderStatus.CANCELLED and "cancelled_at" not in fields:
                order.cancelled_at = now

            paid = money(paid_amount)
            if paid > 0:
                order.payments.append(Payment(kind=PaymentKind.PAYMENT, amount=paid, paid_at=now))
            order.paid_amount = paid
            order.payment_status = payment_status or _payment_status_for(paid, total)

            if delivery is not None:
                delivery_row = Delivery(**{"address_raw": "Душанбе, ул. Рудаки, 1", **delivery})
                order.delivery = delivery_row
                order.delivery_address = delivery_row.address_formatted or delivery_row.address_raw
                order.delivery_latitude = delivery_row.latitude
                order.delivery_longitude = delivery_row.longitude

        db.commit()
        db.refresh(order)
        return order

    return _make
