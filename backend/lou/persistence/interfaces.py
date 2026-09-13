"""Session-independent persistence contracts for Lou."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

RunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "inconclusive"]
TriggerType = Literal["cli", "api", "github_webhook", "fixture"]
Phase = Literal["baseline", "candidate", "fix"]
VerificationStatus = Literal["queued", "running", "passed", "failed", "inconclusive"]
Severity = Literal["info", "low", "medium", "high", "critical"]
RemediationProvider = Literal["mock", "gemini"]
RemediationStatus = Literal[
    "queued", "running", "succeeded", "failed", "abandoned", "cancelled"
]
RemediationStage = Literal[
    "context", "diagnose", "patch", "validate", "verify", "decide", "stopped"
]


class PersistenceError(Exception):
    """Base class for safe, domain-facing persistence errors."""


class RunConflictError(PersistenceError):
    """The deduplication key was reused with different immutable inputs."""


class InvalidRunTransitionError(PersistenceError):
    """A caller requested a transition that the lifecycle does not permit."""


@dataclass(frozen=True)
class AnalysisRunInput:
    """Immutable identity and configuration needed to create an analysis run."""

    repository_id: UUID
    base_commit_sha: str
    candidate_commit_sha: str
    trigger_type: TriggerType
    deduplication_key: str
    configuration: dict[str, object] = field(default_factory=dict)
    toolchain_revision: str = "1"
    policy_revision: str = "1"


@dataclass(frozen=True)
class AnalysisRunView:
    """Database-independent representation of an analysis run."""

    id: UUID
    input: AnalysisRunInput
    status: RunStatus
    fix_commit_sha: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class PersistedVerificationBundle:
    """Database identities for one atomic verification/finding/evidence write."""

    verification_id: UUID
    finding_ids: dict[str, UUID] = field(default_factory=dict)
    evidence_ids: dict[str, UUID] = field(default_factory=dict)


DecisionAction = Literal["report", "recommend", "generate_patch", "open_pr"]


@dataclass(frozen=True)
class DecisionInput:
    """Durable form of a shared Lou decision contract."""

    analysis_run_id: UUID
    decision_id: str
    action: DecisionAction
    debt_risk: float
    remediation_risk: float | None
    confidence: float
    autonomy_level: int
    rationale: dict[str, object] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionView:
    id: UUID
    value: DecisionInput


@dataclass(frozen=True)
class RemediationRunInput:
    """Immutable identity and limits for one agent remediation workflow."""

    analysis_run_id: UUID
    input_fingerprint: str
    deduplication_key: str
    provider: RemediationProvider
    policy_revision: str
    limits: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RemediationRunView:
    """Session-independent persisted state at an orchestration boundary."""

    id: UUID
    input: RemediationRunInput
    status: RemediationStatus
    stage: RemediationStage
    snapshot: dict[str, object] = field(default_factory=dict)
    attempt_count: int = 0
    tokens_spent: int = 0
    estimated_cost_usd: float = 0.0
    termination_reason: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class RemediationAttemptInput:
    """Append-only record of one agent stage outcome."""

    remediation_run_id: UUID
    attempt_key: str
    attempt_number: int
    stage: RemediationStage
    outcome: str
    patch_sha256: str | None = None
    agent_result: dict[str, object] = field(default_factory=dict)
    validation: dict[str, object] = field(default_factory=dict)
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RemediationAttemptView:
    id: UUID
    value: RemediationAttemptInput
    created_at: datetime | None = None


class AnalysisRunRepository(Protocol):
    """Operations available to domain services without exposing SQLAlchemy."""

    def create_or_get(self, value: AnalysisRunInput) -> tuple[AnalysisRunView, bool]: ...

    def get(self, run_id: UUID) -> AnalysisRunView | None: ...

    def transition(self, run_id: UUID, status: RunStatus) -> AnalysisRunView: ...

    def mark_failed(self, run_id: UUID, error_code: str, error_message: str) -> AnalysisRunView: ...


@dataclass(frozen=True)
class VerificationRunInput:
    analysis_run_id: UUID
    phase: Phase
    commit_sha: str
    status: VerificationStatus = "queued"
    workload_id: UUID | None = None
    attempt: int = 1
    metrics: dict[str, object] = field(default_factory=dict)
    artifact_uri: str | None = None
    artifact_sha256: str | None = None
    contract_id: str | None = None


@dataclass(frozen=True)
class FindingInput:
    analysis_run_id: UUID
    fingerprint: str
    source: str
    category: str
    severity: Severity
    confidence: float
    phase: Phase
    title: str
    message: str
    file_path: str | None = None
    symbol_key: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    contract_id: str | None = None


@dataclass(frozen=True)
class EvidenceInput:
    analysis_run_id: UUID
    phase: Literal["baseline", "candidate", "fix", "comparison"]
    kind: str
    source: str
    collected_at: datetime
    finding_id: UUID | None = None
    summary: dict[str, object] = field(default_factory=dict)
    artifact_uri: str | None = None
    artifact_sha256: str | None = None
    contract_id: str | None = None


class ResultRepository(Protocol):
    """Append-only verification, finding, and evidence persistence boundary."""

    def append_verification(self, value: VerificationRunInput) -> UUID: ...

    def complete_verification(self, verification_id: UUID, status: VerificationStatus) -> None: ...

    def complete_with_evidence(
        self,
        verification_id: UUID,
        status: VerificationStatus,
        evidence: list[EvidenceInput],
    ) -> list[UUID]: ...

    def add_finding(self, value: FindingInput) -> tuple[UUID, bool]: ...

    def append_evidence(self, value: EvidenceInput) -> UUID: ...

    def persist_verification_bundle(
        self,
        verification: VerificationRunInput,
        findings: list[FindingInput],
        evidence: list[EvidenceInput],
    ) -> PersistedVerificationBundle: ...


class DecisionRepository(Protocol):
    """Idempotent persistence boundary for an analysis decision."""

    def save(self, value: DecisionInput) -> tuple[DecisionView, bool]: ...

    def get_for_run(self, analysis_run_id: UUID) -> DecisionView | None: ...


class RemediationRunRepository(Protocol):
    """Durable orchestration state without ORM/session leakage."""

    def create_or_get(self, value: RemediationRunInput) -> tuple[RemediationRunView, bool]: ...

    def get(self, remediation_run_id: UUID) -> RemediationRunView | None: ...

    def save_snapshot(
        self,
        remediation_run_id: UUID,
        *,
        status: RemediationStatus,
        stage: RemediationStage,
        snapshot: dict[str, object],
        attempt_count: int,
        tokens_spent: int,
        estimated_cost_usd: float,
        termination_reason: str | None = None,
        error_message: str | None = None,
    ) -> RemediationRunView: ...

    def append_attempt(
        self, value: RemediationAttemptInput
    ) -> tuple[RemediationAttemptView, bool]: ...

    def persist_stage(
        self,
        remediation_run_id: UUID,
        *,
        status: RemediationStatus,
        stage: RemediationStage,
        snapshot: dict[str, object],
        attempt_count: int,
        tokens_spent: int,
        estimated_cost_usd: float,
        attempt: RemediationAttemptInput,
        termination_reason: str | None = None,
        error_message: str | None = None,
    ) -> tuple[RemediationRunView, RemediationAttemptView, bool]: ...

    def list_attempts(self, remediation_run_id: UUID) -> list[RemediationAttemptView]: ...
