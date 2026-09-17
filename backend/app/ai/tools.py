"""AI tools (SPEC §39, docs/architecture/05-ai.md §7).

Twelve tools, two very different kinds:

- **read** tools (``get_products``, ``get_product``, ``get_customer``, ``get_order``,
  ``get_customer_orders``, ``calculate_order_total``, ``get_delivery_info``) are offered to the LLM
  during understanding (strict JSON schemas) and executed here, read-only. The customer is always the
  one of the dialog — it is never taken from the model's arguments — and ``get_order`` only returns
  that customer's orders. Prices and totals come from the database.
- **write** tools (``create_order_draft``, ``update_order_draft``, ``confirm_order``, ``cancel_order``,
  ``request_operator``) are never offered to the model. They are methods called by ``DialogService``
  after its own checks; they enforce ownership once more and ``confirm_order``/``cancel_order`` require
  the deterministic ``classify_confirmation`` decision ``YES`` (03 §5).
"""

import copy
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.ai.confirmation import ConfirmationDecision
from app.core.exceptions import BusinessRuleError
from app.core.logging import get_logger, log_event
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.enums import ActorType
from app.models.order import Order
from app.models.product import Product
from app.repositories.orders import OrderRepository
from app.repositories.products import ProductRepository
from app.services.conversation_service import ConversationService
from app.services.order_pricing import money
from app.services.order_service import OrderService
from app.services.settings_service import SettingsService

logger = get_logger(__name__)

__all__ = [
    "READ_TOOL_NAMES",
    "WRITE_TOOL_NAMES",
    "ToolContext",
    "ToolNotAllowedError",
    "ToolRegistry",
    "order_view",
    "product_view",
    "tool_definitions",
]

READ_TOOL_NAMES: tuple[str, ...] = (
    "get_products",
    "get_product",
    "get_customer",
    "get_order",
    "get_customer_orders",
    "calculate_order_total",
    "get_delivery_info",
)
WRITE_TOOL_NAMES: tuple[str, ...] = (
    "create_order_draft",
    "update_order_draft",
    "confirm_order",
    "cancel_order",
    "request_operator",
)

CUSTOMER_ORDERS_LIMIT = 10
MAX_TOTAL_ITEMS = 20
MAX_TOTAL_QUANTITY = 500


class ToolNotAllowedError(BusinessRuleError):
    code = "tool_not_allowed"
    default_detail = "Операция недоступна AI-ассистенту"


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Whose data the tools may touch: the customer (and thread) of the current dialog."""

    customer: Customer
    conversation: Conversation | None = None


# --------------------------------------------------------------------------- schemas


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_products",
        "description": "Active catalog: id, name, aliases, price (TJS), unit, description.",
        "strict": True,
        "input_schema": _object({}),
    },
    {
        "name": "get_product",
        "description": "One active product of the catalog by id.",
        "strict": True,
        "input_schema": _object({"product_id": {"type": "integer"}}),
    },
    {
        "name": "get_customer",
        "description": "The customer of this dialog: name, phone, language, whether they are a regular customer.",
        "strict": True,
        "input_schema": _object({}),
    },
    {
        "name": "get_order",
        "description": "One order of this customer by its number (other customers' orders are not visible).",
        "strict": True,
        "input_schema": _object({"order_id": {"type": "integer"}}),
    },
    {
        "name": "get_customer_orders",
        "description": "The last 10 orders of this customer with status, date, total and payment status.",
        "strict": True,
        "input_schema": _object({}),
    },
    {
        "name": "calculate_order_total",
        "description": "Total of a list of catalog items, prices taken from the database.",
        "strict": True,
        "input_schema": _object(
            {
                "items": {
                    "type": "array",
                    "items": _object({"product_id": {"type": "integer"}, "quantity": {"type": "integer"}}),
                }
            }
        ),
    },
    {
        "name": "get_delivery_info",
        "description": "Delivery, pickup, working hours and payment methods as configured by the bakery.",
        "strict": True,
        "input_schema": _object({}),
    },
]


def tool_definitions() -> list[dict[str, Any]]:
    """Read tools for ``LLMClient.complete_json(tools=...)`` (a copy — callers may not mutate the source)."""
    return copy.deepcopy(_TOOL_DEFINITIONS)


# --------------------------------------------------------------------------- views


def _money_text(value: Any) -> str:
    return f"{money(value if value is not None else 0):.2f}"


def product_view(product: Product) -> dict[str, Any]:
    return {
        "id": product.id,
        "name": product.name,
        "aliases": list(product.aliases or []),
        "price": _money_text(product.price),
        "unit": product.unit,
        "description": product.description,
    }


def order_view(order: Order) -> dict[str, Any]:
    return {
        "order_id": order.id,
        "status": order.status.value,
        "delivery_type": order.delivery_type.value if order.delivery_type else None,
        "delivery_date": order.delivery_date.isoformat() if order.delivery_date else None,
        "delivery_time": order.delivery_time.strftime("%H:%M") if order.delivery_time else None,
        "total": _money_text(order.total_amount),
        "payment_status": order.payment_status.value,
        "items": [{"name": item.product_name, "quantity": item.quantity} for item in order.items],
    }


# --------------------------------------------------------------------------- registry


class ToolRegistry:
    def __init__(self, db: Session, context: ToolContext, *, order_service: OrderService | None = None) -> None:
        self.db = db
        self.context = context
        self.products = ProductRepository(db)
        self.orders = OrderRepository(db)
        self.order_service = order_service or OrderService(db)
        self._read_handlers: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
            "get_products": lambda args: self.get_products(),
            "get_product": lambda args: self.get_product(args["product_id"]),
            "get_customer": lambda args: self.get_customer(),
            "get_order": lambda args: self.get_order(args["order_id"]),
            "get_customer_orders": lambda args: self.get_customer_orders(),
            "calculate_order_total": lambda args: self.calculate_order_total(args["items"]),
            "get_delivery_info": lambda args: self.get_delivery_info(),
        }

    # ------------------------------------------------------------------ LLM entry point

    def execute(self, name: str, arguments: Mapping[str, Any] | None) -> dict[str, Any]:
        """``tool_executor`` for ``complete_json``: read tools only; errors are data, never exceptions."""
        if name in WRITE_TOOL_NAMES:
            log_event(logger, "ai.tool_denied", level=logging.WARNING, tool=name)
            return {"error": "not_allowed", "detail": "the backend performs this action itself"}
        handler = self._read_handlers.get(name)
        if handler is None:
            log_event(logger, "ai.tool_denied", level=logging.WARNING, tool=name, reason="unknown")
            return {"error": "unknown_tool"}
        try:
            result = handler(dict(arguments or {}))
        except (KeyError, TypeError, ValueError):
            log_event(logger, "ai.tool_call", level=logging.WARNING, tool=name, ok=False)
            return {"error": "invalid_arguments"}
        log_event(logger, "ai.tool_call", tool=name, ok="error" not in result)
        return result

    # ------------------------------------------------------------------ read tools

    def get_products(self) -> dict[str, Any]:
        return {"products": [product_view(product) for product in self.products.list()]}

    def get_product(self, product_id: Any) -> dict[str, Any]:
        product = self.products.get_active(int(product_id))
        return product_view(product) if product is not None else {"error": "not_found"}

    def get_customer(self) -> dict[str, Any]:
        customer = self.context.customer
        return {
            "id": customer.id,
            "name": customer.name,
            "phone": customer.phone,
            "language": customer.language.value,
            "is_regular": not customer.is_new,
        }

    def get_order(self, order_id: Any) -> dict[str, Any]:
        order = self.orders.get(int(order_id))
        if order is None or order.customer_id != self.context.customer.id:
            return {"error": "not_found"}
        return order_view(order)

    def get_customer_orders(self) -> dict[str, Any]:
        orders = self.orders.list_for_customer(self.context.customer.id, limit=CUSTOMER_ORDERS_LIMIT)
        return {"orders": [order_view(order) for order in orders]}

    def calculate_order_total(self, items: Any) -> dict[str, Any]:
        if not isinstance(items, list):
            raise ValueError("items must be a list")
        lines: list[dict[str, Any]] = []
        unavailable: list[int] = []
        total = Decimal("0.00")
        for entry in items[:MAX_TOTAL_ITEMS]:
            product_id, quantity = int(entry["product_id"]), int(entry["quantity"])
            product = self.products.get_active(product_id)
            if product is None or not 1 <= quantity <= MAX_TOTAL_QUANTITY:
                unavailable.append(product_id)
                continue
            line_total = money(money(product.price) * quantity)
            total += line_total
            lines.append(
                {
                    "product_id": product.id,
                    "name": product.name,
                    "quantity": quantity,
                    "unit_price": _money_text(product.price),
                    "total_price": _money_text(line_total),
                }
            )
        return {"items": lines, "total": _money_text(total), "unavailable": unavailable}

    def get_delivery_info(self) -> dict[str, Any]:
        settings = SettingsService(self.db).get()
        return {
            "delivery_info": settings.delivery_info_text or None,
            "pickup_address": settings.pickup_address or None,
            "working_hours": settings.working_hours or None,
            "payment_methods": settings.payment_methods_text or None,
            "min_lead_time_hours": settings.min_lead_time_hours,
            "max_days_ahead": settings.max_days_ahead,
        }

    # ------------------------------------------------------------------ write tools (DialogService only)

    def _own(self, order: Order) -> Order:
        if order.customer_id != self.context.customer.id:
            log_event(logger, "ai.tool_denied", level=logging.WARNING, reason="foreign_order", order_id=order.id)
            raise ToolNotAllowedError("Заказ принадлежит другому клиенту", order_id=order.id)
        return order

    def create_order_draft(self) -> Order:
        return self.order_service.create_draft(self.context.customer, self.context.conversation, actor=ActorType.AI)

    def update_order_draft(
        self,
        order: Order,
        items: list[dict[str, Any]] | None = None,
        items_mode: str = "add",
        **fields: Any,
    ) -> Order:
        """Draft only (``OrderService`` refuses confirmed orders for the bot, 03 §1.5)."""
        return self.order_service.update_draft(
            self._own(order), items=items, items_mode=items_mode, actor=ActorType.AI, request_geocoding=False, **fields
        )

    def confirm_order(self, order: Order, decision: ConfirmationDecision) -> Order:
        """03 §5: only an explicit "Да" (``classify_confirmation == YES``) confirms an order."""
        if decision != ConfirmationDecision.YES:
            raise ToolNotAllowedError("Заказ подтверждается только явным «Да» клиента", decision=str(decision))
        return self.order_service.confirm(self._own(order), actor_type=ActorType.CUSTOMER)

    def cancel_order(self, order: Order, decision: ConfirmationDecision, reason: str | None = None) -> Order:
        if decision != ConfirmationDecision.YES:
            raise ToolNotAllowedError("Заказ отменяется только после явного «Да» клиента", decision=str(decision))
        return self.order_service.cancel(self._own(order), reason=reason, actor_type=ActorType.CUSTOMER)

    def record_change_request(self, order: Order, text: str) -> Order:
        """03 §1.5: a placed order is not changed by the bot — the request goes to the journal."""
        return self.order_service.record_change_request(self._own(order), text, actor_type=ActorType.AI)

    def request_operator(self, reason: str) -> Conversation | None:
        conversation = self.context.conversation
        if conversation is None:
            return None
        return ConversationService(self.db).handoff(conversation, reason, needs_attention=True, commit=False)
