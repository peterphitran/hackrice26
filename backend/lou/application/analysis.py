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
    LouDecision,
    RepositoryChange,
    RepositoryContext,
    VerificationResult,
    WorkloadSelection,
)

AnalysisStatus = Literal["succeeded", "failed", "inconclusive", "running", "cancelled"]
TriggerType = Literal["cli", "api", "github_webhook", "fixture"]
RunSnapshotStatus = Literal[
    "queued", "running", "succeeded", "failed", "cancelled", "inconclusive"
]
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

    def record_verification(self, run_id: str, bundle: VerificationBundle) -> None: ...

    def record_decision(self, run_id: str, decision: LouDecision) -> None: ...

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
    ) -> None:
        self._store = store
        self._intelligence = intelligence
        self._workload_selector = workload_selector
        self._verification = verification
        self._decision = decision

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

        stages = ["validate", "initialize"]
        verification_results: list[VerificationResult] = []
        try:
            change, context = self._intelligence.inspect(request, run_id)
            stages.append("inspect")
            workloads = self._workload_selector.select(context)
            stages.append("select")
            self._store.record_context(run_id, change, context, workloads)
            if not workloads:
                return self._finish(
                    run_id,
                    "inconclusive",
                    stages,
                    "No runnable workload was selected.",
                )

            baseline = self._verification.measure_baseline(request, run_id, workloads)
            self._store.record_verification(run_id, baseline)
            verification_results.append(baseline.result)
            if baseline.result.status != "passed":
                return self._finish(
                    run_id,
                    "inconclusive",
                    [*stages, "verify"],
                    "Baseline verification did not produce a comparable measurement.",
                    verification_results,
                )

            candidate = self._verification.measure_candidate(request, run_id, workloads, baseline)
            self._store.record_verification(run_id, candidate)
            verification_results.append(candidate.result)
            stages.append("verify")
            if candidate.result.status == "inconclusive":
                return self._finish(
                    run_id,
                    "inconclusive",
                    stages,
                    "Candidate verification did not produce a comparable measurement.",
                    verification_results,
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
        )

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
