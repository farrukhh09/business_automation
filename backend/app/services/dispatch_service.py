"""Courier dispatch through Maxim (SPEC §26, 06-integrations.md §4).

The business logic only knows ``MaximIntegration``; the only implementation is the manual mode
(no official Maxim API — docs/research/maxim.md, verdict C).
"""

from datetime import date, time
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import BusinessRuleError
from app.core.logging import get_logger, log_event
from app.core.time import now_utc
from app.integrations.maxim.base import DeliveryDispatchRequest, DispatchPoint
from app.integrations.maxim.factory import get_maxim_integration
from app.models.delivery import Delivery
from app.models.enums import DeliveryStatus, DeliveryType, DispatchProvider
from app.repositories.deliveries import DeliveryRepository, RoutePlanRepository
from app.schemas.delivery import DeliveryOut, DispatchResultOut, DispatchSheetOut, DispatchSheetStop
from app.schemas.order import items_summary
from app.services.formatting import format_date_ru, format_money, format_time
from app.services.order_pricing import ZERO, money
from app.services.settings_service import SettingsService

logger = get_logger(__name__)

NO_DELIVERY_DETAIL = "У заказа нет доставки"
NO_ADDRESS_LABEL = "(адрес не указан)"
SHEET_EMPTY_LINE = "Нет доставок на эту дату."


def _address(delivery: Delivery) -> str:
    return delivery.address_formatted or delivery.address_raw or ""


def _amount_to_collect(delivery: Delivery) -> Decimal:
    order = delivery.order
    if order is None:
        return ZERO
    remaining = money(order.total_amount) - money(order.paid_amount)
    return remaining if remaining > ZERO else ZERO


class DispatchService:
    def __init__(self, db: Session, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings
        self.deliveries = DeliveryRepository(db)
        self.route_plans = RoutePlanRepository(db)

    # ------------------------------------------------------------------ create / cancel

    def dispatch(self, delivery: Delivery) -> DispatchResultOut:
        """``POST /deliveries/{id}/dispatch`` (06 §4)."""
        order = delivery.order
        if order is None or order.delivery_type != DeliveryType.DELIVERY:
            raise BusinessRuleError("delivery_not_applicable", NO_DELIVERY_DETAIL)

        warehouse = SettingsService(self.db).get().warehouse
        request = DeliveryDispatchRequest(
            order_id=order.id,
            delivery_id=delivery.id,
            pickup=DispatchPoint(
                name=warehouse.name,
                address=warehouse.address,
                latitude=warehouse.latitude,
                longitude=warehouse.longitude,
            ),
            dropoff=DispatchPoint(
                name=delivery.recipient_name or "",
                address=_address(delivery),
                latitude=float(delivery.latitude) if delivery.latitude is not None else None,
                longitude=float(delivery.longitude) if delivery.longitude is not None else None,
            ),
            recipient_name=delivery.recipient_name,
            recipient_phone=delivery.recipient_phone,
            courier_comment=delivery.courier_comment,
            items_summary=items_summary(order),
            amount_to_collect=_amount_to_collect(delivery),
            delivery_date=order.delivery_date,
            delivery_time=order.delivery_time,
        )
        integration = get_maxim_integration(self.settings)
        result = integration.create_delivery(request)
        return self._apply_result(delivery, result, event="delivery.dispatch_requested")

    def cancel(self, delivery: Delivery) -> DispatchResultOut:
        """Cancel a dispatched delivery (06 §4: no network, the operator cancels in the app)."""
        integration = get_maxim_integration(self.settings)
        result = integration.cancel_delivery(delivery)
        return self._apply_result(delivery, result, event="delivery.dispatch_cancelled")

    def _apply_result(self, delivery: Delivery, result, event: str) -> DispatchResultOut:
        delivery.status = result.status
        delivery.dispatch_provider = result.provider
        if result.external_id:
            delivery.external_id = result.external_id
        self.db.commit()
        log_event(
            logger,
            event,
            delivery_id=delivery.id,
            order_id=delivery.order_id,
            provider=result.provider.value,
            status=result.status.value,
        )
        return DispatchResultOut(
            delivery=DeliveryOut.model_validate(delivery),
            provider=result.provider,
            requires_operator=result.requires_operator,
            instructions=result.instructions,
            copy_text=result.copy_text,
        )

    # ------------------------------------------------------------------ manual updates (PATCH)

    def record_manual_update(
        self,
        delivery: Delivery,
        *,
        external_id: str | None = None,
        external_status: str | None = None,
        courier_name: str | None = None,
        courier_phone: str | None = None,
        status: DeliveryStatus | None = None,
    ) -> Delivery:
        """``PATCH /deliveries/{id}``: the operator's own dispatch data (06 §4).

        Entering ``external_id``/courier data without an explicit ``status`` means the order was
        just created in Maxim: the delivery moves to ``DISPATCHED``. An explicit ``status`` always
        wins, matching ``DeliveryUpdate``'s docstring in 04-api.md §9.
        """
        changed = False
        for field, value in (
            ("external_id", external_id),
            ("external_status", external_status),
            ("courier_name", courier_name),
            ("courier_phone", courier_phone),
        ):
            if value is not None and getattr(delivery, field) != value:
                setattr(delivery, field, value)
                changed = True

        if status is not None:
            delivery.status = DeliveryStatus(status)
        elif changed and external_id and delivery.status in (DeliveryStatus.PENDING, DeliveryStatus.AWAITING_DISPATCH):
            delivery.status = DeliveryStatus.DISPATCHED
            delivery.dispatch_provider = delivery.dispatch_provider or DispatchProvider.MAXIM_MANUAL

        if delivery.status == DeliveryStatus.DISPATCHED and delivery.dispatched_at is None:
            delivery.dispatched_at = now_utc()
        if delivery.status == DeliveryStatus.DELIVERED and delivery.delivered_at is None:
            delivery.delivered_at = now_utc()

        self.db.commit()
        log_event(logger, "delivery.status_updated", delivery_id=delivery.id, status=delivery.status.value)
        return delivery

    # ------------------------------------------------------------------ dispatch sheet

    def dispatch_sheet(self, delivery_date: date) -> DispatchSheetOut:
        """``GET /deliveries/dispatch-sheet``: route order if a plan exists, else by desired time."""
        deliveries = self.deliveries.list_for_date(delivery_date)
        plan = self.route_plans.latest_for_date(delivery_date)
        sequence_by_delivery: dict[int, int] = (
            {stop.delivery_id: stop.sequence for stop in plan.stops} if plan is not None else {}
        )

        def sort_key(delivery: Delivery) -> tuple[int, object]:
            if delivery.id in sequence_by_delivery:
                return (0, sequence_by_delivery[delivery.id])
            desired = delivery.order.delivery_time if delivery.order else None
            return (1, desired or time(23, 59))

        ordered = sorted(deliveries, key=sort_key)
        stops = [self._sheet_stop(sequence, delivery) for sequence, delivery in enumerate(ordered, start=1)]
        return DispatchSheetOut(date=delivery_date, text=self._sheet_text(delivery_date, stops), stops=stops)

    @staticmethod
    def _sheet_stop(sequence: int, delivery: Delivery) -> DispatchSheetStop:
        order = delivery.order
        return DispatchSheetStop(
            sequence=sequence,
            delivery_id=delivery.id,
            order_id=delivery.order_id,
            address=_address(delivery),
            latitude=float(delivery.latitude) if delivery.latitude is not None else None,
            longitude=float(delivery.longitude) if delivery.longitude is not None else None,
            eta=None,  # only "/deliveries/routes" carries a computed ETA
            desired_time=order.delivery_time if order else None,
            recipient_name=delivery.recipient_name,
            phone=delivery.recipient_phone,
            courier_comment=delivery.courier_comment,
            items_summary=items_summary(order) if order is not None else "",
            amount_to_collect=_amount_to_collect(delivery),
            status=delivery.status,
            external_id=delivery.external_id,
        )

    @staticmethod
    def _sheet_text(delivery_date: date, stops: list[DispatchSheetStop]) -> str:
        lines = [f"Лист доставки на {format_date_ru(delivery_date)}", ""]
        if not stops:
            lines.append(SHEET_EMPTY_LINE)
            return "\n".join(lines)
        for stop in stops:
            lines.append(f"{stop.sequence}. Заказ №{stop.order_id}")
            lines.append(f"   Адрес: {stop.address or NO_ADDRESS_LABEL}")
            contact = " ".join(filter(None, (stop.recipient_name, stop.phone)))
            if contact:
                lines.append(f"   Получатель: {contact}")
            if stop.desired_time:
                lines.append(f"   Желаемое время: {format_time(stop.desired_time)}")
            if stop.items_summary:
                lines.append(f"   Состав: {stop.items_summary}")
            if stop.courier_comment:
                lines.append(f"   Комментарий: {stop.courier_comment}")
            if stop.amount_to_collect > 0:
                lines.append(f"   Взять с клиента: {format_money(stop.amount_to_collect)}")
            lines.append("")
        return "\n".join(lines).rstrip()
