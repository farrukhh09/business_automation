"""Route planning for one delivery day (03-business-rules.md §7, 04-api.md §9).

Wraps the pure solver in ``route_optimizer.py``: loads the day's deliveries, splits them into
located / unlocated, builds a distance matrix (``app.integrations.maps.factory``), runs
``optimize_route`` and persists the result as one ``RoutePlan`` with its ``RouteStop`` rows.
"""

from datetime import date, time

from sqlalchemy.orm import Session

from app.core.exceptions import BusinessRuleError
from app.core.logging import get_logger, log_event
from app.integrations.maps.factory import get_distance_provider
from app.models.delivery import Delivery, RoutePlan
from app.models.delivery import RouteStop as RouteStopModel
from app.models.user import User
from app.repositories.deliveries import DeliveryRepository, RoutePlanRepository
from app.schemas.delivery import RoutePlanOut, RouteStart, RouteStopOut, RouteUnlocatedOut
from app.schemas.order import items_summary
from app.services.route_optimizer import RoutePoint, RouteStopInput, optimize_route
from app.services.settings_service import SettingsService

logger = get_logger(__name__)

UNLOCATED_REASON = "Нет координат — требуется уточнение адреса"
NO_ADDRESS_LABEL = "(адрес не указан)"


def _address(delivery: Delivery) -> str:
    return delivery.address_formatted or delivery.address_raw or ""


class RouteOptimizationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.deliveries = DeliveryRepository(db)
        self.route_plans = RoutePlanRepository(db)

    # ------------------------------------------------------------------ build

    def optimize(self, delivery_date: date, start_time: time | None = None, user: User | None = None) -> RoutePlanOut:
        """``POST /deliveries/optimize`` (03 §7). Persists a new ``RoutePlan`` and returns it.

        Deliveries without coordinates are listed as ``unlocated`` and left out of the route.
        """
        settings = SettingsService(self.db).get()
        warehouse = settings.warehouse
        if not warehouse.has_coordinates:
            raise BusinessRuleError(
                "warehouse_not_configured",
                "Не заданы координаты склада — настройте их в разделе «Настройки»",
            )

        deliveries = self.deliveries.list_for_date(delivery_date)
        located = [delivery for delivery in deliveries if delivery.has_coordinates]
        unlocated = [delivery for delivery in deliveries if not delivery.has_coordinates]

        depot = RoutePoint(latitude=warehouse.latitude, longitude=warehouse.longitude, name=warehouse.name)
        stops_input = [
            RouteStopInput(
                key=delivery.id,
                latitude=float(delivery.latitude),
                longitude=float(delivery.longitude),
                desired_time=delivery.order.delivery_time if delivery.order else None,
            )
            for delivery in located
        ]

        provider = get_distance_provider(business_settings=settings)
        if stops_input:
            points = [(depot.latitude, depot.longitude)] + [(s.latitude, s.longitude) for s in stops_input]
            durations, distances = provider.matrix(points)
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
        unlocated = [d for d in deliveries if d.id not in by_id and not d.has_coordinates]
        return self._to_schema(plan, by_id, unlocated)

    def _to_schema(self, plan: RoutePlan, by_id: dict[int, Delivery], unlocated: list[Delivery]) -> RoutePlanOut:
        stops: list[RouteStopOut] = []
        for stop in sorted(plan.stops, key=lambda item: item.sequence):
            delivery = by_id[stop.delivery_id]
            order = delivery.order
            stops.append(
                RouteStopOut(
                    sequence=stop.sequence,
                    delivery_id=delivery.id,
                    order_id=delivery.order_id,
                    address=_address(delivery) or NO_ADDRESS_LABEL,
                    latitude=float(delivery.latitude),
                    longitude=float(delivery.longitude),
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
