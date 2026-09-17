"""Delivery aggregate (02-data-model.md: deliveries, location_requests, route_plans, route_stops)."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UTCDateTime,
    coordinate_type,
    enum_type,
    json_list_type,
)
from app.models.enums import DeliveryStatus, DispatchProvider, GeocodeStatus, LocationSource

if TYPE_CHECKING:
    from app.models.order import Order
    from app.models.user import User

DEFAULT_CITY = "Худжанд"


class Delivery(TimestampMixin, Base):
    """Structured address and delivery, 1:1 with an order of type DELIVERY."""

    __tablename__ = "deliveries"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        sa.ForeignKey("orders.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    address_raw: Mapped[str] = mapped_column(sa.Text, nullable=False)
    address_formatted: Mapped[str | None] = mapped_column(sa.Text)
    city: Mapped[str] = mapped_column(
        sa.String(64), nullable=False, default=DEFAULT_CITY, server_default=DEFAULT_CITY
    )
    district: Mapped[str | None] = mapped_column(sa.String(64))
    microdistrict: Mapped[str | None] = mapped_column(sa.String(32))
    street: Mapped[str | None] = mapped_column(sa.String(128))
    house: Mapped[str | None] = mapped_column(sa.String(32))
    apartment: Mapped[str | None] = mapped_column(sa.String(16))
    entrance: Mapped[str | None] = mapped_column(sa.String(16))
    floor: Mapped[str | None] = mapped_column(sa.String(8))
    landmark: Mapped[str | None] = mapped_column(sa.Text)
    latitude: Mapped[Decimal | None] = mapped_column(coordinate_type())
    longitude: Mapped[Decimal | None] = mapped_column(coordinate_type())
    location_source: Mapped[LocationSource | None] = mapped_column(enum_type(LocationSource, "location_source"))
    geocode_status: Mapped[GeocodeStatus] = mapped_column(
        enum_type(GeocodeStatus, "geocode_status"),
        nullable=False,
        default=GeocodeStatus.PENDING,
        server_default=GeocodeStatus.PENDING.value,
    )
    geocode_provider: Mapped[str | None] = mapped_column(sa.String(32))
    # [{"formatted": str, "lat": float, "lng": float, "precision": str}]
    geocode_candidates: Mapped[list[dict[str, Any]]] = mapped_column(json_list_type(), nullable=False, default=list)
    recipient_name: Mapped[str | None] = mapped_column(sa.String(128))
    recipient_phone: Mapped[str | None] = mapped_column(sa.String(32))
    courier_comment: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[DeliveryStatus] = mapped_column(
        enum_type(DeliveryStatus, "status"),
        nullable=False,
        default=DeliveryStatus.PENDING,
        server_default=DeliveryStatus.PENDING.value,
    )
    dispatch_provider: Mapped[DispatchProvider | None] = mapped_column(
        enum_type(DispatchProvider, "dispatch_provider")
    )
    external_id: Mapped[str | None] = mapped_column(sa.String(64))
    external_status: Mapped[str | None] = mapped_column(sa.String(64))
    courier_name: Mapped[str | None] = mapped_column(sa.String(128))
    courier_phone: Mapped[str | None] = mapped_column(sa.String(32))
    dispatched_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    order: Mapped["Order"] = relationship(back_populates="delivery")
    location_requests: Mapped[list["LocationRequest"]] = relationship(
        back_populates="delivery",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="LocationRequest.id",
    )
    route_stops: Mapped[list["RouteStop"]] = relationship(
        back_populates="delivery",
        cascade="all",
        passive_deletes=True,
    )

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


class LocationRequest(CreatedAtMixin, Base):
    """Link for the customer to pick a point on the map (``/l/{token}``)."""

    __tablename__ = "location_requests"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    token: Mapped[str] = mapped_column(sa.String(64), unique=True, nullable=False)
    delivery_id: Mapped[int] = mapped_column(
        sa.ForeignKey("deliveries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    latitude: Mapped[Decimal | None] = mapped_column(coordinate_type())
    longitude: Mapped[Decimal | None] = mapped_column(coordinate_type())

    delivery: Mapped[Delivery] = relationship(back_populates="location_requests")


class RoutePlan(TimestampMixin, Base):
    __tablename__ = "route_plans"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    delivery_date: Mapped[date] = mapped_column(sa.Date, nullable=False, index=True)
    start_name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    start_latitude: Mapped[Decimal] = mapped_column(coordinate_type(), nullable=False)
    start_longitude: Mapped[Decimal] = mapped_column(coordinate_type(), nullable=False)
    start_time: Mapped[time] = mapped_column(sa.Time, nullable=False)
    algorithm: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    distance_source: Mapped[str] = mapped_column(sa.String(32), nullable=False)  # "osrm" | "haversine"
    total_distance_m: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")
    total_duration_s: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")
    created_by_user_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))

    stops: Mapped[list["RouteStop"]] = relationship(
        back_populates="route_plan",
        cascade="all, delete-orphan",
        order_by="RouteStop.sequence",
    )
    created_by_user: Mapped[Optional["User"]] = relationship()


class RouteStop(TimestampMixin, Base):
    __tablename__ = "route_stops"
    __table_args__ = (sa.CheckConstraint("sequence >= 1", name="sequence_positive"),)

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    route_plan_id: Mapped[int] = mapped_column(
        sa.ForeignKey("route_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    delivery_id: Mapped[int] = mapped_column(
        sa.ForeignKey("deliveries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    eta: Mapped[time | None] = mapped_column(sa.Time)
    distance_from_prev_m: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")
    duration_from_prev_s: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")
    lateness_min: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")

    route_plan: Mapped[RoutePlan] = relationship(back_populates="stops")
    delivery: Mapped[Delivery] = relationship(back_populates="route_stops")
