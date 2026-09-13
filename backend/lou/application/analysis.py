"""Thin, testable coordination of a repository analysis request."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol

from contracts import (
    Evidence,
    Finding,
    ImpactItem,
    ImpactPrediction,
    LouDecision,
    ObservedImpact,
    RepositoryChange,
    RepositoryContext,
    RuntimeObservation,
    VerificationResult,
    WorkloadSelection,
)
from lou.telemetry import CorrelationResult, NoopTelemetry, RuntimeCorrelationContext, TelemetryPort

AnalysisStatus = Literal["succeeded", "failed", "inconclusive", "running", "cancelled"]
TriggerType = Literal["cli", "api", "github_webhook", "fixture"]
RunSnapshotStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "inconclusive"]
MAX_CONFIGURATION_BYTES = 32_768


@dataclass(frozen=True)
class AnalysisRequest:
    """Validated, framework-neutral inputs to one analysis request."""

    repository_id: str
    repository_path: Path
    base_commit_sha: str
    candidate_commit_sha: str
    configuration: dict[str, object] = field(default_factory=dict)
    toolchain_revision: str = "1"
    policy_revision: str = "1"
    force_new_run: bool = False
    force_token: str | None = None
    trigger_type: TriggerType = "cli"

    def deduplication_key(self) -> str:
        """Return a stable key for identical immutable analysis inputs."""

        stable_config = self._serialized_configuration()
        value = "\x1f".join(
            (
                self.repository_id,
                self.base_commit_sha,
                self.candidate_commit_sha,
                stable_config,
                self.toolchain_revision,
                self.policy_revision,
                self.trigger_type,
            )
        )
        return sha256(value.encode("utf-8")).hexdigest()

    def _serialized_configuration(self) -> str:
        return json.dumps(
            self.configuration,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )


@dataclass(frozen=True)
class VerificationBundle:
    """One verification result and the immutable records that support it."""

    result: VerificationResult
    findings: tuple[Finding, ...] = ()
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class RunSnapshot:
    """State returned when a request creates or reuses an analysis run."""

    analysis_run_id: str
    status: RunSnapshotStatus
    created: bool
    message: str | None = None


@dataclass(frozen=True)
class AnalysisResult:
    """Safe, public result returned to CLI and future API adapters."""

    analysis_run_id: str
    status: AnalysisStatus
    reused: bool
    stages: tuple[str, ...]
    message: str | None = None
    decision: LouDecision | None = None
    verification_results: tuple[VerificationResult, ...] = ()
    prediction: ImpactPrediction | None = None


class AnalysisStore(Protocol):
    """Persistence boundary required by the application service."""

    def create_or_get(self, request: AnalysisRequest, deduplication_key: str) -> RunSnapshot: ...

    def record_context(
        self,
        run_id: str,
        change: RepositoryChange,
        context: RepositoryContext,
        workloads: tuple[WorkloadSelection, ...],
    ) -> None: ...

    def record_prediction(self, run_id: str, prediction: ImpactPrediction) -> None: ...

    def record_verification(self, run_id: str, bundle: VerificationBundle) -> None: ...

    def record_decision(self, run_id: str, decision: LouDecision) -> None: ...

    def record_telemetry(
        self, run_id: str, observations: tuple[RuntimeObservation, ...]
    ) -> None: ...

    def finish(self, run_id: str, status: AnalysisStatus, message: str | None = None) -> None: ...


class RepositoryIntelligencePort(Protocol):
    """Produce changed-code context for a persisted analysis run."""

    def inspect(
        self, request: AnalysisRequest, run_id: str
    ) -> tuple[RepositoryChange, RepositoryContext]: ...


class WorkloadSelectionPort(Protocol):
    """Select ordered workloads from repository context."""

    def select(self, context: RepositoryContext) -> tuple[WorkloadSelection, ...]: ...


class VerificationPort(Protocol):
    """Measure equivalent baseline and candidate workloads in that order."""

    def measure_baseline(
        self,
        request: AnalysisRequest,
        run_id: str,
        workloads: tuple[WorkloadSelection, ...],
    ) -> VerificationBundle: ...

    def measure_candidate(
        self,
        request: AnalysisRequest,
        run_id: str,
        workloads: tuple[WorkloadSelection, ...],
        baseline: VerificationBundle,
    ) -> VerificationBundle: ...


class DecisionPort(Protocol):
    """Make a policy-bounded decision from persisted verification evidence."""

    def decide(
        self,
        request: AnalysisRequest,
        run_id: str,
        baseline: VerificationBundle,
        candidate: VerificationBundle,
        context: RepositoryContext,
    ) -> LouDecision: ...


class AnalysisApplicationService:
    """Coordinate the seven deterministic stages of a repository analysis request."""

    def __init__(
        self,
        store: AnalysisStore,
        intelligence: RepositoryIntelligencePort,
        workload_selector: WorkloadSelectionPort,
        verification: VerificationPort,
        decision: DecisionPort,
        telemetry: TelemetryPort | None = None,
    ) -> None:
        self._store = store
        self._intelligence = intelligence
        self._workload_selector = workload_selector
        self._verification = verification
        self._decision = decision
        self._telemetry = telemetry or NoopTelemetry()
        self._telemetry_observation_count = 0

    def run(self, request: AnalysisRequest) -> AnalysisResult:
        """Run validate → initialize → inspect → select → verify → decide → finalize."""

        self._validate(request)
        key = self._force_key(request) if request.force_new_run else request.deduplication_key()
        snapshot = self._store.create_or_get(request, key)
        run_id = snapshot.analysis_run_id
        if not snapshot.created:
            reused_status: AnalysisStatus = (
                "running" if snapshot.status == "queued" else snapshot.status
            )
            return AnalysisResult(
                run_id,
                reused_status,
                True,
                ("validate", "initialize"),
                snapshot.message,
            )

        root_context = RuntimeCorrelationContext(
            analysis_run_id=run_id,
            repository_id=request.repository_id,
            commit_sha=request.candidate_commit_sha,
            phase="candidate",
        )
        self._telemetry.record(
            "lou.analysis", root_context, {"lou.entrypoint": request.trigger_type}
        )

        stages = ["validate", "initialize"]
        verification_results: list[VerificationResult] = []
        prediction: ImpactPrediction | None = None
        try:
            change, context = self._intelligence.inspect(request, run_id)
            stages.append("inspect")
            workloads = self._workload_selector.select(context)
            stages.append("select")
            prediction_value = context.metadata.get("impact_prediction")
            if isinstance(prediction_value, dict):
                prediction = ImpactPrediction.model_validate(prediction_value)
                record_prediction = getattr(self._store, "record_prediction", None)
                if record_prediction is not None:
                    record_prediction(run_id, prediction)
                stages.append("predict")
            self._store.record_context(run_id, change, context, workloads)
            if not workloads:
                return self._finish(
                    run_id,
                    "inconclusive",
                    stages,
                    "No runnable workload was selected.",
                    prediction=prediction,
                )

            baseline = self._verification.measure_baseline(request, run_id, workloads)
            self._store.record_verification(run_id, baseline)
            self._record_runtime_evidence(request, run_id, baseline, context)
            verification_results.append(baseline.result)
            if baseline.result.status != "passed":
                return self._finish(
                    run_id,
                    "inconclusive",
                    [*stages, "verify"],
                    "Baseline verification did not produce a comparable measurement.",
                    verification_results,
                    prediction=prediction,
                )

            candidate = self._verification.measure_candidate(request, run_id, workloads, baseline)
            if prediction is not None:
                observed = _observed_from_context(run_id, context, candidate)
                from lou.prediction.evaluate import evaluate_impact

                evaluation = evaluate_impact(prediction, observed)
                candidate = VerificationBundle(
                    candidate.result.model_copy(
                        update={
                            "metadata": {
                                **candidate.result.metadata,
                                "observed_impact": observed.model_dump(mode="json"),
                                "impact_evaluation": evaluation.model_dump(mode="json"),
                            }
                        }
                    ),
                    candidate.findings,
                    candidate.evidence,
                )
            self._store.record_verification(run_id, candidate)
            self._record_runtime_evidence(request, run_id, candidate, context)
            verification_results.append(candidate.result)
            stages.append("verify")
            if candidate.result.status == "inconclusive":
                return self._finish(
                    run_id,
                    "inconclusive",
                    stages,
                    "Candidate verification did not produce a comparable measurement.",
                    verification_results,
                    prediction=prediction,
                )

            final_decision = self._decision.decide(request, run_id, baseline, candidate, context)
            self._store.record_decision(run_id, final_decision)
            stages.append("decide")
            return self._finish(
                run_id,
                "succeeded",
                stages,
                None,
                verification_results,
                final_decision,
                prediction=prediction,
            )
        except Exception as error:
            return self._finish(
                run_id,
                "failed",
                stages,
                "Analysis service stage failed.",
                verification_results,
                error_name=type(error).__name__,
            )

    def _finish(
        self,
        run_id: str,
        status: AnalysisStatus,
        stages: list[str],
        message: str | None,
        verification_results: list[VerificationResult] | None = None,
        decision: LouDecision | None = None,
        error_name: str | None = None,
        prediction: ImpactPrediction | None = None,
    ) -> AnalysisResult:
        self._store.finish(run_id, status, message)
        return AnalysisResult(
            run_id,
            status,
            False,
            tuple([*stages, "finalize"]),
            error_name or message,
            decision,
            tuple(verification_results or ()),
            prediction,
        )

    def _record_runtime_evidence(
        self,
        request: AnalysisRequest,
        run_id: str,
        bundle: VerificationBundle,
        context: RepositoryContext,
    ) -> None:
        """Persist bounded telemetry as supporting evidence after each phase.

        This deliberately runs after deterministic measurement. Any telemetry
        failure is ignored: the store already has the authoritative verification
        bundle, and an observability outage must not alter its classification.
        """

        result = bundle.result
        runtime_context = RuntimeCorrelationContext(
            analysis_run_id=run_id,
            repository_id=request.repository_id,
            commit_sha=result.commit_sha,
            phase=result.phase,
            workload_id=result.workload_id,
            verification_run_id=result.verification_run_id,
        )
        workload_type = (
            "k6" if result.workload_id and result.workload_id.endswith("k6") else "pytest"
        )
        self._telemetry.record(
            "lou.workload.execute",
            runtime_context,
            {"lou.workload_type": workload_type, "lou.exit_status": result.status},
        )
        self._telemetry.record(
            "lou.sandbox.execute",
            runtime_context,
            {"lou.sandbox_kind": "docker", "lou.exit_status": result.status},
        )
        query_count = result.metrics.get("query_count")
        if not isinstance(query_count, (int, float)):
            query_count = result.metrics.get("candidate_query_count")
        if isinstance(query_count, (int, float)):
            correlation = self._correlation(context)
            self._telemetry.record(
                "lou.db.query",
                runtime_context,
                {
                    "db.operation": "SELECT",
                    "db.table": "broken_store.products",
                    "db.rows": query_count,
                },
                correlation=correlation,
            )
            # ``record`` owns trace safety; persist correlation only as an allowlisted summary.
            self._telemetry.record(
                "lou.correlation.resolve",
                runtime_context,
                {
                    "lou.mapping_method": correlation.method,
                    "lou.correlation_status": correlation.status,
                },
                correlation=correlation,
            )
        if result.phase == "candidate":
            self._telemetry.record(
                "lou.verification.compare",
                runtime_context,
                {"lou.classification": str(result.metadata.get("classification", "unknown"))},
            )
        flush = getattr(self._telemetry, "flush", None)
        if flush is not None and flush() in {"unavailable", "failed"}:
            self._telemetry.record(
                "lou.analysis",
                runtime_context,
                {"error.type": "collector_unavailable"},
                status="unavailable",
            )
        all_observations = self._telemetry.observations()
        observations = all_observations[self._telemetry_observation_count :]
        self._telemetry_observation_count = len(all_observations)
        if observations:
            record = getattr(self._store, "record_telemetry", None)
            if record is not None:
                try:
                    record(run_id, observations)
                except Exception:
                    pass

    @staticmethod
    def _correlation(context: RepositoryContext) -> CorrelationResult:
        symbol = context.changed_symbols[0] if context.changed_symbols else None
        if symbol is None:
            return CorrelationResult("unresolved", "unresolved", None, None, 0.0)
        return CorrelationResult("resolved", "exact", symbol, f"function:{symbol}", 1.0)

    @staticmethod
    def _validate(request: AnalysisRequest) -> None:
        if not request.repository_id.strip():
            raise ValueError("repository_id is required")
        if not request.repository_path.is_dir():
            raise ValueError("repository_path must be an existing directory")
        if not request.base_commit_sha.strip() or not request.candidate_commit_sha.strip():
            raise ValueError("base and candidate revisions are required")
        if request.base_commit_sha == request.candidate_commit_sha:
            raise ValueError("base and candidate revisions must differ")
        if request.force_new_run and not request.force_token:
            raise ValueError("force_token is required when force_new_run is enabled")
        try:
            configuration_bytes = request._serialized_configuration().encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValueError("configuration must be JSON-serializable") from error
        if len(configuration_bytes) > MAX_CONFIGURATION_BYTES:
            raise ValueError("configuration exceeds the 32 KiB limit")

    @staticmethod
    def _force_key(request: AnalysisRequest) -> str:
        assert request.force_token is not None
        return f"{request.deduplication_key()}-force-{request.force_token}"


def _observed_from_context(
    run_id: str, context: RepositoryContext, candidate: VerificationBundle
) -> ObservedImpact:
    items = [
        ImpactItem(kind="symbol", key=key, score=1, reason="observed in graph context")
        for key in context.affected_symbols
    ]
    items.extend(
        ImpactItem(kind="service", key=key, score=1, reason="observed in graph context")
        for key in context.affected_endpoints + context.affected_data_dependencies
    )
    items.extend(
        ImpactItem(kind="workload", key=key, score=1, reason="executed")
        for key in context.selected_workload_ids
    )
    if candidate.result.metadata.get("classification") == "runtime_regression":
        items.append(
            ImpactItem(
                kind="runtime_path",
                key="candidate:runtime_regression",
                score=1,
                reason="observed by differential verification",
            )
        )
    return ObservedImpact(
        analysis_run_id=run_id,
        items=tuple(sorted(items, key=lambda item: (item.kind, item.key))),
        source_revisions=("m3-verification",),
    )
