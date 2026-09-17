"""Delivery tasks (docs/architecture/06-integrations.md §5).

| task | when |
|---|---|
| ``geocode_delivery(delivery_id)`` | on request: after an order/delivery address changes (``DeliveryService.request_geocoding``) |
| ``optimize_routes(date_iso)`` | on request: build a route plan for a date from outside an HTTP request |
| ``sync_delivery_statuses()`` | beat every 15 min: ask the courier provider about dispatched deliveries |

``POST /deliveries/optimize`` itself calls ``RouteOptimizationService`` synchronously (04 §9 returns
the full ``RoutePlanOut`` in the response); this task exists for triggering the same computation
from a script or a future scheduled job. The manual Maxim mode has no network to poll — its
``get_delivery_status`` just echoes what the operator already entered, so ``sync_delivery_statuses``
is a no-op there today and only matters once an API-backed courier provider exists (06 §4).
"""

from datetime import date

from sqlalchemy.orm import Session

from app.core.database import session_scope
from app.core.exceptions import IntegrationError
from app.core.logging import get_logger, log_event
from app.integrations.maxim.factory import get_maxim_integration
from app.models.enums import DeliveryStatus
from app.repositories.deliveries import DeliveryRepository
from app.services.geocoding_service import GeocodingService
from app.services.route_optimization_service import RouteOptimizationService
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)


def run_geocode_delivery(db: Session, delivery_id: int) -> bool:
    """Geocode one delivery; ``False`` when the delivery no longer exists."""
    delivery = DeliveryRepository(db).get(delivery_id)
    if delivery is None:
        log_event(logger, "task.geocode_delivery_missing", delivery_id=delivery_id)
        return False
    GeocodingService(db).geocode_delivery(delivery)
    return True


def run_optimize_routes(db: Session, delivery_date: date) -> int:
    """Build a route plan for ``delivery_date`` outside an HTTP request; returns the plan id."""
    plan = RouteOptimizationService(db).optimize(delivery_date)
    return plan.id


def run_sync_delivery_statuses(db: Session) -> int:
    """Ask the courier provider about every ``DISPATCHED`` delivery (06 §4). Returns how many changed."""
    integration = get_maxim_integration()
    changed = 0
    for delivery in DeliveryRepository(db).list_by_status(DeliveryStatus.DISPATCHED):
        try:
            status = integration.get_delivery_status(delivery)
        except IntegrationError as exc:
            log_event(
                logger, "task.delivery_status_check_failed", delivery_id=delivery.id, error=type(exc).__name__
            )
            continue
        if not status.changed:
            continue
        delivery.external_status = status.external_status
        delivery.courier_name = status.courier_name or delivery.courier_name
        delivery.courier_phone = status.courier_phone or delivery.courier_phone
        delivery.status = status.status
        db.commit()
        changed += 1
        log_event(logger, "task.delivery_status_synced", delivery_id=delivery.id, status=delivery.status.value)
    return changed


@celery_app.task(name="app.tasks.delivery.geocode_delivery")
def geocode_delivery(delivery_id: int) -> bool:
    with session_scope() as db:
        return run_geocode_delivery(db, delivery_id)


@celery_app.task(name="app.tasks.delivery.optimize_routes")
def optimize_routes(date_iso: str) -> int:
    with session_scope() as db:
        return run_optimize_routes(db, date.fromisoformat(date_iso))


@celery_app.task(name="app.tasks.delivery.sync_delivery_statuses")
def sync_delivery_statuses() -> int:
    with session_scope() as db:
        return run_sync_delivery_statuses(db)
