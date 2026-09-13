"""The orchestrator persists boundaries and trusts only verifier verdicts."""

from __future__ import annotations

import subprocess
from hashlib import sha256
from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel

from contracts import (
    AgentResult,
    AnalysisJob,
    Finding,
    PatchArtifact,
    RepositoryContext,
    VerificationResult,
    WorkloadSelection,
)
from lou.agents import (
    DeterministicMockProvider,
    DeterministicMockVerifier,
    OrchestrationInputs,
    OrchestrationLimits,
    OrchestrationState,
    RemediationOrchestrator,
)
from lou.agents.provider import ProviderRequest, ProviderResponse
from lou.policies import AutonomyPolicy
from lou.scoring import DebtInputs, RemediationInputs

FIXTURES = Path(__file__).parents[3] / "contracts" / "fixtures" / "demo_checkout"
ModelT = TypeVar("ModelT", bound=BaseModel)


def _load(name: str, model: type[ModelT]) -> ModelT:
    return model.model_validate_json((FIXTURES / name).read_text())


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            message,
        ],
        check=True,
    )
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


@pytest.fixture
def scenario(tmp_path: Path) -> tuple[OrchestrationInputs, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    source = repo / "checkout" / "service.py"
    source.parent.mkdir()
    source.write_text("    line_items = []\n")
    base_sha = _commit(repo, "base")
    source.write_text("    line_items = [load_line_item(item_id) for item_id in cart_item_ids]\n")
    candidate_sha = _commit(repo, "candidate")
    source.write_text("    line_items = load_line_items(cart_item_ids)\n")
    fix_sha = _commit(repo, "fix")

    job = _load("analysis_job.json", AnalysisJob).model_copy(
        update={
            "repository_path": str(repo),
            "base_commit_sha": base_sha,
            "candidate_commit_sha": candidate_sha,
        }
    )
    context = _load("repository_context.json", RepositoryContext).model_copy(
        update={"commit_sha": candidate_sha}
    )
    candidate = _load("verification_candidate.json", VerificationResult).model_copy(
        update={"commit_sha": candidate_sha}
    )
    expected_patch = _load("patch_artifact.json", PatchArtifact).model_copy(
        update={"base_commit_sha": candidate_sha}
    )
    inputs = OrchestrationInputs(
        job=job,
        context=context,
        finding=_load("finding.json", Finding),
        candidate_verification=candidate,
        workloads=(
            _load("workload_pytest.json", WorkloadSelection),
            _load("workload_k6.json", WorkloadSelection),
        ),
        expected_patch=expected_patch,
        debt_inputs=DebtInputs(
            complexity=0.8,
            coverage_deficit=0.4,
            estimated_patch_size=0.2,
            churn=0.6,
            graph_centrality=0.7,
            runtime_impact=0.9,
            path_criticality=1.0,
            evidence_confidence=1.0,
        ),
        remediation_inputs=RemediationInputs(
            blast_radius=0.1,
            criticality=0.1,
            coverage=0.9,
            reversibility=1.0,
            verification_strength=1.0,
            patch_size=0.1,
            schema_migration_risk=0.0,
            data_migration_risk=0.0,
            context_completeness=1.0,
            evidence_confidence=1.0,
        ),
        policy=AutonomyPolicy(max_autonomy=3),
        allowed_repository_root=repo,
    )
    return inputs, fix_sha


class AbandonProvider:
    def __init__(self, operation: str) -> None:
        self.operation = operation
        self.mock = DeterministicMockProvider()

    def call(self, request: ProviderRequest) -> ProviderResponse:
        if request.operation != self.operation:
            return self.mock.call(request)
        return ProviderResponse(
            result=AgentResult(
                agent_run_id=f"{request.bundle.analysis_run_id}:abandoned",
                analysis_run_id=request.bundle.analysis_run_id,
                status="abandoned",
            ),
            timeout_seconds=request.timeout_seconds,
            retry_count=0,
            tokens_used=0,
            estimated_cost_usd=0.0,
            elapsed_ms=0.0,
        )


class RejectedPatchProvider:
    def __init__(self) -> None:
        self.mock = DeterministicMockProvider()

    def call(self, request: ProviderRequest) -> ProviderResponse:
        response = self.mock.call(request)
        if request.operation != "patch":
            return response
        assert response.patch_diff is not None and response.patch_artifact is not None
        diff = response.patch_diff.replace("checkout/service.py", ".github/workflows/ci.yml")
        artifact = response.patch_artifact.model_copy(
            update={"patch_sha256": sha256(diff.encode()).hexdigest()}
        )
        return response.model_copy(update={"patch_diff": diff, "patch_artifact": artifact})


class CostlyProvider:
    def __init__(self) -> None:
        self.mock = DeterministicMockProvider()

    def call(self, request: ProviderRequest) -> ProviderResponse:
        return self.mock.call(request).model_copy(update={"estimated_cost_usd": 0.5})


def test_full_run_uses_independent_verdict_and_real_decision(
    scenario: tuple[OrchestrationInputs, str],
) -> None:
    inputs, fix_sha = scenario
    verifier = DeterministicMockVerifier(fix_commit_sha=fix_sha)
    state = RemediationOrchestrator(inputs, verifier=verifier).run()

    assert state.termination_reason == "verified"
    assert state.attempt_count == 1
    assert state.current_validation is not None and state.current_validation.valid
    assert state.decision is not None and state.decision.action == "open_pr"
    assert [result.status for result in state.verification_results] == ["passed", "passed"]
    assert state.decision.metadata["verification_results"] == [
        result.model_dump(mode="json") for result in state.verification_results
    ]
    assert len(verifier.calls) == 2
    assert all(call.validation.valid for call in verifier.calls)


def test_max_attempts_stops_failed_verification(
    scenario: tuple[OrchestrationInputs, str],
) -> None:
    inputs, fix_sha = scenario
    verifier = DeterministicMockVerifier(fix_commit_sha=fix_sha, verdicts_by_attempt=("failed",))
    state = RemediationOrchestrator(inputs, verifier=verifier).run(
        limits=OrchestrationLimits(max_attempts=1)
    )

    assert state.termination_reason == "max_attempts"
    assert state.last_failure_reason == "verification_failed"
    assert state.attempt_count == 1
    assert all(result.status == "failed" for result in state.verification_results)
    assert state.decision is not None and state.decision.autonomy_level <= 2


def test_duplicate_patch_stops_before_second_verification(
    scenario: tuple[OrchestrationInputs, str],
) -> None:
    inputs, fix_sha = scenario
    verifier = DeterministicMockVerifier(fix_commit_sha=fix_sha, verdicts_by_attempt=("failed",))
    state = RemediationOrchestrator(inputs, verifier=verifier).run()

    assert state.termination_reason == "duplicate_patch"
    assert state.attempt_count == 2
    assert len(state.prior_patch_hashes) == 1
    assert len(verifier.calls) == 2
    assert state.attempt_outcomes[-1].outcome == "duplicate_patch"
    assert state.decision is not None
    assert state.decision.metadata["verification_results"] == [
        result.model_dump(mode="json") for result in state.verification_results
    ]


@pytest.mark.parametrize("operation", ["diagnose", "patch"])
def test_every_abandoned_attempt_is_reported(
    scenario: tuple[OrchestrationInputs, str], operation: str
) -> None:
    inputs, fix_sha = scenario
    state = RemediationOrchestrator(
        inputs,
        verifier=DeterministicMockVerifier(fix_commit_sha=fix_sha),
        provider=AbandonProvider(operation),
    ).run(limits=OrchestrationLimits(max_attempts=3))

    assert state.termination_reason == "max_attempts"
    assert state.attempt_count == 3
    assert state.last_failure_reason == f"{operation}_abandoned"
    assert [item.outcome for item in state.attempt_outcomes] == [f"{operation}_abandoned"] * 3
    assert state.verification_results == []
    assert state.decision is not None
    assert state.decision.metadata["verification_results"] == []


def test_rejected_patch_never_reaches_verifier(
    scenario: tuple[OrchestrationInputs, str],
) -> None:
    inputs, fix_sha = scenario
    verifier = DeterministicMockVerifier(fix_commit_sha=fix_sha)
    state = RemediationOrchestrator(
        inputs, verifier=verifier, provider=RejectedPatchProvider()
    ).run(limits=OrchestrationLimits(max_attempts=1))

    assert state.termination_reason == "max_attempts"
    assert state.last_failure_reason == "patch_rejected"
    assert state.current_validation is not None
    assert "workflow_file" in {reason.code for reason in state.current_validation.reasons}
    assert verifier.calls == []


@pytest.mark.parametrize(
    ("stage", "cursor"),
    [
        ("diagnose", 0),
        ("patch", 0),
        ("validate", 0),
        ("verify", 0),
        ("verify", 1),
        ("decide", 2),
    ],
)
def test_intermediate_state_round_trips_and_resumes(
    scenario: tuple[OrchestrationInputs, str], stage: str, cursor: int
) -> None:
    inputs, fix_sha = scenario
    uninterrupted = RemediationOrchestrator(
        inputs, verifier=DeterministicMockVerifier(fix_commit_sha=fix_sha)
    ).run()
    first = RemediationOrchestrator(
        inputs, verifier=DeterministicMockVerifier(fix_commit_sha=fix_sha)
    )
    state = first.start()
    for _ in range(20):
        if state.stage == stage and state.verification_cursor == cursor:
            break
        state = first.step(state)
    else:
        pytest.fail(f"Did not reach {stage} with cursor {cursor}")
    saved = OrchestrationState.model_validate_json(state.model_dump_json())
    resumed = RemediationOrchestrator(
        inputs, verifier=DeterministicMockVerifier(fix_commit_sha=fix_sha)
    ).run(saved)

    assert resumed.termination_reason == uninterrupted.termination_reason
    assert resumed.attempt_count == uninterrupted.attempt_count
    assert resumed.verification_results == uninterrupted.verification_results
    assert resumed.decision is not None and uninterrupted.decision is not None
    assert resumed.decision.action == uninterrupted.decision.action


def test_token_and_cost_budgets_have_distinct_terminal_reasons(
    scenario: tuple[OrchestrationInputs, str],
) -> None:
    inputs, fix_sha = scenario
    token_state = RemediationOrchestrator(
        inputs, verifier=DeterministicMockVerifier(fix_commit_sha=fix_sha)
    ).run(limits=OrchestrationLimits(max_tokens=0))
    cost_state = RemediationOrchestrator(
        inputs,
        verifier=DeterministicMockVerifier(fix_commit_sha=fix_sha),
        provider=CostlyProvider(),
    ).run(limits=OrchestrationLimits(max_cost_usd=0.1))

    assert token_state.termination_reason == "token_budget_exceeded"
    assert token_state.tokens_spent > 0
    assert cost_state.termination_reason == "cost_budget_exceeded"
    assert cost_state.estimated_cost_spent_usd > 0.1


def test_wall_clock_budget_counts_time_across_resume(
    scenario: tuple[OrchestrationInputs, str],
) -> None:
    inputs, fix_sha = scenario
    now = [1_000.0]
    orchestrator = RemediationOrchestrator(
        inputs,
        verifier=DeterministicMockVerifier(fix_commit_sha=fix_sha),
        clock=lambda: now[0],
    )
    state = orchestrator.step(orchestrator.start(OrchestrationLimits(max_wall_seconds=1.0)))
    saved = OrchestrationState.model_validate_json(state.model_dump_json())
    now[0] += 2.0
    final = orchestrator.run(saved)

    assert final.termination_reason == "time_budget_exceeded"
    assert final.elapsed_seconds >= 2.0
    assert final.attempt_count == 1
