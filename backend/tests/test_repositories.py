"""Repositories (01-overview.md §3): filters, pagination totals, aggregates, eager loading, no commit."""

import importlib
import inspect
import pkgutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

import app.repositories as repositories_package
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.time import business_today, now_utc
from app.models import (
    AppSetting,
    Conversation,
    Customer,
    DailyReport,
    Delivery,
    FaqItem,
    LocationRequest,
    Message,
    Order,
    OrderEvent,
    Payment,
    Product,
    RefreshToken,
    RoutePlan,
    RouteStop,
    User,
)
from app.models.enums import (
    ActorType,
    ConversationMode,
    DeliveryStatus,
    DeliveryType,
    GeocodeStatus,
    MessageDirection,
    MessageSender,
    MessageType,
    OrderStatus,
    PaymentKind,
    PaymentStatus,
    UserRole,
)
from app.repositories import (
    AppSettingRepository,
    ConversationRepository,
    CustomerRepository,
    DailyReportRepository,
    DeliveryRepository,
    FaqRepository,
    LocationRequestRepository,
    MessageRepository,
    OrderEventRepository,
    OrderRepository,
    PaymentRepository,
    ProductRepository,
    RefreshTokenRepository,
    RoutePlanRepository,
    UserRepository,
)
from app.schemas.order import CustomerBrief, OrderListItem, build_order_list_item
from app.services.constants import (
    ACTIVE_ORDER_STATUSES,
    DRAFT_ORDER_STATUSES,
    VALID_ORDER_STATUSES,
)

MakeOrder = Callable[..., Order]
MakeCustomer = Callable[..., Customer]
MakeProduct = Callable[..., Product]
MakeUser = Callable[..., User]


@contextmanager
def count_queries(db: Session) -> Iterator[list[str]]:
    statements: list[str] = []
    engine = db.get_bind()

    def _record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _record)


@pytest.fixture
def make_conversation(db: Session, make_customer: MakeCustomer) -> Callable[..., Conversation]:
    def _make(customer: Customer | None = None, **fields: Any) -> Conversation:
        conversation = Conversation(customer=customer or make_customer(), **fields)
        db.add(conversation)
        db.commit()
        return conversation

    return _make


def _message(conversation: Conversation, text: str, **fields: Any) -> Message:
    return Message(
        conversation=conversation,
        direction=fields.pop("direction", MessageDirection.INCOMING),
        message_type=MessageType.TEXT,
        sender=fields.pop("sender", MessageSender.CUSTOMER),
        text=text,
        **fields,
    )


# --------------------------------------------------------------------------- constants & code health


def test_status_groups() -> None:
    assert {status.value for status in VALID_ORDER_STATUSES} == {
        "CONFIRMED",
        "PREPARING",
        "READY",
        "HANDED_TO_COURIER",
        "COMPLETED",
    }
    assert {status.value for status in DRAFT_ORDER_STATUSES} == {"NEW", "WAITING_CONFIRMATION"}
    assert set(OrderStatus) - ACTIVE_ORDER_STATUSES == {OrderStatus.COMPLETED, OrderStatus.CANCELLED}


def test_repository_annotations_evaluate() -> None:
    """A method named ``list`` shadows the builtin in later annotations of the class (breaks on 3.12)."""
    for module_info in pkgutil.iter_modules(repositories_package.__path__):
        module = importlib.import_module(f"app.repositories.{module_info.name}")
        for cls in vars(module).values():
            if not (inspect.isclass(cls) and cls.__module__ == module.__name__):
                continue
            for attribute in vars(cls).values():
                function = attribute.__func__ if isinstance(attribute, (staticmethod, classmethod)) else attribute
                if inspect.isfunction(function):
                    assert isinstance(function.__annotations__, dict)


def test_repositories_never_commit() -> None:
    """01-overview.md §3: repositories select/add/flush; only services commit."""
    for module_info in pkgutil.iter_modules(repositories_package.__path__):
        module = importlib.import_module(f"app.repositories.{module_info.name}")
        assert ".commit(" not in inspect.getsource(module), module_info.name


def test_list_filters_reject_unknown_enum_values(
    db: Session, make_order: MakeOrder, make_conversation: Callable[..., Conversation]
) -> None:
    """A query value that is not a member of the enum is a 400, never an unhandled ValueError."""
    make_order()
    make_conversation()
    orders = OrderRepository(db)
    deliveries = DeliveryRepository(db)
    today = business_today()

    with pytest.raises(BadRequestError) as exc_info:
        orders.list_filtered(statuses=[OrderStatus.NEW, "ГОТОВ"])
    assert (exc_info.value.status_code, exc_info.value.code) == (400, "bad_request")
    assert exc_info.value.detail == "Недопустимый статус заказа"

    for call in (
        lambda: orders.list_filtered(payment_status="OVERPAID"),
        lambda: orders.list_filtered(delivery_type="TELEPORT"),
        lambda: deliveries.list_for_date(today, status="FLYING"),
        lambda: deliveries.list_for_date(today, geocode_status="MAYBE"),
        lambda: deliveries.list_by_status("FLYING"),
        lambda: ConversationRepository(db).list_filtered(mode="ROBOT"),
    ):
        with pytest.raises(BadRequestError):
            call()


# --------------------------------------------------------------------------- base repository


def test_base_get_and_get_or_raise(db: Session, make_product: MakeProduct) -> None:
    product = make_product("Медовик")
    repository = ProductRepository(db)

    assert repository.get(product.id) is product
    assert repository.get(None) is None
    assert repository.get(10_000) is None
    with pytest.raises(NotFoundError) as exc_info:
        repository.get_or_raise(10_000)
    assert exc_info.value.detail == "Товар не найден"
    assert exc_info.value.code == "not_found"
    with pytest.raises(NotFoundError, match="Нет такого"):
        repository.get_or_raise(10_000, detail="Нет такого")


def test_base_add_flushes_but_does_not_commit(db: Session) -> None:
    repository = ProductRepository(db)

    product = repository.add(Product(name="Чизкейк", price=Decimal("120.00")))
    assert product.id is not None
    assert repository.get(product.id) is product

    db.rollback()
    assert db.scalars(select(Product)).all() == []


def test_base_add_all_and_delete(db: Session) -> None:
    repository = FaqRepository(db)
    first, second = repository.add_all([FaqItem(question="Q1", answer="A1"), FaqItem(question="Q2", answer="A2")])
    db.commit()

    repository.delete(first)
    db.commit()

    assert [item.id for item in repository.list(include_inactive=True)] == [second.id]


def test_base_paginate_totals(db: Session, make_product: MakeProduct) -> None:
    for index in range(5):
        make_product(f"Товар {index}")
    repository = ProductRepository(db)
    stmt = select(Product).order_by(Product.id)

    page1, total1 = repository.paginate(stmt, 1, 2)
    page3, total3 = repository.paginate(stmt, 3, 2)
    beyond, total_beyond = repository.paginate(stmt, 10, 2)
    rows, total_rows = repository.paginate(select(Product.id, Product.name).order_by(Product.id), 2, 3)

    assert (len(page1), total1) == (2, 5)
    assert [product.name for product in page1] == ["Товар 0", "Товар 1"]
    assert (len(page3), total3) == (1, 5)
    assert (beyond, total_beyond) == ([], 5)
    assert total_rows == 5
    assert [row.name for row in rows] == ["Товар 3", "Товар 4"]
    assert repository.paginate(select(Product).where(Product.id < 0), 1, 20) == ([], 0)


# --------------------------------------------------------------------------- users & tokens


def test_user_repository(db: Session, make_user: MakeUser) -> None:
    operator = make_user("operator")
    admin = make_user("admin", role=UserRole.ADMIN)
    make_user("old-admin", role=UserRole.ADMIN, is_active=False)
    repository = UserRepository(db)

    assert repository.get_by_username("admin") is admin
    assert repository.get_by_username(" operator ") is operator
    assert repository.get_by_username("ADMIN") is None
    assert [user.username for user in repository.list_all()] == ["operator", "admin", "old-admin"]
    assert repository.count_active_admins() == 1


def test_refresh_token_repository(db: Session, make_user: MakeUser) -> None:
    user = make_user("admin")
    other = make_user("other")
    now = now_utc()
    tokens = [
        RefreshToken(user=user, jti="a", expires_at=now + timedelta(days=1)),
        RefreshToken(user=user, jti="b", expires_at=now + timedelta(days=1)),
        RefreshToken(user=user, jti="old", expires_at=now - timedelta(days=1)),
        RefreshToken(user=other, jti="c", expires_at=now + timedelta(days=1)),
    ]
    db.add_all(tokens)
    db.commit()
    user_id = user.id
    repository = RefreshTokenRepository(db)

    token_a = repository.get_by_jti("a")
    assert token_a is tokens[0]
    assert repository.get_by_jti("missing") is None

    first_revocation = now - timedelta(minutes=5)
    repository.revoke(token_a, at=first_revocation)  # type: ignore[arg-type]
    repository.revoke(token_a)  # type: ignore[arg-type]
    assert token_a.revoked_at == first_revocation  # type: ignore[union-attr]

    assert repository.revoke_all_for_user(user_id) == 2  # "b" and "old"
    db.commit()
    db.expire_all()
    user_tokens = db.scalars(select(RefreshToken).where(RefreshToken.user_id == user_id)).all()
    assert all(token.revoked_at is not None for token in user_tokens)
    assert repository.get_by_jti("c").revoked_at is None  # type: ignore[union-attr]
    assert repository.get_by_jti("a").revoked_at == first_revocation  # type: ignore[union-attr]

    assert repository.delete_expired() == 1
    db.commit()
    assert repository.get_by_jti("old") is None


# --------------------------------------------------------------------------- customers


def test_customer_lookups(db: Session, make_customer: MakeCustomer) -> None:
    first = make_customer("Алия", phone="+992901234567", instagram_user_id="ig-1")
    make_customer("Другая Алия", phone="+992901234567")
    repository = CustomerRepository(db)

    assert repository.get_by_instagram_id("ig-1") is first
    assert repository.get_by_instagram_id("ig-2") is None
    assert repository.get_by_phone("+992901234567") is first
    assert repository.get_by_phone("+992000000000") is None
    assert repository.count_all() == 2


def test_customer_stats_count_only_valid_orders(
    db: Session, make_customer: MakeCustomer, make_product: MakeProduct, make_order: MakeOrder
) -> None:
    cake = make_product("Медовик", price="100.50")
    buyer = make_customer("Покупатель")
    idle = make_customer("Без заказов")
    make_order(buyer, [(cake, 2)], OrderStatus.CONFIRMED)  # 201.00
    make_order(buyer, [(cake, 1)], OrderStatus.COMPLETED)  # 100.50
    make_order(buyer, [(cake, 1)], OrderStatus.HANDED_TO_COURIER)  # 100.50
    make_order(buyer, [(cake, 5)], OrderStatus.NEW)
    make_order(buyer, [(cake, 5)], OrderStatus.WAITING_CONFIRMATION)
    make_order(buyer, [(cake, 5)], OrderStatus.CANCELLED)
    repository = CustomerRepository(db)

    rows, total = repository.search_with_stats(sort="created_at")

    assert total == 2
    by_id = {row.customer.id: row for row in rows}
    assert by_id[buyer.id].orders_count == 3
    assert by_id[buyer.id].total_spent == Decimal("402.00")
    assert by_id[idle.id].orders_count == 0
    assert by_id[idle.id].total_spent == Decimal("0.00")
    assert repository.get_stats(buyer.id) == (3, Decimal("402.00"))
    assert repository.get_stats(idle.id) == (0, Decimal("0.00"))


def test_customer_search_and_type_filter(db: Session, make_customer: MakeCustomer) -> None:
    aliya = make_customer("Алия Каримова", username="aliya_k", phone="+992901234567", is_new=False)
    make_customer("Фарход", username="farhod", phone="+992935556677")
    make_customer("Nigora_%", username=None, phone=None)
    repository = CustomerRepository(db)

    def ids(**kwargs: Any) -> list[int]:
        return [row.customer.id for row in repository.search_with_stats(**kwargs)[0]]

    assert ids(search="Алия") == [aliya.id]
    assert ids(search="ALIYA") == [aliya.id]
    assert ids(search="@aliya_k") == [aliya.id]
    assert ids(search="90 123") == [aliya.id]
    assert ids(search="+99290") == [aliya.id]
    assert ids(search="3") == []  # too few digits for a phone fragment
    assert len(ids(search="%")) == 1  # LIKE wildcards are escaped
    assert ids(customer_type="regular") == [aliya.id]
    assert len(ids(customer_type="NEW")) == 2
    with pytest.raises(BadRequestError):
        repository.search_with_stats(customer_type="vip")


def test_customer_search_is_case_insensitive_for_cyrillic(db: Session, make_customer: MakeCustomer) -> None:
    """ILIKE folds only ASCII under a C collation (SQLite, ``initdb --no-locale``)."""
    aliya = make_customer("Алия Каримова", username="Aliya_K")
    yolka = make_customer("Ёлочка", username=None)
    make_customer("Фарход")
    repository = CustomerRepository(db)

    def ids(search: str) -> list[int]:
        return [row.customer.id for row in repository.search_with_stats(search=search)[0]]

    assert ids("алия") == [aliya.id]
    assert ids("АЛИЯ") == [aliya.id]
    assert ids("аЛиЯ") == [aliya.id]
    assert ids("каримова") == [aliya.id]
    assert ids("алия каримова") == [aliya.id]
    assert ids("Алия Каримова") == [aliya.id]
    assert ids("ёлочка") == [yolka.id]
    assert ids("aliya_k") == [aliya.id]  # ASCII username: plain ILIKE
    assert ids("ALIYA_K") == [aliya.id]
    assert ids("Нигора") == []


def test_customer_count_registered_uses_business_days(db: Session, make_customer: MakeCustomer) -> None:
    day = date(2026, 9, 15)  # Asia/Dushanbe = UTC+5: the business day is [14th 19:00Z, 15th 19:00Z)
    make_customer("Вчерашний", created_at=datetime(2026, 9, 14, 18, 59, tzinfo=UTC))
    make_customer("Начало дня", created_at=datetime(2026, 9, 14, 19, 0, tzinfo=UTC))
    make_customer("Конец дня", created_at=datetime(2026, 9, 15, 18, 59, tzinfo=UTC))
    make_customer("Завтрашний", created_at=datetime(2026, 9, 15, 19, 0, tzinfo=UTC))
    repository = CustomerRepository(db)

    assert repository.count_registered(day, day) == 2
    assert repository.count_registered(day, day + timedelta(days=1)) == 3
    assert repository.count_registered(day - timedelta(days=1), day) == 3
    assert repository.count_registered(date(2026, 1, 1), date(2026, 1, 31)) == 0
    assert repository.count_all() == 4


def test_customer_sorting_and_pagination(
    db: Session, make_customer: MakeCustomer, make_product: MakeProduct, make_order: MakeOrder
) -> None:
    product = make_product(price="10.00")
    base = datetime(2026, 9, 1, tzinfo=UTC)
    never = make_customer("Никогда")
    early = make_customer("Ранний", last_order_at=base)
    late = make_customer("Поздний", last_order_at=base + timedelta(days=3))
    make_order(early, [(product, 50)], OrderStatus.CONFIRMED)  # 500
    make_order(late, [(product, 5)], OrderStatus.CONFIRMED)  # 50
    repository = CustomerRepository(db)

    def names(sort: str | None, **kwargs: Any) -> list[str | None]:
        return [row.customer.name for row in repository.search_with_stats(sort=sort, **kwargs)[0]]

    assert names("-last_order_at") == ["Поздний", "Ранний", "Никогда"]
    assert names("last_order_at") == ["Ранний", "Поздний", "Никогда"]
    assert names("-total_spent") == ["Ранний", "Поздний", "Никогда"]
    assert names("total_spent") == ["Никогда", "Поздний", "Ранний"]
    assert names("created_at") == ["Никогда", "Ранний", "Поздний"]
    assert names(None) == names("-last_order_at")

    page, total = repository.search_with_stats(page=2, page_size=2)
    assert total == 3
    assert [row.customer.id for row in page] == [never.id]
    with pytest.raises(BadRequestError):
        repository.search_with_stats(sort="-name")


def test_customer_search_with_stats_has_no_n_plus_one(
    db: Session, make_customer: MakeCustomer, make_order: MakeOrder
) -> None:
    for index in range(6):
        make_order(make_customer(f"Клиент {index}"), status=OrderStatus.CONFIRMED)
    db.expire_all()

    with count_queries(db) as statements:
        rows, total = CustomerRepository(db).search_with_stats(page_size=10)
        assert [row.orders_count for row in rows] == [1] * 6

    assert total == 6
    assert len(statements) == 2  # count + page


# --------------------------------------------------------------------------- products


def test_product_list_hides_deleted_and_inactive(db: Session, make_product: MakeProduct) -> None:
    make_product("Чизкейк", sort_order=2)
    make_product("Медовик", sort_order=1)
    make_product("Архивный", is_active=False)
    make_product("Удалённый", is_active=False, deleted_at=now_utc())
    make_product("Удалённый активный", deleted_at=now_utc())
    repository = ProductRepository(db)

    assert [product.name for product in repository.list()] == ["Медовик", "Чизкейк"]
    assert [product.name for product in repository.list(include_inactive=True)] == ["Архивный", "Медовик", "Чизкейк"]


def test_product_search_is_case_insensitive_and_matches_aliases(db: Session, make_product: MakeProduct) -> None:
    make_product("Медовик", aliases=["торти асалӣ", "honey cake"])
    make_product("Красный бархат")
    make_product("Ёлочка")
    repository = ProductRepository(db)

    def names(search: str) -> list[str]:
        return [product.name for product in repository.list(search=search)]

    assert names("медовик") == ["Медовик"]
    assert names("АСАЛӢ") == ["Медовик"]
    assert names("Honey") == ["Медовик"]
    assert names("БАРХАТ") == ["Красный бархат"]
    assert names("елочка") == ["Ёлочка"]
    assert len(names("  ")) == 3


def test_product_get_active_visible_and_many(db: Session, make_product: MakeProduct) -> None:
    active = make_product("Медовик")
    inactive = make_product("Архивный", is_active=False)
    deleted = make_product("Удалённый", deleted_at=now_utc())
    repository = ProductRepository(db)

    assert repository.get_active(active.id) is active
    assert repository.get_active(inactive.id) is None
    assert repository.get_active(deleted.id) is None
    assert repository.get_active(10_000) is None
    assert repository.get_visible(inactive.id) is inactive
    assert repository.get_visible(deleted.id) is None
    with pytest.raises(NotFoundError):
        repository.get_visible_or_raise(deleted.id)
    many = repository.get_many([active.id, deleted.id, active.id, 10_000])
    assert many == {active.id: active, deleted.id: deleted}
    assert repository.get_many([]) == {}


# --------------------------------------------------------------------------- orders: list_filtered


def test_order_list_filters(
    db: Session, make_customer: MakeCustomer, make_product: MakeProduct, make_order: MakeOrder
) -> None:
    today = business_today()
    aliya = make_customer("Алия", username="aliya_k", phone="+992901234567")
    farhod = make_customer("Фарход", phone="+992935556677")
    cake = make_product(price="100.00")
    o1 = make_order(aliya, [(cake, 1)], OrderStatus.NEW, delivery_date=today)
    o2 = make_order(
        aliya, [(cake, 2)], OrderStatus.CONFIRMED, delivery_date=today + timedelta(days=1), paid_amount=200
    )
    o3 = make_order(
        farhod,
        [(cake, 3)],
        OrderStatus.READY,
        delivery_type=DeliveryType.DELIVERY,
        delivery_date=today + timedelta(days=2),
    )
    o4 = make_order(farhod, [(cake, 1)], OrderStatus.CANCELLED, delivery_date=None)
    repository = OrderRepository(db)

    def ids(**kwargs: Any) -> set[int]:
        items, total = repository.list_filtered(**kwargs)
        assert total == len(items)
        return {order.id for order in items}

    assert ids() == {o1.id, o2.id, o3.id, o4.id}
    assert ids(statuses=[OrderStatus.NEW, "READY"]) == {o1.id, o3.id}
    assert ids(payment_status=PaymentStatus.PAID) == {o2.id}
    assert ids(delivery_type="DELIVERY") == {o3.id}
    assert ids(customer_id=aliya.id) == {o1.id, o2.id}
    assert ids(delivery_date=today) == {o1.id}
    assert ids(date_from=today + timedelta(days=1)) == {o2.id, o3.id}
    assert ids(date_to=today + timedelta(days=1)) == {o1.id, o2.id}
    assert ids(date_from=today, date_to=today) == {o1.id}
    assert ids(search=str(o3.id)) == {o3.id}  # a short number is an order id, not a phone fragment
    assert ids(search=f"#{o4.id}") == {o4.id}
    assert ids(search=f"№ {o2.id}") == {o2.id}
    assert ids(search="Фарход") == {o3.id, o4.id}
    assert ids(search="ALIYA") == {o1.id, o2.id}
    assert ids(search="93 555") == {o3.id, o4.id}
    assert ids(search="93555") == {o3.id, o4.id}  # long number: phone fragment too
    assert ids(search="#93555") == set()  # "#" means order number only
    assert ids(search="Алия", statuses=[OrderStatus.CONFIRMED]) == {o2.id}
    assert ids(search="нет такого") == set()


def test_order_list_sorting(db: Session, make_product: MakeProduct, make_order: MakeOrder) -> None:
    today = business_today()
    cheap = make_order(items=[(make_product(price="10.00"), 1)], delivery_date=today + timedelta(days=2))
    pricey = make_order(items=[(make_product(price="999.00"), 1)], delivery_date=today, delivery_time=time(18, 0))
    no_date = make_order(items=[(make_product(price="50.00"), 1)], delivery_date=None)
    early = make_order(items=[(make_product(price="20.00"), 1)], delivery_date=today, delivery_time=time(9, 0))
    repository = OrderRepository(db)

    def order_ids(sort: str | None) -> list[int]:
        return [order.id for order in repository.list_filtered(sort=sort)[0]]

    assert order_ids("-created_at") == [early.id, no_date.id, pricey.id, cheap.id]
    assert order_ids(None) == order_ids("-created_at")
    assert order_ids("created_at") == [cheap.id, pricey.id, no_date.id, early.id]
    assert order_ids("delivery_date") == [early.id, pricey.id, cheap.id, no_date.id]
    assert order_ids("-delivery_date") == [cheap.id, pricey.id, early.id, no_date.id]
    assert order_ids("total_amount") == [cheap.id, early.id, no_date.id, pricey.id]
    assert order_ids("-total_amount") == [pricey.id, no_date.id, early.id, cheap.id]
    with pytest.raises(BadRequestError):
        repository.list_filtered(sort="status")


def test_order_list_pagination_eager_loading_and_query_count(
    db: Session, make_customer: MakeCustomer, make_product: MakeProduct, make_order: MakeOrder
) -> None:
    products = [make_product(), make_product()]
    for index in range(5):
        make_order(make_customer(f"Клиент {index}"), [(products[0], 1), (products[1], 2)])
    db.expunge_all()

    with count_queries(db) as statements:
        page, total = OrderRepository(db).list_filtered(page=2, page_size=2)
        summaries = [(order.customer.name, len(order.items)) for order in page]

    assert total == 5
    assert len(page) == 2
    assert summaries == [("Клиент 2", 2), ("Клиент 1", 2)]
    assert len(statements) == 4  # count, page, customers, items


def test_order_get_detail_loads_relationships(db: Session, make_order: MakeOrder, make_user: MakeUser) -> None:
    user = make_user("admin")
    order = make_order(delivery_type=DeliveryType.DELIVERY, paid_amount=50, delivery={"address_raw": "Сино"})
    db.add(OrderEvent(order=order, actor_type=ActorType.USER, actor_user=user, event_type="CREATED", changes={}))
    db.commit()
    order_id = order.id
    db.expunge_all()
    repository = OrderRepository(db)

    detail = repository.get_detail(order_id)

    assert detail is not None
    unloaded = sa_inspect(detail).unloaded
    assert not {"items", "customer", "delivery", "payments", "events"} & unloaded
    with count_queries(db) as statements:
        assert detail.events[0].actor_user.username == "admin"  # type: ignore[union-attr]
        assert detail.delivery.address_raw == "Сино"  # type: ignore[union-attr]
        assert len(detail.payments) == 1
    assert statements == []
    assert repository.get_detail(10_000) is None
    with pytest.raises(NotFoundError, match="Заказ не найден"):
        repository.get_detail_or_raise(10_000)


# --------------------------------------------------------------------------- shared order schemas (04 §5)


ORDER_LIST_ITEM_FIELDS = {
    "id",
    "customer",
    "status",
    "payment_status",
    "delivery_type",
    "delivery_date",
    "delivery_time",
    "delivery_address",
    "total_amount",
    "paid_amount",
    "items_summary",
    "items_count",
    "comment",
    "created_at",
}


def test_order_list_item_matches_contract(
    db: Session, make_customer: MakeCustomer, make_product: MakeProduct, make_order: MakeOrder
) -> None:
    customer = make_customer("Алия", username="aliya_k", phone="+992901234567")
    order = make_order(
        customer,
        [(make_product("Медовик", price="150.00"), 2), (make_product("Чизкейк", price="50.00"), 1)],
        OrderStatus.CONFIRMED,
        delivery_type=DeliveryType.DELIVERY,
        delivery_date=date(2026, 9, 16),
        delivery_time=time(18, 0),
        paid_amount=100,
        comment="Надпись «С днём рождения»",
        delivery={"address_raw": "Сино, 82 мкр, дом 5"},
    )

    data = build_order_list_item(order).model_dump(mode="json")

    assert set(OrderListItem.model_fields) == ORDER_LIST_ITEM_FIELDS
    assert set(data) == ORDER_LIST_ITEM_FIELDS
    assert set(CustomerBrief.model_fields) == {"id", "name", "username", "phone"}
    assert data["customer"] == {"id": customer.id, "name": "Алия", "username": "aliya_k", "phone": "+992901234567"}
    assert data["items_summary"] == "Медовик ×2, Чизкейк ×1"
    assert data["items_count"] == 3
    assert (data["total_amount"], data["paid_amount"]) == (350.0, 100.0)
    assert (data["status"], data["payment_status"]) == ("CONFIRMED", "PARTIALLY_PAID")
    assert (data["delivery_type"], data["delivery_date"], data["delivery_time"]) == ("DELIVERY", "2026-09-16", "18:00")
    assert data["delivery_address"] == "Сино, 82 мкр, дом 5"
    assert data["comment"] == "Надпись «С днём рождения»"


def test_order_list_item_with_no_items_or_delivery(db: Session, make_order: MakeOrder) -> None:
    order = make_order(items=[], delivery_type=None, delivery_date=None, delivery_time=None)

    data = build_order_list_item(order).model_dump(mode="json")

    assert (data["items_summary"], data["items_count"]) == ("", 0)
    assert (data["total_amount"], data["paid_amount"]) == (0.0, 0.0)
    assert data["delivery_type"] is data["delivery_date"] is data["delivery_time"] is None
    assert data["delivery_address"] is data["comment"] is None
    assert data["customer"]["name"] is not None


# --------------------------------------------------------------------------- orders: periods & helpers


def test_valid_orders_for_period_by_delivery_date(db: Session, make_order: MakeOrder) -> None:
    day = date(2026, 9, 16)
    included = [
        make_order(status=OrderStatus.CONFIRMED, delivery_date=day),
        make_order(status=OrderStatus.COMPLETED, delivery_date=day + timedelta(days=1)),
    ]
    make_order(status=OrderStatus.NEW, delivery_date=day)
    make_order(status=OrderStatus.WAITING_CONFIRMATION, delivery_date=day)
    make_order(status=OrderStatus.CANCELLED, delivery_date=day)
    make_order(status=OrderStatus.READY, delivery_date=day - timedelta(days=1))
    make_order(status=OrderStatus.READY, delivery_date=day + timedelta(days=2))
    repository = OrderRepository(db)

    orders = repository.valid_orders_for_period(day, day + timedelta(days=1))

    assert [order.id for order in orders] == [order.id for order in included]
    assert repository.valid_orders_for_period(day, day, "delivery")[0].items  # items loaded


def test_valid_orders_for_period_by_confirmation_business_date(db: Session, make_order: MakeOrder) -> None:
    day = date(2026, 9, 15)  # Asia/Dushanbe = UTC+5: the business day is [14th 19:00Z, 15th 19:00Z)
    inside_start = make_order(status=OrderStatus.CONFIRMED, confirmed_at=datetime(2026, 9, 14, 19, 0, tzinfo=UTC))
    inside_end = make_order(status=OrderStatus.READY, confirmed_at=datetime(2026, 9, 15, 18, 59, tzinfo=UTC))
    make_order(status=OrderStatus.CONFIRMED, confirmed_at=datetime(2026, 9, 14, 18, 59, tzinfo=UTC))
    make_order(status=OrderStatus.CONFIRMED, confirmed_at=datetime(2026, 9, 15, 19, 0, tzinfo=UTC))
    make_order(status=OrderStatus.CANCELLED, confirmed_at=datetime(2026, 9, 15, 10, 0, tzinfo=UTC))
    repository = OrderRepository(db)

    orders = repository.valid_orders_for_period(day, day, date_basis="created")

    assert [order.id for order in orders] == [inside_start.id, inside_end.id]
    with pytest.raises(BadRequestError):
        repository.valid_orders_for_period(day, day, date_basis="paid")


def test_drafts_and_latest_active_orders(
    db: Session, make_customer: MakeCustomer, make_order: MakeOrder, make_conversation: Callable[..., Conversation]
) -> None:
    customer = make_customer()
    conversation = make_conversation(customer)
    other_conversation = make_conversation(customer)
    new = make_order(customer, status=OrderStatus.NEW, conversation_id=conversation.id)
    waiting = make_order(customer, status=OrderStatus.WAITING_CONFIRMATION, conversation_id=conversation.id)
    make_order(customer, status=OrderStatus.CONFIRMED, conversation_id=conversation.id)
    make_order(customer, status=OrderStatus.NEW, conversation_id=other_conversation.id)
    ready = make_order(customer, status=OrderStatus.READY)
    make_order(customer, status=OrderStatus.COMPLETED)
    make_order(customer, status=OrderStatus.CANCELLED)
    finished = make_customer()
    make_order(finished, status=OrderStatus.COMPLETED)
    repository = OrderRepository(db)

    assert [order.id for order in repository.drafts_for_conversation(conversation.id)] == [waiting.id, new.id]
    assert repository.drafts_for_conversation(10_000) == []
    latest = repository.latest_active_for_customer(customer.id)
    assert latest is not None and latest.id == ready.id
    assert repository.latest_active_for_customer(finished.id) is None
    assert repository.latest_active_ids_for_customers([customer.id, finished.id]) == {customer.id: ready.id}
    assert repository.latest_active_ids_for_customers([]) == {}


def test_order_customer_helpers_and_counters(db: Session, make_customer: MakeCustomer, make_order: MakeOrder) -> None:
    today = business_today()
    customer = make_customer()
    confirmed_at = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)
    first = make_order(customer, status=OrderStatus.CONFIRMED, confirmed_at=confirmed_at, delivery_date=today)
    cancelled = make_order(customer, status=OrderStatus.CANCELLED, confirmed_at=confirmed_at + timedelta(days=3))
    second = make_order(customer, status=OrderStatus.WAITING_CONFIRMATION, delivery_date=today)
    make_order(status=OrderStatus.COMPLETED, paid_amount=100, delivery_date=today)  # PAID
    make_order(status=OrderStatus.READY, delivery_date=today - timedelta(days=31))  # too old
    repository = OrderRepository(db)

    assert [order.id for order in repository.list_for_customer(customer.id)] == [second.id, cancelled.id, first.id]
    assert len(repository.list_for_customer(customer.id, limit=1)) == 1
    recent_ids = [order.id for order in repository.recent(limit=2)]
    assert recent_ids == [order.id for order in repository.list_filtered(page_size=2)[0]]
    assert repository.count_by_statuses([OrderStatus.WAITING_CONFIRMATION]) == 1
    assert repository.count_by_statuses(VALID_ORDER_STATUSES, delivery_date=today) == 2
    assert repository.count_unpaid_valid(since=today - timedelta(days=30)) == 1
    assert repository.customer_has_completed_orders(customer.id) is False
    assert repository.max_confirmed_at_for_customer(customer.id) == confirmed_at  # cancelled one ignored


def test_order_events_and_payments(db: Session, make_order: MakeOrder, make_user: MakeUser) -> None:
    user = make_user("operator")
    order = make_order()
    other = make_order()
    now = now_utc()
    db.add_all(
        [
            OrderEvent(order=order, actor_type=ActorType.SYSTEM, event_type="CREATED", changes={}),
            OrderEvent(
                order=order,
                actor_type=ActorType.USER,
                actor_user=user,
                event_type="UPDATED",
                changes={"comment": [None, "x"]},
            ),
            OrderEvent(order=other, actor_type=ActorType.SYSTEM, event_type="CREATED", changes={}),
            Payment(order=order, kind=PaymentKind.PAYMENT, amount=Decimal("100.00"), paid_at=now),
            Payment(order=order, kind=PaymentKind.PAYMENT, amount=Decimal("50.25"), paid_at=now + timedelta(minutes=1)),
            Payment(order=order, kind=PaymentKind.REFUND, amount=Decimal("30.00"), paid_at=now + timedelta(minutes=2)),
        ]
    )
    db.commit()
    order_id, other_id = order.id, other.id
    db.expunge_all()

    events = OrderEventRepository(db).list_for_order(order_id)
    assert [item.event_type for item in events] == ["CREATED", "UPDATED"]
    assert "actor_user" not in sa_inspect(events[1]).unloaded
    assert events[1].actor_user.username == "operator"  # type: ignore[union-attr]

    payments = PaymentRepository(db)
    amounts = [payment.amount for payment in payments.list_for_order(order_id)]
    assert amounts == [Decimal("100.00"), Decimal("50.25"), Decimal("30.00")]
    assert payments.sums_by_kind(order_id) == {
        PaymentKind.PAYMENT: Decimal("150.25"),
        PaymentKind.REFUND: Decimal("30.00"),
    }
    assert payments.sums_by_kind(other_id) == {
        PaymentKind.PAYMENT: Decimal("0.00"),
        PaymentKind.REFUND: Decimal("0.00"),
    }
    assert payments.has_refund(order_id) is True
    assert payments.has_refund(other_id) is False


# --------------------------------------------------------------------------- deliveries


def test_delivery_list_for_date_only_valid_delivery_orders(db: Session, make_order: MakeOrder) -> None:
    day = date(2026, 9, 16)

    def delivery_order(status: OrderStatus, **fields: Any) -> int:
        delivery = fields.pop("delivery", {"address_raw": "Сино"})
        fields.setdefault("delivery_date", day)
        order = make_order(status=status, delivery_type=DeliveryType.DELIVERY, delivery=delivery, **fields)
        return order.id

    late = delivery_order(OrderStatus.CONFIRMED, delivery_time=time(18, 0))
    early = delivery_order(
        OrderStatus.READY,
        delivery_time=time(10, 0),
        delivery={
            "address_raw": "Шохмансур",
            "status": DeliveryStatus.AWAITING_DISPATCH,
            "geocode_status": GeocodeStatus.OK,
        },
    )
    no_time = delivery_order(OrderStatus.PREPARING, delivery_time=None)
    delivery_order(OrderStatus.NEW)
    delivery_order(OrderStatus.CANCELLED)
    delivery_order(OrderStatus.CONFIRMED, delivery_date=day + timedelta(days=1))
    switched = db.get(Order, delivery_order(OrderStatus.CONFIRMED))
    assert switched is not None
    switched.delivery_type = DeliveryType.PICKUP  # delivery row left behind
    db.commit()
    db.expunge_all()
    repository = DeliveryRepository(db)

    with count_queries(db) as statements:
        deliveries = repository.list_for_date(day)
        loaded = [(len(d.order.items), d.order.customer.name) for d in deliveries]

    assert [d.order_id for d in deliveries] == [early, late, no_time]
    assert all(items == 1 and name for items, name in loaded)
    assert len(statements) == 3  # deliveries+orders, items, customers
    assert [d.order_id for d in repository.list_for_date(day, status="AWAITING_DISPATCH")] == [early]
    pending = repository.list_for_date(day, geocode_status=GeocodeStatus.PENDING)
    assert [d.order_id for d in pending] == [late, no_time]
    assert [d.order_id for d in repository.list_by_status(DeliveryStatus.AWAITING_DISPATCH)] == [early]


def test_delivery_lookups(db: Session, make_order: MakeOrder) -> None:
    order = make_order(delivery_type=DeliveryType.DELIVERY, delivery={"address_raw": "Сино"})
    delivery_id = order.delivery.id  # type: ignore[union-attr]
    order_id = order.id
    db.expunge_all()
    repository = DeliveryRepository(db)

    assert repository.get_by_order(order_id).id == delivery_id  # type: ignore[union-attr]
    assert repository.get_by_order(10_000) is None
    loaded = repository.get_with_order(delivery_id)
    assert loaded is not None
    assert "order" not in sa_inspect(loaded).unloaded
    assert not {"items", "customer"} & sa_inspect(loaded.order).unloaded
    with pytest.raises(NotFoundError, match="Доставка не найдена"):
        repository.get_or_raise(10_000)


def test_location_request_repository(db: Session, make_order: MakeOrder) -> None:
    order = make_order(delivery_type=DeliveryType.DELIVERY, delivery={"address_raw": "Сино"})
    delivery = order.delivery
    assert delivery is not None
    order_id, delivery_id = order.id, delivery.id
    now = now_utc()
    db.add_all(
        [
            LocationRequest(token="expired", delivery=delivery, expires_at=now - timedelta(hours=1)),
            LocationRequest(token="usable", delivery=delivery, expires_at=now + timedelta(hours=48)),
            LocationRequest(token="used", delivery=delivery, expires_at=now + timedelta(hours=48), used_at=now),
        ]
    )
    db.commit()
    db.expunge_all()
    repository = LocationRequestRepository(db)

    found = repository.get_by_token("usable")
    assert found is not None
    assert "delivery" not in sa_inspect(found).unloaded
    assert found.delivery.order.id == order_id
    assert repository.get_by_token("nope") is None
    latest = repository.latest_usable_for_delivery(delivery_id)
    assert latest is not None and latest.token == "usable"
    assert repository.latest_usable_for_delivery(delivery_id, now=now + timedelta(days=3)) is None


def test_route_plan_latest_for_date(db: Session, make_order: MakeOrder) -> None:
    day = date(2026, 9, 16)

    def delivery_order(address: str) -> Order:
        return make_order(
            status=OrderStatus.CONFIRMED, delivery_type=DeliveryType.DELIVERY, delivery={"address_raw": address}
        )

    first = delivery_order("A")
    second = delivery_order("B")
    first_id, second_id = first.id, second.id

    def plan(created_at: datetime, stops: list[Delivery | None], plan_day: date = day) -> RoutePlan:
        route = RoutePlan(
            delivery_date=plan_day,
            start_name="Склад",
            start_latitude=Decimal("38.56"),
            start_longitude=Decimal("68.77"),
            start_time=time(9, 0),
            algorithm="brute_force",
            distance_source="haversine",
            created_at=created_at,
        )
        for sequence, delivery in reversed(list(enumerate(stops, start=1))):
            route.stops.append(RouteStop(delivery=delivery, sequence=sequence))
        db.add(route)
        return route

    base = now_utc()
    plan(base - timedelta(hours=1), [first.delivery])
    newest = plan(base, [second.delivery, first.delivery])
    plan(base + timedelta(hours=1), [first.delivery], plan_day=day + timedelta(days=1))
    db.commit()
    newest_id = newest.id
    db.expunge_all()
    repository = RoutePlanRepository(db)

    latest = repository.latest_for_date(day)

    assert latest is not None and latest.id == newest_id
    with count_queries(db) as statements:
        stops = [(stop.sequence, stop.delivery.order.id, len(stop.delivery.order.items)) for stop in latest.stops]
    assert stops == [(1, second_id, 1), (2, first_id, 1)]
    assert statements == []
    assert repository.latest_for_date(day - timedelta(days=1)) is None


# --------------------------------------------------------------------------- conversations & messages


def test_conversation_repository(
    db: Session, make_customer: MakeCustomer, make_conversation: Callable[..., Conversation]
) -> None:
    base = now_utc()
    aliya = make_customer("Алия", username="aliya_k", phone="+992901234567")
    old = make_conversation(aliya, instagram_conversation_id="acc:1", last_message_at=base - timedelta(days=2))
    recent = make_conversation(
        make_customer("Фарход"),
        mode=ConversationMode.HUMAN_HANDOFF,
        needs_attention=True,
        last_message_at=base,
    )
    silent = make_conversation(make_customer("Молчун"), needs_attention=True)
    repository = ConversationRepository(db)

    def ids(**kwargs: Any) -> list[int]:
        return [conversation.id for conversation in repository.list_filtered(**kwargs)[0]]

    assert repository.get_by_instagram_conversation_id("acc:1") is old
    assert repository.get_by_instagram_conversation_id("acc:2") is None
    assert ids() == [recent.id, old.id, silent.id]
    assert ids(mode="HUMAN_HANDOFF") == [recent.id]
    assert ids(needs_attention=True) == [recent.id, silent.id]
    assert ids(needs_attention=False) == [old.id]
    assert ids(search="aliya") == [old.id]
    assert ids(search="90 123") == [old.id]
    page, total = repository.list_filtered(page=2, page_size=2)
    assert (total, [conversation.id for conversation in page]) == (3, [silent.id])
    assert "customer" not in sa_inspect(page[0]).unloaded
    assert repository.count_needing_attention() == 2
    latest = make_conversation(aliya, last_message_at=base - timedelta(hours=1))
    assert repository.latest_for_customer(aliya.id) is latest
    assert repository.latest_for_customer(10_000) is None


def test_message_repository(db: Session, make_conversation: Callable[..., Conversation]) -> None:
    conversation = make_conversation()
    other = make_conversation()
    messages = [_message(conversation, f"m{index}") for index in range(5)]
    messages[0].instagram_message_id = "mid-0"
    db.add_all([*messages, _message(other, "other-1"), _message(other, "other-2")])
    db.commit()
    repository = MessageRepository(db)

    def texts(items: list[Message]) -> list[str | None]:
        return [message.text for message in items]

    assert repository.get_by_instagram_message_id("mid-0") is messages[0]
    assert repository.get_by_instagram_message_id("mid-x") is None
    assert texts(repository.recent_for_conversation(conversation.id, limit=3)) == ["m2", "m3", "m4"]
    assert texts(repository.recent_for_conversation(conversation.id)) == ["m0", "m1", "m2", "m3", "m4"]
    assert texts(repository.page_before(conversation.id, before_id=messages[3].id, limit=2)) == ["m1", "m2"]
    assert repository.page_before(conversation.id, before_id=messages[0].id) == []
    last = repository.last_for_conversations([conversation.id, other.id, 10_000])
    assert {key: message.text for key, message in last.items()} == {conversation.id: "m4", other.id: "other-2"}
    assert repository.last_for_conversations([]) == {}


# --------------------------------------------------------------------------- faq, settings, reports


def test_faq_repository_order_and_active_filter(db: Session) -> None:
    db.add_all(
        [
            FaqItem(question="Третий", answer="3", sort_order=2),
            FaqItem(question="Первый", answer="1", sort_order=1),
            FaqItem(question="Второй", answer="2", sort_order=1),
            FaqItem(question="Скрытый", answer="x", sort_order=0, is_active=False),
        ]
    )
    db.commit()
    repository = FaqRepository(db)

    assert [item.question for item in repository.list()] == ["Первый", "Второй", "Третий"]
    all_questions = [item.question for item in repository.list(include_inactive=True)]
    assert all_questions == ["Скрытый", "Первый", "Второй", "Третий"]


def test_app_setting_repository_get_set_without_commit(db: Session, make_user: MakeUser) -> None:
    user = make_user("admin")
    repository = AppSettingRepository(db)

    assert repository.get_value("business") is None
    assert repository.get_value("business", default={}) == {}

    value = {"warehouse": {"name": "Склад"}}
    repository.set_value("business", value)
    value["warehouse"]["name"] = "changed outside"
    db.commit()
    stored = repository.get_value("business")
    assert stored == {"warehouse": {"name": "Склад"}}
    stored["warehouse"]["name"] = "mutated copy"
    assert repository.get_value("business") == {"warehouse": {"name": "Склад"}}

    repository.set_value("business", {"warehouse": {"name": "Кухня"}}, updated_by_user_id=user.id)
    db.commit()
    db.expire_all()
    row = db.get(AppSetting, "business")
    assert row is not None and row.value == {"warehouse": {"name": "Кухня"}}
    assert row.updated_by_user_id == user.id

    repository.set_value("business", {"never": "committed"})
    db.rollback()
    assert repository.get_value("business") == {"warehouse": {"name": "Кухня"}}


def test_daily_report_repository(db: Session) -> None:
    for offset in range(4):
        db.add(DailyReport(report_date=date(2026, 9, 10) + timedelta(days=offset), data={}, text=f"r{offset}"))
    db.commit()
    repository = DailyReportRepository(db)

    report = repository.get_by_date(date(2026, 9, 12))
    assert report is not None and report.text == "r2"
    assert repository.get_by_date(date(2026, 1, 1)) is None
    assert [item.text for item in repository.history(limit=2)] == ["r3", "r2"]
    assert len(repository.history()) == 4
