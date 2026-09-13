"""Database engine and session-factory construction."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from lou.core.settings import Settings, get_settings


@lru_cache(maxsize=8)
def _engine_for(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def create_database_engine(settings: Settings | None = None) -> Engine:
    """Return the shared engine for a database URL.

    Engines own a connection pool and are safe to share, so one is reused per URL;
    creating a new engine per CLI command or HTTP request leaks connections until
    PostgreSQL refuses them.
    """

    active_settings = settings or get_settings()
    return _engine_for(active_settings.database_url)


def create_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    """Create a session factory for persistence adapters."""

    return sessionmaker(bind=create_database_engine(settings), expire_on_commit=False)


@dataclass(frozen=True)
class DatabaseHealth:
    """Bounded result of a database connectivity check."""

    available: bool


def check_database_health(settings: Settings | None = None) -> DatabaseHealth:
    """Check application-role connectivity without exposing connection details."""

    try:
        with create_database_engine(settings).connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return DatabaseHealth(available=False)

    return DatabaseHealth(available=True)
