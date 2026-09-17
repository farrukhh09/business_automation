"""ORM models: every table, defaults, constraints, enum storage, cascades (02-data-model.md)."""

import time as time_module
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.core.time import now_utc
from app.models import (
    AppSetting,
    Base,
    Conversation,
    Customer,
    DailyReport,
    Delivery,
    FaqItem,
    LocationRequest,
    Message,
    Order,
    OrderEvent,
    OrderItem,
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
    GeocodeStatus,
    Language,
    MessageDeliveryStatus,
    MessageDirection,
    MessageSender,
    MessageType,
    OrderSource,
    OrderStatus,
    PaymentKind,
    PaymentMethod,
    PaymentStatus,
    UserRole,
)

EXPECTED_TABLES = {
    "users",
    "refresh_tokens",
    "customers",
    "products",
    "orders",
    "order_items",
    "order_events",
    "payments",
    "deliveries",
    "location_requests",
    "route_plans",
    "route_stops",
    "conversations",
    "messages",
    "faq_items",
    "app_settings",
    "daily_reports",
}

ALL_MODELS = (
    User,
    RefreshToken,
    Customer,
    Product,
    Order,
    OrderItem,
    OrderEvent,
    Payment,
    Delivery,
    LocationRequest,
    RoutePlan,
    RouteStop,
    Conversation,
    Message,
    FaqItem,
    AppSetting,
    DailyReport,
)


def _count(db: Session, model: type[Base]) -> int:
    return db.scalar(sa.select(sa.func.count()).select_from(model)) or 0


def _is_utc(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() == timedelta(0)


def test_metadata_contains_every_table() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert {model.__tablename__ for model in ALL_MODELS} == EXPECTED_TABLES


def test_timestamp_columns_follow_contract() -> None:
    without_updated_at = {"refresh_tokens", "order_events", "payments", "location_requests", "messages"}
    for name, table in Base.metadata.tables.items():
        assert "created_at" in table.c, name
        assert ("updated_at" in table.c) is (name not in without_updated_at), name


def test_create_every_model_with_defaults(db: Session, admin_user: User) -> None:
    customer = Customer(name="Мадина", instagram_user_id="17841400000000001", username="madina")
    product = Product(name="Медовик", price=Decimal("150.00"))
    conversation = Conversation(customer=customer, instagram_conversation_id="17841400000000999:17841400000000001")
    order = Order(customer=customer, conversation=conversation, source=OrderSource.INSTAGRAM)
    order.items.append(
        OrderItem(
            product=product,
            product_name=product.name,
            quantity=2,
            unit_price=Decimal("150.00"),
            total_price=Decimal("300.00"),
        )
    )
    order.events.append(OrderEvent(actor_type=ActorType.AI, event_type="CREATED", changes={"status": [None, "NEW"]}))
    order.payments.append(
        Payment(kind=PaymentKind.PAYMENT, amount=Decimal("100.00"), method=PaymentMethod.CASH, created_by_user_id=admin_user.id)
    )
    delivery = Delivery(order=order, address_raw="82 мкр, дом 5, кв 12")
    location_request = LocationRequest(token="tok-123", delivery=delivery, expires_at=now_utc() + timedelta(hours=48))
    plan = RoutePlan(
        delivery_date=date(2026, 9, 16),
        start_name="Кухня",
        start_latitude=Decimal("38.560000"),
        start_longitude=Decimal("68.774000"),
        start_time=time(9, 0),
        algorithm="brute_force",
        distance_source="haversine",
        created_by_user_id=admin_user.id,
    )
    plan.stops.append(RouteStop(delivery=delivery, sequence=1, eta=time(9, 25), distance_from_prev_m=5400, duration_from_prev_s=900))
    message = Message(
        conversation=conversation,
        direction=MessageDirection.INCOMING,
        message_type=MessageType.TEXT,
        sender=MessageSender.CUSTOMER,
        text="Здравствуйте, хочу 2 торта на завтра",
        instagram_message_id="mid.1",
    )
    faq = FaqItem(question="Есть доставка?", answer="Да, по Худжанду")
    setting = AppSetting(key="business", value={"business_name": "Домашняя выпечка"}, updated_by_user_id=admin_user.id)
    report = DailyReport(report_date=date(2026, 9, 15), data={"finance": {"revenue": 0}}, text="ОТЧЁТ ЗА 15.09.2026")
    refresh = RefreshToken(user=admin_user, jti="jti-1", expires_at=now_utc() + timedelta(days=14))

    db.add_all([order, location_request, plan, message, faq, setting, report, refresh])
    db.commit()
    db.expire_all()

    for model in ALL_MODELS:
        assert _count(db, model) == 1, model.__name__

    saved_customer = db.scalars(sa.select(Customer)).one()
    assert saved_customer.language is Language.RU
    assert saved_customer.is_new is True
    assert saved_customer.customer_type == "NEW"
    assert saved_customer.last_order_at is None
    assert [c.id for c in saved_customer.conversations] == [conversation.id]

    saved_product = db.scalars(sa.select(Product)).one()
    assert (saved_product.currency, saved_product.unit) == ("TJS", "шт.")
    assert saved_product.aliases == []
    assert saved_product.is_active is True
    assert saved_product.sort_order == 0
    assert saved_product.deleted_at is None

    saved_order = db.scalars(sa.select(Order)).one()
    assert saved_order.status is OrderStatus.NEW
    assert saved_order.payment_status is PaymentStatus.UNPAID
    assert saved_order.total_amount == Decimal("0")
    assert saved_order.paid_amount == Decimal("0")
    assert saved_order.is_repeat_customer is False
    assert saved_order.customer.name == "Мадина"
    assert saved_order.conversation_id == conversation.id
    assert saved_order.items[0].product_id == product.id
    assert saved_order.items[0].quantity == 2
    assert saved_order.payments[0].method is PaymentMethod.CASH
    assert _is_utc(saved_order.payments[0].paid_at)
    assert saved_order.events[0].changes == {"status": [None, "NEW"]}
    assert _is_utc(saved_order.created_at)
    assert _is_utc(saved_order.updated_at)

    saved_delivery = saved_order.delivery
    assert saved_delivery is not None
    assert saved_delivery.city == "Худжанд"
    assert saved_delivery.geocode_status is GeocodeStatus.PENDING
    assert saved_delivery.status is DeliveryStatus.PENDING
    assert saved_delivery.geocode_candidates == []
    assert saved_delivery.has_coordinates is False
    assert saved_delivery.order.id == saved_order.id
    assert [lr.token for lr in saved_delivery.location_requests] == ["tok-123"]
    assert [stop.sequence for stop in saved_delivery.route_stops] == [1]

    saved_conversation = db.scalars(sa.select(Conversation)).one()
    assert saved_conversation.mode is ConversationMode.AI
    assert saved_conversation.state == {}
    assert saved_conversation.failed_ai_attempts == 0
    assert saved_conversation.needs_attention is False
    saved_message = saved_conversation.messages[0]
    assert saved_message.delivery_status is MessageDeliveryStatus.NOT_APPLICABLE
    assert saved_message.ai_processed is False
    assert saved_message.ai_payload is None

    saved_plan = db.scalars(sa.select(RoutePlan)).one()
    assert saved_plan.total_distance_m == 0
    assert saved_plan.stops[0].delivery_id == saved_delivery.id
    assert saved_plan.stops[0].lateness_min == 0
    assert saved_plan.stops[0].eta == time(9, 25)

    assert db.scalars(sa.select(FaqItem)).one().keywords == []
    assert db.get(AppSetting, "business").value == {"business_name": "Домашняя выпечка"}  # type: ignore[union-attr]
    assert _is_utc(db.scalars(sa.select(DailyReport)).one().generated_at)
    assert db.scalars(sa.select(RefreshToken)).one().user.username == "admin"


# --------------------------------------------------------------------------- constraints


def test_order_item_quantity_must_be_positive(
    db: Session, make_order: Callable[..., Order], make_product: Callable[..., Product]
) -> None:
    order = make_order()
    product = make_product()
    db.add(
        OrderItem(
            order_id=order.id,
            product_id=product.id,
            product_name=product.name,
            quantity=0,
            unit_price=product.price,
            total_price=Decimal("0.00"),
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


@pytest.mark.parametrize("price", [Decimal("0"), Decimal("-1.00")])
def test_product_price_must_be_positive(db: Session, price: Decimal) -> None:
    db.add(Product(name="Бесплатный торт", price=price))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_payment_amount_must_be_positive(db: Session, make_order: Callable[..., Order]) -> None:
    order = make_order()
    db.add(Payment(order_id=order.id, kind=PaymentKind.PAYMENT, amount=Decimal("0.00")))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_customer_instagram_user_id_is_unique(db: Session) -> None:
    db.add(Customer(instagram_user_id="17841400000000001"))
    db.commit()
    db.add(Customer(instagram_user_id="17841400000000001"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_customers_without_instagram_id_are_allowed(db: Session) -> None:
    db.add_all([Customer(name="Ручной 1"), Customer(name="Ручной 2")])
    db.commit()
    assert _count(db, Customer) == 2


def test_message_instagram_message_id_is_unique(db: Session, make_customer: Callable[..., Customer]) -> None:
    conversation = Conversation(customer=make_customer())
    for _ in range(2):
        conversation.messages.append(
            Message(
                direction=MessageDirection.INCOMING,
                message_type=MessageType.TEXT,
                sender=MessageSender.CUSTOMER,
                instagram_message_id="mid.duplicate",
            )
        )
    db.add(conversation)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_username_is_unique(db: Session, make_user: Callable[..., User]) -> None:
    make_user("same")
    db.add(User(username="same", password_hash="x", role=UserRole.OPERATOR))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_only_one_delivery_per_order(db: Session, make_order: Callable[..., Order]) -> None:
    order = make_order(delivery={"address_raw": "Сино, 5"})
    db.add(Delivery(order_id=order.id, address_raw="Другой адрес"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_route_stop_sequence_starts_at_one(db: Session, make_order: Callable[..., Order]) -> None:
    order = make_order(delivery={"address_raw": "Сино, 5"})
    plan = RoutePlan(
        delivery_date=date(2026, 9, 16),
        start_name="Кухня",
        start_latitude=Decimal("38.56"),
        start_longitude=Decimal("68.77"),
        start_time=time(9, 0),
        algorithm="brute_force",
        distance_source="haversine",
    )
    plan.stops.append(RouteStop(delivery_id=order.delivery.id, sequence=0))  # type: ignore[union-attr]
    db.add(plan)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# --------------------------------------------------------------------------- enums


def test_enum_values_are_stored_as_strings(
    db: Session, make_customer: Callable[..., Customer], make_order: Callable[..., Order]
) -> None:
    customer = make_customer(language=Language.TG)
    order = make_order(customer=customer, status=OrderStatus.WAITING_CONFIRMATION)

    raw = db.execute(
        sa.text("SELECT status, payment_status, source FROM orders WHERE id = :id"), {"id": order.id}
    ).one()
    assert tuple(raw) == ("WAITING_CONFIRMATION", "UNPAID", "ADMIN")
    raw_language = db.execute(sa.text("SELECT language FROM customers WHERE id = :id"), {"id": customer.id}).scalar_one()
    assert raw_language == "tg"

    db.expire_all()
    assert db.get(Order, order.id).status is OrderStatus.WAITING_CONFIRMATION  # type: ignore[union-attr]
    assert db.get(Customer, customer.id).language is Language.TG  # type: ignore[union-attr]


def test_enum_accepts_value_strings(db: Session, make_customer: Callable[..., Customer]) -> None:
    customer = make_customer(language="tg")
    db.expire_all()
    assert db.get(Customer, customer.id).language is Language.TG  # type: ignore[union-attr]


def test_enum_rejects_unknown_values_before_sql(db: Session, make_order: Callable[..., Order]) -> None:
    order = make_order()
    order.status = "SHIPPED"  # type: ignore[assignment]
    with pytest.raises((StatementError, LookupError)):
        db.flush()
    db.rollback()


def test_enum_check_constraint_exists_in_database(db: Session, make_order: Callable[..., Order]) -> None:
    order = make_order()
    with pytest.raises(IntegrityError):
        db.execute(sa.text("UPDATE orders SET status = 'SHIPPED' WHERE id = :id"), {"id": order.id})
    db.rollback()


# --------------------------------------------------------------------------- relationships & cascades


def test_order_items_are_deleted_with_order(
    db: Session, make_order: Callable[..., Order], make_product: Callable[..., Product]
) -> None:
    order = make_order(items=[(make_product(), 1), (make_product(), 2)], paid_amount="50")
    order.events.append(OrderEvent(actor_type=ActorType.SYSTEM, event_type="CREATED"))
    db.commit()
    order_id = order.id
    assert _count(db, OrderItem) == 2

    db.delete(order)
    db.commit()

    assert db.get(Order, order_id) is None
    assert _count(db, OrderItem) == 0
    assert _count(db, Payment) == 0
    assert _count(db, OrderEvent) == 0


def test_removing_item_from_order_deletes_it(
    db: Session, make_order: Callable[..., Order], make_product: Callable[..., Product]
) -> None:
    order = make_order(items=[(make_product(), 1), (make_product(), 1)])
    removed_id = order.items[0].id
    order.items.remove(order.items[0])
    db.commit()
    assert db.get(OrderItem, removed_id) is None
    assert len(order.items) == 1


def test_database_level_on_delete_rules(
    db: Session, make_order: Callable[..., Order], make_product: Callable[..., Product]
) -> None:
    product = make_product("Чизкейк")
    order = make_order(items=[(product, 3)], delivery={"address_raw": "Шохмансур, 10"}, paid_amount="10")
    assert order.delivery is not None
    db.add(LocationRequest(token="t-1", delivery_id=order.delivery.id, expires_at=now_utc()))
    db.commit()
    order_id, product_id = order.id, product.id
    db.expunge_all()

    # products → order_items: ON DELETE SET NULL (the name snapshot stays)
    db.execute(sa.text("DELETE FROM products WHERE id = :id"), {"id": product_id})
    row = db.execute(sa.text("SELECT product_id, product_name FROM order_items WHERE order_id = :id"), {"id": order_id}).one()
    assert tuple(row) == (None, "Чизкейк")

    # orders → items, payments, deliveries → location_requests: ON DELETE CASCADE
    db.execute(sa.text("DELETE FROM orders WHERE id = :id"), {"id": order_id})
    for table in ("order_items", "payments", "deliveries", "location_requests"):
        assert db.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one() == 0, table  # noqa: S608
    db.commit()


def test_items_ordered_by_id_and_route_stops_by_sequence(
    db: Session, make_order: Callable[..., Order], make_product: Callable[..., Product]
) -> None:
    orders = [make_order(delivery={"address_raw": f"Адрес {n}"}) for n in range(3)]
    plan = RoutePlan(
        delivery_date=date(2026, 9, 16),
        start_name="Кухня",
        start_latitude=Decimal("38.56"),
        start_longitude=Decimal("68.77"),
        start_time=time(9, 0),
        algorithm="brute_force",
        distance_source="haversine",
    )
    for sequence, order in zip((3, 1, 2), orders, strict=True):
        plan.stops.append(RouteStop(delivery_id=order.delivery.id, sequence=sequence))  # type: ignore[union-attr]
    db.add(plan)
    db.commit()
    db.expire_all()
    assert [stop.sequence for stop in plan.stops] == [1, 2, 3]

    multi = make_order(items=[(make_product(), 1), (make_product(), 2), (make_product(), 3)])
    db.expire_all()
    ids = [item.id for item in multi.items]
    assert ids == sorted(ids)
    assert [item.quantity for item in multi.items] == [1, 2, 3]


def test_customer_orders_relationship(
    make_customer: Callable[..., Customer], make_order: Callable[..., Order]
) -> None:
    customer = make_customer("Постоянный")
    first = make_order(customer=customer)
    second = make_order(customer=customer)
    assert [order.id for order in customer.orders] == [first.id, second.id]


def test_json_columns_track_in_place_changes(
    db: Session, make_customer: Callable[..., Customer], make_product: Callable[..., Product]
) -> None:
    conversation = Conversation(customer=make_customer())
    db.add(conversation)
    db.commit()
    conversation.state["draft_order_id"] = 12
    conversation.state["awaiting"] = "confirmation"
    product = make_product(aliases=["медовик"])
    product.aliases.append("асалӣ")
    db.commit()
    db.expire_all()
    assert db.get(Conversation, conversation.id).state == {"draft_order_id": 12, "awaiting": "confirmation"}  # type: ignore[union-attr]
    assert db.get(Product, product.id).aliases == ["медовик", "асалӣ"]  # type: ignore[union-attr]


def test_nullable_json_payload_is_sql_null(db: Session, make_customer: Callable[..., Customer]) -> None:
    conversation = Conversation(customer=make_customer())
    conversation.messages.append(
        Message(direction=MessageDirection.OUTGOING, message_type=MessageType.TEXT, sender=MessageSender.AI, text="Привет")
    )
    db.add(conversation)
    db.commit()
    raw = db.execute(sa.text("SELECT ai_payload IS NULL FROM messages")).scalar_one()
    assert bool(raw) is True


# --------------------------------------------------------------------------- datetimes


def test_datetimes_round_trip_as_aware_utc(db: Session, make_order: Callable[..., Order]) -> None:
    local = datetime(2026, 9, 15, 23, 30, tzinfo=ZoneInfo("Asia/Dushanbe"))
    order = make_order(status=OrderStatus.CONFIRMED, confirmed_at=local)
    order.cancelled_at = datetime(2026, 9, 15, 10, 0)  # naive → treated as UTC
    db.commit()
    db.expire_all()
    loaded = db.get(Order, order.id)
    assert loaded is not None
    assert loaded.confirmed_at == local
    assert loaded.confirmed_at is not None and _is_utc(loaded.confirmed_at)
    assert loaded.confirmed_at.hour == 18
    assert loaded.cancelled_at is not None and loaded.cancelled_at.hour == 10 and _is_utc(loaded.cancelled_at)


def test_updated_at_changes_on_update(db: Session, make_product: Callable[..., Product]) -> None:
    product = make_product()
    created_at, first_updated_at = product.created_at, product.updated_at
    time_module.sleep(0.02)
    product.price = Decimal("120.00")
    db.commit()
    db.refresh(product)
    assert product.created_at == created_at
    assert product.updated_at > first_updated_at


# --------------------------------------------------------------------------- factories


def test_make_order_factory_computes_totals(
    make_order: Callable[..., Order], make_product: Callable[..., Product], db: Session
) -> None:
    medovik = make_product("Медовик", price="150.00")
    cheesecake = make_product("Чизкейк", price="33.33")
    order = make_order(
        items=[(medovik, 2), {"product": cheesecake, "quantity": 3, "comment": "без сахара"}],
        status=OrderStatus.CONFIRMED,
        paid_amount="100",
    )
    assert [item.total_price for item in order.items] == [Decimal("300.00"), Decimal("99.99")]
    assert [item.product_name for item in order.items] == ["Медовик", "Чизкейк"]
    assert order.items[1].comment == "без сахара"
    assert order.total_amount == Decimal("399.99")
    assert order.paid_amount == Decimal("100.00")
    assert order.payment_status is PaymentStatus.PARTIALLY_PAID
    assert order.confirmed_at is not None
    assert len(order.payments) == 1

    # the price snapshot does not follow later product price changes (03 §1.4)
    medovik.price = Decimal("999.00")
    db.commit()
    db.refresh(order)
    assert order.items[0].unit_price == Decimal("150.00")


def test_make_order_factory_statuses_and_delivery(make_order: Callable[..., Order]) -> None:
    completed = make_order(status=OrderStatus.COMPLETED, paid_amount="100")
    assert completed.completed_at is not None
    assert completed.payment_status is PaymentStatus.PAID
    cancelled = make_order(status=OrderStatus.CANCELLED)
    assert cancelled.cancelled_at is not None
    assert cancelled.confirmed_at is None

    with_delivery = make_order(
        delivery={"address_raw": "82 мкр", "latitude": Decimal("38.580000"), "longitude": Decimal("68.790000")},
    )
    assert with_delivery.delivery is not None
    assert with_delivery.delivery_address == "82 мкр"
    assert with_delivery.delivery_latitude == Decimal("38.580000")
