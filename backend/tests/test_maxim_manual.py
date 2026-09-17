"""``ManualMaximIntegration`` — 06-integrations.md §4, docs/research/maxim.md (no official API)."""

from datetime import date, time
from decimal import Decimal

from app.integrations.maxim.base import DeliveryDispatchRequest, DispatchPoint
from app.integrations.maxim.factory import get_maxim_integration
from app.integrations.maxim.manual import ManualMaximIntegration
from app.models.enums import DeliveryStatus, DispatchProvider
from app.services.formatting import format_money


def _request(**overrides) -> DeliveryDispatchRequest:
    fields = dict(
        order_id=42,
        delivery_id=7,
        pickup=DispatchPoint(name="Склад", address="ул. Складская, 1", latitude=38.56, longitude=68.78),
        dropoff=DispatchPoint(name="Клиент", address="ул. Рудаки, 1", latitude=38.57, longitude=68.79),
        recipient_name="Иван",
        recipient_phone="+992900000000",
        items_summary="Медовик ×1",
        amount_to_collect=Decimal("150.00"),
        delivery_date=date(2026, 9, 16),
        delivery_time=time(18, 0),
    )
    fields.update(overrides)
    return DeliveryDispatchRequest(**fields)


def test_create_delivery_requires_operator_and_returns_a_copyable_card() -> None:
    integration = ManualMaximIntegration()
    result = integration.create_delivery(_request())
    assert result.requires_operator is True
    assert result.status == DeliveryStatus.AWAITING_DISPATCH
    assert result.provider == DispatchProvider.MAXIM_MANUAL
    assert "Заказ №42" in result.copy_text
    assert "Откуда: Склад — ул. Складская, 1" in result.copy_text
    assert "Куда: Клиент — ул. Рудаки, 1" in result.copy_text
    assert "150" in result.copy_text and "сомони" in result.copy_text
    assert "\xa0" not in result.copy_text  # clipboard-safe: plain spaces only (03 §4)
    assert "16.09.2026" in result.copy_text
    assert "18:00" in result.copy_text
    assert "Официального API" in result.instructions


def test_paid_order_card_says_nothing_to_collect() -> None:
    result = ManualMaximIntegration().create_delivery(_request(amount_to_collect=Decimal("0.00")))
    assert "оплачен" in result.copy_text


def test_missing_coordinates_ask_to_clarify_the_address() -> None:
    dropoff = DispatchPoint(name="Клиент", address="Без координат")
    result = ManualMaximIntegration().create_delivery(_request(dropoff=dropoff))
    assert "уточните адрес" in result.copy_text


def test_get_delivery_status_never_touches_the_network_and_echoes_the_stored_state() -> None:
    class _Delivery:
        status = DeliveryStatus.DISPATCHED
        external_id = "M-100"
        external_status = "в пути"
        courier_name = "Курьер"
        courier_phone = "+992900000001"
        order_id = 42

    status = ManualMaximIntegration().get_delivery_status(_Delivery())
    assert status.changed is False
    assert status.external_id == "M-100"
    assert status.courier_name == "Курьер"


def test_cancel_delivery_mentions_the_external_id() -> None:
    class _Delivery:
        order_id = 42
        external_id = "M-100"
        courier_name = None
        courier_phone = None

    result = ManualMaximIntegration().cancel_delivery(_Delivery())
    assert result.status == DeliveryStatus.CANCELLED
    assert "M-100" in result.copy_text


def test_amount_uses_the_shared_plain_space_formatting() -> None:
    # The card is pasted into the Maxim app / operator's clipboard — same convention as the daily
    # report (03 §4): a plain space (U+0020), never a non-breaking one that some clients mangle.
    assert format_money(Decimal("12500")) == "12 500 сомони"
    assert "\xa0" not in format_money(Decimal("12500"))


def test_factory_returns_manual_integration_in_manual_mode() -> None:
    from app.core.config import Settings

    settings = Settings(MAXIM_MODE="manual", _env_file=None)
    assert isinstance(get_maxim_integration(settings), ManualMaximIntegration)


def test_factory_rejects_api_mode() -> None:
    from app.core.config import Settings
    from app.core.exceptions import IntegrationNotConfiguredError

    settings = Settings(MAXIM_MODE="api", _env_file=None)
    try:
        get_maxim_integration(settings)
        assert False, "expected IntegrationNotConfiguredError"
    except IntegrationNotConfiguredError as exc:
        assert exc.status_code == 503
