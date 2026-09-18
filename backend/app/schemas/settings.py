"""Business settings edited from the admin panel (04-api.md §12).

``BusinessSettings`` is the full, typed view (defaults from the contract); ``BusinessSettingsUpdate``
is the partial body of ``PUT /settings`` (every field optional, ``warehouse`` merged key by key).
Stored as JSON under ``app_settings.key = "business"`` by ``SettingsService``.

``IntegrationStatus`` / ``IntegrationsOut`` are the read-only answer of
``GET /settings/integrations`` — built from configuration presence only, never from secret values
(see ``app.services.integration_status``).
"""

from datetime import time
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import get_settings
from app.schemas.common import HHMM
from app.services.constants import is_inside_tajikistan

DEFAULT_BUSINESS_NAME = "Домашняя выпечка"
DEFAULT_WAREHOUSE_NAME = "Склад"
DEFAULT_ROUTE_START_TIME = time(9, 0)

SHORT_TEXT_MAX = 128
ADDRESS_MAX = 500
LONG_TEXT_MAX = 4000


def default_daily_report_time() -> time:
    """``Settings.DAILY_REPORT_TIME`` (env) is only the default of the admin setting (06 §5)."""
    return get_settings().daily_report_time


def _check_coordinates(latitude: float | None, longitude: float | None) -> None:
    if (latitude is None) != (longitude is None):
        raise ValueError("Укажите обе координаты склада: широту и долготу")
    if latitude is not None and longitude is not None and not is_inside_tajikistan(latitude, longitude):
        raise ValueError("Координаты склада должны быть внутри Таджикистана")


class Warehouse(BaseModel):
    """Route start point (kitchen / warehouse). Coordinates are ``null`` until configured."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str = Field(default=DEFAULT_WAREHOUSE_NAME, max_length=SHORT_TEXT_MAX)
    address: str = Field(default="", max_length=ADDRESS_MAX)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def _validate_coordinates(self) -> Self:
        _check_coordinates(self.latitude, self.longitude)
        return self

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


class BusinessSettings(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    business_name: str = Field(default=DEFAULT_BUSINESS_NAME, min_length=1, max_length=SHORT_TEXT_MAX)
    ai_enabled: bool = True
    voice_replies_enabled: bool = False
    warehouse: Warehouse = Field(default_factory=Warehouse)
    pickup_address: str = Field(default="", max_length=ADDRESS_MAX)
    working_hours: str = Field(default="", max_length=ADDRESS_MAX)
    # Hours the bot takes delivery/pickup times in (03 §1.3, 18.09.2026): "к 23:30" is refused when the
    # bakery works 9:00–20:00. ``working_hours`` above is only the text shown to customers. ``null`` = no limit.
    order_hours_start: HHMM | None = None
    order_hours_end: HHMM | None = None
    min_lead_time_hours: int = Field(default=24, ge=0, le=24 * 30)
    max_days_ahead: int = Field(default=60, ge=1, le=366)
    delivery_time_window_minutes: int = Field(default=60, ge=0, le=12 * 60)
    route_start_time: HHMM = DEFAULT_ROUTE_START_TIME
    service_time_minutes: int = Field(default=5, ge=0, le=240)
    average_speed_kmh: float = Field(default=25, gt=0, le=150)
    daily_report_time: HHMM = Field(default_factory=default_daily_report_time)
    payment_methods_text: str = Field(default="", max_length=LONG_TEXT_MAX)
    delivery_info_text: str = Field(default="", max_length=LONG_TEXT_MAX)
    # Prepayment (03 §3, 17.09.2026): asked by the bot after the customer's "Да"; the receipt screenshot
    # is read by the bot and checked in Python; the operator confirms unless auto-confirm is switched on.
    prepayment_enabled: bool = False
    prepayment_percent: int = Field(default=100, ge=1, le=100)
    prepayment_wallet: str = Field(default="", max_length=SHORT_TEXT_MAX)
    prepayment_wallet_banks: str = Field(default="", max_length=ADDRESS_MAX)
    prepayment_auto_confirm: bool = False

    @model_validator(mode="after")
    def _validate_order_hours(self) -> Self:
        start, end = self.order_hours_start, self.order_hours_end
        if start is not None and end is not None and end <= start:
            raise ValueError("Время окончания приёма заказов должно быть позже начала")
        return self


class WarehouseUpdate(BaseModel):
    """Partial warehouse; ``latitude``/``longitude`` may be set to ``null`` to clear the point."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str | None = Field(default=None, max_length=SHORT_TEXT_MAX)
    address: str | None = Field(default=None, max_length=ADDRESS_MAX)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class BusinessSettingsUpdate(BaseModel):
    """``PUT /settings`` body: any subset of ``BusinessSettings``. ``null`` means "leave unchanged"
    (except warehouse coordinates, see ``WarehouseUpdate``)."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    business_name: str | None = Field(default=None, min_length=1, max_length=SHORT_TEXT_MAX)
    ai_enabled: bool | None = None
    voice_replies_enabled: bool | None = None
    warehouse: WarehouseUpdate | None = None
    pickup_address: str | None = Field(default=None, max_length=ADDRESS_MAX)
    working_hours: str | None = Field(default=None, max_length=ADDRESS_MAX)
    order_hours_start: HHMM | None = None  # explicit ``null`` removes the limit
    order_hours_end: HHMM | None = None
    min_lead_time_hours: int | None = Field(default=None, ge=0, le=24 * 30)
    max_days_ahead: int | None = Field(default=None, ge=1, le=366)
    delivery_time_window_minutes: int | None = Field(default=None, ge=0, le=12 * 60)
    route_start_time: HHMM | None = None
    service_time_minutes: int | None = Field(default=None, ge=0, le=240)
    average_speed_kmh: float | None = Field(default=None, gt=0, le=150)
    daily_report_time: HHMM | None = None
    payment_methods_text: str | None = Field(default=None, max_length=LONG_TEXT_MAX)
    delivery_info_text: str | None = Field(default=None, max_length=LONG_TEXT_MAX)
    prepayment_enabled: bool | None = None
    prepayment_percent: int | None = Field(default=None, ge=1, le=100)
    prepayment_wallet: str | None = Field(default=None, max_length=SHORT_TEXT_MAX)
    prepayment_wallet_banks: str | None = Field(default=None, max_length=ADDRESS_MAX)
    prepayment_auto_confirm: bool | None = None


# --------------------------------------------------------------------------- integrations status


class IntegrationStatus(BaseModel):
    """``{configured, provider, details}`` — 04-api.md §12. ``details`` never contains a secret."""

    model_config = ConfigDict(extra="ignore")

    configured: bool
    provider: str | None = None
    details: str = ""


class IntegrationsOut(BaseModel):
    """Answer of ``GET /settings/integrations`` (ADMIN)."""

    model_config = ConfigDict(extra="ignore")

    instagram: IntegrationStatus
    llm: IntegrationStatus
    stt: IntegrationStatus
    tts: IntegrationStatus
    geocoder: IntegrationStatus
    routing: IntegrationStatus
    maxim: IntegrationStatus
