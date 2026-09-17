"""Deliveries and routes — docs/architecture/04-api.md §9 "Deliveries".

- ``GET   /api/deliveries``                         ``date``, ``status``, ``geocode_status`` → ``DeliveryListItem[]``
- ``POST  /api/deliveries/optimize``                ``{date, start_time?}`` → ``RoutePlanOut``
- ``GET   /api/deliveries/routes``                  ``date`` → ``RoutePlanOut | null``
- ``GET   /api/deliveries/dispatch-sheet``          ``date`` → ``{text, stops}``
- ``GET   /api/deliveries/{id}``                    → ``DeliveryOut``
- ``PATCH /api/deliveries/{id}``                    ``DeliveryIn + {status?, external_id?, ...}`` → ``DeliveryOut``
- ``POST  /api/deliveries/{id}/geocode``            → ``DeliveryOut``
- ``POST  /api/deliveries/{id}/select-candidate``   ``{index}`` → ``DeliveryOut``
- ``POST  /api/deliveries/{id}/location-link``      → ``{url, expires_at}``
- ``POST  /api/deliveries/{id}/dispatch``           → ``DispatchResultOut``

The static paths (``optimize``, ``routes``, ``dispatch-sheet``) are declared **before**
``/{delivery_id}``: FastAPI matches a plain ``{delivery_id}`` segment as a string first and only
then converts it to ``int``, so a later int-conversion failure would 422 instead of falling through
to the static route if the order were reversed.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import DbSession, StaffUser
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.time import business_today
from app.models.delivery import Delivery
from app.models.enums import DeliveryStatus, GeocodeStatus, LocationSource
from app.repositories.deliveries import DeliveryRepository
from app.schemas.delivery import (
    DeliveryIn,
    DeliveryListItem,
    DeliveryOut,
    DeliveryUpdate,
    DispatchResultOut,
    DispatchSheetOut,
    LocationLinkOut,
    RouteOptimizeIn,
    RoutePlanOut,
    SelectCandidateIn,
    build_delivery_list_item,
)
from app.services.delivery_service import DeliveryService
from app.services.dispatch_service import DispatchService
from app.services.geocoding_service import GeocodingService
from app.services.location_service import LocationService
from app.services.route_optimization_service import RouteOptimizationService

router = APIRouter(prefix="/deliveries", tags=["deliveries"])

DeliveryId = Annotated[int, Path(ge=1, description="Идентификатор доставки")]
DeliveryDateFilter = Annotated[date | None, Query(alias="date", description="Дата (по умолчанию сегодня)")]
RequiredDeliveryDate = Annotated[date, Query(alias="date", description="Дата")]
DeliveryStatusFilter = Annotated[DeliveryStatus | None, Query(description="Статус доставки")]
GeocodeStatusFilter = Annotated[GeocodeStatus | None, Query(description="Статус геокодирования")]

#: PATCH fields the operator fills in by hand — never routed through ``DeliveryService.upsert_for_order``.
MANUAL_FIELDS = frozenset({"status", "external_id", "external_status", "courier_name", "courier_phone"})


def _get_delivery(db: Session, delivery_id: int) -> Delivery:
    delivery = DeliveryRepository(db).get_with_order(delivery_id)
    if delivery is None:
        raise NotFoundError("Доставка не найдена")
    return delivery


@router.get("", response_model=list[DeliveryListItem], summary="Список доставок на дату")
def list_deliveries(
    db: DbSession,
    user: StaffUser,
    delivery_date: DeliveryDateFilter = None,
    status: DeliveryStatusFilter = None,
    geocode_status: GeocodeStatusFilter = None,
) -> list[DeliveryListItem]:
    day = delivery_date or business_today()
    deliveries = DeliveryRepository(db).list_for_date(day, status=status, geocode_status=geocode_status)
    return [build_delivery_list_item(delivery) for delivery in deliveries]


@router.post("/optimize", response_model=RoutePlanOut, summary="Построить маршрут на дату")
def optimize_routes(payload: RouteOptimizeIn, db: DbSession, user: StaffUser) -> RoutePlanOut:
    return RouteOptimizationService(db).optimize(payload.date, payload.start_time, user=user)


@router.get("/routes", response_model=RoutePlanOut | None, summary="Последний построенный маршрут на дату")
def get_route_plan(db: DbSession, user: StaffUser, delivery_date: RequiredDeliveryDate) -> RoutePlanOut | None:
    return RouteOptimizationService(db).latest(delivery_date)


@router.get("/dispatch-sheet", response_model=DispatchSheetOut, summary="Копируемый лист для передачи в Maxim")
def get_dispatch_sheet(db: DbSession, user: StaffUser, delivery_date: RequiredDeliveryDate) -> DispatchSheetOut:
    return DispatchService(db).dispatch_sheet(delivery_date)


@router.get("/{delivery_id}", response_model=DeliveryOut, summary="Доставка")
def get_delivery(delivery_id: DeliveryId, db: DbSession, user: StaffUser) -> Delivery:
    return _get_delivery(db, delivery_id)


@router.patch("/{delivery_id}", response_model=DeliveryOut, summary="Изменить доставку")
def update_delivery(delivery_id: DeliveryId, payload: DeliveryUpdate, db: DbSession, user: StaffUser) -> Delivery:
    delivery = _get_delivery(db, delivery_id)
    values = payload.model_dump(exclude_unset=True)
    address_fields = {field: value for field, value in values.items() if field not in MANUAL_FIELDS}
    if address_fields:
        DeliveryService(db).upsert_for_order(delivery.order, DeliveryIn(**address_fields), actor_user=user)
    if MANUAL_FIELDS & set(values):
        DispatchService(db).record_manual_update(
            delivery,
            external_id=values.get("external_id"),
            external_status=values.get("external_status"),
            courier_name=values.get("courier_name"),
            courier_phone=values.get("courier_phone"),
            status=values.get("status"),
        )
    db.refresh(delivery)
    return delivery


@router.post("/{delivery_id}/geocode", response_model=DeliveryOut, summary="Перезапустить геокодирование")
def geocode_delivery_now(delivery_id: DeliveryId, db: DbSession, user: StaffUser) -> Delivery:
    delivery = _get_delivery(db, delivery_id)
    GeocodingService(db).geocode_delivery(delivery, force=True)
    db.refresh(delivery)
    return delivery


@router.post("/{delivery_id}/select-candidate", response_model=DeliveryOut, summary="Выбрать вариант адреса")
def select_candidate(delivery_id: DeliveryId, payload: SelectCandidateIn, db: DbSession, user: StaffUser) -> Delivery:
    delivery = _get_delivery(db, delivery_id)
    candidates = delivery.geocode_candidates or []
    if payload.index >= len(candidates):
        raise BadRequestError("Некорректный индекс варианта адреса")
    candidate = candidates[payload.index]
    DeliveryService(db).apply_location(
        delivery, candidate["lat"], candidate["lng"], LocationSource.OPERATOR, candidate.get("formatted")
    )
    db.commit()
    db.refresh(delivery)
    return delivery


@router.post("/{delivery_id}/location-link", response_model=LocationLinkOut, summary="Ссылка клиенту на карту")
def create_location_link(delivery_id: DeliveryId, db: DbSession, user: StaffUser) -> LocationLinkOut:
    delivery = _get_delivery(db, delivery_id)
    service = LocationService(db)
    request = service.create_link(delivery)
    return LocationLinkOut(url=service.link_url(request), expires_at=request.expires_at)


@router.post("/{delivery_id}/dispatch", response_model=DispatchResultOut, summary="Передать доставку в Maxim")
def dispatch_delivery(delivery_id: DeliveryId, db: DbSession, user: StaffUser) -> DispatchResultOut:
    delivery = _get_delivery(db, delivery_id)
    return DispatchService(db).dispatch(delivery)
