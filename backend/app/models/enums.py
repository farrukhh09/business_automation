"""Domain enums (docs/architecture/02-data-model.md, section "Enums").

Every enum value equals its name, except ``Language`` ("ru", "tg").
Stored in the DB as VARCHAR(32) + CHECK (see ``app.models.base.enum_type``).
"""

from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"


class Language(StrEnum):
    RU = "ru"
    TG = "tg"


class OrderStatus(StrEnum):
    NEW = "NEW"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    PREPARING = "PREPARING"
    READY = "READY"
    HANDED_TO_COURIER = "HANDED_TO_COURIER"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class PaymentStatus(StrEnum):
    UNPAID = "UNPAID"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    REFUNDED = "REFUNDED"


class DeliveryType(StrEnum):
    DELIVERY = "DELIVERY"
    PICKUP = "PICKUP"


class PaymentMethod(StrEnum):
    CASH = "CASH"
    CARD = "CARD"
    TRANSFER = "TRANSFER"
    OTHER = "OTHER"


class PaymentKind(StrEnum):
    PAYMENT = "PAYMENT"
    REFUND = "REFUND"


class OrderSource(StrEnum):
    INSTAGRAM = "INSTAGRAM"
    ADMIN = "ADMIN"


class ActorType(StrEnum):
    USER = "USER"
    CUSTOMER = "CUSTOMER"
    AI = "AI"
    SYSTEM = "SYSTEM"


class ConversationMode(StrEnum):
    AI = "AI"
    HUMAN_HANDOFF = "HUMAN_HANDOFF"


class MessageDirection(StrEnum):
    INCOMING = "INCOMING"
    OUTGOING = "OUTGOING"


class MessageType(StrEnum):
    TEXT = "TEXT"
    VOICE = "VOICE"
    IMAGE = "IMAGE"
    SYSTEM = "SYSTEM"


class MessageSender(StrEnum):
    CUSTOMER = "CUSTOMER"
    AI = "AI"
    OPERATOR = "OPERATOR"
    SYSTEM = "SYSTEM"


class MessageDeliveryStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Intent(StrEnum):
    FAQ = "FAQ"
    PRODUCT_QUERY = "PRODUCT_QUERY"
    CREATE_ORDER = "CREATE_ORDER"
    CHANGE_ORDER = "CHANGE_ORDER"
    CANCEL_ORDER = "CANCEL_ORDER"
    DELIVERY_QUERY = "DELIVERY_QUERY"
    PAYMENT_QUERY = "PAYMENT_QUERY"
    ORDER_STATUS = "ORDER_STATUS"
    GREETING = "GREETING"
    COMPLAINT = "COMPLAINT"
    OPERATOR_REQUEST = "OPERATOR_REQUEST"
    OTHER = "OTHER"


class GeocodeStatus(StrEnum):
    PENDING = "PENDING"
    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    FAILED = "FAILED"
    MANUAL = "MANUAL"


class LocationSource(StrEnum):
    CUSTOMER_PIN = "CUSTOMER_PIN"
    GEOCODER = "GEOCODER"
    OPERATOR = "OPERATOR"
    COURIER = "COURIER"


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    AWAITING_DISPATCH = "AWAITING_DISPATCH"
    DISPATCHED = "DISPATCHED"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DispatchProvider(StrEnum):
    MAXIM_MANUAL = "MAXIM_MANUAL"
    MAXIM_API = "MAXIM_API"


__all__ = [
    "ActorType",
    "ConversationMode",
    "DeliveryStatus",
    "DeliveryType",
    "DispatchProvider",
    "GeocodeStatus",
    "Intent",
    "Language",
    "LocationSource",
    "MessageDeliveryStatus",
    "MessageDirection",
    "MessageSender",
    "MessageType",
    "OrderSource",
    "OrderStatus",
    "PaymentKind",
    "PaymentMethod",
    "PaymentStatus",
    "UserRole",
]
