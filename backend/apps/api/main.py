"""FastAPI adapter for the Lou application."""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from lou.core.settings import get_settings
from lou.core.version import APP_VERSION


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


def create_app() -> FastAPI:
    """Create the API without forcing infrastructure connections at import time."""

    app = FastAPI(title="Lou API", version=APP_VERSION)

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        get_settings()
        return HealthResponse(status="ok", service="lou", version=APP_VERSION)

    return app


app = create_app()
