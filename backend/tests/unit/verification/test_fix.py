"""The fix verifier applies real diffs only in detached worktrees."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Literal

import pytest

from contracts import AnalysisJob, PatchArtifact, VerificationResult, WorkloadSelection
from lou.agents.orchestration import ValidatedPatch
from lou.agents.patch_validation import validate_patch
from lou.decision.autonomy import decide_autonomy
from lou.execution import CommandOutput, CommandResult
from lou.loadtest import K6Experiment, K6Sample, aggregate
from lou.policies import AutonomyPolicy
from lou.scoring import DebtInputs, RemediationInputs
from lou.verification.checks import PhaseCheck
from lou.verification.fix import FixVerifier, PhaseObservations, _protected_path

FIXTURE = Path(__file__).parents[3] / "fixtures" / "broken-store"
COMMAND = (sys.executable, "-m", "pytest", "-q", "tests/test_checkout.py")


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).rstrip("\n")


def _observations(
    phase: Literal["baseline", "candidate", "fix"], sha: str, queries: float, p95: float
) -> PhaseObservations:
    output = CommandOutput("", 0, False, None, None)
    command = CommandResult(COMMAND, 0, 0, output, output, False, False, False, {})
    check = PhaseCheck(phase, sha, "checkout-pytest", COMMAND, "passed", command)
    sample = K6Sample(10, p95, 30, 4, 0, queries)
    samples = (sample,) * 5
    load = K6Experiment(
        phase,
        sha,
        "checkout-k6",
        samples,
        (),
        aggregate(samples),
        False,
    )
    return PhaseObservations((check,), load)


def _artifact(diff: str, candidate: str, run_id: str) -> PatchArtifact:
    lines = diff.splitlines()
    additions = sum(line.startswith("+") and not line.startswith("+++") for line in lines)
    deletions = sum(line.startswith("-") and not line.startswith("---") for line in lines)
    return PatchArtifact(
        patch_id="real-reverse-n-plus-one",
        analysis_run_id=run_id,
        base_commit_sha=candidate,
        patch_sha256=sha256(diff.encode()).hexdigest(),
        artifact_uri="memory://real-reverse-n-plus-one",
        files_changed=1,
        lines_added=additions,
        lines_deleted=deletions,
    )


def _request(repo: Path, diff: str, candidate: str, base: str) -> ValidatedPatch:
    job = AnalysisJob(
        analysis_run_id="ev007-real-patch",
        repository_id="broken-store",
        repository_path=str(repo),
        base_commit_sha=base,
        candidate_commit_sha=candidate,
        verification_plan={"workloads": ["checkout-pytest", "checkout-k6"]},
        resource_limits={"timeout_seconds": 120, "k6_repetitions": 5},
    )
    artifact = _artifact(diff, candidate, job.analysis_run_id)
    validation = validate_patch(
        diff, artifact, expected_base_commit_sha=candidate, allowed_repository_root=repo
    )
    assert validation.valid, validation.reasons
    return ValidatedPatch(
        patch_diff=diff,
        patch_artifact=artifact,
        validation=validation,
        analysis_job=job,
        allowed_repository_root=repo,
        workload_id="checkout-pytest",
        verification_attempt_id="ev007-real-patch:attempt:1",
        attempt_count=1,
    )


class RecordingRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run(
        self,
        *,
        repository: Path,
        commit_sha: str,
        selections: object,
        commands: object,
        job: AnalysisJob,
        artifact_dir: Path,
    ) -> PhaseObservations:
        self.calls += 1
        assert repository != Path(job.repository_path)
        source = (repository / "store" / "app.py").read_text()
        assert "id = ANY(%s)" in source
        assert "for product_id, quantity in cart:" not in source
        completed = subprocess.run(
            list(COMMAND),
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return _observations("fix", commit_sha, 2, 21)


class FailingRunner:
    def __init__(self, error: BaseException = RuntimeError("fixture runner failed")) -> None:
        self.calls = 0
        self.error = error

    def run(
        self,
        *,
        repository: Path,
        commit_sha: str,
        selections: object,
        commands: object,
        job: AnalysisJob,
        artifact_dir: Path,
    ) -> PhaseObservations:
        self.calls += 1
        raise self.error


class SingleTypeRunner:
    def __init__(self, workload_type: Literal["pytest", "k6"]) -> None:
        self.workload_type = workload_type
        self.calls = 0

    def run(
        self,
        *,
        repository: Path,
        commit_sha: str,
        selections: Sequence[WorkloadSelection],
        commands: Mapping[str, Sequence[str]],
        job: AnalysisJob,
        artifact_dir: Path,
    ) -> PhaseObservations:
        del repository, selections, commands, job, artifact_dir
        self.calls += 1
        complete = _observations("fix", commit_sha, 2, 20)
        if self.workload_type == "pytest":
            return PhaseObservations(complete.checks, None)
        return PhaseObservations((), complete.load)


def _setup(tmp_path: Path) -> tuple[Path, str, str, str]:
    repo = tmp_path / "broken-store"
    subprocess.run(
        [sys.executable, str(FIXTURE / "scripts" / "seed_fixture_repo.py"), str(repo)],
        check=True,
        capture_output=True,
    )
    base = _git(repo, "rev-parse", "good")
    candidate = _git(repo, "rev-parse", "n-plus-one")
    reverse = _git(repo, "diff", "n-plus-one", "good", "--", "store/app.py") + "\n"
    return repo, base, candidate, reverse


def _verifier(tmp_path: Path, base: str, candidate: str, runner: RecordingRunner) -> FixVerifier:
    selections = (
        WorkloadSelection(
            workload_id="checkout-pytest",
            workload_type="pytest",
            definition_path="tests/test_checkout.py",
            phase="candidate",
            reason="checkout test",
            confidence=1,
        ),
        WorkloadSelection(
            workload_id="checkout-k6",
            workload_type="k6",
            definition_path="loadtests/checkout.js",
            phase="candidate",
            reason="checkout load",
            confidence=1,
        ),
    )
    return FixVerifier(
        baseline=_observations("baseline", base, 2, 20),
        candidate=_observations("candidate", candidate, 51, 50),
        selections=selections,
        commands={"checkout-pytest": COMMAND},
        runner=runner,
        artifact_root=tmp_path / "artifacts",
    )


def test_real_reverse_patch_applies_and_verifies_in_fresh_worktree(tmp_path: Path) -> None:
    repo, base, candidate, reverse = _setup(tmp_path)
    request = _request(repo, reverse, candidate, base)
    runner = RecordingRunner()
    verifier = _verifier(tmp_path, base, candidate, runner)
    pytest_result = verifier.verify(request)
    k6_result = verifier.verify(request.model_copy(update={"workload_id": "checkout-k6"}))

    assert runner.calls == 1
    assert pytest_result.status == k6_result.status == "passed"
    assert pytest_result.metadata["classification"] == "improvement"
    assert pytest_result.phase == "fix"
    assert pytest_result.commit_sha != candidate
    assert pytest_result.metadata["patch_sha256"] == sha256(reverse.encode()).hexdigest()
    assert pytest_result.metadata["verification_attempt_id"] == request.verification_attempt_id
    assert _git(repo, "rev-parse", "HEAD") == candidate
    assert "for product_id, quantity in cart:" in (repo / "store/app.py").read_text()


@pytest.mark.parametrize("workload_type", ["pytest", "k6"])
def test_fix_verifier_supports_single_type_finalized_plan(
    tmp_path: Path, workload_type: Literal["pytest", "k6"]
) -> None:
    repo, base, candidate, reverse = _setup(tmp_path)
    request = _request(repo, reverse, candidate, base)
    workload_id = f"checkout-{workload_type}"
    selection = WorkloadSelection(
        workload_id=workload_id,
        workload_type=workload_type,
        definition_path=(
            "tests/test_checkout.py" if workload_type == "pytest" else "loadtests/checkout.js"
        ),
        phase="candidate",
        reason="finalized adaptive plan",
        confidence=1,
    )
    baseline_complete = _observations("baseline", base, 2, 20)
    candidate_complete = _observations("candidate", candidate, 51, 50)
    baseline = PhaseObservations(
        baseline_complete.checks if workload_type == "pytest" else (),
        baseline_complete.load if workload_type == "k6" else None,
    )
    candidate_observations = PhaseObservations(
        candidate_complete.checks if workload_type == "pytest" else (),
        candidate_complete.load if workload_type == "k6" else None,
    )
    job = request.analysis_job.model_copy(
        update={"verification_plan": {"workloads": [workload_id]}}
    )
    request = request.model_copy(update={"analysis_job": job, "workload_id": workload_id})
    runner = SingleTypeRunner(workload_type)
    verifier = FixVerifier(
        baseline=baseline,
        candidate=candidate_observations,
        selections=(selection,),
        commands={"checkout-pytest": COMMAND} if workload_type == "pytest" else {},
        runner=runner,
        artifact_root=tmp_path / "single-type-artifacts",
    )

    result = verifier.verify(request)

    assert runner.calls == 1
    assert result.metadata["workloads_rerun"] is True
    assert result.metadata["classification"] in {"improvement", "no_material_change"}


def test_fix_verifier_rejects_reordered_finalized_plan(tmp_path: Path) -> None:
    repo, base, candidate, reverse = _setup(tmp_path)
    request = _request(repo, reverse, candidate, base)
    job = request.analysis_job.model_copy(
        update={"verification_plan": {"workloads": ["checkout-k6", "checkout-pytest"]}}
    )
    request = request.model_copy(update={"analysis_job": job})
    runner = RecordingRunner()

    result = _verifier(tmp_path, base, candidate, runner).verify(request)

    assert result.status == "inconclusive"
    assert result.metadata["classification"] == "verification_plan_mismatch"
    assert runner.calls == 0


def test_nonapplicable_patch_is_failed_before_workloads(tmp_path: Path) -> None:
    repo, base, candidate, reverse = _setup(tmp_path)
    wrong = reverse.replace("SELECT price_cents", "SELECT wrong_price_cents", 1)
    request = _request(repo, wrong, candidate, base)
    runner = RecordingRunner()
    result = _verifier(tmp_path, base, candidate, runner).verify(request)
    assert result.status == "failed"
    assert result.metadata["classification"] == "patch_does_not_apply"
    assert runner.calls == 0


@pytest.mark.parametrize(
    ("field", "replacement", "classification"),
    [
        ("workload_id", "unplanned-workload", "workload_id_mismatch"),
        ("verification_attempt_id", "wrong-attempt", "verification_attempt_id_mismatch"),
    ],
)
def test_identity_mismatches_are_inconclusive_before_workloads(
    tmp_path: Path, field: str, replacement: str, classification: str
) -> None:
    repo, base, candidate, reverse = _setup(tmp_path)
    request = _request(repo, reverse, candidate, base)
    runner = RecordingRunner()

    result = _verifier(tmp_path, base, candidate, runner).verify(
        request.model_copy(update={field: replacement})
    )

    assert result.status == "inconclusive"
    assert result.metadata["classification"] == classification
    assert result.metadata["workloads_rerun"] is False
    assert runner.calls == 0


@pytest.mark.parametrize(
    ("field", "replacement", "classification"),
    [
        ("analysis_run_id", "wrong-run", "analysis_run_id_mismatch"),
        ("base_commit_sha", "b" * 40, "base_commit_mismatch"),
        ("patch_sha256", "0" * 64, "patch_hash_mismatch"),
    ],
)
def test_patch_identity_mismatches_are_inconclusive_before_workloads(
    tmp_path: Path, field: str, replacement: str, classification: str
) -> None:
    repo, base, candidate, reverse = _setup(tmp_path)
    request = _request(repo, reverse, candidate, base)
    runner = RecordingRunner()
    artifact = request.patch_artifact.model_copy(update={field: replacement})

    result = _verifier(tmp_path, base, candidate, runner).verify(
        request.model_copy(update={"patch_artifact": artifact})
    )

    assert result.status == "inconclusive"
    assert result.metadata["classification"] == classification
    assert result.metadata["workloads_rerun"] is False
    assert runner.calls == 0


def test_test_file_change_is_rejected_before_worktree_or_runner(tmp_path: Path) -> None:
    repo, base, candidate, _ = _setup(tmp_path)
    path = "tests/test_checkout.py"
    original = (repo / path).read_text()
    altered = original.replace("range(1, 51)", "range(1, 50)", 1)
    diff = (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        "@@ -12,1 +12,1 @@\n"
        "-            self.rows = [(product_id, 1) for product_id in range(1, 51)]\n"
        "+            self.rows = [(product_id, 1) for product_id in range(1, 50)]\n"
    )
    assert altered != original
    request = _request(repo, diff, candidate, base)
    runner = RecordingRunner()
    result = _verifier(tmp_path, base, candidate, runner).verify(request)
    assert result.status == "failed"
    assert result.metadata["classification"] == "verification_file_modified"
    assert runner.calls == 0


def test_fix_identity_fields_match_decision_contract(tmp_path: Path) -> None:
    repo, base, candidate, reverse = _setup(tmp_path)
    request = _request(repo, reverse, candidate, base)
    verifier = _verifier(tmp_path, base, candidate, RecordingRunner())
    result = verifier.verify(request)
    k6_result = verifier.verify(request.model_copy(update={"workload_id": "checkout-k6"}))
    assert VerificationResult.model_validate(result.model_dump()) == result
    assert result.analysis_run_id == request.analysis_job.analysis_run_id
    assert result.metadata["patch_sha256"] == request.patch_artifact.patch_sha256
    assert result.metadata["verification_attempt_id"] == request.verification_attempt_id
    assert _git(repo, "merge-base", "--is-ancestor", candidate, result.commit_sha) == ""
    decision = decide_autonomy(
        decision_id="ev007-identity-test",
        analysis_run_id=request.analysis_job.analysis_run_id,
        debt_inputs=DebtInputs(),
        remediation_inputs=RemediationInputs(),
        policy=AutonomyPolicy(max_autonomy=3),
        patch=request.patch_artifact,
        patch_content=reverse.encode(),
        analysis_job=request.analysis_job,
        expected_fix_commit_sha=result.commit_sha,
        expected_verification_attempt_id=request.verification_attempt_id,
        required_workload_ids=["checkout-pytest", "checkout-k6"],
        verification_results=[result, k6_result],
        candidate_regression=VerificationResult(
            verification_run_id="candidate-regression",
            analysis_run_id=request.analysis_job.analysis_run_id,
            phase="candidate",
            commit_sha=candidate,
            status="failed",
        ),
    )
    gates = {gate["code"]: gate["passed"] for gate in decision.rationale["gates"]}
    assert all(
        gates[code]
        for code in (
            "patch_identity",
            "verification_passed",
            "fix_identity",
            "verified_patch_binding",
            "required_workloads",
            "candidate_regression_evidence",
            "evidence_identity",
        )
    )


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_checkout.py",
        "test/test_checkout.py",
        "Tests/test_checkout.py",
        "store/checkout_test.py",
        "tests/conftest.py",
        "loadtests/checkout.js",
        "loadtest/checkout.js",
    ],
)
def test_protected_paths_cover_case_and_naming_variants(path: str) -> None:
    assert _protected_path(path)


@pytest.mark.parametrize("path", ["store/app.py", "store/testing_utils.py", "docs/tests.md"])
def test_unprotected_paths_are_not_blocked(path: str) -> None:
    assert not _protected_path(path)


def test_failed_result_does_not_claim_a_fix_commit(tmp_path: Path) -> None:
    repo, base, candidate, reverse = _setup(tmp_path)
    wrong = reverse.replace("SELECT price_cents", "SELECT wrong_price_cents", 1)
    request = _request(repo, wrong, candidate, base)
    runner = RecordingRunner()

    result = _verifier(tmp_path, base, candidate, runner).verify(request)

    assert result.status == "failed"
    assert result.metadata["fix_commit_sha"] is None
    assert result.metadata["workloads_rerun"] is False
    assert runner.calls == 0


def test_runner_failure_is_inconclusive_and_removes_the_detached_worktree(tmp_path: Path) -> None:
    repo, base, candidate, reverse = _setup(tmp_path)
    request = _request(repo, reverse, candidate, base)
    runner = FailingRunner()

    result = _verifier(tmp_path, base, candidate, runner).verify(request)  # type: ignore[arg-type]

    assert result.status == "inconclusive"
    assert result.metadata["classification"] == "execution_failed:RuntimeError"
    assert runner.calls == 1
    assert "lou-fix-" not in _git(repo, "worktree", "list", "--porcelain")


def test_timeout_is_inconclusive_and_removes_the_detached_worktree(tmp_path: Path) -> None:
    repo, base, candidate, reverse = _setup(tmp_path)
    request = _request(repo, reverse, candidate, base)
    runner = FailingRunner(TimeoutError("fixture runner timed out"))

    result = _verifier(tmp_path, base, candidate, runner).verify(request)  # type: ignore[arg-type]

    assert result.status == "inconclusive"
    assert result.metadata["classification"] == "execution_failed:TimeoutError"
    assert runner.calls == 1
    assert "lou-fix-" not in _git(repo, "worktree", "list", "--porcelain")


def test_cancellation_removes_the_detached_worktree(tmp_path: Path) -> None:
    repo, base, candidate, reverse = _setup(tmp_path)
    request = _request(repo, reverse, candidate, base)
    runner = FailingRunner(KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        _verifier(tmp_path, base, candidate, runner).verify(request)  # type: ignore[arg-type]

    assert runner.calls == 1
    assert "lou-fix-" not in _git(repo, "worktree", "list", "--porcelain")
