"""Versioned API surface. All product routes live under /api/v1."""

from fastapi import APIRouter

from app.api.v1 import auth as auth_routes
from app.api.v1 import behavior as behavior_routes
from app.api.v1 import dashboard as dashboard_routes
from app.api.v1 import debug as debug_routes
from app.api.v1 import events as event_routes
from app.api.v1 import notifications as notification_routes
from app.api.v1 import replay as replay_routes
from app.api.v1 import stocks as stock_routes
from app.api.v1 import watchlists as watchlist_routes
from app.api.v1 import ws as ws_routes

api_router = APIRouter()

for _module in (
    auth_routes,
    dashboard_routes,
    watchlist_routes,
    stock_routes,
    event_routes,
    behavior_routes,
    replay_routes,
    notification_routes,
    debug_routes,
    ws_routes,
):
    api_router.include_router(_module.router)

__all__ = ["api_router"]
