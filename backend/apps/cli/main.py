"""Typer CLI adapter for local Lou workflows."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from lou.core.settings import get_settings
from lou.core.version import APP_VERSION

app = typer.Typer(help="Local-first evidence-driven software maintenance.", no_args_is_help=True)
console = Console()


@app.command()
def version() -> None:
    """Print the Lou application version."""

    console.print(APP_VERSION)


@app.command()
def doctor() -> None:
    """Print safe local configuration diagnostics."""

    settings = get_settings()
    console.print(f"environment: {settings.environment}")
    console.print(f"artifact_root: {settings.artifact_root}")
    console.print(f"database_configured: {bool(settings.database_url)}")


@app.command()
def analyze(
    repo: Path = typer.Option(..., "--repo", exists=True, file_okay=False, resolve_path=True),
    base: str = typer.Option(..., "--base"),
    candidate: str = typer.Option(..., "--candidate"),
) -> None:
    """Validate analysis input until the orchestration service is integrated."""

    console.print(f"validated repository: {repo}")
    console.print(f"base: {base}")
    console.print(f"candidate: {candidate}")
    console.print("analysis service is not wired yet; foundation validation completed.")
    raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
