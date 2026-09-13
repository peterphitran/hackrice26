"""Thin, testable coordination of a repository analysis request."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol

from contracts import RepositoryChange, RepositoryContext, WorkloadSelection

AnalysisStatus = Literal["succeeded", "failed", "inconclusive", "blocked"]


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

    def deduplication_key(self) -> str:
        """Return a stable key for identical immutable analysis inputs."""

        stable_config = repr(sorted(self.configuration.items()))
        value = "\x1f".join(
            (
                self.repository_id,
                self.base_commit_sha,
                self.candidate_commit_sha,
                stable_config,
                self.toolchain_revision,
                self.policy_revision,
            )
        )
        return sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AnalysisResult:
    """Safe, public result returned to CLI and future API adapters."""

    analysis_run_id: str
    status: AnalysisStatus
    reused: bool
    stages: tuple[str, ...]
    message: str | None = None


class AnalysisStore(Protocol):
    """Persistence boundary required by the application service."""

    def create_or_get(
        self, request: AnalysisRequest, deduplication_key: str
    ) -> tuple[str, bool]: ...

    def record_context(
        self,
        run_id: str,
        change: RepositoryChange,
        context: RepositoryContext,
        workloads: tuple[WorkloadSelection, ...],
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


class AnalysisApplicationService:
    """Coordinate request validation, context production, selection, and persistence."""

    def __init__(
        self,
        store: AnalysisStore,
        intelligence: RepositoryIntelligencePort,
        workload_selector: WorkloadSelectionPort,
    ) -> None:
        self._store = store
        self._intelligence = intelligence
        self._workload_selector = workload_selector

    def run(self, request: AnalysisRequest) -> AnalysisResult:
        """Execute the deterministic pre-verification portion of an analysis request."""

        self._validate(request)
        key = self._force_key(request) if request.force_new_run else request.deduplication_key()
        run_id, reused = self._store.create_or_get(request, key)
        if reused:
            return AnalysisResult(run_id, "succeeded", True, ("initialize",))

        stages = ["initialize"]
        try:
            change, context = self._intelligence.inspect(request, run_id)
            stages.append("inspect")
            workloads = self._workload_selector.select(context)
            stages.append("select")
            if not workloads:
                self._store.finish(run_id, "inconclusive", "No runnable workload was selected.")
                return AnalysisResult(run_id, "inconclusive", False, tuple(stages))
            self._store.record_context(run_id, change, context, workloads)
            return AnalysisResult(run_id, "succeeded", False, tuple(stages))
        except Exception as error:
            self._store.finish(run_id, "failed", "Analysis service stage failed.")
            return AnalysisResult(run_id, "failed", False, tuple(stages), type(error).__name__)

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

    @staticmethod
    def _force_key(request: AnalysisRequest) -> str:
        return f"{request.deduplication_key()}-force-{id(request)}"
