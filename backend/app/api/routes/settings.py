"""Business settings routes — docs/architecture/04-api.md §12 "Settings".

- ``GET    /api/settings``                    STAFF  → ``BusinessSettings``
- ``PUT    /api/settings``                    ADMIN  ``BusinessSettingsUpdate`` (partial) → ``BusinessSettings``
- ``GET    /api/settings/integrations``       ADMIN  → ``{instagram, llm, stt, tts, geocoder, routing, maxim}``
- ``POST   /api/settings/price-list-image``   ADMIN  ``multipart{file}`` → ``BusinessSettings``
- ``DELETE /api/settings/price-list-image``   ADMIN  → ``BusinessSettings``

The integration status is computed from the environment configuration only (no external calls, no
secret values) — see ``app.services.integration_status``. ``Settings`` is injected through
``Depends(get_settings)`` so that it can be overridden in tests.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile

from app.api.deps import AdminUser, DbSession, StaffUser
from app.core.config import Settings, get_settings
from app.schemas.settings import BusinessSettings, BusinessSettingsUpdate, IntegrationsOut
from app.services.integration_status import build_integration_status
from app.services.media_storage import MediaStorage
from app.services.price_list_service import MAX_IMAGE_BYTES, PriceListService
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


@router.post("/price-list-image", response_model=BusinessSettings, summary="Загрузить фото прайс-листа")
def upload_price_list_image(
    db: DbSession,
    user: AdminUser,
    settings: AppSettings,
    file: Annotated[UploadFile, File(description="Фото прайс-листа: JPEG или PNG, до 8 МБ")],
) -> BusinessSettings:
    """The picture the bot sends on a question about prices or the assortment (03 §1.4)."""
    data = file.file.read(MAX_IMAGE_BYTES + 1)  # one byte past the limit is enough to reject it
    service = PriceListService(db, MediaStorage(settings.media_root))
    return service.save(data, file.content_type, user=user)


@router.delete("/price-list-image", response_model=BusinessSettings, summary="Убрать фото прайс-листа")
def delete_price_list_image(db: DbSession, user: AdminUser, settings: AppSettings) -> BusinessSettings:
    return PriceListService(db, MediaStorage(settings.media_root)).clear(user=user)
