"""Route planning for one delivery day (03-business-rules.md §7, 04-api.md §9).

Wraps the pure solver in ``route_optimizer.py``: loads the day's deliveries, finds a point for each,
builds a distance matrix (``app.integrations.maps.factory``), runs ``optimize_route`` and persists the
result as one ``RoutePlan`` with its ``RouteStop`` rows.

The point of a delivery (``stop_point``): its coordinates; otherwise, for an address the geocoder found
only approximately (``AMBIGUOUS``: the microdistrict, the street, a landmark the customer named), the
best stored candidate inside the city — the stop is then marked ``approximate`` with the place named,
so the courier knows to call. OpenStreetMap rarely has Khujand's house numbers, so without this most
deliveries fell out of the route altogether (18.09.2026: every route of the test days was empty).
Only a delivery with no usable point at all is ``unlocated``.
"""

from dataclasses import dataclass
from datetime import date, time

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import BusinessRuleError
from app.core.logging import get_logger, log_event
from app.integrations.maps.factory import get_distance_provider
from app.integrations.maps.types import as_float, inside_bbox
from app.models.delivery import Delivery, RoutePlan
from app.models.delivery import RouteStop as RouteStopModel
from app.models.enums import GeocodeStatus
from app.models.user import User
from app.repositories.deliveries import DeliveryRepository, RoutePlanRepository
from app.schemas.delivery import RoutePlanOut, RouteStart, RouteStopOut, RouteUnlocatedOut
from app.schemas.order import items_summary
from app.services.route_optimizer import RoutePoint, RouteStopInput, optimize_route
from app.services.settings_service import SettingsService

logger = get_logger(__name__)

UNLOCATED_REASON = "Нет координат — требуется уточнение адреса"
NO_ADDRESS_LABEL = "(адрес не указан)"


@dataclass(frozen=True, slots=True)
class StopPoint:
    latitude: float
    longitude: float
    #: The place the point stands for when it is not the address itself ("28 микрорайон, г.Худжанд").
    approximate_place: str | None = None


def stop_point(delivery: Delivery, settings: Settings | None = None) -> StopPoint | None:
    """Where the courier goes: the coordinates, else the approximate place (module docstring), else None."""
    if delivery.has_coordinates:
        return StopPoint(float(delivery.latitude), float(delivery.longitude))
    if delivery.geocode_status != GeocodeStatus.AMBIGUOUS:
        return None
    bbox = (settings or get_settings()).city_bbox
    for candidate in delivery.geocode_candidates or []:
        lat, lng = as_float(candidate.get("lat")), as_float(candidate.get("lng"))
        if lat is None or lng is None or not inside_bbox(lat, lng, bbox):
            continue
        return StopPoint(lat, lng, _short_place(str(candidate.get("formatted") or "")) or "примерное место")
    return None


def _short_place(formatted: str) -> str:
    """ "28 микрорайон, г.Худжанд, Согдийская область, 735700, Таджикистан" → "28 микрорайон, г.Худжанд"."""
    parts = [part.strip() for part in formatted.split(",") if part.strip()]
    kept = [part for part in parts if not part.isdigit() and part not in ("Таджикистан", "Согдийская область")]
    return ", ".join(kept[:3])


def _address(delivery: Delivery) -> str:
    return delivery.address_formatted or delivery.address_raw or ""


class RouteOptimizationService:
    def __init__(self, db: Session, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.deliveries = DeliveryRepository(db)
        self.route_plans = RoutePlanRepository(db)

    # ------------------------------------------------------------------ build

    def optimize(self, delivery_date: date, start_time: time | None = None, user: User | None = None) -> RoutePlanOut:
        """``POST /deliveries/optimize`` (03 §7). Persists a new ``RoutePlan`` and returns it.

        A delivery found only approximately rides on its approximate point (``stop_point``); one with no
        usable point is listed as ``unlocated`` and left out of the route.
        """
        settings = SettingsService(self.db).get()
        warehouse = settings.warehouse
        if not warehouse.has_coordinates:
            raise BusinessRuleError(
                "warehouse_not_configured",
                "Не заданы координаты склада — настройте их в разделе «Настройки»",
            )

        deliveries = self.deliveries.list_for_date(delivery_date)
        stop_points = {delivery.id: stop_point(delivery, self.settings) for delivery in deliveries}
        located = [delivery for delivery in deliveries if stop_points[delivery.id] is not None]
        unlocated = [delivery for delivery in deliveries if stop_points[delivery.id] is None]

        depot = RoutePoint(latitude=warehouse.latitude, longitude=warehouse.longitude, name=warehouse.name)
        stops_input = [
            RouteStopInput(
                key=delivery.id,
                latitude=stop_points[delivery.id].latitude,
                longitude=stop_points[delivery.id].longitude,
                desired_time=delivery.order.delivery_time if delivery.order else None,
            )
            for delivery in located
        ]

        provider = get_distance_provider(business_settings=settings)
        if stops_input:
            matrix_points = [(depot.latitude, depot.longitude)] + [(s.latitude, s.longitude) for s in stops_input]
            durations, distances = provider.matrix(matrix_points)
        else:
            durations, distances = [[0.0]], [[0.0]]

        chosen_start = start_time or settings.route_start_time
        result = optimize_route(
            depot,
            stops_input,
            durations,
            distances,
            start_time=chosen_start,
            service_time_min=settings.service_time_minutes,
            window_min=settings.delivery_time_window_minutes,
        )

        by_id = {delivery.id: delivery for delivery in located}
        plan = RoutePlan(
            delivery_date=delivery_date,
            start_name=warehouse.name,
            start_latitude=warehouse.latitude,
            start_longitude=warehouse.longitude,
            start_time=chosen_start,
            algorithm=result.algorithm,
            distance_source=provider.name,
            total_distance_m=result.total_distance_m,
            total_duration_s=result.total_duration_s,
            created_by_user_id=user.id if user is not None else None,
        )
        self.db.add(plan)
        self.db.flush()
        self.db.add_all(
            RouteStopModel(
                route_plan_id=plan.id,
                delivery_id=planned.key,
                sequence=planned.sequence,
                eta=planned.eta,
                distance_from_prev_m=planned.distance_from_prev_m,
                duration_from_prev_s=planned.duration_from_prev_s,
                lateness_min=planned.lateness_min,
            )
            for planned in result.stops
        )
        self.db.commit()
        log_event(
            logger,
            "route.optimized",
            delivery_date=delivery_date.isoformat(),
            stops=len(result.stops),
            approximate=sum(1 for delivery in located if stop_points[delivery.id].approximate_place),
            unlocated=len(unlocated),
            algorithm=result.algorithm,
            distance_source=provider.name,
        )
        self.db.refresh(plan)
        return self._to_schema(plan, by_id, unlocated)

    # ------------------------------------------------------------------ read

    def latest(self, delivery_date: date) -> RoutePlanOut | None:
        """``GET /deliveries/routes`` — the newest plan of the date, if any."""
        plan = self.route_plans.latest_for_date(delivery_date)
        if plan is None:
            return None
        by_id = {stop.delivery_id: stop.delivery for stop in plan.stops}
        # Best-effort: a delivery geocoded after the plan was built no longer shows as unlocated,
        # but it is not re-inserted into an already-computed route either (re-run "Оптимизировать").
        deliveries = self.deliveries.list_for_date(delivery_date)
        unlocated = [d for d in deliveries if d.id not in by_id and stop_point(d, self.settings) is None]
        return self._to_schema(plan, by_id, unlocated)

    def _to_schema(self, plan: RoutePlan, by_id: dict[int, Delivery], unlocated: list[Delivery]) -> RoutePlanOut:
        stops: list[RouteStopOut] = []
        unlocated = list(unlocated)
        for stop in sorted(plan.stops, key=lambda item: item.sequence):
            delivery = by_id[stop.delivery_id]
            point = stop_point(delivery, self.settings)
            if point is None:
                # The address changed after the plan was built and has no point yet: the stored stop can
                # no longer be drawn (this used to fail with a 500 on float(None)).
                unlocated.append(delivery)
                continue
            order = delivery.order
            stops.append(
                RouteStopOut(
                    sequence=stop.sequence,
                    delivery_id=delivery.id,
                    order_id=delivery.order_id,
                    address=_address(delivery) or NO_ADDRESS_LABEL,
                    latitude=point.latitude,
                    longitude=point.longitude,
                    approximate=point.approximate_place is not None,
                    approximate_place=point.approximate_place,
                    eta=stop.eta,
                    desired_time=order.delivery_time if order else None,
                    lateness_min=stop.lateness_min,
                    distance_from_prev_m=stop.distance_from_prev_m,
                    duration_from_prev_s=stop.duration_from_prev_s,
                    recipient_name=delivery.recipient_name,
                    phone=delivery.recipient_phone,
                    courier_comment=delivery.courier_comment,
                    items_summary=items_summary(order) if order is not None else "",
                )
            )
        return RoutePlanOut(
            id=plan.id,
            delivery_date=plan.delivery_date,
            start=RouteStart(
                name=plan.start_name,
                latitude=float(plan.start_latitude),
                longitude=float(plan.start_longitude),
            ),
            start_time=plan.start_time,
            algorithm=plan.algorithm,
            distance_source=plan.distance_source,
            total_distance_m=plan.total_distance_m,
            total_duration_s=plan.total_duration_s,
            created_at=plan.created_at,
            stops=stops,
            unlocated=[
                RouteUnlocatedOut(
                    delivery_id=delivery.id,
                    order_id=delivery.order_id,
                    address=_address(delivery) or NO_ADDRESS_LABEL,
                    reason=UNLOCATED_REASON,
                )
                for delivery in unlocated
            ],
        )
