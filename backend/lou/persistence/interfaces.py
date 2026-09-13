"""Session-independent persistence contracts for Lou."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

RunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "inconclusive"]
TriggerType = Literal["cli", "api", "github_webhook", "fixture"]


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


class AnalysisRunRepository(Protocol):
    """Operations available to domain services without exposing SQLAlchemy."""

    def create_or_get(self, value: AnalysisRunInput) -> tuple[AnalysisRunView, bool]: ...

    def get(self, run_id: UUID) -> AnalysisRunView | None: ...

    def transition(self, run_id: UUID, status: RunStatus) -> AnalysisRunView: ...

    def mark_failed(self, run_id: UUID, error_code: str, error_message: str) -> AnalysisRunView: ...
