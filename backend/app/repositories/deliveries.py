"""Delivery aggregate: deliveries, location requests, route plans (02-data-model.md; 04-api.md §9, §13)."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import contains_eager, joinedload, selectinload

from app.core.time import now_utc
from app.models.delivery import Delivery, LocationRequest, RoutePlan, RouteStop
from app.models.enums import DeliveryStatus, DeliveryType, GeocodeStatus
from app.models.order import Order
from app.repositories.base import BaseRepository, coerce_enum
from app.services.constants import VALID_ORDER_STATUSES, sorted_statuses

DELIVERY_STATUS_DETAIL = "Недопустимый статус доставки"
GEOCODE_STATUS_DETAIL = "Недопустимый статус геокодирования"


class DeliveryRepository(BaseRepository[Delivery]):
    model = Delivery
    not_found_detail = "Доставка не найдена"

    def get_by_order(self, order_id: int) -> Delivery | None:
        return self.db.scalars(select(Delivery).where(Delivery.order_id == order_id)).first()

    def get_with_order(self, delivery_id: int) -> Delivery | None:
        """Delivery with its order (+ items and customer) loaded."""
        stmt = (
            select(Delivery)
            .where(Delivery.id == delivery_id)
            .options(
                joinedload(Delivery.order).selectinload(Order.items),
                joinedload(Delivery.order).selectinload(Order.customer),
            )
        )
        return self.db.scalars(stmt).first()

    def list_for_date(
        self,
        delivery_date: date,
        status: DeliveryStatus | str | None = None,
        geocode_status: GeocodeStatus | str | None = None,
    ) -> list[Delivery]:
        """Deliveries of VALID ``DELIVERY`` orders with ``delivery_date`` = the date (04 §9).

        Order (+ items, customer) is loaded; sorted by the desired delivery time (NULLs last), then id.
        """
        stmt = (
            select(Delivery)
            .join(Delivery.order)
            .where(
                Order.delivery_date == delivery_date,
                Order.status.in_(sorted_statuses(VALID_ORDER_STATUSES)),
                Order.delivery_type == DeliveryType.DELIVERY,
            )
            .options(
                contains_eager(Delivery.order).selectinload(Order.items),
                contains_eager(Delivery.order).selectinload(Order.customer),
            )
            .order_by(Order.delivery_time.asc().nulls_last(), Delivery.id)
        )
        if status:
            stmt = stmt.where(Delivery.status == coerce_enum(DeliveryStatus, status, DELIVERY_STATUS_DETAIL))
        if geocode_status:
            stmt = stmt.where(
                Delivery.geocode_status == coerce_enum(GeocodeStatus, geocode_status, GEOCODE_STATUS_DETAIL)
            )
        return list(self.db.scalars(stmt).all())

    def list_by_status(self, status: DeliveryStatus | str) -> list[Delivery]:
        """E.g. all ``DISPATCHED`` deliveries for ``sync_delivery_statuses``."""
        wanted = coerce_enum(DeliveryStatus, status, DELIVERY_STATUS_DETAIL)
        stmt = select(Delivery).where(Delivery.status == wanted).order_by(Delivery.id)
        return list(self.db.scalars(stmt).all())


class LocationRequestRepository(BaseRepository[LocationRequest]):
    model = LocationRequest
    not_found_detail = "Ссылка не найдена"

    def get_by_token(self, token: str) -> LocationRequest | None:
        """Location request with its delivery and order loaded (public map page)."""
        stmt = (
            select(LocationRequest)
            .where(LocationRequest.token == token)
            .options(joinedload(LocationRequest.delivery).joinedload(Delivery.order))
        )
        return self.db.scalars(stmt).first()

    def latest_usable_for_delivery(self, delivery_id: int, now: datetime | None = None) -> LocationRequest | None:
        """Newest unused, unexpired link of the delivery (to reuse instead of creating another)."""
        stmt = (
            select(LocationRequest)
            .where(
                LocationRequest.delivery_id == delivery_id,
                LocationRequest.used_at.is_(None),
                LocationRequest.expires_at > (now or now_utc()),
            )
            .order_by(LocationRequest.id.desc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()


class RoutePlanRepository(BaseRepository[RoutePlan]):
    model = RoutePlan
    not_found_detail = "Маршрут не найден"

    def latest_for_date(self, delivery_date: date) -> RoutePlan | None:
        """Newest plan of the date with stops → delivery → order (+ items, customer) loaded."""
        def order_path() -> Any:
            return selectinload(RoutePlan.stops).selectinload(RouteStop.delivery).selectinload(Delivery.order)

        stmt = (
            select(RoutePlan)
            .where(RoutePlan.delivery_date == delivery_date)
            .options(order_path().selectinload(Order.items), order_path().selectinload(Order.customer))
            .order_by(RoutePlan.created_at.desc(), RoutePlan.id.desc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()
