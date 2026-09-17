"""Rate limiting (slowapi) — docs/architecture/04-api.md §0.

Storage: Redis (``REDIS_URL``) in development/production, ``memory://`` when ``APP_ENV=test`` or
``REDIS_URL`` is empty. A Redis outage falls back to per-process memory limits.

- Default limit (120/min per IP): the ``default_rate_limit`` dependency attached to ``api_router``.
  slowapi's own middleware is deliberately NOT used: with FastAPI 0.141 ``app.routes`` holds
  included routers lazily (``_IncludedRouter``), so ``SlowAPI(ASGI)Middleware`` cannot resolve the
  endpoint and silently treats every ``/api`` route as exempt. The dependency runs after routing,
  when ``scope["endpoint"]`` is known.
- Route-specific limits: the decorator (the endpoint needs a ``request: Request`` parameter); it
  replaces the default limit for that route::

      @router.post("/login")
      @limiter.limit(LOGIN_LIMIT)
      def login(request: Request, ...): ...

- Probes: ``@limiter.exempt``.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_LIMIT = "120/minute"
LOGIN_LIMIT = "5/minute"
REFRESH_LIMIT = "30/minute"
WEBHOOK_LIMIT = "600/minute"
PUBLIC_LOCATION_LIMIT = "30/minute"

MEMORY_STORAGE_URI = "memory://"


def rate_limit_storage_uri(settings: Settings) -> str:
    redis_url = settings.REDIS_URL.strip()
    if settings.is_test or not redis_url:
        return MEMORY_STORAGE_URI
    return redis_url


def build_limiter(settings: Settings | None = None) -> Limiter:
    settings = settings or get_settings()
    storage_uri = rate_limit_storage_uri(settings)
    uses_redis = storage_uri != MEMORY_STORAGE_URI
    return Limiter(
        key_func=get_remote_address,
        default_limits=[DEFAULT_LIMIT],
        storage_uri=storage_uri,
        key_prefix="bakery-rl",
        headers_enabled=False,
        # A Redis outage must not take the API down: fall back to per-process memory limits.
        in_memory_fallback_enabled=uses_redis,
        swallow_errors=uses_redis,
    )


limiter: Limiter = build_limiter()


def _route_name(endpoint: object) -> str:
    return f"{getattr(endpoint, '__module__', '')}.{getattr(endpoint, '__name__', '')}"


def has_own_limits(endpoint: object) -> bool:
    """True when the endpoint is ``@limiter.exempt`` or declares its own ``@limiter.limit``."""
    name = _route_name(endpoint)
    return name in limiter._exempt_routes or name in limiter._route_limits or name in limiter._dynamic_route_limits


def default_rate_limit(request: Request) -> None:
    """Router-level dependency applying ``DEFAULT_LIMIT`` per client IP and path.

    Mirrors slowapi's middleware (``slowapi.middleware._should_exempt``): exempt routes and routes
    with their own ``@limiter.limit`` are skipped, so the decorator's limit *replaces* the default
    one (e.g. the webhook gets 600/min, not min(600, 120)). ``_check_request_limit(...,
    in_middleware=True)`` itself would add the default limits to decorated routes too, hence the
    explicit check. Sync on purpose: storage access may block (Redis), so FastAPI runs it in the
    threadpool.
    """
    endpoint = request.scope.get("endpoint")
    if endpoint is None or not limiter.enabled or has_own_limits(endpoint):
        return
    limiter._check_request_limit(request, endpoint, in_middleware=True)


def rate_limit_exceeded_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RateLimitExceeded)
    headers: dict[str, str] | None = None
    limit = getattr(exc, "limit", None)
    try:
        headers = {"Retry-After": str(int(limit.limit.get_expiry()))}  # type: ignore[union-attr]
    except Exception:  # limit details are best-effort
        headers = None
    logger.warning(
        "rate limit exceeded",
        extra={"event": "http.rate_limited", "path": request.url.path, "method": request.method},
    )
    return JSONResponse(
        status_code=429,
        content={"detail": "Слишком много запросов, попробуйте позже", "code": "rate_limited"},
        headers=headers,
    )
