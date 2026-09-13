"""Database engine and session-factory construction."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from lou.core.settings import Settings, get_settings


def create_database_engine(settings: Settings | None = None) -> Engine:
    """Create a synchronous engine; callers own connection lifecycle."""

    active_settings = settings or get_settings()
    return create_engine(active_settings.database_url, pool_pre_ping=True)


def create_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    """Create a session factory for persistence adapters."""

    return sessionmaker(bind=create_database_engine(settings), expire_on_commit=False)


@dataclass(frozen=True)
class DatabaseHealth:
    """Bounded result of a database connectivity check."""

    available: bool


def check_database_health(settings: Settings | None = None) -> DatabaseHealth:
    """Check application-role connectivity without exposing connection details."""

    engine = create_database_engine(settings)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return DatabaseHealth(available=False)
    finally:
        engine.dispose()

    return DatabaseHealth(available=True)
