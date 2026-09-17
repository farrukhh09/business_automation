"""Completeness and timing checks of an order (03-business-rules.md §1.3).

``missing_fields`` returns machine names **in the order the bot asks about them**; the admin panel
shows the same list in ``OrderDetail.missing_fields``. Entering ``WAITING_CONFIRMATION`` or
``CONFIRMED`` is refused while the list is not empty (422 ``order_incomplete``).

The timing checks (``timing_problems``) are separate on purpose: they block the bot (it offers
another slot) but never a manual order created by staff (03 §1.3).
"""

from datetime import date, datetime, time, timedelta

from app.core.time import business_today, combine_business, now_utc
from app.models.enums import DeliveryType, GeocodeStatus
from app.models.order import Order, OrderItem
from app.schemas.settings import BusinessSettings

# Field names, in the order the bot asks about them (03 §1.3).
ITEMS = "items"
DELIVERY_DATE = "delivery_date"
DELIVERY_TIME = "delivery_time"
DELIVERY_TYPE = "delivery_type"
CUSTOMER_NAME = "customer_name"
PHONE = "phone"
ADDRESS = "address"
RECIPIENT_NAME = "recipient_name"
LOCATION = "location"

MISSING_FIELD_ORDER: tuple[str, ...] = (
    ITEMS,
    DELIVERY_DATE,
    DELIVERY_TIME,
    DELIVERY_TYPE,
    CUSTOMER_NAME,
    PHONE,
    ADDRESS,
    RECIPIENT_NAME,
    LOCATION,
)

#: Coordinates are usable when a person placed the point or the geocoder accepted it (03 §7).
USABLE_GEOCODE_STATUSES: frozenset[GeocodeStatus] = frozenset({GeocodeStatus.OK, GeocodeStatus.MANUAL})

# Timing problems (not part of missing_fields).
TOO_SOON = "delivery_too_soon"
TOO_FAR = "delivery_too_far"
DATE_PAST = "delivery_date_past"  # "5 августа" said in September: a slip, not a slot that is too soon


def _filled(value: str | None) -> bool:
    return bool(value and value.strip())


def item_is_orderable(item: OrderItem) -> bool:
    """A position counts when its quantity is ≥ 1 and its product still exists and is active."""
    if item.quantity is None or item.quantity < 1:
        return False
    product = item.product
    return product is not None and product.is_active and product.deleted_at is None


class OrderValidator:
    """Stateless checks over an ``Order`` and its loaded relations."""

    @staticmethod
    def missing_fields(order: Order) -> list[str]:
        """Machine names of the required data the order still lacks (03 §1.3), in asking order."""
        delivery = order.delivery
        is_delivery = order.delivery_type == DeliveryType.DELIVERY
        missing: list[str] = []

        if not order.items or not all(item_is_orderable(item) for item in order.items):
            missing.append(ITEMS)
        if order.delivery_date is None or order.delivery_date < business_today():
            missing.append(DELIVERY_DATE)
        if order.delivery_time is None:
            missing.append(DELIVERY_TIME)
        if order.delivery_type is None:
            missing.append(DELIVERY_TYPE)

        recipient_name = delivery.recipient_name if delivery is not None else None
        customer_name = order.customer.name if order.customer is not None else None
        if not _filled(customer_name) and not (is_delivery and _filled(recipient_name)):
            missing.append(CUSTOMER_NAME)

        customer_phone = order.customer.phone if order.customer is not None else None
        recipient_phone = delivery.recipient_phone if delivery is not None else None
        if not _filled(customer_phone) and not _filled(recipient_phone):
            missing.append(PHONE)

        if is_delivery:
            if delivery is None or not _filled(delivery.address_raw):
                missing.append(ADDRESS)
            if not _filled(recipient_name):
                missing.append(RECIPIENT_NAME)
            if delivery is None or (not delivery.has_coordinates and delivery.geocode_status == GeocodeStatus.PENDING):
                # 03 §1.3/§7: the point is asked for while nobody has tried to locate the address yet.
                # Once geocoding ran (OK/MANUAL → coordinates; NOT_FOUND/AMBIGUOUS/FAILED → the map link
                # was offered and the operator sees the delivery without coordinates) the order goes on:
                # OpenStreetMap rarely knows house numbers in Khujand's microdistricts, and a customer
                # is never blocked by that.
                missing.append(LOCATION)
        return missing

    @classmethod
    def is_complete(cls, order: Order) -> bool:
        return not cls.missing_fields(order)

    @staticmethod
    def timing_problems(
        order: Order,
        settings: BusinessSettings,
        now: datetime | None = None,
    ) -> list[str]:
        """``["delivery_date_past"]`` / ``["delivery_too_soon"]`` / ``["delivery_too_far"]`` (03 §1.3),
        empty when the slot is fine.

        - date in the past: reported alone (the customer slipped on the month, not on the lead time);
        - too soon: the slot is earlier than ``now + min_lead_time_hours``; when the time is not
          known yet the end of the delivery day is used, so "today" is still reported as too soon;
        - too far: ``delivery_date`` is more than ``max_days_ahead`` days ahead.

        An unknown ``delivery_date`` is reported by ``missing_fields``, not here (empty list).
        """
        return OrderValidator.slot_problems(order.delivery_date, order.delivery_time, settings, now)

    @staticmethod
    def slot_problems(
        delivery_date: date | None,
        delivery_time: time | None,
        settings: BusinessSettings,
        now: datetime | None = None,
    ) -> list[str]:
        """``timing_problems`` for a candidate slot that is not stored yet (the bot checks a date/time
        the customer proposes before it touches the draft)."""
        if delivery_date is None:
            return []
        moment = now or now_utc()
        if delivery_date < business_today():
            return [DATE_PAST]
        problems: list[str] = []

        slot_time: time = delivery_time if delivery_time is not None else time(23, 59)
        slot = combine_business(delivery_date, slot_time)
        if slot < moment + timedelta(hours=settings.min_lead_time_hours):
            problems.append(TOO_SOON)
        if delivery_date > business_today() + timedelta(days=settings.max_days_ahead):
            problems.append(TOO_FAR)
        return problems
