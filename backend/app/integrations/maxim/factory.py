"""Maxim adapter factory (06 §4).

``MAXIM_MODE=manual`` (default) → ``ManualMaximIntegration``.
``MAXIM_MODE=api`` → ``IntegrationNotConfiguredError``: there is no official Maxim API
(docs/research/maxim.md, verdict C), and production code must never pretend otherwise.
``MaximApiIntegration`` will be added only after official documentation / a contract.
"""

from app.core.config import Settings, get_settings
from app.core.exceptions import IntegrationNotConfiguredError
from app.integrations.maxim.base import MaximIntegration
from app.integrations.maxim.manual import ManualMaximIntegration

API_NOT_AVAILABLE_MESSAGE = "Официальный API Maxim недоступен"
MANUAL_MODE = "manual"
API_MODE = "api"


def get_maxim_integration(settings: Settings | None = None) -> MaximIntegration:
    settings = settings or get_settings()
    mode = (settings.MAXIM_MODE or "").strip().lower()
    if mode == MANUAL_MODE:
        return ManualMaximIntegration(settings)
    if mode == API_MODE:
        raise IntegrationNotConfiguredError(API_NOT_AVAILABLE_MESSAGE, provider=API_MODE)
    raise IntegrationNotConfiguredError(f"Неизвестный режим Maxim: {mode}", provider=mode or None)
