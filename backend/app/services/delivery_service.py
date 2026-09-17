"""Delivery core (03-business-rules.md §7): structured address, coordinates, order copy.

Transactions: methods of this service only **flush**; the calling service commits (one commit per
operation). ``request_geocoding`` enqueues a Celery task that reads the delivery in another session,
so call it **after** the commit::

    delivery = DeliveryService(db).upsert_for_order(order, data, actor_user=user)
    db.commit()
    if DeliveryService.needs_geocoding(delivery):
        DeliveryService(db).request_geocoding(delivery)

After ``upsert_for_order`` / ``apply_location`` the diff of the last call is in ``last_changes``
(``{"field": [old, new]}``, JSON-serializable) for ``OrderEvent(DELIVERY_CHANGED)``.
"""

import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from typing import Any

from kombu.exceptions import OperationalError as BrokerOperationalError
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessRuleError, ValidationError
from app.core.logging import get_logger, log_event
from app.models.delivery import Delivery
from app.models.enums import DeliveryStatus, DeliveryType, GeocodeStatus, LocationSource
from app.models.order import Order
from app.models.user import User
from app.repositories.deliveries import DeliveryRepository
from app.schemas.delivery import DeliveryIn
from app.services.constants import is_inside_tajikistan
from app.services.phone import normalize_phone

logger = get_logger(__name__)

# Fields that define where the point is: changing one of them without new coordinates invalidates the
# current coordinates. Apartment/entrance/floor/landmark refine the address but do not move the point.
GEO_ADDRESS_FIELDS: tuple[str, ...] = ("address_raw", "district", "microdistrict", "street", "house")
TEXT_FIELDS: tuple[str, ...] = (
    "address_raw",
    "district",
    "microdistrict",
    "street",
    "house",
    "apartment",
    "entrance",
    "floor",
    "landmark",
    "recipient_name",
    "recipient_phone",
    "courier_comment",
)
# Deliveries already handed to a courier cannot be removed (03 §7 "если нет dispatch").
LOCKED_DELIVERY_STATUSES = frozenset({DeliveryStatus.DISPATCHED, DeliveryStatus.DELIVERED})
MANUAL_LOCATION_SOURCES = frozenset({LocationSource.CUSTOMER_PIN, LocationSource.OPERATOR, LocationSource.COURIER})
COORDINATE_QUANT = Decimal("0.000001")


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return float(value)
    return value


def _to_coordinate(value: Any) -> Decimal:
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
        if not number.is_finite():
            raise InvalidOperation
        return number.quantize(COORDINATE_QUANT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise BusinessRuleError("location_invalid", "Некорректные координаты") from exc


class DeliveryService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = DeliveryRepository(db)
        self.last_changes: dict[str, list[Any]] = {}

    # ------------------------------------------------------------------ upsert / remove

    def upsert_for_order(
        self,
        order: Order,
        data: DeliveryIn | dict[str, Any],
        actor_user: User | None = None,
    ) -> Delivery:
        """Create or update the order's delivery from ``data`` (only fields present in ``data``).

        - order must have ``delivery_type=DELIVERY`` → else ``BusinessRuleError("delivery_not_applicable")``;
        - a new delivery without ``address_raw`` is stored with ``address_raw=""`` (address still missing);
        - ``recipient_phone`` is normalized; invalid → ``BusinessRuleError("invalid_phone")``;
        - ``latitude``+``longitude`` → ``apply_location(..., OPERATOR)``;
        - a changed location field (``GEO_ADDRESS_FIELDS``) without coordinates → coordinates reset,
          ``geocode_status=PENDING``, candidates cleared (then call ``request_geocoding`` after commit);
        - order copy (``delivery_address``/``delivery_latitude``/``delivery_longitude``) is synced.
        """
        if not isinstance(data, DeliveryIn):
            try:
                data = DeliveryIn.model_validate(data)
            except PydanticValidationError as exc:
                raise ValidationError(detail="Некорректные данные доставки") from exc
        if order.delivery_type != DeliveryType.DELIVERY:
            raise BusinessRuleError(
                "delivery_not_applicable",
                "Адрес доставки можно указать только для заказа с доставкой",
            )

        self.last_changes = {}
        provided = data.model_dump(exclude_unset=True)
        latitude = provided.pop("latitude", None)
        longitude = provided.pop("longitude", None)

        if provided.get("recipient_phone") is not None:
            normalized = normalize_phone(provided["recipient_phone"])
            if normalized is None:
                raise BusinessRuleError(
                    "invalid_phone", "Некорректный номер телефона получателя", field="recipient_phone"
                )
            provided["recipient_phone"] = normalized

        delivery = order.delivery
        created = delivery is None
        if delivery is None:
            delivery = Delivery(
                address_raw="",
                geocode_status=GeocodeStatus.PENDING,
                geocode_candidates=[],
                status=DeliveryStatus.PENDING,
            )
            order.delivery = delivery

        for field in TEXT_FIELDS:
            if field not in provided:
                continue
            value = provided[field]
            if field == "address_raw" and value is None:
                continue  # NOT NULL column: null means "not provided"
            old = getattr(delivery, field)
            if old != value:
                setattr(delivery, field, value)
                self._record(field, old, value)

        address_changed = any(field in self.last_changes for field in GEO_ADDRESS_FIELDS)
        if latitude is not None and longitude is not None:
            self._apply_location(delivery, latitude, longitude, LocationSource.OPERATOR)
        elif address_changed and not created:
            self._reset_location(delivery)

        self.sync_order_copy(delivery)
        self.db.flush()
        log_event(
            logger,
            "delivery.created" if created else "delivery.updated",
            order_id=order.id,
            delivery_id=delivery.id,
            fields=sorted(self.last_changes),
            actor_user_id=actor_user.id if actor_user else None,
        )
        return delivery

    def remove_for_order(self, order: Order) -> bool:
        """Delete the order's delivery (switch to PICKUP). Returns ``False`` when there is none.

        Dispatched/delivered → ``BusinessRuleError("delivery_already_dispatched")``.
        """
        delivery = order.delivery
        if delivery is None:
            return False
        if delivery.status in LOCKED_DELIVERY_STATUSES:
            raise BusinessRuleError(
                "delivery_already_dispatched",
                "Доставка уже передана курьеру — её нельзя удалить",
                delivery_id=delivery.id,
            )
        delivery_id = delivery.id
        order.delivery = None  # delete-orphan cascade removes the row on flush
        if inspect(delivery).persistent:
            self.db.delete(delivery)
        order.delivery_address = None
        order.delivery_latitude = None
        order.delivery_longitude = None
        self.db.flush()
        log_event(logger, "delivery.removed", order_id=order.id, delivery_id=delivery_id)
        return True

    # ------------------------------------------------------------------ location

    def apply_location(
        self,
        delivery: Delivery,
        latitude: float | Decimal | str,
        longitude: float | Decimal | str,
        source: LocationSource,
        formatted: str | None = None,
    ) -> Delivery:
        """Set coordinates from ``source`` and copy them to the order (flush only).

        Outside Tajikistan (lat 36.6..41.1, lng 67.3..75.2) → ``BusinessRuleError("location_out_of_bounds")``.
        ``geocode_status`` = ``OK`` for ``GEOCODER``, ``MANUAL`` for customer pin / operator / courier.
        """
        self.last_changes = {}
        self._apply_location(delivery, latitude, longitude, source, formatted)
        self.sync_order_copy(delivery)
        self.db.flush()
        return delivery

    def _apply_location(
        self,
        delivery: Delivery,
        latitude: Any,
        longitude: Any,
        source: LocationSource,
        formatted: str | None = None,
    ) -> None:
        lat = _to_coordinate(latitude)
        lng = _to_coordinate(longitude)
        if not is_inside_tajikistan(float(lat), float(lng)):
            raise BusinessRuleError("location_out_of_bounds", "Точка находится за пределами Таджикистана")
        source = LocationSource(source)
        status = GeocodeStatus.OK if source == LocationSource.GEOCODER else GeocodeStatus.MANUAL
        updates: dict[str, Any] = {
            "latitude": lat,
            "longitude": lng,
            "location_source": source,
            "geocode_status": status,
        }
        if formatted and formatted.strip():
            updates["address_formatted"] = formatted.strip()
        for field, value in updates.items():
            old = getattr(delivery, field)
            if old != value:
                setattr(delivery, field, value)
                self._record(field, old, value)
        log_event(
            logger,
            "delivery.location_set",
            delivery_id=delivery.id,
            order_id=delivery.order_id,
            source=source.value,
            geocode_status=status.value,
        )

    def _reset_location(self, delivery: Delivery) -> None:
        updates: dict[str, Any] = {
            "latitude": None,
            "longitude": None,
            "location_source": None,
            "geocode_status": GeocodeStatus.PENDING,
            "address_formatted": None,
            "geocode_provider": None,
        }
        for field, value in updates.items():
            old = getattr(delivery, field)
            if old != value:
                setattr(delivery, field, value)
                self._record(field, old, value)
        if delivery.geocode_candidates:
            delivery.geocode_candidates = []

    def sync_order_copy(self, delivery: Delivery) -> None:
        """Copy formatted (or raw) address and coordinates to ``orders.delivery_*`` (02: "копия")."""
        order = delivery.order
        if order is None:
            return
        order.delivery_address = delivery.address_formatted or (delivery.address_raw or None)
        order.delivery_latitude = delivery.latitude
        order.delivery_longitude = delivery.longitude

    # ------------------------------------------------------------------ geocoding

    @staticmethod
    def has_manual_location(delivery: Delivery) -> bool:
        """03 §7 source priority: the point was set by a person (customer pin / operator / courier).

        Automatic geocoding must not overwrite such a point — ``GEOCODER`` is the least trusted
        source. An explicit re-geocode requested by staff (``POST /deliveries/{id}/geocode``) is a
        human decision and may still call ``apply_location``.
        """
        return delivery.has_coordinates and delivery.location_source in MANUAL_LOCATION_SOURCES

    @staticmethod
    def needs_geocoding(delivery: Delivery) -> bool:
        """Address present, no coordinates yet, geocoding not attempted."""
        return (
            bool((delivery.address_raw or "").strip())
            and not delivery.has_coordinates
            and delivery.geocode_status == GeocodeStatus.PENDING
        )

    def request_geocoding(self, delivery: Delivery) -> bool:
        """Enqueue ``app.tasks.delivery.geocode_delivery`` (call after commit). ``False`` if not enqueued."""
        try:
            from app.tasks.delivery import geocode_delivery
        except ImportError as exc:
            log_event(
                logger,
                "delivery.geocoding_task_missing",
                level=logging.WARNING,
                delivery_id=delivery.id,
                error=type(exc).__name__,
            )
            return False
        try:
            geocode_delivery.delay(delivery.id)
        except BrokerOperationalError as exc:
            log_event(
                logger,
                "delivery.geocoding_enqueue_failed",
                level=logging.ERROR,
                delivery_id=delivery.id,
                error=type(exc).__name__,
            )
            return False
        log_event(logger, "delivery.geocoding_requested", delivery_id=delivery.id)
        return True

    # ------------------------------------------------------------------ helpers

    def _record(self, field: str, old: Any, new: Any) -> None:
        if field in self.last_changes:
            self.last_changes[field][1] = _json_value(new)
        else:
            self.last_changes[field] = [_json_value(old), _json_value(new)]
