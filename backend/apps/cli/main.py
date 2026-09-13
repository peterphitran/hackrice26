"""Typer CLI adapter for local Lou workflows."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID

import typer
from rich.console import Console
from sqlalchemy.exc import SQLAlchemyError

from contracts import CanaryObservation, CanaryWindow, DeploymentTarget, Release, SLOPolicy
from lou.application import (
    AnalysisRequest,
    AnalysisResult,
    GitHubCliPublisher,
    PublicationError,
    PublicationService,
    RemediationAssemblyError,
    build_fixture_service,
    run_fixture_remediation,
)
from lou.core.errors import LouError
from lou.core.settings import get_settings
from lou.core.version import APP_VERSION
from lou.deployment import (
    DeploymentConflictError,
    DeploymentJournal,
    DeploymentService,
    InMemoryDeploymentAdapter,
    SqlAlchemyVerificationLookup,
)
from lou.persistence.database import create_session_factory
from lou.persistence.repositories import SqlAlchemyRemediationRunRepository
from lou.reporting import EvidenceReportReader, ReportError
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


@app.command()
def report(
    run: str = typer.Option(..., "--run", help="Persisted analysis run ID."),
    output: str = typer.Option("markdown", "--output", help="markdown or json"),
    file: Path | None = typer.Option(None, "--file", help="Optional report output file."),
) -> None:
    """Render a saved evidence report without rerunning analysis."""

    if output not in {"markdown", "json"}:
        _input_error("--output must be either markdown or json")
    settings = get_settings()
    try:
        report_document = EvidenceReportReader(
            create_session_factory(settings), settings.artifact_root
        ).read(run)
        rendered = (
            report_document.render_json() if output == "json" else report_document.render_markdown()
        )
        if file is not None:
            file.write_text(rendered, encoding="utf-8")
        else:
            console.print(rendered, end="")
    except (OSError, ReportError, SQLAlchemyError):
        console.print("report unavailable; review the run ID and local artifacts")
        raise typer.Exit(code=3) from None


@app.command()
def remediate(
    run: str = typer.Option(..., "--run", help="Persisted analysis run ID."),
    provider: str = typer.Option("mock", "--provider", help="mock or gemini"),
    force_token: str | None = typer.Option(None, "--force-token"),
    output: str = typer.Option("text", "--output", help="text or json"),
) -> None:
    """Propose and independently verify the supported local checkout repair."""

    if provider not in {"mock", "gemini"} or output not in {"text", "json"}:
        _input_error("--provider must be mock or gemini and --output must be text or json")
    try:
        result = run_fixture_remediation(
            UUID(run),
            provider=cast(Literal["mock", "gemini"], provider),
            settings=get_settings(),
            force_token=force_token,
        )
    except (RemediationAssemblyError, ValueError):
        _input_error("remediation input is unavailable or unsupported")
    except Exception:
        console.print(
            "remediation unavailable; review Docker, persisted evidence, and local artifacts"
        )
        raise typer.Exit(code=3) from None
    payload = _remediation_summary(result)
    if output == "json":
        console.print_json(json.dumps(payload, sort_keys=True))
    else:
        console.print(f"remediation: {payload['agent_run_id']}")
        console.print(f"status: {payload['status']}; stage: {payload['stage']}")
        console.print(f"termination: {payload['termination_reason']}")


@app.command("remediation-status")
def remediation_status(
    remediation: str = typer.Option(..., "--remediation", help="Persisted remediation run ID."),
    output: str = typer.Option("text", "--output", help="text or json"),
) -> None:
    """Read a remediation state and append-only stage history without rerunning it."""

    if output not in {"text", "json"}:
        _input_error("--output must be text or json")
    try:
        repository = SqlAlchemyRemediationRunRepository(create_session_factory(get_settings()))
        view = repository.get(UUID(remediation))
        if view is None:
            _input_error("remediation run was not found")
        assert view is not None
        payload = {
            "agent_run_id": str(view.id),
            "analysis_run_id": str(view.input.analysis_run_id),
            "status": view.status,
            "stage": view.stage,
            "attempt_count": view.attempt_count,
            "tokens_spent": view.tokens_spent,
            "estimated_cost_usd": view.estimated_cost_usd,
            "termination_reason": view.termination_reason,
            "attempts": [
                {"stage": item.value.stage, "outcome": item.value.outcome}
                for item in repository.list_attempts(view.id)
            ],
        }
    except (ValueError, SQLAlchemyError):
        _input_error("remediation run is unavailable")
    if output == "json":
        console.print_json(json.dumps(payload, sort_keys=True))
    else:
        console.print(f"remediation: {payload['agent_run_id']}")
        console.print(f"status: {payload['status']}; stage: {payload['stage']}")


@app.command("publish-remediation")
def publish_remediation(
    remediation: str = typer.Option(..., "--remediation"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    publish: bool = typer.Option(False, "--publish"),
    acknowledge: str | None = typer.Option(None, "--acknowledge"),
) -> None:
    """Create a credential-free PR plan or explicitly call the trusted publisher."""

    if dry_run == publish:
        _input_error("choose exactly one of --dry-run or --publish")
    settings = get_settings()
    session_factory = create_session_factory(settings)
    service = PublicationService(
        session_factory,
        EvidenceReportReader(session_factory, settings.artifact_root),
    )
    try:
        result = (
            service.dry_run(UUID(remediation))
            if dry_run
            else service.publish(
                UUID(remediation),
                acknowledgement=acknowledge or "",
                publisher=GitHubCliPublisher(),
            )
        )
    except (PublicationError, ValueError):
        _input_error("publication request was denied or unavailable")
    console.print_json(result.model_dump_json())


@app.command()
def deploy(
    run: str = typer.Option(..., "--run", help="Completed analysis run ID."),
    commit: str = typer.Option(..., "--commit", help="Verified commit SHA."),
    repository: str = typer.Option("local-demo", "--repository"),
    release: str | None = typer.Option(None, "--release", help="Idempotent release ID."),
    validation_passed: bool = typer.Option(True, "--validation-passed/--validation-failed"),
    telemetry_available: bool = typer.Option(True, "--telemetry-available/--telemetry-missing"),
    error_rate: float = typer.Option(0.0, "--error-rate", min=0, max=1),
    latency_ms: float = typer.Option(100.0, "--latency-ms", min=0.1),
    samples: int = typer.Option(20, "--samples", min=0),
    complete: bool = typer.Option(True, "--complete/--observing"),
    verification_run: list[str] = typer.Option(
        [], "--verification-run", help="Verification run ID backing the validation result."
    ),
    trace_id: list[str] = typer.Option(
        [], "--trace-id", help="Recorded trace ID backing the telemetry measurement."
    ),
    output: str = typer.Option("text", "--output", help="text or json"),
) -> None:
    """Run a bounded staging canary using local, credential-free composition."""

    if output not in {"text", "json"}:
        _input_error("--output must be text or json")
    if len(commit) < 7:
        _input_error("--commit must contain at least seven characters")
    release_id = release or f"release_{hashlib.sha256(f'{run}:{commit}'.encode()).hexdigest()[:16]}"
    now = datetime.now(UTC)
    service = _deployment_service()
    value = Release(
        release_id=release_id,
        repository_id=repository,
        commit_sha=commit,
        analysis_run_id=run,
        target=DeploymentTarget(
            environment="staging", adapter="local", namespace="lou", service="lou-demo"
        ),
        requested_by="lou-cli",
    )
    try:
        service.release(value)
        result = service.observe(
            release_id=release_id,
            window=CanaryWindow(
                release_id=release_id,
                started_at=now - timedelta(minutes=5 if complete else 0),
                deadline_at=now if complete else now + timedelta(minutes=5),
                minimum_samples=5,
            ),
            observation=CanaryObservation(
                release_id=release_id,
                observed_at=now,
                sample_count=samples,
                error_rate=error_rate if telemetry_available else None,
                latency_ms=latency_ms if telemetry_available else None,
                telemetry_available=telemetry_available,
                telemetry_age_seconds=0 if telemetry_available else None,
                validation_passed=validation_passed,
            ),
            policy=SLOPolicy(
                policy_revision="staging-local-v1",
                max_error_rate=0.05,
                max_latency_ms=300,
                minimum_samples=5,
                telemetry_max_age_seconds=60,
            ),
            verification_run_ids=tuple(verification_run),
            trace_ids=tuple(trace_id),
        )
    except (DeploymentConflictError, ValueError):
        _input_error("deployment inputs are invalid or conflict with an existing release")
    payload = _deployment_summary(result)
    if output == "json":
        console.print_json(json.dumps(payload, sort_keys=True))
    else:
        console.print(f"release: {payload['release_id']}")
        console.print(f"status: {payload['status']}")
        console.print(f"decision: {payload['decision']}")


@app.command("deploy-status")
def deploy_status(
    release: str = typer.Option(..., "--release"), output: str = typer.Option("text", "--output")
) -> None:
    """Read an append-only staging deployment record without rerunning it."""

    if output not in {"text", "json"}:
        _input_error("--output must be text or json")
    result = _deployment_service().status(release)
    if result is None:
        _input_error("release was not found")
    payload = _deployment_summary(result)
    if output == "json":
        console.print_json(json.dumps(payload, sort_keys=True))
    else:
        console.print(f"release: {payload['release_id']}; status: {payload['status']}")


@app.command("deploy-rollback")
def deploy_rollback(
    release: str = typer.Option(..., "--release"),
    reason: str = typer.Option(..., "--reason"),
    output: str = typer.Option("text", "--output"),
) -> None:
    """Record an idempotent, operator-attributed staging rollback."""

    if output not in {"text", "json"} or not reason.strip():
        _input_error("--output must be text or json")
    try:
        result = _deployment_service().rollback(release, actor="lou-cli-operator", reason=reason)
    except DeploymentConflictError:
        _input_error("release was not found")
    payload = _deployment_summary(result)
    if output == "json":
        console.print_json(json.dumps(payload, sort_keys=True))
    else:
        console.print(f"release: {payload['release_id']}; status: {payload['status']}")


@app.command("deploy-report")
def deploy_report(release: str = typer.Option(..., "--release")) -> None:
    """Render the complete immutable evidence report for a staging release."""

    report = _deployment_service().report(release)
    if report is None:
        _input_error("release was not found")
    console.print_json(json.dumps(report, sort_keys=True))


def _build_service() -> Any:
    """Keep persistence and Docker composition outside the command body."""

    return build_fixture_service(get_settings())


def _deployment_service() -> DeploymentService:
    settings = get_settings()
    return DeploymentService(
        DeploymentJournal(settings.artifact_root),
        InMemoryDeploymentAdapter(),
        verifications=SqlAlchemyVerificationLookup(create_session_factory(settings)),
    )


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
        "prediction": result.prediction.model_dump(mode="json") if result.prediction else None,
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


def _remediation_summary(result: Any) -> dict[str, object]:
    state = result.state
    return {
        "agent_run_id": str(result.run.id),
        "analysis_run_id": str(result.run.input.analysis_run_id),
        "status": result.run.status,
        "stage": result.run.stage,
        "reused": result.reused,
        "attempt_count": state.attempt_count,
        "tokens_spent": state.tokens_spent,
        "estimated_cost_usd": state.estimated_cost_spent_usd,
        "termination_reason": state.termination_reason,
        "decision": (
            {
                "action": state.decision.action,
                "autonomy_level": state.decision.autonomy_level,
                "confidence": state.decision.confidence,
            }
            if state.decision is not None
            else None
        ),
    }


def _deployment_summary(result: object) -> dict[str, object]:
    from lou.deployment.service import DeploymentResult

    if not isinstance(result, DeploymentResult):
        raise ValueError("invalid deployment result")
    return {
        "release_id": result.release.release_id,
        "analysis_run_id": result.release.analysis_run_id,
        "commit_sha": result.release.commit_sha,
        "status": result.status,
        "reused": result.reused,
        "decision": result.decision.action if result.decision else None,
        "reasons": list(result.decision.reasons) if result.decision else [],
        "evidence_count": len(result.evidence),
    }


def _exit_code(result: AnalysisResult, ci: bool) -> int:
    if result.status == "failed":
        return 3
    if result.status in {"running", "cancelled"}:
        # No verdict was produced for this invocation, so success must never be reported.
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
