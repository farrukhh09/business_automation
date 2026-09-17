"""FastAPI application factory (docs/architecture/01-overview.md §2).

Run: ``uvicorn app.main:app --host 0.0.0.0 --port 8000``
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.core.rate_limit import limiter, rate_limit_exceeded_handler

logger = get_logger(__name__)

API_TITLE = "Домашняя выпечка — API"
API_VERSION = "1.0.0"
MIN_JWT_SECRET_LENGTH = 32


def _validate_production_settings(settings: Settings) -> None:
    if len(settings.JWT_SECRET) < MIN_JWT_SECRET_LENGTH:
        raise RuntimeError(
            f"JWT_SECRET must be set to at least {MIN_JWT_SECRET_LENGTH} characters when APP_ENV=production"
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    if settings.is_production:
        _validate_production_settings(settings)

    docs_enabled = not settings.is_production
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        docs_url="/api/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs_enabled else None,
        swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect" if docs_enabled else None,
    )
    app.state.settings = settings
    # Rate limiting: default limit via the api_router dependency, specific limits via
    # @limiter.limit decorators (app.core.rate_limit); 429 → {"detail", "code": "rate_limited"}.
    app.state.limiter = limiter

    register_exception_handlers(app)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    # add_middleware() prepends, so the last one added is the outermost:
    # RequestContext (request id + access log) → CORS → routes.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_middleware(RequestContextMiddleware)

    app.include_router(api_router)

    logger.info(
        "application created",
        extra={"event": "app.created", "app_env": settings.APP_ENV, "docs_enabled": docs_enabled},
    )
    return app


app = create_app()
