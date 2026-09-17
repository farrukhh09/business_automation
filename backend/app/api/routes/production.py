"""Production routes — docs/architecture/04-api.md §6 "Production" (SPEC §19).

- ``GET /api/production`` STAFF ``date`` (default today) → ``ProductionSummaryOut``
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession, StaffUser
from app.schemas.production import ProductionSummaryOut
from app.services.production_service import ProductionService

router = APIRouter(prefix="/production", tags=["production"])

DateQuery = Annotated[date | None, Query(alias="date", description="Дата (по умолчанию сегодня)")]


@router.get("", response_model=ProductionSummaryOut, summary="Производственная сводка на дату")
def get_production(db: DbSession, user: StaffUser, summary_date: DateQuery = None) -> ProductionSummaryOut:
    return ProductionService(db).summary(summary_date)
