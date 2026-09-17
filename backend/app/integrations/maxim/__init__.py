"""MaximIntegration interface and implementations (06 §4; manual mode only — no official API)."""

from app.integrations.maxim.base import (
    DeliveryDispatchRequest,
    DispatchPoint,
    DispatchResult,
    DispatchStatus,
    MaximIntegration,
)
from app.integrations.maxim.factory import API_NOT_AVAILABLE_MESSAGE, get_maxim_integration
from app.integrations.maxim.manual import ManualMaximIntegration

__all__ = [
    "API_NOT_AVAILABLE_MESSAGE",
    "DeliveryDispatchRequest",
    "DispatchPoint",
    "DispatchResult",
    "DispatchStatus",
    "ManualMaximIntegration",
    "MaximIntegration",
    "get_maxim_integration",
]
