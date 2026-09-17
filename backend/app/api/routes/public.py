"""Public routes — docs/architecture/04-api.md §13 (the ``/l/{token}`` map-pin page).

No authentication: the token itself is the capability. Rate limited
(``PUBLIC_LOCATION_LIMIT``, 30/minute) since anyone holding the link can call these.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request

from app.api.deps import DbSession
from app.core.rate_limit import PUBLIC_LOCATION_LIMIT, limiter
from app.schemas.public import PublicLocationIn, PublicLocationOut, PublicLocationSubmitOut
from app.services.location_service import LocationService
from app.services.task_queue import TaskQueue, get_task_queue

router = APIRouter(prefix="/public", tags=["public"])

Token = Annotated[str, Path(min_length=1, max_length=128, description="Токен ссылки на карту")]
Queue = Annotated[TaskQueue, Depends(get_task_queue)]


@router.get("/location/{token}", response_model=PublicLocationOut, summary="Страница «Отметьте точку на карте»")
@limiter.limit(PUBLIC_LOCATION_LIMIT)
def get_public_location(request: Request, token: Token, db: DbSession) -> PublicLocationOut:
    return LocationService(db).public_info(token)


@router.post("/location/{token}", response_model=PublicLocationSubmitOut, summary="Сохранить точку клиента")
@limiter.limit(PUBLIC_LOCATION_LIMIT)
def submit_public_location(
    request: Request, token: Token, payload: PublicLocationIn, db: DbSession, queue: Queue
) -> PublicLocationSubmitOut:
    return LocationService(db, queue=queue).submit(token, payload.latitude, payload.longitude)
