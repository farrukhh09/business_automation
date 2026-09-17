"""API router: every module from docs/architecture/04-api.md under the ``/api`` prefix.

The default rate limit (04-api.md §0: 120/min per IP) is applied here as a router dependency;
see ``app.core.rate_limit`` for why slowapi's middleware is not used.
"""

from fastapi import APIRouter, Depends

from app.api.routes import (
    auth,
    conversations,
    customers,
    deliveries,
    faq,
    health,
    media,
    orders,
    production,
    products,
    public,
    reports,
    settings,
    statistics,
    users,
    webhooks,
)
from app.core.rate_limit import default_rate_limit

ROUTE_MODULES = (
    health,
    auth,
    users,
    customers,
    products,
    orders,
    production,
    statistics,
    reports,
    deliveries,
    faq,
    conversations,
    settings,
    webhooks,
    public,
    media,
)

api_router = APIRouter(prefix="/api", dependencies=[Depends(default_rate_limit)])
for _module in ROUTE_MODULES:
    api_router.include_router(_module.router)
