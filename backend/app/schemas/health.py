"""Health check schemas (04-api.md §13)."""

from pydantic import BaseModel


class HealthOut(BaseModel):
    status: str


class ReadinessOut(BaseModel):
    status: str  # "ok" | "unavailable"
    checks: dict[str, str]  # {"database": "ok"|"error", "redis": "ok"|"error"|"not_configured"}
