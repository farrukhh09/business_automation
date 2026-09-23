"""The price list picture the bot sends (03-business-rules.md §1.4, 04-api.md §12).

The owner keeps a photo of the price list — the one they send in Direct by hand — and the bot sends
it whenever someone asks about prices or the assortment.  The picture is uploaded in the admin panel
(«Настройки → Фото прайс-листа»), stored in ``MEDIA_ROOT`` under a ``pl-`` name and remembered in the
business settings as that name; Instagram downloads it from ``{PUBLIC_BASE_URL}/api/media/{name}``,
exactly as it downloads a voice reply (06 §3).

Limits are Instagram's own (docs/research/instagram.md, Send API): a picture sent by URL may be
JPEG or PNG and at most 8 MB.  A file that Instagram would refuse is refused here, at upload time,
where the owner can see why — not silently in a customer's dialog.  Replacing the picture deletes
the previous file, so the media folder holds one price list, not a history of them.
"""

from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.models.user import User
from app.schemas.settings import BusinessSettings
from app.services.media_storage import PRICE_LIST_PREFIX, MediaStorage, extension_for
from app.services.settings_service import SettingsService

__all__ = [
    "IMAGE_EMPTY",
    "IMAGE_MEDIA_TYPES",
    "IMAGE_TOO_LARGE",
    "IMAGE_TYPE_NOT_SUPPORTED",
    "MAX_IMAGE_BYTES",
    "PriceListService",
]

#: What Instagram accepts as a picture sent by URL — WebP and GIF are not on that list.
IMAGE_MEDIA_TYPES: frozenset[str] = frozenset({"image/jpeg", "image/jpg", "image/png"})
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_EMPTY = "Файл пустой"
IMAGE_TYPE_NOT_SUPPORTED = "Instagram принимает только JPEG или PNG — сохраните фото в этом формате"
IMAGE_TOO_LARGE = "Изображение слишком большое: максимум 8 МБ"

SETTING_FIELD = "price_list_image"


class PriceListService:
    def __init__(self, db: Session, media: MediaStorage) -> None:
        self.db = db
        self.media = media
        self.settings = SettingsService(db)

    def save(self, data: bytes, content_type: str | None, *, user: User | None = None) -> BusinessSettings:
        """Store the picture and point the settings at it; the previous file is deleted."""
        media_type = (content_type or "").split(";", 1)[0].strip().lower()
        if not data:
            raise ValidationError(detail=IMAGE_EMPTY)
        if media_type not in IMAGE_MEDIA_TYPES:
            raise ValidationError(detail=IMAGE_TYPE_NOT_SUPPORTED)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValidationError(detail=IMAGE_TOO_LARGE)
        previous = self.settings.get().price_list_image
        filename = self.media.save(data, prefix=PRICE_LIST_PREFIX, extension=extension_for(media_type, "jpg"))
        updated = self.settings.update({SETTING_FIELD: filename}, user=user)
        if previous and previous != filename:
            self.media.delete(previous)
        return updated

    def clear(self, *, user: User | None = None) -> BusinessSettings:
        """Stop sending the picture and remove the file."""
        previous = self.settings.get().price_list_image
        updated = self.settings.update({SETTING_FIELD: None}, user=user)
        if previous:
            self.media.delete(previous)
        return updated
