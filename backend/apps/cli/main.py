"""Typer CLI adapter for local Lou workflows."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from lou.application import AnalysisRequest, AnalysisResult, build_fixture_service
from lou.core.errors import LouError
from lou.core.settings import get_settings
from lou.core.version import APP_VERSION
from lou.repository import resolve_repository_revisions

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
    ci: bool = typer.Option(False, "--ci", help="Use CI exit codes for measured outcomes."),
    config: Path | None = typer.Option(
        None, "--config", exists=True, dir_okay=False, readable=True, help="Bounded JSON config."
    ),
    force: bool = typer.Option(False, "--force", help="Create a fresh run for identical inputs."),
    force_token: str | None = typer.Option(None, "--force-token"),
    output: str = typer.Option("text", "--output", help="text or json"),
) -> None:
    """Run the local broken-store fixture verification vertical slice."""

    if output not in {"text", "json"}:
        _input_error("--output must be either text or json")
    if force and not force_token:
        _input_error("--force requires --force-token")
    try:
        configuration = _read_configuration(config)
        revisions = resolve_repository_revisions(
            repository_path=repo,
            base_revision=base,
            candidate_revision=candidate,
        )
        request = AnalysisRequest(
            repository_id=_repository_id(revisions.repository_root),
            repository_path=revisions.repository_root,
            base_commit_sha=revisions.base_commit_sha,
            candidate_commit_sha=revisions.candidate_commit_sha,
            configuration=configuration,
            toolchain_revision=_revision(configuration, "toolchain_revision"),
            policy_revision=_revision(configuration, "policy_revision"),
            force_new_run=force,
            force_token=force_token,
        )
    except (json.JSONDecodeError, LouError, OSError, ValueError) as error:
        _input_error(str(error) or "Invalid analysis input.")

    try:
        result = _build_service().run(request)
    except Exception:
        console.print("analysis failed; run [bold]lou doctor[/bold] and review local artifacts")
        raise typer.Exit(code=3) from None

    summary = _summary(result)
    if output == "json":
        console.print_json(json.dumps(summary, sort_keys=True))
    else:
        console.print(f"run: {summary['analysis_run_id']}")
        console.print(f"status: {summary['status']}")
        console.print(f"reused: {summary['reused']}")
        if summary["candidate"]:
            console.print(f"candidate: {summary['candidate']}")
        if summary["decision"]:
            console.print(f"decision: {summary['decision']}")
        if summary["message"]:
            console.print(f"message: {summary['message']}")
    raise typer.Exit(code=_exit_code(result, ci))


def _build_service() -> Any:
    """Keep persistence and Docker composition outside the command body."""

    return build_fixture_service(get_settings())


def _read_configuration(config: Path | None) -> dict[str, object]:
    if config is None:
        return {}
    raw = config.read_bytes()
    if len(raw) > 32_768:
        raise ValueError("configuration exceeds the 32 KiB limit")
    value = json.loads(raw)
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("configuration must be a JSON object")
    return value


def _repository_id(repository: Path) -> str:
    return f"local-{hashlib.sha256(str(repository).encode('utf-8')).hexdigest()[:20]}"


def _revision(configuration: dict[str, object], key: str) -> str:
    value = configuration.get(key, "1")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"configuration.{key} must be a non-empty string")
    return value


def _summary(result: AnalysisResult) -> dict[str, object]:
    candidate = next(
        (item for item in result.verification_results if item.phase == "candidate"), None
    )
    return {
        "analysis_run_id": result.analysis_run_id,
        "status": result.status,
        "reused": result.reused,
        "stages": list(result.stages),
        "message": result.message,
        "candidate": (
            {
                "status": candidate.status,
                "workload_id": candidate.workload_id,
                "classification": candidate.metadata.get("classification"),
                "query_count_delta": candidate.metrics.get("query_count_delta"),
                "artifact_uri": candidate.artifact_uri,
            }
            if candidate is not None
            else None
        ),
        "decision": (
            {
                "action": result.decision.action,
                "confidence": result.decision.confidence,
                "classification": result.decision.metadata.get("outcome_classification"),
            }
            if result.decision is not None
            else None
        ),
    }


def _exit_code(result: AnalysisResult, ci: bool) -> int:
    if result.status == "failed":
        return 3
    if not ci:
        return 0
    candidate = next(
        (item for item in result.verification_results if item.phase == "candidate"), None
    )
    if result.status == "inconclusive" or (candidate and candidate.status == "inconclusive"):
        return 2
    return 1 if candidate and candidate.status == "failed" else 0


def _input_error(message: str) -> None:
    console.print(f"input error: {message}")
    raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
