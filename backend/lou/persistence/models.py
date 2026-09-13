"""SQLAlchemy mappings for PF-002's PostgreSQL vertical slice."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base metadata for Lou persistence models."""

    pass


class RepositoryRecord(Base):
    """A local or hosted repository registered for analysis."""

    __tablename__ = "repositories"
    __table_args__ = (
        CheckConstraint(
            "local_path IS NOT NULL OR clone_url IS NOT NULL", name="repository_location"
        ),
        UniqueConstraint("provider", "provider_repository_id", name="repository_provider_identity"),
        {"schema": "lou"},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_repository_id: Mapped[str | None] = mapped_column(String(255))
    owner_name: Mapped[str | None] = mapped_column(String(255))
    repository_name: Mapped[str] = mapped_column(String(255), nullable=False)
    local_path: Mapped[str | None] = mapped_column(Text)
    clone_url: Mapped[str | None] = mapped_column(Text)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False, server_default="main")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalysisRunRecord(Base):
    """The immutable input identity and lifecycle of one analysis."""

    __tablename__ = "analysis_runs"
    __table_args__ = (
        CheckConstraint("base_commit_sha <> candidate_commit_sha", name="run_distinct_commits"),
        Index("analysis_runs_repository_status_idx", "repository_id", "status"),
        {"schema": "lou"},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    repository_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("lou.repositories.id", ondelete="CASCADE"), nullable=False
    )
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="queued")
    base_commit_sha: Mapped[str] = mapped_column(String(255), nullable=False)
    candidate_commit_sha: Mapped[str] = mapped_column(String(255), nullable=False)
    fix_commit_sha: Mapped[str | None] = mapped_column(String(255))
    deduplication_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    configuration: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    toolchain_revision: Mapped[str] = mapped_column(String(255), nullable=False)
    policy_revision: Mapped[str] = mapped_column(String(255), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkloadRecord(Base):
    """A checked-in verification workload."""

    __tablename__ = "workloads"
    __table_args__ = (
        UniqueConstraint("repository_id", "name", name="workload_repository_name"),
        {"schema": "lou"},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    repository_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("lou.repositories.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    workload_type: Mapped[str] = mapped_column(String(32), nullable=False)
    definition_path: Mapped[str] = mapped_column(Text, nullable=False)
    definition_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    selectors: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")
    enabled: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VerificationRunRecord(Base):
    """One baseline, candidate, or fix workload attempt."""

    __tablename__ = "verification_runs"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id", "workload_id", "phase", "attempt", name="verification_run_attempt"
        ),
        Index("verification_runs_run_phase_idx", "analysis_run_id", "phase"),
        {"schema": "lou"},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    contract_id: Mapped[str | None] = mapped_column(String(255))
    analysis_run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("lou.analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    workload_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("lou.workloads.id", ondelete="SET NULL")
    )
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(255), nullable=False)
    environment_image_digest: Mapped[str | None] = mapped_column(String(255))
    resource_limits: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    aggregate_metrics: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    artifact_uri: Mapped[str | None] = mapped_column(Text)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FindingRecord(Base):
    """A normalized finding emitted by an analysis run."""

    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id", "phase", "fingerprint", name="finding_run_phase_fingerprint"
        ),
        Index("findings_run_phase_idx", "analysis_run_id", "phase"),
        {"schema": "lou"},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    contract_id: Mapped[str | None] = mapped_column(String(255))
    analysis_run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("lou.analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    rule_id: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    file_path: Mapped[str | None] = mapped_column(Text)
    symbol_key: Mapped[str | None] = mapped_column(Text)
    start_line: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvidenceRecord(Base):
    """An immutable observation supporting a finding or decision."""

    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            "(artifact_uri IS NULL AND artifact_sha256 IS NULL) OR "
            "(artifact_uri IS NOT NULL AND artifact_sha256 IS NOT NULL)",
            name="evidence_artifact_pair",
        ),
        Index("evidence_run_phase_idx", "analysis_run_id", "phase"),
        {"schema": "lou"},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    contract_id: Mapped[str | None] = mapped_column(String(255))
    analysis_run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("lou.analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    finding_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("lou.findings.id", ondelete="SET NULL")
    )
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(10), nullable=False, server_default="1")
    summary: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")
    artifact_uri: Mapped[str | None] = mapped_column(Text)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PredictionRecord(Base):
    """Immutable prediction snapshot captured before verification."""

    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "predictor_revision", name="prediction_run_revision"),
        {"schema": "lou"},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    analysis_run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("lou.analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    predictor_name: Mapped[str] = mapped_column(String(100), nullable=False)
    predictor_revision: Mapped[str] = mapped_column(String(100), nullable=False)
    repository_id: Mapped[str] = mapped_column(String(255), nullable=False)
    base_commit_sha: Mapped[str] = mapped_column(String(255), nullable=False)
    candidate_commit_sha: Mapped[str] = mapped_column(String(255), nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LouDecisionRecord(Base):
    """One durable deterministic decision for an analysis run."""

    __tablename__ = "lou_decisions"
    __table_args__ = ({"schema": "lou"},)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    analysis_run_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("lou.analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    decision_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    debt_risk: Mapped[float] = mapped_column(nullable=False)
    remediation_risk: Mapped[float | None] = mapped_column()
    confidence: Mapped[float] = mapped_column(nullable=False)
    autonomy_level: Mapped[int] = mapped_column(Integer, nullable=False)
    rationale: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")
    details: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentRunRecord(Base):
    """Durable stage-boundary state for one bounded remediation workflow."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'abandoned', 'cancelled')",
            name="agent_run_status",
        ),
        CheckConstraint(
            "stage IN ('context', 'diagnose', 'patch', 'validate', 'verify', 'decide', 'stopped')",
            name="agent_run_stage",
        ),
        Index("agent_runs_analysis_status_idx", "analysis_run_id", "status"),
        {"schema": "lou"},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    analysis_run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("lou.analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_revision: Mapped[str] = mapped_column(String(255), nullable=False)
    limits: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="queued")
    stage: Mapped[str] = mapped_column(String(32), nullable=False, server_default="context")
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_spent: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    estimated_cost_usd: Mapped[float] = mapped_column(nullable=False, server_default="0")
    termination_reason: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RemediationAttemptRecord(Base):
    """Append-only output from one remediation stage."""

    __tablename__ = "remediation_attempts"
    __table_args__ = (
        UniqueConstraint("agent_run_id", "attempt_key", name="remediation_attempt_key"),
        Index("remediation_attempts_run_number_idx", "agent_run_id", "attempt_number"),
        {"schema": "lou"},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    agent_run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("lou.agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_key: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(100), nullable=False)
    patch_sha256: Mapped[str | None] = mapped_column(String(64))
    agent_result: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    validation: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    details: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PatchArtifactRecord(Base):
    """Validated patch identity retained independently from an agent snapshot."""

    __tablename__ = "patch_artifacts"
    __table_args__ = (
        UniqueConstraint("agent_run_id", "patch_sha256", name="patch_artifact_run_hash"),
        {"schema": "lou"},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    agent_run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("lou.agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    patch_id: Mapped[str] = mapped_column(String(255), nullable=False)
    patch_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_uri: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PublicationAttemptRecord(Base):
    """Idempotent trusted-publisher outcome, including credential-free dry runs."""

    __tablename__ = "publication_attempts"
    __table_args__ = (
        UniqueConstraint("publication_plan_id", name="publication_attempt_plan"),
        Index("publication_attempts_agent_run_idx", "agent_run_id", "created_at"),
        {"schema": "lou"},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    agent_run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("lou.agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    publication_plan_id: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeploymentJournalRecord(Base):
    """One hash-chained entry in a release's append-only deployment journal.

    The chain columns make an out-of-band edit detectable, and the sequence uniqueness
    constraint makes concurrent appends conflict instead of silently interleaving.
    """

    __tablename__ = "deployment_journal"
    __table_args__ = (
        CheckConstraint("kind IN ('release', 'evidence')", name="deployment_journal_kind"),
        CheckConstraint("sequence >= 0", name="deployment_journal_sequence_bounds"),
        UniqueConstraint("release_id", "sequence", name="deployment_journal_release_sequence"),
        Index("deployment_journal_release_idx", "release_id", "sequence"),
        {"schema": "lou"},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    release_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    body: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    record_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
