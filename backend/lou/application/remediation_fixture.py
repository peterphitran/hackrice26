"""Supported local remediation composition for Lou's Python fixture profile."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from contracts import AnalysisJob, Finding, PatchArtifact, RepositoryContext, VerificationResult
from lou.agents import AgentProvider, GeminiProvider, OrchestrationInputs, OrchestrationLimits
from lou.application.live import fixture_commands
from lou.application.persistence import SqlAlchemyAnalysisStore
from lou.application.remediation import build_remediation_orchestrator
from lou.application.remediation_runs import RemediationExecutionResult, RemediationExecutionService
from lou.core.settings import Settings
from lou.persistence.database import create_session_factory
from lou.persistence.models import (
    AnalysisRunRecord,
    EvidenceRecord,
    FindingRecord,
    PatchArtifactRecord,
    RepositoryRecord,
    VerificationRunRecord,
    WorkloadRecord,
)
from lou.persistence.repositories import SqlAlchemyRemediationRunRepository
from lou.policies import AutonomyPolicy
from lou.scoring import DebtInputs, RemediationInputs
from lou.scoring.observed import (
    graph_debt_features,
    graph_remediation_features,
    measured_runtime_impact,
)
from lou.verification import DockerWorkloadRunner, disposable_worktree
from lou.verification.int003 import RecordedPatchProvider, _patches


class RemediationAssemblyError(ValueError):
    """A safe reason the supported local composition cannot start."""


@dataclass(frozen=True)
class FixtureRemediationInputs:
    analysis_run_id: UUID
    repository: Path
    base_commit_sha: str
    candidate_commit_sha: str
    orchestration: OrchestrationInputs


class FixtureRemediationAssembler:
    """Read-only assembler for a persisted fixture regression run.

    This deliberately supports only the Python checkout fixture profile.  It never
    trusts caller-provided repository paths, shell commands, or workload names.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def assemble(self, analysis_run_id: UUID) -> FixtureRemediationInputs:
        with self._session_factory() as session:
            run = session.get(AnalysisRunRecord, analysis_run_id)
            if run is None:
                raise RemediationAssemblyError("analysis run was not found")
            repository = session.get(RepositoryRecord, run.repository_id)
            context_evidence = session.scalar(
                select(EvidenceRecord).where(
                    EvidenceRecord.analysis_run_id == run.id,
                    EvidenceRecord.kind == "repository-context",
                )
            )
            finding = session.scalar(
                select(FindingRecord)
                .where(
                    FindingRecord.analysis_run_id == run.id,
                    FindingRecord.phase == "candidate",
                )
                .order_by(FindingRecord.confidence.desc().nullslast(), FindingRecord.created_at)
            )
            candidate = session.scalar(
                select(VerificationRunRecord)
                .where(
                    VerificationRunRecord.analysis_run_id == run.id,
                    VerificationRunRecord.phase == "candidate",
                )
                .order_by(
                    VerificationRunRecord.attempt.desc(),
                    VerificationRunRecord.created_at.desc(),
                )
            )
            workloads = list(
                session.scalars(
                    select(WorkloadRecord).where(WorkloadRecord.repository_id == run.repository_id)
                )
            )
        return _assemble(run, repository, context_evidence, finding, candidate, workloads)


def run_fixture_remediation(
    analysis_run_id: UUID,
    *,
    provider: Literal["mock", "gemini"],
    settings: Settings,
    limits: OrchestrationLimits | None = None,
    force_token: str | None = None,
) -> RemediationExecutionResult:
    """Execute the same verified local composition used for fixture rehearsals."""

    session_factory = create_session_factory(settings)
    assembled = FixtureRemediationAssembler(session_factory).assemble(analysis_run_id)
    runner = DockerWorkloadRunner(database_url=getattr(settings, "fixture_database_url"))
    commands = fixture_commands()
    with disposable_worktree(assembled.repository, assembled.base_commit_sha) as base_tree:
        baseline = runner.run_phase(
            "baseline",
            repository=base_tree,
            commit_sha=assembled.base_commit_sha,
            selections=assembled.orchestration.workloads,
            commands=commands,
            job=assembled.orchestration.job,
            artifact_dir=getattr(settings, "artifact_root") / str(analysis_run_id) / "m2-baseline",
        )
    with disposable_worktree(
        assembled.repository, assembled.candidate_commit_sha
    ) as candidate_tree:
        candidate = runner.run_phase(
            "candidate",
            repository=candidate_tree,
            commit_sha=assembled.candidate_commit_sha,
            selections=assembled.orchestration.workloads,
            commands=commands,
            job=assembled.orchestration.job,
            artifact_dir=getattr(settings, "artifact_root") / str(analysis_run_id) / "m2-candidate",
        )
    selected_provider = _provider(provider, assembled)
    orchestrator = build_remediation_orchestrator(
        assembled.orchestration,
        store=SqlAlchemyAnalysisStore(session_factory),
        baseline=baseline,
        candidate=candidate,
        commands=commands,
        runner=runner,
        artifact_root=getattr(settings, "artifact_root") / str(analysis_run_id) / "m2-fix",
        provider=selected_provider,
    )
    execution = RemediationExecutionService(SqlAlchemyRemediationRunRepository(session_factory))
    result = execution.execute(
        analysis_run_id=analysis_run_id,
        inputs_fingerprint=assembled.orchestration.fingerprint(),
        provider=provider,
        policy_revision=assembled.orchestration.policy.revision,
        driver=orchestrator,
        limits=limits,
        force_token=force_token,
    )
    _persist_patch_artifact(session_factory, result)
    return result


def _assemble(
    run: AnalysisRunRecord,
    repository: RepositoryRecord | None,
    context_evidence: EvidenceRecord | None,
    finding: FindingRecord | None,
    candidate: VerificationRunRecord | None,
    workloads: list[WorkloadRecord],
) -> FixtureRemediationInputs:
    if repository is None or repository.local_path is None:
        raise RemediationAssemblyError("analysis repository is unavailable")
    path = Path(repository.local_path)
    if not path.is_dir() or not _has_revision(path, run.candidate_commit_sha):
        raise RemediationAssemblyError(
            "analysis repository no longer contains the candidate revision"
        )
    if context_evidence is None or not isinstance(context_evidence.summary.get("context"), dict):
        raise RemediationAssemblyError("analysis run has no persisted repository context")
    if finding is None or candidate is None or candidate.status != "failed":
        raise RemediationAssemblyError("analysis run has no verified candidate regression")
    try:
        context = RepositoryContext.model_validate(context_evidence.summary["context"])
    except ValueError as error:
        raise RemediationAssemblyError("persisted repository context is invalid") from error
    selected = [record for record in workloads if record.name in context.selected_workload_ids]
    if {item.name for item in selected} != set(context.selected_workload_ids):
        raise RemediationAssemblyError("selected workload definitions are unavailable")
    try:
        from contracts import WorkloadSelection

        selections = tuple(
            WorkloadSelection(
                workload_id=item.name,
                workload_type=cast(
                    Literal["pytest", "k6", "semgrep", "custom"], item.workload_type
                ),
                definition_path=item.definition_path,
                phase="candidate",
                reason=str(item.selectors.get("reason", "Persisted selected workload.")),
                confidence=_confidence(item.selectors.get("confidence", 0)),
                metadata=cast(dict[str, object], item.selectors.get("metadata", {})),
            )
            for item in sorted(selected, key=lambda item: item.name)
        )
    except (TypeError, ValueError) as error:
        raise RemediationAssemblyError("persisted workload definitions are invalid") from error
    if {item.workload_type for item in selections} != {"pytest", "k6"}:
        raise RemediationAssemblyError(
            "supported remediation requires the saved pytest and k6 workloads"
        )
    job = AnalysisJob(
        analysis_run_id=str(run.id),
        repository_id=context.repository_id,
        repository_path=str(path.resolve()),
        base_commit_sha=run.base_commit_sha,
        candidate_commit_sha=run.candidate_commit_sha,
        policy_revision=run.policy_revision,
        toolchain_revision=run.toolchain_revision,
        verification_plan={"workloads": [item.workload_id for item in selections]},
    )
    public_finding = Finding(
        finding_id=finding.contract_id or str(finding.id),
        analysis_run_id=str(run.id),
        fingerprint=finding.fingerprint,
        source=finding.source,
        category=finding.category,
        severity=cast(Literal["info", "low", "medium", "high", "critical"], finding.severity),
        confidence=float(finding.confidence or 0),
        phase="candidate",
        title=finding.title,
        message=finding.message,
        file_path=finding.file_path or _changed_file_path(context),
        symbol_key=finding.symbol_key,
        metadata=dict(finding.details),
    )
    candidate_result = VerificationResult(
        verification_run_id=candidate.contract_id or str(candidate.id),
        analysis_run_id=str(run.id),
        phase="candidate",
        commit_sha=candidate.commit_sha,
        status=cast(Literal["passed", "failed", "inconclusive"], candidate.status),
        workload_id=next(
            (item.name for item in selected if item.id == candidate.workload_id), None
        ),
        metrics=cast(dict[str, float], candidate.aggregate_metrics),
    )
    expected = PatchArtifact(
        patch_id=f"expected-{run.id}",
        analysis_run_id=str(run.id),
        base_commit_sha=run.candidate_commit_sha,
        patch_sha256="0" * 64,
        artifact_uri="recorded://lou/m2-placeholder.diff",
        files_changed=0,
        lines_added=0,
        lines_deleted=0,
    )
    return FixtureRemediationInputs(
        analysis_run_id=run.id,
        repository=path.resolve(),
        base_commit_sha=run.base_commit_sha,
        candidate_commit_sha=run.candidate_commit_sha,
        orchestration=OrchestrationInputs(
            job=job,
            context=context,
            finding=public_finding,
            candidate_verification=candidate_result,
            workloads=selections,
            expected_patch=expected,
            debt_inputs=DebtInputs.model_validate(
                {
                    **graph_debt_features(context),
                    **measured_runtime_impact(public_finding),
                    "evidence_confidence": float(finding.confidence or 0),
                }
            ),
            remediation_inputs=RemediationInputs.model_validate(
                {
                    **graph_remediation_features(context),
                    "evidence_confidence": float(finding.confidence or 0),
                }
            ),
            policy=AutonomyPolicy(revision=run.policy_revision, max_autonomy=2),
            allowed_repository_root=path.resolve(),
            live_sources=("candidate_change", "selected_graph_context", "candidate_verification"),
        ),
    )


def _provider(
    provider: Literal["mock", "gemini"], assembled: FixtureRemediationInputs
) -> AgentProvider:
    if provider == "gemini":
        return GeminiProvider()
    patch_path = "store/app.py"
    if assembled.orchestration.finding.file_path != patch_path:
        raise RemediationAssemblyError("mock provider supports only the recorded checkout repair")
    good, _ = _patches(
        assembled.repository, assembled.base_commit_sha, assembled.candidate_commit_sha
    )
    return RecordedPatchProvider(good, case="good")


def _has_revision(repository: Path, revision: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--verify", f"{revision}^{{commit}}"],
        capture_output=True,
        check=False,
        timeout=10,
    )
    return result.returncode == 0


def _confidence(value: object) -> float:
    if not isinstance(value, (int, float)):
        raise RemediationAssemblyError("persisted workload confidence is invalid")
    return float(value)


def _changed_file_path(context: RepositoryContext) -> str | None:
    """Recover the root changed-file path when a normalized finding is fileless."""

    traversal = context.metadata.get("impact_traversal")
    nodes = traversal.get("nodes") if isinstance(traversal, dict) else None
    if not isinstance(nodes, list):
        return None
    for node in nodes:
        if not isinstance(node, dict) or node.get("distance") != 0:
            continue
        path = node.get("path")
        if isinstance(path, str):
            return path
    return None


def _persist_patch_artifact(
    session_factory: sessionmaker[Session], result: RemediationExecutionResult
) -> None:
    response = result.state.current_patch_response
    artifact = response.patch_artifact if response is not None else None
    if artifact is None:
        return
    with session_factory.begin() as session:
        existing = session.scalar(
            select(PatchArtifactRecord).where(
                PatchArtifactRecord.agent_run_id == result.run.id,
                PatchArtifactRecord.patch_sha256 == artifact.patch_sha256,
            )
        )
        if existing is None:
            session.add(
                PatchArtifactRecord(
                    agent_run_id=result.run.id,
                    patch_id=artifact.patch_id,
                    patch_sha256=artifact.patch_sha256,
                    artifact_uri=artifact.artifact_uri,
                    details=dict(artifact.metadata),
                )
            )
