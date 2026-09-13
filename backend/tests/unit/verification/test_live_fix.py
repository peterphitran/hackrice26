"""Opt-in Docker/k6 exercise with fresh measurements and no recorded results."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from contracts import WorkloadSelection
from lou.decision.autonomy import decide_autonomy
from lou.policies import AutonomyPolicy
from lou.scoring import DebtInputs, RemediationInputs
from lou.verification.compare import compare_candidate
from lou.verification.fix import FixVerifier
from lou.verification.runtime import DockerWorkloadRunner
from tests.unit.verification.test_fix import _request, _setup

pytestmark = pytest.mark.integration


def test_live_reverse_patch_restores_checkout(tmp_path: Path) -> None:
    if os.getenv("RUN_LOU_LIVE_EV007") != "1":
        pytest.skip("set RUN_LOU_LIVE_EV007=1 with Docker and lou-fixture Postgres ready")
    repo, base, candidate, reverse = _setup(tmp_path)
    request = _request(repo, reverse, candidate, base)
    selections = (
        WorkloadSelection(
            workload_id="checkout-pytest",
            workload_type="pytest",
            definition_path="tests/test_checkout.py",
            phase="candidate",
            reason="fixture checkout test",
            confidence=1,
        ),
        WorkloadSelection(
            workload_id="checkout-k6",
            workload_type="k6",
            definition_path="loadtests/checkout.js",
            phase="candidate",
            reason="fixture checkout load",
            confidence=1,
        ),
    )
    commands = {
        "checkout-pytest": (
            "python",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_checkout.py",
        )
    }
    runner = DockerWorkloadRunner(
        database_url=os.environ.get(
            "LOU_FIXTURE_DATABASE_URL", "postgresql://lou_migrator:lou_migrator@postgres:5432/lou"
        )
    )
    good_worktree = tmp_path / "good"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(good_worktree), base],
        check=True,
        capture_output=True,
    )
    try:
        baseline = runner.run_phase(
            "baseline",
            repository=good_worktree,
            commit_sha=base,
            selections=selections,
            commands=commands,
            job=request.analysis_job,
            artifact_dir=tmp_path / "baseline",
        )
        observed_candidate = runner.run_phase(
            "candidate",
            repository=repo,
            commit_sha=candidate,
            selections=selections,
            commands=commands,
            job=request.analysis_job,
            artifact_dir=tmp_path / "candidate",
        )
        regression = compare_candidate(
            request.analysis_job,
            baseline.checks,
            observed_candidate.checks,
            baseline.load,
            observed_candidate.load,
            artifact_dir=tmp_path / "comparison",
        )
        assert regression.verification.status in {"failed", "inconclusive"}
        verifier = FixVerifier(
            baseline=baseline,
            candidate=observed_candidate,
            selections=selections,
            commands=commands,
            runner=runner,
            artifact_root=tmp_path / "fix-artifacts",
        )
        fixed = verifier.verify(request)
        fixed_k6 = verifier.verify(request.model_copy(update={"workload_id": "checkout-k6"}))
        if regression.verification.status == "failed":
            assert fixed.status == fixed_k6.status == "passed"
            assert fixed.metadata["classification"] == "improvement"
            assert fixed.metrics["candidate_query_count"] == 2
        else:
            assert fixed.status == fixed_k6.status == "inconclusive"
        assert fixed.metadata["patch_sha256"] == request.patch_artifact.patch_sha256
        decision = decide_autonomy(
            decision_id="ev007-live-no-scoring-substitution",
            analysis_run_id=request.analysis_job.analysis_run_id,
            debt_inputs=DebtInputs(),
            remediation_inputs=RemediationInputs(),
            policy=AutonomyPolicy(max_autonomy=3),
            patch=request.patch_artifact,
            patch_content=request.patch_diff.encode(),
            analysis_job=request.analysis_job,
            expected_fix_commit_sha=fixed.commit_sha,
            expected_verification_attempt_id=request.verification_attempt_id,
            required_workload_ids=["checkout-pytest", "checkout-k6"],
            verification_results=[fixed, fixed_k6],
            candidate_regression=regression.verification,
        )
        gates = {gate["code"]: gate["passed"] for gate in decision.rationale["gates"]}
        assert all(
            gates[code]
            for code in (
                "patch_identity",
                "verification_present",
                "fix_identity",
                "verified_patch_binding",
                "required_workloads",
                "evidence_identity",
            )
        )
        assert gates["verification_passed"] == (fixed.status == "passed")
        assert gates["candidate_regression_evidence"] == (
            regression.verification.status == "failed"
        )
        assert decision.autonomy_level < 3
        assert not gates["complete_context"]
    finally:
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", "--force", str(good_worktree)],
            check=True,
            capture_output=True,
        )
