"""Database engine and session-factory construction."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from lou.core.settings import Settings, get_settings


def create_database_engine(settings: Settings | None = None) -> Engine:
    """Create a synchronous engine; callers own connection lifecycle."""

    active_settings = settings or get_settings()
    return create_engine(active_settings.database_url, pool_pre_ping=True)


def create_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    """Create a session factory for persistence adapters."""

    return sessionmaker(bind=create_database_engine(settings), expire_on_commit=False)
