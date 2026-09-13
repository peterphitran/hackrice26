"""Composition for independently verified, durable remediation attempts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from contracts import Evidence, VerificationResult
from lou.agents.orchestration import (
    OrchestrationInputs,
    RemediationOrchestrator,
    ValidatedPatch,
    Verifier,
)
from lou.agents.provider import AgentProvider
from lou.application.analysis import AnalysisStore, VerificationBundle
from lou.policies.engine import PolicyEvaluator
from lou.verification.fix import FixVerifier, PhaseObservations, WorkloadRunner


class PersistedFixVerifier:
    """Persist every independent fix verdict before returning it to the agent loop.

    The orchestration state machine never decides whether a patch worked.  This
    adapter keeps that separation while ensuring a returned verdict has a durable
    evidence record that a report can later display.
    """

    def __init__(self, *, verifier: Verifier, store: AnalysisStore) -> None:
        self._verifier = verifier
        self._store = store
        self._persisted_ids: set[str] = set()

    def verify(self, patch: ValidatedPatch) -> VerificationResult:
        result = self._verifier.verify(patch)
        if result.analysis_run_id != patch.analysis_job.analysis_run_id:
            raise ValueError("fix verifier returned a result for a different analysis run")
        if result.verification_run_id not in self._persisted_ids:
            evidence = Evidence(
                evidence_id=f"fix-verdict:{result.verification_run_id}",
                analysis_run_id=result.analysis_run_id,
                phase="fix",
                kind="fix-verification-verdict",
                source="lou.application.remediation",
                collected_at=datetime.now(UTC),
                summary={
                    "workload_id": result.workload_id,
                    "status": result.status,
                    "commit_sha": result.commit_sha,
                    "metrics": result.metrics,
                    "metadata": result.metadata,
                    "patch_sha256": patch.patch_artifact.patch_sha256,
                },
            )
            self._store.record_verification(
                result.analysis_run_id,
                VerificationBundle(result=result, evidence=(evidence,)),
            )
            self._persisted_ids.add(result.verification_run_id)
        return result


def build_remediation_orchestrator(
    inputs: OrchestrationInputs,
    *,
    store: AnalysisStore,
    baseline: PhaseObservations,
    candidate: PhaseObservations,
    commands: dict[str, tuple[str, ...]],
    runner: WorkloadRunner,
    artifact_root: Path,
    provider: AgentProvider | None = None,
    policy_evaluator: PolicyEvaluator | None = None,
) -> RemediationOrchestrator:
    """Build the production remediation path around the real ``FixVerifier``.

    CLI/API adapters provide trusted analysis inputs and a provider; this factory
    owns the crucial invariant that no provider verdict bypasses independent
    workload verification and persistence.
    """

    verifier = FixVerifier(
        baseline=baseline,
        candidate=candidate,
        selections=inputs.workloads,
        commands=commands,
        runner=runner,
        artifact_root=artifact_root,
    )
    return RemediationOrchestrator(
        inputs,
        verifier=PersistedFixVerifier(verifier=verifier, store=store),
        provider=provider,
        policy_evaluator=policy_evaluator,
    )
