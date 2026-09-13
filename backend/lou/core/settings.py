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
    migration_database_url: str = (
        "postgresql+psycopg://lou_migrator:lou_migrator@localhost:5432/lou"
    )
    database_url: str = "postgresql+psycopg://lou:lou@localhost:5432/lou"
    test_database_url: str = (
        "postgresql+psycopg://lou_test_app:lou_test_app@localhost:5432/lou_test"
    )
    fixture_database_url: str = "postgresql://lou_migrator:lou_migrator@postgres:5432/lou"
    artifact_root: Path = Field(default=Path(".lou/artifacts"))
    telemetry_exporter: str = "none"
    telemetry_otlp_endpoint: str = "http://127.0.0.1:4318/v1/traces"
    telemetry_export_timeout_seconds: float = Field(default=2.0, gt=0, le=10)
    telemetry_sample_rate: float = Field(default=0.1, ge=0, le=1)
    telemetry_max_spans_per_run: int = Field(default=500, ge=1, le=5000)
    telemetry_max_artifact_bytes: int = Field(default=1024 * 1024, ge=1024, le=10 * 1024 * 1024)
    telemetry_retention_days: int = Field(default=7, ge=1, le=90)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""

    return Settings()
