"""FastAPI app factory for the read-only data API."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from finora.core.config import Settings

_DEV_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API app. Without an explicit Settings, configuration is
    loaded from $FINORA_CONFIG / ./config — which is what `finora serve
    --reload` relies on (uvicorn re-imports this factory by string)."""
    if settings is None:
        settings = Settings.load(None)
    app = FastAPI(title="Finora Data API", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_DEV_ORIGINS,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    from finora.web.routes import router

    app.include_router(router)
    return app
