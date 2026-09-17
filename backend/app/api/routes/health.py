"""Health routes — docs/architecture/04-api.md §13 "Публичные и служебные маршруты".

- ``GET /api/health`` → ``{"status": "ok"}`` (liveness, no dependencies)
- ``GET /api/health/ready`` → 200/503 with database and Redis checks (short timeouts)

Both are PUBLIC and exempt from rate limiting (container/orchestrator probes).
"""

from typing import Any

import redis
import sqlalchemy as sa
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from redis.backoff import NoBackoff
from redis.retry import Retry
from sqlalchemy.orm import Session

from app.api.deps import DbSession
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.schemas.health import HealthOut, ReadinessOut

logger = get_logger(__name__)

router = APIRouter(prefix="/health", tags=["health"])

REDIS_TIMEOUT_SECONDS = 2.0

CheckResult = tuple[bool, str]


def check_database(db: Session) -> CheckResult:
    try:
        db.execute(sa.text("SELECT 1"))
    except Exception as exc:
        logger.warning(
            "database readiness check failed",
            extra={"event": "health.database_unavailable", "error": type(exc).__name__},
        )
        try:
            db.rollback()
        except Exception:  # the connection may be unusable; nothing else to do
            pass
        return False, "error"
    return True, "ok"


def check_redis(settings: Settings) -> CheckResult:
    url = settings.REDIS_URL.strip()
    if not url:
        # Redis is mandatory in production (Celery broker); optional for local development/tests.
        return (not settings.is_production), "not_configured"
    client: Any = redis.Redis.from_url(
        url,
        socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        socket_timeout=REDIS_TIMEOUT_SECONDS,
        retry=Retry(NoBackoff(), 0),
    )
    try:
        client.ping()
    except (redis.RedisError, OSError) as exc:
        logger.warning(
            "redis readiness check failed",
            extra={"event": "health.redis_unavailable", "error": type(exc).__name__},
        )
        return False, "error"
    finally:
        client.close()
    return True, "ok"


@router.get("", response_model=HealthOut, summary="Liveness probe")
@limiter.exempt
def health() -> HealthOut:
    return HealthOut(status="ok")


@router.get(
    "/ready",
    response_model=ReadinessOut,
    responses={503: {"model": ReadinessOut, "description": "A dependency is unavailable"}},
    summary="Readiness probe (database and Redis)",
)
@limiter.exempt
def ready(db: DbSession) -> JSONResponse:
    database_ok, database_status = check_database(db)
    redis_ok, redis_status = check_redis(get_settings())
    is_ready = database_ok and redis_ok
    body = ReadinessOut(
        status="ok" if is_ready else "unavailable",
        checks={"database": database_status, "redis": redis_status},
    )
    return JSONResponse(status_code=200 if is_ready else 503, content=body.model_dump())
