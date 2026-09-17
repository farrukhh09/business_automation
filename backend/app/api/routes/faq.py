"""FAQ routes — docs/architecture/04-api.md §10 "FAQ".

- ``GET    /api/faq``       STAFF  ``include_inactive`` → ``FaqOut[]``
- ``POST   /api/faq``       ADMIN  ``FaqCreate`` → ``FaqOut`` 201
- ``PATCH  /api/faq/{id}``  ADMIN  ``FaqUpdate`` (partial) → ``FaqOut``
- ``DELETE /api/faq/{id}``  ADMIN  → 204 (hard delete)

Thin layer (01 §3): every rule lives in ``FaqService``.
"""

from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status

from app.api.deps import AdminUser, DbSession, StaffUser
from app.models.faq import FaqItem
from app.schemas.faq import FaqCreate, FaqOut, FaqUpdate
from app.services.faq_service import FaqService

router = APIRouter(prefix="/faq", tags=["faq"])

FaqId = Annotated[int, Path(ge=1, description="Идентификатор вопроса")]
IncludeInactive = Annotated[bool, Query(description="Показывать выключенные вопросы")]


@router.get("", response_model=list[FaqOut], summary="Список вопросов и ответов")
def list_faq(db: DbSession, user: StaffUser, include_inactive: IncludeInactive = False) -> list[FaqItem]:
    return FaqService(db).list(include_inactive=include_inactive)


@router.post("", response_model=FaqOut, status_code=status.HTTP_201_CREATED, summary="Создать вопрос")
def create_faq(payload: FaqCreate, db: DbSession, user: AdminUser) -> FaqItem:
    return FaqService(db).create(payload, user=user)


@router.patch("/{faq_id}", response_model=FaqOut, summary="Изменить вопрос")
def update_faq(faq_id: FaqId, payload: FaqUpdate, db: DbSession, user: AdminUser) -> FaqItem:
    return FaqService(db).update(faq_id, payload, user=user)


@router.delete(
    "/{faq_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Удалить вопрос",
)
def delete_faq(faq_id: FaqId, db: DbSession, user: AdminUser) -> Response:
    FaqService(db).delete(faq_id, user=user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
