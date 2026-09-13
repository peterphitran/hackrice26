"""Local-first configuration for Lou."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="LOU_",
        extra="ignore",
    )

    environment: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://lou:lou@localhost:5432/lou"
    artifact_root: Path = Field(default=Path(".lou/artifacts"))


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""

    return Settings()
