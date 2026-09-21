"""SettingsService + BusinessSettings (04-api.md §12, 02-data-model.md app_settings)."""

import logging
from collections.abc import Callable
from datetime import time
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import ValidationError
from app.models import AppSetting, User
from app.schemas import settings as settings_schemas
from app.schemas.settings import BusinessSettings, BusinessSettingsUpdate, Warehouse, WarehouseUpdate
from app.services.settings_service import BUSINESS_SETTINGS_KEY, SettingsService

CONTRACT_KEYS = {
    "business_name",
    "ai_enabled",
    "voice_replies_enabled",
    "warehouse",
    "pickup_address",
    "working_hours",
    "order_hours_start",
    "order_hours_end",
    "closed_weekdays",
    "min_order_quantity",
    "order_quantity_step",
    "min_lead_time_hours",
    "max_days_ahead",
    "delivery_time_window_minutes",
    "route_start_time",
    "service_time_minutes",
    "average_speed_kmh",
    "daily_report_time",
    "payment_methods_text",
    "delivery_info_text",
    "prepayment_enabled",
    "prepayment_percent",
    "prepayment_wallet",
    "prepayment_wallet_banks",
    "prepayment_auto_confirm",
}


def _store(db: Session, value: Any) -> None:
    db.add(AppSetting(key=BUSINESS_SETTINGS_KEY, value=value))
    db.commit()


def _stored(db: Session) -> Any:
    db.expire_all()
    row = db.get(AppSetting, BUSINESS_SETTINGS_KEY)
    return None if row is None else row.value


def _events(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if getattr(record, "event", None) == event]


# --------------------------------------------------------------------------- defaults


def test_defaults_when_nothing_stored(db: Session) -> None:
    settings = SettingsService(db).get()

    assert settings.business_name == "Домашняя выпечка"
    assert settings.ai_enabled is True
    assert settings.voice_replies_enabled is False
    assert settings.warehouse == Warehouse(name="Склад", address="", latitude=None, longitude=None)
    assert settings.pickup_address == ""
    assert settings.working_hours == ""
    assert (settings.order_hours_start, settings.order_hours_end) == (None, None)
    assert settings.min_lead_time_hours == 24
    assert settings.max_days_ahead == 60
    assert settings.delivery_time_window_minutes == 60
    assert settings.route_start_time == time(9, 0)
    assert settings.service_time_minutes == 5
    assert settings.average_speed_kmh == 25
    assert settings.daily_report_time == time(21, 0)
    assert settings.payment_methods_text == ""
    assert settings.delivery_info_text == ""
    # the prepayment policy is off until the owner switches it on in the admin panel (03 §3)
    assert settings.prepayment_enabled is False and settings.prepayment_auto_confirm is False
    assert settings.prepayment_percent == 100
    assert settings.prepayment_wallet == "" and settings.prepayment_wallet_banks == ""
    assert _stored(db) is None


def test_json_shape_matches_contract(db: Session) -> None:
    data = SettingsService(db).get().model_dump(mode="json")

    assert set(data) == CONTRACT_KEYS
    assert set(data["warehouse"]) == {"name", "address", "latitude", "longitude"}
    assert data["route_start_time"] == "09:00"
    assert data["daily_report_time"] == "21:00"
    assert data["average_speed_kmh"] == 25


def test_daily_report_time_default_comes_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_schemas, "get_settings", lambda: Settings(_env_file=None, DAILY_REPORT_TIME="20:30"))

    assert BusinessSettings().daily_report_time == time(20, 30)


def test_update_schema_fields_mirror_settings() -> None:
    assert set(BusinessSettingsUpdate.model_fields) == set(BusinessSettings.model_fields) == CONTRACT_KEYS
    assert set(WarehouseUpdate.model_fields) == set(Warehouse.model_fields)


# --------------------------------------------------------------------------- reading stored values


def test_stored_values_are_merged_over_defaults_and_unknown_keys_ignored(db: Session) -> None:
    _store(
        db,
        {
            "business_name": "Пекарня Нур",
            "min_lead_time_hours": 12,
            "route_start_time": "08:15",
            "legacy_option": True,
            "warehouse": {"name": "Кухня", "floor": 2},
        },
    )

    settings = SettingsService(db).get()

    assert settings.business_name == "Пекарня Нур"
    assert settings.min_lead_time_hours == 12
    assert settings.route_start_time == time(8, 15)
    assert settings.max_days_ahead == 60
    assert settings.warehouse.name == "Кухня"
    assert settings.warehouse.address == ""
    assert "legacy_option" not in settings.model_dump()


def test_invalid_stored_values_fall_back_to_defaults(db: Session, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="app.services.settings_service")
    _store(
        db,
        {
            "max_days_ahead": -5,
            "route_start_time": "25:99",
            "ai_enabled": False,
            "warehouse": {"name": "Кухня", "latitude": "north"},
        },
    )

    settings = SettingsService(db).get()

    assert settings.max_days_ahead == 60
    assert settings.route_start_time == time(9, 0)
    assert settings.ai_enabled is False
    assert settings.warehouse.name == "Кухня"
    assert settings.warehouse.latitude is None
    [record] = _events(caplog, "settings.invalid_stored_value")
    assert record.fields == ["max_days_ahead", "route_start_time", "warehouse.latitude"]


def test_invalid_warehouse_as_a_whole_falls_back(db: Session) -> None:
    _store(db, {"warehouse": {"name": "Кухня", "latitude": 38.5}})  # longitude missing

    assert SettingsService(db).get().warehouse == Warehouse()


@pytest.mark.parametrize("value", [["not", "a", "dict"], "text", 42, {"warehouse": "nope"}])
def test_malformed_stored_json_gives_defaults(db: Session, value: Any) -> None:
    _store(db, value)

    assert SettingsService(db).get() == BusinessSettings()


# --------------------------------------------------------------------------- update


def test_update_persists_commits_and_returns_new_settings(db: Session, admin_user: User) -> None:
    result = SettingsService(db).update(
        BusinessSettingsUpdate(business_name="Нур", min_lead_time_hours=48, route_start_time="08:30"),
        admin_user,
    )

    assert result.business_name == "Нур"
    assert result.min_lead_time_hours == 48
    assert result.route_start_time == time(8, 30)

    db.rollback()  # committed data survives a rollback
    stored = _stored(db)
    assert stored == {"business_name": "Нур", "min_lead_time_hours": 48, "route_start_time": "08:30"}
    assert db.get(AppSetting, BUSINESS_SETTINGS_KEY).updated_by_user_id == admin_user.id  # type: ignore[union-attr]
    assert SettingsService(db).get() == result


def test_update_accepts_mapping_and_keeps_previous_overrides(db: Session) -> None:
    service = SettingsService(db)
    service.update({"business_name": "Нур"})
    result = service.update({"ai_enabled": False, "average_speed_kmh": 30.5, "daily_report_time": "20:00:00"})

    assert result.business_name == "Нур"
    assert result.ai_enabled is False
    assert result.average_speed_kmh == 30.5
    assert result.daily_report_time == time(20, 0)
    assert _stored(db) == {
        "ai_enabled": False,
        "average_speed_kmh": 30.5,
        "business_name": "Нур",
        "daily_report_time": "20:00",
    }


def test_update_null_top_level_values_are_ignored(db: Session) -> None:
    service = SettingsService(db)
    service.update({"business_name": "Нур"})

    result = service.update({"business_name": None, "voice_replies_enabled": True})

    assert result.business_name == "Нур"
    assert result.voice_replies_enabled is True


def test_update_merges_warehouse_partially_and_can_clear_coordinates(db: Session) -> None:
    service = SettingsService(db)
    service.update({"warehouse": {"name": "Кухня", "address": "ул. Айни, 1"}})
    result = service.update(BusinessSettingsUpdate(warehouse=WarehouseUpdate(latitude=38.57, longitude=68.78)))

    assert result.warehouse == Warehouse(name="Кухня", address="ул. Айни, 1", latitude=38.57, longitude=68.78)

    cleared = service.update({"warehouse": {"latitude": None, "longitude": None, "name": None}})

    assert cleared.warehouse == Warehouse(name="Кухня", address="ул. Айни, 1", latitude=None, longitude=None)
    assert _stored(db)["warehouse"] == {"name": "Кухня", "address": "ул. Айни, 1", "latitude": None, "longitude": None}


@pytest.mark.parametrize(
    ("patch", "field"),
    [
        ({"max_days_ahead": 0}, "max_days_ahead"),
        ({"min_lead_time_hours": -1}, "min_lead_time_hours"),
        ({"average_speed_kmh": 0}, "average_speed_kmh"),
        ({"route_start_time": "9 утра"}, "route_start_time"),
        ({"business_name": ""}, "business_name"),
    ],
)
def test_update_rejects_invalid_values(db: Session, patch: dict[str, Any], field: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        SettingsService(db).update(patch)

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "validation_error"
    assert field in exc_info.value.details["fields"]
    assert _stored(db) is None


def test_update_schema_rejects_invalid_values_before_the_service() -> None:
    with pytest.raises(PydanticValidationError):
        BusinessSettingsUpdate.model_validate({"service_time_minutes": 1000})


@pytest.mark.parametrize(
    "warehouse",
    [
        {"latitude": 38.57},  # only one coordinate
        {"latitude": 55.75, "longitude": 37.61},  # Moscow: outside Tajikistan
    ],
)
def test_update_rejects_invalid_warehouse_point(db: Session, warehouse: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as exc_info:
        SettingsService(db).update({"warehouse": warehouse})

    assert exc_info.value.details["fields"] == ["warehouse"]
    assert _stored(db) is None


def test_update_drops_stored_values_that_no_longer_validate(db: Session) -> None:
    _store(db, {"max_days_ahead": -1, "business_name": "Нур", "unknown": 1})

    SettingsService(db).update({"ai_enabled": False})

    assert _stored(db) == {"ai_enabled": False, "business_name": "Нур"}


def test_update_logs_changed_field_names_without_values(
    db: Session,
    make_user: Callable[..., User],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.services.settings_service")
    user = make_user("settings-admin")

    SettingsService(db).update(
        {"payment_methods_text": "Наличные или перевод на карту", "business_name": "Домашняя выпечка"},
        user,
    )

    [record] = _events(caplog, "settings.updated")
    assert record.changed_fields == ["payment_methods_text"]
    assert record.user_id == user.id
    assert "Наличные" not in repr(record.__dict__)
