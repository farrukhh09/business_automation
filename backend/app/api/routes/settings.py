"""Business settings routes — docs/architecture/04-api.md §12 "Settings".

- ``GET /api/settings``               STAFF  → ``BusinessSettings``
- ``PUT /api/settings``               ADMIN  ``BusinessSettingsUpdate`` (partial) → ``BusinessSettings``
- ``GET /api/settings/integrations``  ADMIN  → ``{instagram, llm, stt, tts, geocoder, routing, maxim}``

The integration status is computed from the environment configuration only (no external calls, no
secret values) — see ``app.services.integration_status``. ``Settings`` is injected through
``Depends(get_settings)`` so that it can be overridden in tests.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import AdminUser, DbSession, StaffUser
from app.core.config import Settings, get_settings
from app.schemas.settings import BusinessSettings, BusinessSettingsUpdate, IntegrationsOut
from app.services.integration_status import build_integration_status
from app.services.settings_service import SettingsService

router = APIRouter(prefix="/settings", tags=["settings"])

AppSettings = Annotated[Settings, Depends(get_settings)]


@router.get("", response_model=BusinessSettings, summary="Настройки бизнеса")
def read_settings(db: DbSession, user: StaffUser) -> BusinessSettings:
    return SettingsService(db).get()


@router.put("", response_model=BusinessSettings, summary="Изменить настройки бизнеса")
def update_settings(payload: BusinessSettingsUpdate, db: DbSession, user: AdminUser) -> BusinessSettings:
    return SettingsService(db).update(payload, user=user)


@router.get("/integrations", response_model=IntegrationsOut, summary="Статус интеграций")
def read_integrations(user: AdminUser, settings: AppSettings) -> IntegrationsOut:
    return build_integration_status(settings)
