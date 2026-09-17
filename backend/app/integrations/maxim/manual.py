"""Manual Maxim mode (``MAXIM_MODE=manual``) — 06 §4, docs/research/maxim.md §1, §2.

Maxim has no official API for courier orders (verdict C) and reverse-engineering the app is out of
the question, so this implementation **never touches the network**. ``create_delivery`` builds a
Russian card the operator copies into the Maxim app (or dictates by phone), marks the delivery
``AWAITING_DISPATCH`` and waits for the operator to type the Maxim order number and the courier in
``PATCH /deliveries/{id}`` → ``DISPATCHED``. ``get_delivery_status`` returns what the operator
entered; ``cancel_delivery`` tells them to cancel in the app.
"""

from decimal import Decimal

from app.core.config import Settings
from app.integrations.maxim.base import (
    DeliveryDispatchRequest,
    DispatchPoint,
    DispatchResult,
    DispatchStatus,
    MaximIntegration,
)
from app.models.delivery import Delivery
from app.models.enums import DeliveryStatus, DispatchProvider
from app.services.formatting import format_money

PROVIDER = DispatchProvider.MAXIM_MANUAL

CREATE_INSTRUCTIONS = (
    "Официального API «Максим» нет. Скопируйте карточку, оформите заказ в приложении «Максим» "
    "(или по телефону службы), затем внесите номер заказа и данные курьера в доставку — "
    "статус станет «Передан курьеру»."
)
CANCEL_INSTRUCTIONS = (
    "Отмените заказ в приложении «Максим» вручную. После отмены доставка помечена как отменённая."
)



def format_coordinates(point: DispatchPoint | Delivery) -> str | None:
    latitude = point.latitude
    longitude = point.longitude
    if latitude is None or longitude is None:
        return None
    return f"{float(latitude):.6f}, {float(longitude):.6f}"


def _point_line(point: DispatchPoint) -> str:
    parts = [part for part in (point.name.strip(), point.address.strip()) if part]
    return " — ".join(parts) if parts else "не указан"


class ManualMaximIntegration(MaximIntegration):
    """The only Maxim implementation: a human does the actual dispatch."""

    provider = PROVIDER

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    def __repr__(self) -> str:
        return "ManualMaximIntegration(mode='manual')"

    # ------------------------------------------------------------------ interface

    def create_delivery(self, request: DeliveryDispatchRequest) -> DispatchResult:
        return DispatchResult(
            provider=PROVIDER,
            status=DeliveryStatus.AWAITING_DISPATCH,
            requires_operator=True,
            instructions=CREATE_INSTRUCTIONS,
            copy_text=self.build_copy_text(request),
        )

    def get_delivery_status(self, delivery: Delivery) -> DispatchStatus:
        """No network: the stored status is the truth (the operator maintains it)."""
        return DispatchStatus(
            provider=PROVIDER,
            status=delivery.status,
            external_id=delivery.external_id,
            external_status=delivery.external_status,
            courier_name=delivery.courier_name,
            courier_phone=delivery.courier_phone,
            changed=False,
        )

    def cancel_delivery(self, delivery: Delivery) -> DispatchResult:
        lines = [f"Отмена доставки по заказу №{delivery.order_id}"]
        if delivery.external_id:
            lines.append(f"Номер заказа в «Максим»: {delivery.external_id}")
        if delivery.courier_name or delivery.courier_phone:
            lines.append(f"Курьер: {' '.join(filter(None, (delivery.courier_name, delivery.courier_phone)))}")
        return DispatchResult(
            provider=PROVIDER,
            status=DeliveryStatus.CANCELLED,
            requires_operator=True,
            instructions=CANCEL_INSTRUCTIONS,
            copy_text="\n".join(lines),
            external_id=delivery.external_id,
        )

    # ------------------------------------------------------------------ card

    @staticmethod
    def build_copy_text(request: DeliveryDispatchRequest) -> str:
        """Card for the operator: what to type into the Maxim app (Russian, ready to copy)."""
        header = f"Заказ №{request.order_id} — доставка"
        when = []
        if request.delivery_date:
            when.append(request.delivery_date.strftime("%d.%m.%Y"))
        if request.delivery_time:
            when.append(f"к {request.delivery_time.strftime('%H:%M')}")
        if when:
            header = f"{header} {' '.join(when)}"

        lines = [header, f"Откуда: {_point_line(request.pickup)}"]
        pickup_coordinates = format_coordinates(request.pickup)
        if pickup_coordinates:
            lines.append(f"Координаты: {pickup_coordinates}")
        lines.append(f"Куда: {_point_line(request.dropoff)}")
        dropoff_coordinates = format_coordinates(request.dropoff)
        if dropoff_coordinates:
            lines.append(f"Координаты: {dropoff_coordinates}")
        else:
            lines.append("Координаты: не заданы — уточните адрес у клиента")
        if request.recipient_name:
            lines.append(f"Получатель: {request.recipient_name}")
        if request.recipient_phone:
            lines.append(f"Телефон: {request.recipient_phone}")
        if request.items_summary:
            lines.append(f"Состав: {request.items_summary}")
        if request.courier_comment:
            lines.append(f"Комментарий курьеру: {request.courier_comment}")
        if request.amount_to_collect > 0:
            lines.append(f"Взять с клиента: {format_money(request.amount_to_collect)}")
        else:
            lines.append("Взять с клиента: не нужно, заказ оплачен")
        return "\n".join(lines)
