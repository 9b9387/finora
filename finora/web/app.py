"""FastAPI app factory for the local data + strategy management API."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from finora.core.config import Settings

_DEV_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]


def create_app(
    settings: Settings | None = None, config_dir: Path | None = None
) -> FastAPI:
    """Build the API app. Without an explicit Settings, configuration is
    loaded from $FINORA_CONFIG / ./config — which is what `finora serve
    --reload` relies on (uvicorn re-imports this factory by string).
    config_dir is where the strategy manager persists strategies.yaml."""
    if config_dir is None:
        config_dir = Path(os.environ.get("FINORA_CONFIG", "config"))
    if settings is None:
        settings = Settings.load(config_dir)
    app = FastAPI(title="Finora Data API", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings = settings
    app.state.config_dir = config_dir
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_DEV_ORIGINS,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )

    from finora.web.routes import router

    app.include_router(router)
    return app
