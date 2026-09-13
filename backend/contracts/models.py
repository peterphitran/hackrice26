"""Stable, version-one data contracts for the Lou hackathon prototype."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    """Base model that makes contract evolution explicit and safe."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"


class AnalysisJob(ContractModel):
    analysis_run_id: str
    repository_id: str
    repository_path: str
    base_commit_sha: str
    candidate_commit_sha: str
    policy_revision: str = "1"
    toolchain_revision: str = "1"
    verification_plan: dict[str, Any] = Field(default_factory=dict)
    resource_limits: dict[str, Any] = Field(default_factory=dict)


class RepositoryChange(ContractModel):
    repository_id: str
    base_commit_sha: str
    candidate_commit_sha: str
    added_files: list[str] = Field(default_factory=list)
    modified_files: list[str] = Field(default_factory=list)
    deleted_files: list[str] = Field(default_factory=list)
    renamed_files: dict[str, str] = Field(default_factory=dict)
    changed_symbols: list[str] = Field(default_factory=list)
    completeness: float = Field(ge=0, le=1, default=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepositoryContext(ContractModel):
    repository_id: str
    commit_sha: str
    changed_symbols: list[str] = Field(default_factory=list)
    affected_symbols: list[str] = Field(default_factory=list)
    affected_tests: list[str] = Field(default_factory=list)
    affected_endpoints: list[str] = Field(default_factory=list)
    affected_data_dependencies: list[str] = Field(default_factory=list)
    selected_workload_ids: list[str] = Field(default_factory=list)
    selection_reasons: dict[str, str] = Field(default_factory=dict)
    unresolved_relationships: list[str] = Field(default_factory=list)
    completeness: float = Field(ge=0, le=1, default=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ImpactItem(ContractModel):
    kind: Literal["symbol", "relationship", "service", "workload", "runtime_path"]
    key: str
    score: float = Field(ge=0, le=1)
    reason: str
    provenance: list[str] = Field(default_factory=list)


class PredictionFeatures(ContractModel):
    reachability: float | None = Field(default=None, ge=0, le=1)
    centrality: float | None = Field(default=None, ge=0, le=1)
    changed_file_type: float | None = Field(default=None, ge=0, le=1)
    history_churn: float | None = Field(default=None, ge=0, le=1)
    coverage_signal: float | None = Field(default=None, ge=0, le=1)
    ownership_signal: float | None = Field(default=None, ge=0, le=1)


class ImpactPrediction(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis_run_id: str
    repository_id: str
    base_commit_sha: str
    candidate_commit_sha: str
    items: tuple[ImpactItem, ...] = ()
    required_workload_ids: tuple[str, ...] = ()
    risk_signals: dict[str, float] = Field(default_factory=dict)
    features: PredictionFeatures = Field(default_factory=PredictionFeatures)
    confidence: float = Field(ge=0, le=1)
    omitted_context: tuple[str, ...] = ()
    predictor_name: str = "deterministic-heuristic"
    predictor_revision: str = "impact-v1"


class ObservedImpact(ContractModel):
    analysis_run_id: str
    items: tuple[ImpactItem, ...] = ()
    source_revisions: tuple[str, ...] = ()


class ImpactEvaluation(ContractModel):
    analysis_run_id: str
    repository_id: str
    predictor_revision: str
    labels: dict[str, Literal["true_positive", "false_positive", "false_negative"]] = Field(default_factory=dict)
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    false_negative_rate: float = Field(ge=0, le=1)
    calibration: dict[str, float] = Field(default_factory=dict)


class WorkloadSelection(ContractModel):
    workload_id: str
    workload_type: Literal["pytest", "k6", "semgrep", "custom"]
    definition_path: str
    phase: Literal["baseline", "candidate", "fix"]
    reason: str
    confidence: float = Field(ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Finding(ContractModel):
    finding_id: str
    analysis_run_id: str
    fingerprint: str
    source: str
    category: str
    severity: Literal["info", "low", "medium", "high", "critical"]
    confidence: float = Field(ge=0, le=1)
    phase: Literal["baseline", "candidate", "fix"]
    title: str
    message: str
    file_path: str | None = None
    symbol_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Evidence(ContractModel):
    evidence_id: str
    analysis_run_id: str
    phase: Literal["baseline", "candidate", "fix", "comparison"]
    kind: str
    source: str
    collected_at: datetime
    summary: dict[str, Any] = Field(default_factory=dict)
    artifact_uri: str | None = None
    artifact_sha256: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class VerificationResult(ContractModel):
    verification_run_id: str
    analysis_run_id: str
    phase: Literal["baseline", "candidate", "fix"]
    commit_sha: str
    status: Literal["passed", "failed", "inconclusive"]
    workload_id: str | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    findings: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    artifact_uri: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentResult(ContractModel):
    agent_run_id: str
    analysis_run_id: str
    status: Literal["succeeded", "failed", "abandoned"]
    diagnosis: str | None = None
    plan: str | None = None
    confidence: float = Field(ge=0, le=1, default=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RemediationRequest(ContractModel):
    """Immutable, user-requested input for one bounded remediation run."""

    analysis_run_id: str
    provider: Literal["mock", "gemini"] = "mock"
    policy_revision: str = "local-v1"
    limits: dict[str, Any] = Field(default_factory=dict)
    force_new_run: bool = False
    force_token: str | None = None


class AgentRun(ContractModel):
    """Durable public status for a remediation workflow."""

    agent_run_id: str
    analysis_run_id: str
    input_fingerprint: str
    provider: Literal["mock", "gemini"]
    status: Literal["queued", "running", "succeeded", "failed", "abandoned", "cancelled"]
    stage: Literal["context", "diagnose", "patch", "validate", "verify", "decide", "stopped"]
    attempt_count: int = Field(ge=0)
    tokens_spent: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    termination_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PublicationPlan(ContractModel):
    """Credential-free, immutable description of an allowed pull-request action."""

    publication_plan_id: str
    agent_run_id: str
    analysis_run_id: str
    decision_id: str
    repository: str
    base_branch: str
    branch_name: str
    title: str
    body: str
    patch_sha256: str
    evidence_report_sha256: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class PublicationResult(ContractModel):
    """Safe durable result of a dry run, denied request, or trusted publication."""

    publication_plan_id: str
    agent_run_id: str
    status: Literal["dry_run", "published", "denied", "failed"]
    provider_reference: str | None = None
    decision_id: str | None = None
    message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PatchArtifact(ContractModel):
    patch_id: str
    analysis_run_id: str
    base_commit_sha: str
    patch_sha256: str
    artifact_uri: str
    files_changed: int = Field(ge=0)
    lines_added: int = Field(ge=0)
    lines_deleted: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LouDecision(ContractModel):
    decision_id: str
    analysis_run_id: str
    debt_risk: float = Field(ge=0, le=1)
    remediation_risk: float | None = Field(default=None, ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    autonomy_level: int = Field(ge=0, le=3)
    action: Literal["report", "recommend", "generate_patch", "open_pr"]
    rationale: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
