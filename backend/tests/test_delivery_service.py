"""DeliveryService core (03-business-rules.md §7; 04-api.md §5 DeliveryIn)."""

import logging
import sys
import types
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from kombu.exceptions import OperationalError
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessRuleError, ValidationError
from app.models import Delivery, Order, User
from app.models.enums import DeliveryStatus, DeliveryType, GeocodeStatus, LocationSource, OrderStatus
from app.schemas.delivery import DeliveryIn, DeliveryOut
from app.services.delivery_service import DeliveryService

FORMATTED = "Худжанд, проспект Рудаки, 10"
LOCATED: dict[str, Any] = {
    "address_raw": "ул. Рудаки, 10",
    "address_formatted": FORMATTED,
    "recipient_name": "Алия",
    "apartment": "12",
    "latitude": Decimal("38.560000"),
    "longitude": Decimal("68.780000"),
    "location_source": LocationSource.GEOCODER,
    "geocode_status": GeocodeStatus.OK,
    "geocode_provider": "nominatim",
    "geocode_candidates": [{"formatted": FORMATTED, "lat": 38.56, "lng": 68.78, "precision": "house"}],
}


DELIVERY_IN_FIELDS = {
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
    "latitude",
    "longitude",
}
DELIVERY_OUT_FIELDS = DELIVERY_IN_FIELDS | {
    "id",
    "order_id",
    "address_formatted",
    "city",
    "location_source",
    "geocode_status",
    "geocode_provider",
    "geocode_candidates",
    "status",
    "dispatch_provider",
    "external_id",
    "external_status",
    "courier_name",
    "courier_phone",
    "dispatched_at",
    "delivered_at",
    "created_at",
    "updated_at",
}


@pytest.fixture
def delivery_order(make_order: Callable[..., Order]) -> Callable[..., Order]:
    def _make(delivery: dict[str, Any] | None = None, **fields: Any) -> Order:
        fields.setdefault("status", OrderStatus.CONFIRMED)
        return make_order(delivery_type=DeliveryType.DELIVERY, delivery=delivery, **fields)

    return _make


def _delivery_of(order: Order) -> Delivery:
    assert order.delivery is not None
    return order.delivery


def _events(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if getattr(record, "event", None) == event]


def _count_deliveries(db: Session) -> int:
    return int(db.execute(select(func.count()).select_from(Delivery)).scalar_one())


# --------------------------------------------------------------------------- contract shape


def test_delivery_schemas_match_contract(db: Session, delivery_order: Callable[..., Order]) -> None:
    """04 §5 ``DeliveryIn`` and §9 ``DeliveryOut`` field names are fixed (frontend depends on them)."""
    delivery = _delivery_of(delivery_order(delivery=LOCATED))

    assert set(DeliveryIn.model_fields) == DELIVERY_IN_FIELDS
    assert set(DeliveryOut.model_fields) == DELIVERY_OUT_FIELDS

    data = DeliveryOut.model_validate(delivery).model_dump(mode="json")

    assert set(data) == DELIVERY_OUT_FIELDS
    assert data["city"] == "Худжанд"
    assert (data["latitude"], data["longitude"]) == (38.56, 68.78)
    assert (data["location_source"], data["geocode_status"], data["status"]) == ("GEOCODER", "OK", "PENDING")
    assert data["geocode_candidates"] == [{"formatted": FORMATTED, "lat": 38.56, "lng": 68.78, "precision": "house"}]
    assert data["dispatch_provider"] is data["external_id"] is data["dispatched_at"] is None


def test_delivery_out_drops_malformed_candidates(db: Session, delivery_order: Callable[..., Order]) -> None:
    delivery = _delivery_of(
        delivery_order(delivery={**LOCATED, "geocode_candidates": [{"formatted": "без точки"}, "мусор", None]})
    )

    assert DeliveryOut.model_validate(delivery).geocode_candidates == []


# --------------------------------------------------------------------------- upsert: create


def test_upsert_creates_delivery_normalizes_phone_and_syncs_order(
    db: Session, delivery_order: Callable[..., Order]
) -> None:
    order = delivery_order()
    service = DeliveryService(db)

    delivery = service.upsert_for_order(
        order,
        DeliveryIn(
            address_raw="Сино, 82 мкр, дом 5",
            district="Сино",
            microdistrict="82 мкр",
            recipient_name="Алия",
            recipient_phone="90 123-45-67",
            courier_comment="Позвонить за 10 минут",
        ),
    )
    db.commit()
    db.refresh(order)

    assert delivery.id is not None
    assert delivery.order_id == order.id
    assert order.delivery is delivery
    assert delivery.recipient_phone == "+992901234567"
    assert delivery.city == "Худжанд"
    assert delivery.status == DeliveryStatus.PENDING
    assert delivery.geocode_status == GeocodeStatus.PENDING
    assert delivery.location_source is None
    assert delivery.geocode_candidates == []
    assert order.delivery_address == "Сино, 82 мкр, дом 5"
    assert order.delivery_latitude is None
    assert DeliveryService.needs_geocoding(delivery) is True
    assert service.last_changes["recipient_phone"] == [None, "+992901234567"]
    assert service.last_changes["address_raw"] == ["", "Сино, 82 мкр, дом 5"]
    DeliveryOut.model_validate(delivery)  # serializable per contract


def test_upsert_without_address_creates_placeholder_that_needs_address(
    db: Session, delivery_order: Callable[..., Order]
) -> None:
    order = delivery_order()

    delivery = DeliveryService(db).upsert_for_order(order, {"recipient_name": "Алия"})
    db.commit()

    assert delivery.address_raw == ""
    assert order.delivery_address is None
    assert DeliveryService.needs_geocoding(delivery) is False


def test_upsert_with_coordinates_sets_operator_manual_location(
    db: Session, delivery_order: Callable[..., Order], admin_user: User, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="app.services.delivery_service")
    order = delivery_order()
    service = DeliveryService(db)

    delivery = service.upsert_for_order(
        order,
        DeliveryIn(address_raw="ул. Айни, 45", latitude=38.5598, longitude=68.787),
        actor_user=admin_user,
    )
    db.commit()

    assert delivery.location_source == LocationSource.OPERATOR
    assert delivery.geocode_status == GeocodeStatus.MANUAL
    assert delivery.latitude == Decimal("38.559800")
    assert delivery.longitude == Decimal("68.787000")
    assert order.delivery_latitude == Decimal("38.559800")
    assert order.delivery_longitude == Decimal("68.787000")
    assert DeliveryService.needs_geocoding(delivery) is False
    assert service.last_changes["latitude"] == [None, 38.5598]
    assert service.last_changes["geocode_status"] == ["PENDING", "MANUAL"]
    [created] = _events(caplog, "delivery.created")
    assert created.actor_user_id == admin_user.id
    assert "Айни" not in repr(created.__dict__)


# --------------------------------------------------------------------------- upsert: update


def test_upsert_updates_only_provided_fields(db: Session, delivery_order: Callable[..., Order]) -> None:
    order = delivery_order(delivery=LOCATED)
    service = DeliveryService(db)

    delivery = service.upsert_for_order(order, DeliveryIn(courier_comment="Домофон 12"))
    db.commit()

    assert service.last_changes == {"courier_comment": [None, "Домофон 12"]}
    assert delivery.recipient_name == "Алия"
    assert delivery.apartment == "12"
    assert delivery.latitude == Decimal("38.560000")
    assert delivery.location_source == LocationSource.GEOCODER
    assert delivery.geocode_status == GeocodeStatus.OK
    assert _count_deliveries(db) == 1


def test_details_that_do_not_move_the_point_keep_coordinates(
    db: Session, delivery_order: Callable[..., Order]
) -> None:
    order = delivery_order(delivery=LOCATED)

    delivery = DeliveryService(db).upsert_for_order(
        order, {"apartment": "14", "entrance": "2", "floor": "5", "landmark": "напротив школы"}
    )

    assert delivery.latitude == Decimal("38.560000")
    assert delivery.geocode_status == GeocodeStatus.OK
    assert order.delivery_address == FORMATTED


@pytest.mark.parametrize("field", ["address_raw", "district", "microdistrict", "street", "house"])
def test_address_change_without_coordinates_resets_location(
    db: Session, delivery_order: Callable[..., Order], field: str
) -> None:
    order = delivery_order(delivery=LOCATED)
    service = DeliveryService(db)

    delivery = service.upsert_for_order(order, {field: "Шохмансур, ул. Айни, 45"})
    db.commit()
    db.refresh(order)

    assert delivery.latitude is None
    assert delivery.longitude is None
    assert delivery.location_source is None
    assert delivery.geocode_status == GeocodeStatus.PENDING
    assert delivery.geocode_candidates == []
    assert delivery.address_formatted is None
    assert delivery.geocode_provider is None
    assert order.delivery_latitude is None
    assert order.delivery_longitude is None
    assert order.delivery_address == delivery.address_raw
    assert DeliveryService.needs_geocoding(delivery) is True
    assert service.last_changes["latitude"] == [38.56, None]


def test_same_address_is_not_a_change(db: Session, delivery_order: Callable[..., Order]) -> None:
    order = delivery_order(delivery=LOCATED)
    service = DeliveryService(db)

    delivery = service.upsert_for_order(order, {"address_raw": "  ул. Рудаки, 10 "})

    assert service.last_changes == {}
    assert delivery.latitude == Decimal("38.560000")
    assert delivery.geocode_status == GeocodeStatus.OK


def test_address_change_with_coordinates_uses_new_point(db: Session, delivery_order: Callable[..., Order]) -> None:
    order = delivery_order(delivery=LOCATED)

    delivery = DeliveryService(db).upsert_for_order(
        order, {"address_raw": "ул. Айни, 45", "latitude": 38.5731, "longitude": 68.7864}
    )

    assert delivery.latitude == Decimal("38.573100")
    assert delivery.location_source == LocationSource.OPERATOR
    assert delivery.geocode_status == GeocodeStatus.MANUAL
    assert delivery.address_formatted == FORMATTED  # kept: the operator did not give a new one
    assert order.delivery_latitude == Decimal("38.573100")


def test_blank_strings_clear_optional_fields_and_null_address_is_ignored(
    db: Session, delivery_order: Callable[..., Order]
) -> None:
    order = delivery_order(delivery=LOCATED)

    delivery = DeliveryService(db).upsert_for_order(
        order, {"apartment": "  ", "recipient_name": None, "address_raw": None}
    )

    assert delivery.apartment is None
    assert delivery.recipient_name is None
    assert delivery.address_raw == "ул. Рудаки, 10"
    assert delivery.latitude == Decimal("38.560000")


# --------------------------------------------------------------------------- upsert: errors


@pytest.mark.parametrize("delivery_type", [DeliveryType.PICKUP, None])
def test_upsert_requires_delivery_order(
    db: Session, make_order: Callable[..., Order], delivery_type: DeliveryType | None
) -> None:
    order = make_order(delivery_type=delivery_type)

    with pytest.raises(BusinessRuleError) as exc_info:
        DeliveryService(db).upsert_for_order(order, DeliveryIn(address_raw="ул. Рудаки, 10"))

    assert exc_info.value.code == "delivery_not_applicable"
    assert _count_deliveries(db) == 0


def test_upsert_rejects_invalid_recipient_phone(db: Session, delivery_order: Callable[..., Order]) -> None:
    order = delivery_order()

    with pytest.raises(BusinessRuleError) as exc_info:
        DeliveryService(db).upsert_for_order(
            order, DeliveryIn(address_raw="ул. Рудаки, 10", recipient_phone="12-34")
        )

    assert exc_info.value.code == "invalid_phone"
    assert exc_info.value.to_dict()["field"] == "recipient_phone"


def test_upsert_rejects_coordinates_outside_tajikistan(db: Session, delivery_order: Callable[..., Order]) -> None:
    order = delivery_order()

    with pytest.raises(BusinessRuleError) as exc_info:
        DeliveryService(db).upsert_for_order(
            order, DeliveryIn(address_raw="Москва", latitude=55.75, longitude=37.61)
        )

    assert exc_info.value.code == "location_out_of_bounds"


def test_delivery_in_requires_both_coordinates(db: Session, delivery_order: Callable[..., Order]) -> None:
    with pytest.raises(PydanticValidationError):
        DeliveryIn(latitude=38.5)

    with pytest.raises(ValidationError):
        DeliveryService(db).upsert_for_order(delivery_order(), {"longitude": 68.7})


# --------------------------------------------------------------------------- remove


def test_remove_for_order_deletes_delivery_and_clears_order_copy(
    db: Session, delivery_order: Callable[..., Order]
) -> None:
    order = delivery_order(delivery=LOCATED)
    service = DeliveryService(db)
    order.delivery_type = DeliveryType.PICKUP

    assert service.remove_for_order(order) is True
    db.commit()
    db.refresh(order)

    assert _count_deliveries(db) == 0
    assert order.delivery is None
    assert order.delivery_address is None
    assert order.delivery_latitude is None
    assert order.delivery_longitude is None
    assert service.remove_for_order(order) is False


def test_remove_not_yet_flushed_delivery(db: Session, delivery_order: Callable[..., Order]) -> None:
    order = delivery_order()
    service = DeliveryService(db)
    with db.no_autoflush:
        order.delivery = Delivery(address_raw="ул. Рудаки, 10")
        assert service.remove_for_order(order) is True
    db.commit()

    assert _count_deliveries(db) == 0


@pytest.mark.parametrize("status", [DeliveryStatus.DISPATCHED, DeliveryStatus.DELIVERED])
def test_remove_dispatched_delivery_is_rejected(
    db: Session, delivery_order: Callable[..., Order], status: DeliveryStatus
) -> None:
    order = delivery_order(delivery={**LOCATED, "status": status})

    with pytest.raises(BusinessRuleError) as exc_info:
        DeliveryService(db).remove_for_order(order)

    assert exc_info.value.code == "delivery_already_dispatched"
    assert _count_deliveries(db) == 1


# --------------------------------------------------------------------------- apply_location


@pytest.mark.parametrize(
    ("source", "expected_status"),
    [
        (LocationSource.CUSTOMER_PIN, GeocodeStatus.MANUAL),
        (LocationSource.OPERATOR, GeocodeStatus.MANUAL),
        (LocationSource.COURIER, GeocodeStatus.MANUAL),
        (LocationSource.GEOCODER, GeocodeStatus.OK),
    ],
)
def test_apply_location_sets_status_by_source_and_copies_to_order(
    db: Session,
    delivery_order: Callable[..., Order],
    source: LocationSource,
    expected_status: GeocodeStatus,
) -> None:
    order = delivery_order(delivery={"address_raw": "Сино, 82 мкр"})
    service = DeliveryService(db)

    delivery = service.apply_location(_delivery_of(order), "38.5731234", 68.7864, source)
    db.commit()
    db.refresh(order)

    assert delivery.latitude == Decimal("38.573123")
    assert delivery.longitude == Decimal("68.786400")
    assert delivery.location_source == source
    assert delivery.geocode_status == expected_status
    assert order.delivery_latitude == Decimal("38.573123")
    assert order.delivery_longitude == Decimal("68.786400")
    assert order.delivery_address == "Сино, 82 мкр"


def test_apply_location_with_formatted_address_updates_order_address(
    db: Session, delivery_order: Callable[..., Order]
) -> None:
    order = delivery_order(delivery={"address_raw": "рудаки 10"})
    delivery = _delivery_of(order)

    DeliveryService(db).apply_location(delivery, 38.56, 68.78, LocationSource.GEOCODER, formatted=FORMATTED)

    assert delivery.address_formatted == FORMATTED
    assert order.delivery_address == FORMATTED


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(55.75, 37.61), (36.59, 70.0), (41.11, 70.0), (38.5, 67.29), (38.5, 75.21), (-38.56, 68.78)],
)
def test_apply_location_rejects_points_outside_tajikistan(
    db: Session, delivery_order: Callable[..., Order], latitude: float, longitude: float
) -> None:
    delivery = _delivery_of(delivery_order(delivery=LOCATED))

    with pytest.raises(BusinessRuleError) as exc_info:
        DeliveryService(db).apply_location(delivery, latitude, longitude, LocationSource.CUSTOMER_PIN)

    assert exc_info.value.code == "location_out_of_bounds"
    assert exc_info.value.status_code == 422
    assert delivery.latitude == Decimal("38.560000")


@pytest.mark.parametrize(("latitude", "longitude"), [(36.6, 67.3), (41.1, 75.2)])
def test_apply_location_bounds_are_inclusive(
    db: Session, delivery_order: Callable[..., Order], latitude: float, longitude: float
) -> None:
    delivery = _delivery_of(delivery_order(delivery=LOCATED))

    DeliveryService(db).apply_location(delivery, latitude, longitude, LocationSource.OPERATOR)

    assert delivery.latitude == Decimal(str(latitude)).quantize(Decimal("0.000001"))


@pytest.mark.parametrize("latitude", ["abc", float("nan"), float("inf"), None])
def test_apply_location_rejects_garbage(db: Session, delivery_order: Callable[..., Order], latitude: Any) -> None:
    delivery = _delivery_of(delivery_order(delivery=LOCATED))

    with pytest.raises(BusinessRuleError) as exc_info:
        DeliveryService(db).apply_location(delivery, latitude, 68.78, LocationSource.OPERATOR)

    assert exc_info.value.code == "location_invalid"


@pytest.mark.parametrize(
    ("source", "manual"),
    [
        (LocationSource.CUSTOMER_PIN, True),
        (LocationSource.OPERATOR, True),
        (LocationSource.COURIER, True),
        (LocationSource.GEOCODER, False),
    ],
)
def test_has_manual_location_follows_source_priority(
    db: Session, delivery_order: Callable[..., Order], source: LocationSource, manual: bool
) -> None:
    """03 §7: CUSTOMER_PIN = OPERATOR = COURIER outrank GEOCODER — automatic geocoding must not
    overwrite a point a person set."""
    delivery = _delivery_of(delivery_order(delivery={"address_raw": "Сино, 82 мкр"}))

    DeliveryService(db).apply_location(delivery, 38.56, 68.78, source)

    assert DeliveryService.has_manual_location(delivery) is manual


def test_has_manual_location_is_false_without_coordinates(
    db: Session, delivery_order: Callable[..., Order]
) -> None:
    assert DeliveryService.has_manual_location(_delivery_of(delivery_order(delivery={"address_raw": "Сино"}))) is False


def test_sync_order_copy_prefers_formatted_address(db: Session, delivery_order: Callable[..., Order]) -> None:
    order = delivery_order(delivery=LOCATED)
    delivery = _delivery_of(order)
    delivery.address_formatted = None
    order.delivery_address = "stale"

    DeliveryService(db).sync_order_copy(delivery)

    assert order.delivery_address == "ул. Рудаки, 10"


# --------------------------------------------------------------------------- geocoding task


def test_request_geocoding_without_task_module_logs_warning(
    db: Session,
    delivery_order: Callable[..., Order],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.services.delivery_service")
    monkeypatch.setitem(sys.modules, "app.tasks.delivery", None)  # import → ImportError
    delivery = _delivery_of(delivery_order(delivery={"address_raw": "Сино, 82 мкр"}))

    assert DeliveryService(db).request_geocoding(delivery) is False

    [record] = _events(caplog, "delivery.geocoding_task_missing")
    assert record.levelno == logging.WARNING
    assert record.delivery_id == delivery.id


def _fake_task_module(delay: Callable[[int], Any]) -> types.ModuleType:
    module = types.ModuleType("app.tasks.delivery")
    module.geocode_delivery = types.SimpleNamespace(delay=delay)  # type: ignore[attr-defined]
    return module


def test_request_geocoding_enqueues_task(
    db: Session, delivery_order: Callable[..., Order], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setitem(sys.modules, "app.tasks.delivery", _fake_task_module(calls.append))
    delivery = _delivery_of(delivery_order(delivery={"address_raw": "Сино, 82 мкр"}))

    assert DeliveryService(db).request_geocoding(delivery) is True
    assert calls == [delivery.id]


def test_request_geocoding_broker_failure_returns_false(
    db: Session,
    delivery_order: Callable[..., Order],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def _broken(_: int) -> None:
        raise OperationalError("broker unavailable")

    monkeypatch.setitem(sys.modules, "app.tasks.delivery", _fake_task_module(_broken))
    delivery = _delivery_of(delivery_order(delivery={"address_raw": "Сино, 82 мкр"}))

    assert DeliveryService(db).request_geocoding(delivery) is False
    assert _events(caplog, "delivery.geocoding_enqueue_failed")
