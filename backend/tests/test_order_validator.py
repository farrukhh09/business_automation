"""``order_validator.py`` — completeness and timing checks (03-business-rules.md §1.3)."""

from datetime import datetime, time, timedelta

from app.core.time import business_today, now_utc
from app.models.customer import Customer
from app.models.delivery import Delivery
from app.models.enums import DeliveryType, GeocodeStatus
from app.models.order import Order, OrderItem
from app.schemas.settings import BusinessSettings
from app.services.order_validator import OrderValidator


def _pickup_order(**overrides) -> Order:
    customer = overrides.pop("customer", None)
    items = overrides.pop("items", None)
    return Order(
        delivery_type=DeliveryType.PICKUP,
        delivery_date=business_today() + timedelta(days=1),
        delivery_time=time(18, 0),
        customer=customer,
        items=items if items is not None else [_item()],
        **overrides,
    )


def _item(quantity: int = 1, active: bool = True, deleted: bool = False) -> OrderItem:
    from app.models.product import Product

    product = Product(name="Медовик", price="100.00", is_active=active)
    if deleted:
        product.deleted_at = now_utc()
    item = OrderItem(
        product=product, product_name="Медовик", quantity=quantity, unit_price="100.00", total_price="100.00"
    )
    return item


def test_complete_pickup_order_has_no_missing_fields() -> None:
    order = _pickup_order(customer=Customer(name="Клиент", phone="+992900000000"))
    assert OrderValidator.missing_fields(order) == []
    assert OrderValidator.is_complete(order)


def test_missing_fields_are_reported_in_asking_order() -> None:
    order = Order(delivery_type=None, delivery_date=None, delivery_time=None, items=[], customer=None)
    missing = OrderValidator.missing_fields(order)
    assert missing == ["items", "delivery_date", "delivery_time", "delivery_type", "customer_name", "phone"]


def test_item_without_active_product_counts_as_missing() -> None:
    order = _pickup_order(customer=Customer(name="Клиент", phone="+992900000000"), items=[_item(active=False)])
    assert "items" in OrderValidator.missing_fields(order)

    order2 = _pickup_order(customer=Customer(name="Клиент", phone="+992900000000"), items=[_item(deleted=True)])
    assert "items" in OrderValidator.missing_fields(order2)


def test_past_delivery_date_counts_as_missing() -> None:
    order = _pickup_order(customer=Customer(name="Клиент", phone="+992900000000"))
    order.delivery_date = business_today() - timedelta(days=1)
    assert "delivery_date" in OrderValidator.missing_fields(order)


def test_delivery_order_requires_address_recipient_and_location() -> None:
    order = Order(
        delivery_type=DeliveryType.DELIVERY,
        delivery_date=business_today() + timedelta(days=1),
        delivery_time=time(18, 0),
        items=[_item()],
        customer=Customer(phone="+992900000000"),  # no name -> recipient_name must cover it
    )
    missing = OrderValidator.missing_fields(order)
    assert "address" in missing
    assert "recipient_name" in missing
    assert "location" in missing
    assert "customer_name" in missing  # neither customer.name nor delivery.recipient_name is filled

    order.delivery = Delivery(
        address_raw="Душанбе, ул. Рудаки, 1",
        recipient_name="Получатель",
        recipient_phone="+992900000001",
        latitude="38.56",
        longitude="68.77",
        geocode_status=GeocodeStatus.OK,
    )
    missing = OrderValidator.missing_fields(order)
    assert missing == []


def _delivery_order(**delivery_fields) -> Order:
    return Order(
        delivery_type=DeliveryType.DELIVERY,
        delivery_date=business_today() + timedelta(days=1),
        delivery_time=time(18, 0),
        items=[_item()],
        customer=Customer(name="Клиент", phone="+992900000000"),
        delivery=Delivery(address_raw="Душанбе", recipient_name="Получатель", **delivery_fields),
    )


def test_delivery_location_is_asked_only_while_geocoding_is_pending() -> None:
    # 03 §1.3/§7: not looked up yet → the point is asked for
    assert "location" in OrderValidator.missing_fields(_delivery_order(geocode_status=GeocodeStatus.PENDING))
    # looked up without a usable point (variants / nothing / provider down) → the order goes on,
    # the map link was offered and the operator sees the delivery without coordinates
    for status in (GeocodeStatus.AMBIGUOUS, GeocodeStatus.NOT_FOUND, GeocodeStatus.FAILED):
        assert "location" not in OrderValidator.missing_fields(_delivery_order(geocode_status=status)), status
    # a person's or the geocoder's accepted point
    located = _delivery_order(latitude="38.56", longitude="68.77", geocode_status=GeocodeStatus.MANUAL)
    assert "location" not in OrderValidator.missing_fields(located)


def test_timing_problems_too_soon_and_too_far() -> None:
    settings = BusinessSettings(min_lead_time_hours=24, max_days_ahead=60)
    now = datetime(2026, 9, 15, 12, 0, tzinfo=now_utc().tzinfo)

    today = business_today()
    soon = Order(delivery_date=today, delivery_time=time(23, 0))
    assert OrderValidator.timing_problems(soon, settings) == ["delivery_too_soon"]

    far = Order(delivery_date=today + timedelta(days=90), delivery_time=time(13, 0))
    assert OrderValidator.timing_problems(far, settings) == ["delivery_too_far"]

    fine = Order(delivery_date=today + timedelta(days=4), delivery_time=time(13, 0))
    assert OrderValidator.timing_problems(fine, settings) == []

    # a date that has already passed is a slip on the month, reported on its own
    past = Order(delivery_date=today - timedelta(days=40), delivery_time=time(13, 0))
    assert OrderValidator.timing_problems(past, settings, now) == ["delivery_date_past"]


def test_timing_problems_empty_when_date_unknown() -> None:
    settings = BusinessSettings()
    order = Order(delivery_date=None, delivery_time=None)
    assert OrderValidator.timing_problems(order, settings) == []
