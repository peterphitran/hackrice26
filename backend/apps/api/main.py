"""FastAPI adapter for the local Lou analysis and evidence-report services."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
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
from lou.application.analysis import MAX_CONFIGURATION_BYTES
from lou.core.errors import LouError
from lou.core.settings import Settings, get_settings
from lou.core.version import APP_VERSION
from lou.deployment import (
    DeploymentConflictError,
    DeploymentJournal,
    DeploymentService,
    InMemoryDeploymentAdapter,
    SqlAlchemyVerificationLookup,
)
from lou.persistence.database import create_session_factory
from lou.persistence.interfaces import AnalysisRunView, RemediationRunView
from lou.persistence.models import LouDecisionRecord
from lou.persistence.repositories import (
    SqlAlchemyAnalysisRunRepository,
    SqlAlchemyRemediationRunRepository,
)
from lou.reporting import EvidenceReport, EvidenceReportReader, ReportError
from lou.repository import resolve_repository_revisions

RunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "inconclusive"]


class HealthResponse(BaseModel):
    """Keep the existing database-free health contract stable."""

    status: str
    service: str
    version: str


class ApiModel(BaseModel):
    """Base for versioned API contracts."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"


class ApiError(ApiModel):
    code: str
    message: str


class AnalysisCreateRequest(ApiModel):
    repository_path: Path
    base_revision: str = Field(min_length=1, max_length=256)
    candidate_revision: str = Field(min_length=1, max_length=256)
    configuration: dict[str, object] = Field(default_factory=dict)
    force_new_run: bool = False
    force_token: str | None = Field(default=None, min_length=1, max_length=256)


class AnalysisAcceptedResponse(ApiModel):
    analysis_run_id: str
    status: RunStatus
    reused: bool
    stages: list[str]
    message: str | None = None
    decision_id: str | None = None


class AnalysisStatusResponse(ApiModel):
    analysis_run_id: str
    status: RunStatus
    message: str | None = None
    decision_id: str | None = None
    fix_commit_sha: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class RemediationCreateRequest(ApiModel):
    provider: Literal["mock", "gemini"] = "mock"
    force_token: str | None = Field(default=None, min_length=1, max_length=256)


class RemediationStatusResponse(ApiModel):
    agent_run_id: str
    analysis_run_id: str
    status: Literal["queued", "running", "succeeded", "failed", "abandoned", "cancelled"]
    stage: Literal["context", "diagnose", "patch", "validate", "verify", "decide", "stopped"]
    attempt_count: int
    tokens_spent: int
    estimated_cost_usd: float
    termination_reason: str | None = None
    reused: bool = False


class PublicationRequest(ApiModel):
    acknowledgement: str | None = Field(default=None, min_length=1, max_length=128)


class PublicationResponse(ApiModel):
    publication_plan_id: str
    agent_run_id: str
    status: Literal["dry_run", "published", "denied", "failed"]
    provider_reference: str | None = None
    decision_id: str | None = None
    message: str | None = None


class DeploymentCreateRequest(ApiModel):
    release_id: str = Field(min_length=1, max_length=128)
    repository_id: str = Field(min_length=1, max_length=255)
    commit_sha: str = Field(min_length=7, max_length=255)
    validation_passed: bool = True
    telemetry_available: bool = True
    error_rate: float = Field(default=0, ge=0, le=1)
    latency_ms: float = Field(default=100, gt=0, le=600_000)
    sample_count: int = Field(default=20, ge=0, le=100_000)
    complete: bool = True
    verification_run_ids: tuple[str, ...] = ()
    trace_ids: tuple[str, ...] = ()


class DeploymentRollbackRequest(ApiModel):
    reason: str = Field(min_length=1, max_length=512)


class DeploymentResponse(ApiModel):
    release_id: str
    analysis_run_id: str
    commit_sha: str
    status: Literal["released", "paused", "promoted", "rolled_back"]
    reused: bool
    decision: Literal["promote", "pause", "rollback"] | None = None
    reasons: list[str] = Field(default_factory=list)
    evidence_count: int = Field(ge=0)


class AnalysisService(Protocol):
    def run(self, request: AnalysisRequest) -> AnalysisResult: ...


class ReportReader(Protocol):
    def read(self, run_id: str) -> EvidenceReport: ...


AnalysisServiceFactory = Callable[[], AnalysisService]
RunStatusReader = Callable[[UUID], "RunStatusView | None"]
ReportReaderFactory = Callable[[], ReportReader]


@dataclass(frozen=True)
class RunStatusView:
    run: AnalysisRunView
    decision_id: str | None


def create_app(
    *,
    settings: Settings | None = None,
    service_factory: AnalysisServiceFactory | None = None,
    run_status_reader: RunStatusReader | None = None,
    report_reader_factory: ReportReaderFactory | None = None,
    deployment_service_factory: Callable[[], DeploymentService] | None = None,
) -> FastAPI:
    """Create an API whose dependencies can be replaced in tests.

    Construction creates no database connection. The live service is deliberately the
    same fixture composition used by ``lou analyze``; FastAPI only translates HTTP
    contracts to that application boundary.
    """

    active_settings = settings or get_settings()
    build_service = service_factory or (lambda: build_fixture_service(active_settings))
    read_status = run_status_reader or _database_status_reader(active_settings)
    read_report = report_reader_factory or _report_reader_factory(active_settings)
    build_deployment = deployment_service_factory or (lambda: _deployment_service(active_settings))
    app = FastAPI(title="Lou API", version=APP_VERSION)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, __: RequestValidationError) -> JSONResponse:
        return _error_response(status.HTTP_400_BAD_REQUEST, "invalid_request", "Invalid request.")

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="lou", version=APP_VERSION)

    @app.post(
        "/analysis",
        response_model=AnalysisAcceptedResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["analysis"],
    )
    def create_analysis(payload: AnalysisCreateRequest) -> AnalysisAcceptedResponse | JSONResponse:
        try:
            request = _analysis_request(payload)
        except (LouError, OSError, ValueError):
            return _error_response(
                status.HTTP_400_BAD_REQUEST,
                "invalid_analysis_request",
                "Repository path, revisions, or configuration are invalid.",
            )

        try:
            result = build_service().run(request)
        except (SQLAlchemyError, OSError):
            return _error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "analysis_unavailable",
                "Analysis is temporarily unavailable.",
            )
        except Exception:
            # Docker or an optional local tool may fail; never return its raw output.
            return _error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "analysis_unavailable",
                "Analysis is temporarily unavailable.",
            )
        return _accepted_response(result)

    @app.post(
        "/analysis/{analysis_run_id}/remediations",
        response_model=RemediationStatusResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["remediation"],
    )
    def create_remediation(
        analysis_run_id: UUID, payload: RemediationCreateRequest
    ) -> RemediationStatusResponse | JSONResponse:
        try:
            result = run_fixture_remediation(
                analysis_run_id,
                provider=payload.provider,
                settings=active_settings,
                force_token=payload.force_token,
            )
        except RemediationAssemblyError:
            return _error_response(
                status.HTTP_409_CONFLICT,
                "remediation_not_eligible",
                "The analysis run does not have eligible verified remediation evidence.",
            )
        except (OSError, SQLAlchemyError):
            return _error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "remediation_unavailable",
                "Remediation is temporarily unavailable.",
            )
        except Exception:
            return _error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "remediation_unavailable",
                "Remediation is temporarily unavailable.",
            )
        return _remediation_response(result.run, result.reused)

    @app.get("/analysis/{run_id}", response_model=AnalysisStatusResponse, tags=["analysis"])
    def get_analysis(run_id: UUID) -> AnalysisStatusResponse | JSONResponse:
        try:
            view = read_status(run_id)
        except SQLAlchemyError:
            return _error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "status_unavailable",
                "Analysis status is temporarily unavailable.",
            )
        if view is None:
            return _error_response(
                status.HTTP_404_NOT_FOUND,
                "analysis_not_found",
                "Analysis run was not found.",
            )
        return _status_response(view)

    @app.get(
        "/remediations/{agent_run_id}",
        response_model=RemediationStatusResponse,
        tags=["remediation"],
    )
    def get_remediation(agent_run_id: UUID) -> RemediationStatusResponse | JSONResponse:
        try:
            view = SqlAlchemyRemediationRunRepository(create_session_factory(active_settings)).get(
                agent_run_id
            )
        except SQLAlchemyError:
            return _error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "remediation_unavailable",
                "Remediation is temporarily unavailable.",
            )
        if view is None:
            return _error_response(
                status.HTTP_404_NOT_FOUND,
                "remediation_not_found",
                "Remediation run was not found.",
            )
        return _remediation_response(view, False)

    @app.post(
        "/remediations/{agent_run_id}/publication-dry-run",
        response_model=PublicationResponse,
        tags=["remediation"],
    )
    def publication_dry_run(agent_run_id: UUID) -> PublicationResponse | JSONResponse:
        try:
            result = _publication_service(active_settings).dry_run(agent_run_id)
        except PublicationError:
            return _error_response(
                status.HTTP_404_NOT_FOUND,
                "remediation_not_found",
                "Remediation run was not found.",
            )
        return _publication_response(result)

    @app.post(
        "/remediations/{agent_run_id}/publish",
        response_model=PublicationResponse,
        tags=["remediation"],
    )
    def publish_remediation(
        agent_run_id: UUID, payload: PublicationRequest
    ) -> PublicationResponse | JSONResponse:
        try:
            result = _publication_service(active_settings).publish(
                agent_run_id,
                acknowledgement=payload.acknowledgement or "",
                publisher=GitHubCliPublisher(),
            )
        except PublicationError:
            return _error_response(
                status.HTTP_409_CONFLICT,
                "publication_denied",
                "Publication requires explicit A3 authorization and acknowledgement.",
            )
        return _publication_response(result)

    @app.post(
        "/analysis/{analysis_run_id}/deployments",
        response_model=DeploymentResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["deployment"],
    )
    def create_deployment(
        analysis_run_id: UUID, payload: DeploymentCreateRequest
    ) -> DeploymentResponse | JSONResponse:
        now = datetime.now(UTC)
        service = build_deployment()
        try:
            release = Release(
                release_id=payload.release_id,
                repository_id=payload.repository_id,
                commit_sha=payload.commit_sha,
                analysis_run_id=str(analysis_run_id),
                target=DeploymentTarget(
                    environment="staging", adapter="local", namespace="lou", service="lou-demo"
                ),
                requested_by="lou-api",
            )
            service.release(release)
            result = service.observe(
                release_id=release.release_id,
                window=CanaryWindow(
                    release_id=release.release_id,
                    started_at=now - timedelta(minutes=5 if payload.complete else 0),
                    deadline_at=now if payload.complete else now + timedelta(minutes=5),
                    minimum_samples=5,
                ),
                observation=CanaryObservation(
                    release_id=release.release_id,
                    observed_at=now,
                    sample_count=payload.sample_count,
                    error_rate=payload.error_rate if payload.telemetry_available else None,
                    latency_ms=payload.latency_ms if payload.telemetry_available else None,
                    telemetry_available=payload.telemetry_available,
                    telemetry_age_seconds=0 if payload.telemetry_available else None,
                    validation_passed=payload.validation_passed,
                ),
                policy=SLOPolicy(
                    policy_revision="staging-local-v1",
                    max_error_rate=0.05,
                    max_latency_ms=300,
                    minimum_samples=5,
                    telemetry_max_age_seconds=60,
                ),
                verification_run_ids=payload.verification_run_ids,
                trace_ids=payload.trace_ids,
            )
        except (DeploymentConflictError, ValueError):
            return _error_response(
                status.HTTP_409_CONFLICT,
                "deployment_conflict",
                "Deployment inputs conflict with the saved release.",
            )
        return _deployment_response(result)

    @app.get("/deployments/{release_id}", response_model=DeploymentResponse, tags=["deployment"])
    def get_deployment(release_id: str) -> DeploymentResponse | JSONResponse:
        result = build_deployment().status(release_id)
        if result is None:
            return _error_response(
                status.HTTP_404_NOT_FOUND, "release_not_found", "Release was not found."
            )
        return _deployment_response(result)

    @app.post(
        "/deployments/{release_id}/rollback",
        response_model=DeploymentResponse,
        tags=["deployment"],
    )
    def rollback_deployment(
        release_id: str, payload: DeploymentRollbackRequest
    ) -> DeploymentResponse | JSONResponse:
        try:
            result = build_deployment().rollback(
                release_id, actor="lou-api-operator", reason=payload.reason
            )
        except DeploymentConflictError:
            return _error_response(
                status.HTTP_404_NOT_FOUND, "release_not_found", "Release was not found."
            )
        return _deployment_response(result)

    @app.get("/deployments/{release_id}/report", response_model=None, tags=["deployment"])
    def get_deployment_report(release_id: str) -> Response:
        report = build_deployment().report(release_id)
        if report is None:
            return _error_response(
                status.HTTP_404_NOT_FOUND, "release_not_found", "Release was not found."
            )
        return Response(
            json.dumps(report, sort_keys=True, separators=(",", ":")),
            media_type="application/json",
        )

    @app.get("/analysis/{run_id}/report", response_model=None, tags=["analysis"])
    def get_report(run_id: UUID, format: Literal["json", "markdown"] = "json") -> Response:
        try:
            report = read_report().read(str(run_id))
        except ReportError as error:
            if str(error) in {"analysis run was not found", "run ID is invalid"}:
                return _error_response(
                    status.HTTP_404_NOT_FOUND,
                    "analysis_not_found",
                    "Analysis run was not found.",
                )
            return _error_response(
                status.HTTP_409_CONFLICT,
                "report_artifact_unavailable",
                "Evidence report artifacts are unavailable.",
            )
        except SQLAlchemyError:
            return _error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "report_unavailable",
                "Evidence report is temporarily unavailable.",
            )
        if format == "markdown":
            return Response(report.render_markdown(), media_type="text/markdown")
        # Preserve the report reader's canonical bytes so CLI and HTTP JSON agree exactly.
        return Response(report.render_json(), media_type="application/json")

    return app


def _analysis_request(payload: AnalysisCreateRequest) -> AnalysisRequest:
    if payload.force_new_run and payload.force_token is None:
        raise ValueError("force_token is required when force_new_run is enabled")
    configuration_bytes = json.dumps(
        payload.configuration, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    if len(configuration_bytes) > MAX_CONFIGURATION_BYTES:
        raise ValueError("configuration exceeds the 32 KiB limit")
    revisions = resolve_repository_revisions(
        repository_path=payload.repository_path,
        base_revision=payload.base_revision,
        candidate_revision=payload.candidate_revision,
    )
    return AnalysisRequest(
        repository_id=_repository_id(revisions.repository_root),
        repository_path=revisions.repository_root,
        base_commit_sha=revisions.base_commit_sha,
        candidate_commit_sha=revisions.candidate_commit_sha,
        configuration=payload.configuration,
        toolchain_revision=_revision(payload.configuration, "toolchain_revision"),
        policy_revision=_revision(payload.configuration, "policy_revision"),
        force_new_run=payload.force_new_run,
        force_token=payload.force_token,
        trigger_type="api",
    )


def _database_status_reader(settings: Settings) -> RunStatusReader:
    session_factory = create_session_factory(settings)
    runs = SqlAlchemyAnalysisRunRepository(session_factory)

    def read(run_id: UUID) -> RunStatusView | None:
        run = runs.get(run_id)
        if run is None:
            return None
        with session_factory() as session:
            decision = session.scalar(
                select(LouDecisionRecord.decision_id).where(
                    LouDecisionRecord.analysis_run_id == run_id
                )
            )
        return RunStatusView(run, decision)

    return read


def _report_reader_factory(settings: Settings) -> ReportReaderFactory:
    return lambda: EvidenceReportReader(create_session_factory(settings), settings.artifact_root)


def _repository_id(repository: Path) -> str:
    digest = hashlib.sha256(str(repository).encode("utf-8")).hexdigest()[:20]
    return f"local-{digest}"


def _revision(configuration: dict[str, object], key: str) -> str:
    value = configuration.get(key, "1")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"configuration.{key} must be a non-empty string")
    return value


def _accepted_response(result: AnalysisResult) -> AnalysisAcceptedResponse:
    decision_id = result.decision.decision_id if result.decision is not None else None
    return AnalysisAcceptedResponse(
        analysis_run_id=result.analysis_run_id,
        status=result.status,
        reused=result.reused,
        stages=list(result.stages),
        message=result.message,
        decision_id=decision_id,
    )


def _status_response(view: RunStatusView) -> AnalysisStatusResponse:
    run = view.run
    return AnalysisStatusResponse(
        analysis_run_id=str(run.id),
        status=run.status,
        message=run.error_message,
        decision_id=view.decision_id,
        fix_commit_sha=run.fix_commit_sha,
        started_at=run.started_at.isoformat() if run.started_at is not None else None,
        completed_at=run.completed_at.isoformat() if run.completed_at is not None else None,
    )


def _remediation_response(view: RemediationRunView, reused: bool) -> RemediationStatusResponse:
    return RemediationStatusResponse(
        agent_run_id=str(view.id),
        analysis_run_id=str(view.input.analysis_run_id),
        status=view.status,
        stage=view.stage,
        attempt_count=view.attempt_count,
        tokens_spent=view.tokens_spent,
        estimated_cost_usd=view.estimated_cost_usd,
        termination_reason=view.termination_reason,
        reused=reused,
    )


def _publication_service(settings: Settings) -> PublicationService:
    factory = create_session_factory(settings)
    return PublicationService(factory, EvidenceReportReader(factory, settings.artifact_root))


def _publication_response(result: object) -> PublicationResponse:
    from contracts import PublicationResult

    value = PublicationResult.model_validate(result)
    return PublicationResponse(
        publication_plan_id=value.publication_plan_id,
        agent_run_id=value.agent_run_id,
        status=value.status,
        provider_reference=value.provider_reference,
        decision_id=value.decision_id,
        message=value.message,
    )


def _deployment_service(settings: Settings) -> DeploymentService:
    return DeploymentService(
        DeploymentJournal(settings.artifact_root),
        InMemoryDeploymentAdapter(),
        verifications=SqlAlchemyVerificationLookup(create_session_factory(settings)),
    )


def _deployment_response(result: object) -> DeploymentResponse:
    from lou.deployment.service import DeploymentResult

    if not isinstance(result, DeploymentResult):
        raise ValueError("invalid deployment result")
    return DeploymentResponse(
        release_id=result.release.release_id,
        analysis_run_id=result.release.analysis_run_id,
        commit_sha=result.release.commit_sha,
        status=result.status,
        reused=result.reused,
        decision=result.decision.action if result.decision else None,
        reasons=list(result.decision.reasons) if result.decision else [],
        evidence_count=len(result.evidence),
    )


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    error = ApiError(code=code, message=message)
    return JSONResponse(status_code=status_code, content=error.model_dump())


app = create_app()
