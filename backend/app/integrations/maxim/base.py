"""Courier dispatch interface (SPEC §26, 06-integrations.md §4).

The business logic never knows the provider: it builds a ``DeliveryDispatchRequest`` and gets a
``DispatchResult`` back. Maxim has no official API (docs/research/maxim.md, verdict C), so the only
implementation is ``ManualMaximIntegration`` — it makes **no** network calls and returns a card the
operator copies into the Maxim app.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import time as time_type
from decimal import Decimal

from app.models.delivery import Delivery
from app.models.enums import DeliveryStatus, DispatchProvider

CURRENCY_LABEL = "сомони"


@dataclass(frozen=True, slots=True)
class DispatchPoint:
    """Pickup or drop-off point of the courier task."""

    name: str = ""
    address: str = ""
    latitude: float | None = None
    longitude: float | None = None

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


@dataclass(frozen=True, slots=True)
class DeliveryDispatchRequest:
    """Everything a courier service (or a human operator) needs to take the parcel."""

    order_id: int
    delivery_id: int
    pickup: DispatchPoint
    dropoff: DispatchPoint
    recipient_name: str | None = None
    recipient_phone: str | None = None
    courier_comment: str | None = None
    items_summary: str = ""
    amount_to_collect: Decimal = Decimal("0.00")
    delivery_date: date_type | None = None
    delivery_time: time_type | None = None


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """Outcome of ``create_delivery`` / ``cancel_delivery`` (04 §9 ``DispatchResultOut``)."""

    provider: DispatchProvider
    status: DeliveryStatus
    requires_operator: bool
    instructions: str
    copy_text: str
    external_id: str | None = None


@dataclass(frozen=True, slots=True)
class DispatchStatus:
    """Current courier status as the provider reports it."""

    provider: DispatchProvider
    status: DeliveryStatus
    external_id: str | None = None
    external_status: str | None = None
    courier_name: str | None = None
    courier_phone: str | None = None
    changed: bool = False  # the provider knows something the database does not
    details: dict[str, str] = field(default_factory=dict)


class MaximIntegration(ABC):
    """Courier provider interface (SPEC §26)."""

    provider: DispatchProvider

    @abstractmethod
    def create_delivery(self, request: DeliveryDispatchRequest) -> DispatchResult:
        """Hand the delivery over to the courier service."""

    @abstractmethod
    def get_delivery_status(self, delivery: Delivery) -> DispatchStatus:
        """Current status of an already dispatched delivery."""

    @abstractmethod
    def cancel_delivery(self, delivery: Delivery) -> DispatchResult:
        """Cancel a dispatched delivery."""
