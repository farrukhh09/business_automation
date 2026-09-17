"""Media routes — docs/architecture/04-api.md §13 (06-integrations.md §1, §3). PUBLIC.

``GET /api/media/{filename}`` serves files of ``MEDIA_ROOT``: synthesized voice replies (Instagram
downloads them by URL) and the media of incoming messages shown in "Диалоги". Names are random
(``MediaStorage``); anything that is not such a name, or not a file inside the root, is 404.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from fastapi.responses import FileResponse

from app.core.config import Settings, get_settings
from app.core.exceptions import NotFoundError
from app.services.media_storage import MediaStorage, media_type_for

router = APIRouter(prefix="/media", tags=["media"])

AppSettings = Annotated[Settings, Depends(get_settings)]


@router.get("/{filename}", response_class=FileResponse, summary="Медиафайл (голосовой ответ, вложение клиента)")
def get_media(filename: Annotated[str, Path(max_length=128)], settings: AppSettings) -> FileResponse:
    path = MediaStorage(settings.media_root).path_for(filename)
    if path is None:
        raise NotFoundError("Файл не найден")
    return FileResponse(
        path,
        media_type=media_type_for(path.name),
        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )
