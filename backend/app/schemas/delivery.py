"""Delivery schemas (04-api.md §5 ``DeliveryIn``, §9 Deliveries).

``DeliveryIn`` / ``DeliveryOut`` are shared with the Orders domain; the rest belongs to
``/api/deliveries``: the staff update body, the list item with its order, route plans and the
Maxim dispatch card.
"""

from datetime import date, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import (
    DeliveryStatus,
    DispatchProvider,
    GeocodeStatus,
    LocationSource,
    OrderStatus,
    PaymentStatus,
)
from app.schemas.common import HHMM, Money, ORMModel

TEXT_MAX = 1000


class DeliveryIn(BaseModel):
    """Address / recipient block of an order. Every field is optional; blank strings mean ``null``.

    Coordinates come in pairs; given coordinates → ``location_source=OPERATOR``, ``geocode_status=MANUAL``.
    ``recipient_phone`` is normalized by ``DeliveryService`` (03 §2).
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    address_raw: str | None = Field(default=None, max_length=TEXT_MAX)
    district: str | None = Field(default=None, max_length=64)
    microdistrict: str | None = Field(default=None, max_length=32)
    street: str | None = Field(default=None, max_length=128)
    house: str | None = Field(default=None, max_length=32)
    apartment: str | None = Field(default=None, max_length=16)
    entrance: str | None = Field(default=None, max_length=16)
    floor: str | None = Field(default=None, max_length=8)
    landmark: str | None = Field(default=None, max_length=TEXT_MAX)
    recipient_name: str | None = Field(default=None, max_length=128)
    recipient_phone: str | None = Field(default=None, max_length=64)
    courier_comment: str | None = Field(default=None, max_length=TEXT_MAX)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _coordinates_in_pairs(self) -> Self:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Укажите обе координаты: широту и долготу")
        return self


class GeoCandidateOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    formatted: str = ""
    lat: float
    lng: float
    precision: str = "other"


class DeliveryOut(ORMModel):
    id: int
    order_id: int
    address_raw: str
    address_formatted: str | None = None
    city: str
    district: str | None = None
    microdistrict: str | None = None
    street: str | None = None
    house: str | None = None
    apartment: str | None = None
    entrance: str | None = None
    floor: str | None = None
    landmark: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_source: LocationSource | None = None
    geocode_status: GeocodeStatus
    geocode_provider: str | None = None
    geocode_candidates: list[GeoCandidateOut] = Field(default_factory=list)
    recipient_name: str | None = None
    recipient_phone: str | None = None
    courier_comment: str | None = None
    status: DeliveryStatus
    dispatch_provider: DispatchProvider | None = None
    external_id: str | None = None
    external_status: str | None = None
    courier_name: str | None = None
    courier_phone: str | None = None
    dispatched_at: datetime | None = None
    delivered_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("geocode_candidates", mode="before")
    @classmethod
    def _drop_malformed_candidates(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, list):
            return [
                item
                for item in value
                if isinstance(item, dict) and item.get("lat") is not None and item.get("lng") is not None
            ]
        return value


# --------------------------------------------------------------------------- deliveries API (§9)


class DeliveryUpdate(DeliveryIn):
    """``PATCH /deliveries/{id}``: address block plus the fields the operator fills in by hand.

    Entering ``external_id`` / courier data means the order has been created in Maxim, so the
    delivery becomes ``DISPATCHED`` (06 §4); an explicit ``status`` always wins.
    """

    status: DeliveryStatus | None = None
    external_id: str | None = Field(default=None, max_length=64)
    external_status: str | None = Field(default=None, max_length=64)
    courier_name: str | None = Field(default=None, max_length=128)
    courier_phone: str | None = Field(default=None, max_length=32)


class DeliveryOrderCustomerRef(ORMModel):
    id: int
    name: str | None = None
    phone: str | None = None


class DeliveryOrderRef(BaseModel):
    """The order behind a delivery, as shown in the deliveries list (04 §9 ``DeliveryListItem``)."""

    id: int
    status: OrderStatus
    payment_status: PaymentStatus
    total_amount: Money
    paid_amount: Money
    delivery_date: date | None = None
    delivery_time: HHMM | None = None
    items_summary: str = ""
    customer: DeliveryOrderCustomerRef


class DeliveryListItem(DeliveryOut):
    order: DeliveryOrderRef


class SelectCandidateIn(BaseModel):
    """``POST /deliveries/{id}/select-candidate``: index in ``geocode_candidates``."""

    index: int = Field(ge=0)


class LocationLinkOut(BaseModel):
    """``POST /deliveries/{id}/location-link``: the link sent to the customer (TTL 48 h)."""

    url: str
    expires_at: datetime


# --------------------------------------------------------------------------- route plan (§9)


class RouteOptimizeIn(BaseModel):
    """``POST /deliveries/optimize``. ``start_time`` defaults to ``BusinessSettings.route_start_time``."""

    date: date
    start_time: HHMM | None = None


class RouteStart(BaseModel):
    """Route start point: the warehouse / kitchen from the settings."""

    name: str
    latitude: float
    longitude: float


class RouteStopOut(BaseModel):
    sequence: int
    delivery_id: int
    order_id: int
    address: str
    latitude: float
    longitude: float
    #: The point is not the address but the place the geocoder found (microdistrict, street, landmark).
    approximate: bool = False
    approximate_place: str | None = None
    eta: HHMM | None = None
    desired_time: HHMM | None = None
    lateness_min: int = 0
    distance_from_prev_m: int = 0
    duration_from_prev_s: int = 0
    recipient_name: str | None = None
    phone: str | None = None
    courier_comment: str | None = None
    items_summary: str = ""


class RouteUnlocatedOut(BaseModel):
    """Delivery left out of the route: no coordinates yet (03 §7)."""

    delivery_id: int
    order_id: int
    address: str
    reason: str


class RoutePlanOut(BaseModel):
    id: int
    delivery_date: date
    start: RouteStart
    start_time: HHMM
    algorithm: str
    distance_source: str  # "osrm" | "haversine"
    total_distance_m: int
    total_duration_s: int
    created_at: datetime
    stops: list[RouteStopOut] = Field(default_factory=list)
    unlocated: list[RouteUnlocatedOut] = Field(default_factory=list)


# --------------------------------------------------------------------------- dispatch (§9)


class DispatchResultOut(BaseModel):
    """``POST /deliveries/{id}/dispatch``: the card the operator copies into Maxim (06 §4)."""

    delivery: DeliveryOut
    provider: DispatchProvider
    requires_operator: bool
    instructions: str
    copy_text: str


class DispatchSheetStop(BaseModel):
    """One line of the dispatch sheet; ``latitude``/``longitude`` are null until the point is known."""

    sequence: int
    delivery_id: int
    order_id: int
    address: str
    latitude: float | None = None
    longitude: float | None = None
    eta: HHMM | None = None
    desired_time: HHMM | None = None
    recipient_name: str | None = None
    phone: str | None = None
    courier_comment: str | None = None
    items_summary: str = ""
    amount_to_collect: Money
    status: DeliveryStatus
    external_id: str | None = None


class DispatchSheetOut(BaseModel):
    """``GET /deliveries/dispatch-sheet``: copyable list in route order."""

    date: date
    text: str
    stops: list[DispatchSheetStop] = Field(default_factory=list)


def build_order_ref(order: Any) -> DeliveryOrderRef:
    """``Order`` (items eager-loaded) → the reference shown next to a delivery."""
    from app.schemas.order import items_summary  # local import: avoids a schemas import cycle

    return DeliveryOrderRef(
        id=order.id,
        status=order.status,
        payment_status=order.payment_status,
        total_amount=order.total_amount,
        paid_amount=order.paid_amount,
        delivery_date=order.delivery_date,
        delivery_time=order.delivery_time,
        items_summary=items_summary(order),
        customer=DeliveryOrderCustomerRef.model_validate(order.customer),
    )


def build_delivery_list_item(delivery: Any) -> DeliveryListItem:
    """``Delivery`` (with ``order`` → ``items``/``customer`` eager-loaded) → ``DeliveryListItem``."""
    base = DeliveryOut.model_validate(delivery)
    return DeliveryListItem(**base.model_dump(), order=build_order_ref(delivery.order))
