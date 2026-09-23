"""Business settings edited from the admin panel (04-api.md §12).

``BusinessSettings`` is the full, typed view (defaults from the contract); ``BusinessSettingsUpdate``
is the partial body of ``PUT /settings`` (every field optional, ``warehouse`` merged key by key).
Stored as JSON under ``app_settings.key = "business"`` by ``SettingsService``.

``IntegrationStatus`` / ``IntegrationsOut`` are the read-only answer of
``GET /settings/integrations`` — built from configuration presence only, never from secret values
(see ``app.services.integration_status``).
"""

from datetime import time
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import get_settings
from app.schemas.common import HHMM
from app.services.constants import is_inside_tajikistan
from app.services.media_storage import PRICE_LIST_NAME_PATTERN

DEFAULT_BUSINESS_NAME = "Домашняя выпечка"
DEFAULT_WAREHOUSE_NAME = "Склад"
DEFAULT_ROUTE_START_TIME = time(9, 0)

SHORT_TEXT_MAX = 128
ADDRESS_MAX = 500
LONG_TEXT_MAX = 4000

#: ``date.weekday()``: 0 = Monday … 6 = Sunday.
WEEKDAY = Annotated[int, Field(ge=0, le=6)]
DAYS_IN_WEEK = 7
#: Upper bound of the packing rule — a sanity limit, not a business one.
MAX_QUANTITY_RULE = 1000


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
    # Days the bakery hands nothing over (0 = Monday … 6 = Sunday), 03 §1.3, 21.09.2026: the
    # Instagram archive shows "завтра мы не работаем, в понедельник снова будем" every Friday.
    closed_weekdays: list[WEEKDAY] = Field(default_factory=list)
    # Packing (03 §1.3, 21.09.2026): the rolls go into boxes of 4 and the bakery sells "либо 4, либо 8" —
    # the order total must be at least ``min_order_quantity`` and a multiple of ``order_quantity_step``.
    # Both 1 = no packing rule.
    min_order_quantity: int = Field(default=1, ge=1, le=MAX_QUANTITY_RULE)
    order_quantity_step: int = Field(default=1, ge=1, le=MAX_QUANTITY_RULE)
    min_lead_time_hours: int = Field(default=24, ge=0, le=24 * 30)
    max_days_ahead: int = Field(default=60, ge=1, le=366)
    # Follow-up questions (03 §6a, 22.09.2026): while the bot is waiting for the customer, it asks
    # once more by itself — "Вам коробочку оставить?", "Заказ оформляем?". The delay is capped below
    # 24 h because Instagram closes the messaging window then (06 §1) and nothing could be sent.
    follow_up_enabled: bool = True
    follow_up_after_hours: int = Field(default=3, ge=1, le=23)
    delivery_time_window_minutes: int = Field(default=60, ge=0, le=12 * 60)
    route_start_time: HHMM = DEFAULT_ROUTE_START_TIME
    service_time_minutes: int = Field(default=5, ge=0, le=240)
    average_speed_kmh: float = Field(default=25, gt=0, le=150)
    daily_report_time: HHMM = Field(default_factory=default_daily_report_time)
    payment_methods_text: str = Field(default="", max_length=LONG_TEXT_MAX)
    delivery_info_text: str = Field(default="", max_length=LONG_TEXT_MAX)
    # The price list picture (03 §1.4, 23.09.2026): the name of a stored media file (``pl-….jpg``),
    # which the bot sends on a question about prices or the assortment. It is written only by
    # ``POST /settings/price-list-image`` — the panel uploads a picture, never types a name.
    price_list_image: str | None = Field(default=None, pattern=PRICE_LIST_NAME_PATTERN)
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

    @model_validator(mode="after")
    def _validate_closed_weekdays(self) -> Self:
        """De-duplicated and sorted; a week that is closed on all seven days leaves no slot to offer."""
        unique = sorted(set(self.closed_weekdays))
        if len(unique) >= DAYS_IN_WEEK:
            raise ValueError("Нельзя сделать выходными все семь дней недели")
        if unique != self.closed_weekdays:
            object.__setattr__(self, "closed_weekdays", unique)
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
    closed_weekdays: list[WEEKDAY] | None = None
    min_order_quantity: int | None = Field(default=None, ge=1, le=MAX_QUANTITY_RULE)
    order_quantity_step: int | None = Field(default=None, ge=1, le=MAX_QUANTITY_RULE)
    min_lead_time_hours: int | None = Field(default=None, ge=0, le=24 * 30)
    max_days_ahead: int | None = Field(default=None, ge=1, le=366)
    follow_up_enabled: bool | None = None
    follow_up_after_hours: int | None = Field(default=None, ge=1, le=23)
    delivery_time_window_minutes: int | None = Field(default=None, ge=0, le=12 * 60)
    route_start_time: HHMM | None = None
    service_time_minutes: int | None = Field(default=None, ge=0, le=240)
    average_speed_kmh: float | None = Field(default=None, gt=0, le=150)
    daily_report_time: HHMM | None = None
    payment_methods_text: str | None = Field(default=None, max_length=LONG_TEXT_MAX)
    delivery_info_text: str | None = Field(default=None, max_length=LONG_TEXT_MAX)
    #: Only the upload endpoint sets this (a name it just saved) — ``PUT /settings`` leaves it out.
    price_list_image: str | None = Field(default=None, pattern=PRICE_LIST_NAME_PATTERN)
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
