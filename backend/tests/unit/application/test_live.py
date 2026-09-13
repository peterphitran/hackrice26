"""Tests for the first live fixture adapter set without invoking Docker."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from contracts import WorkloadSelection
from lou.application.analysis import AnalysisRequest
from lou.application.live import (
    FixtureDecisionAdapter,
    FixtureRepositoryIntelligence,
    FixtureVerificationAdapter,
    FixtureWorkloadSelector,
)
from lou.execution import CommandOutput, CommandResult
from lou.loadtest import K6Experiment, K6Sample
from lou.verification import PhaseCheck, PhaseObservations


@pytest.fixture
def fixture_repository(tmp_path: Path) -> tuple[Path, str, str]:
    repository = tmp_path / "broken-store"
    (repository / "store").mkdir(parents=True)
    (repository / "tests").mkdir()
    (repository / "loadtests").mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    for key, value in (("user.name", "Lou Tests"), ("user.email", "lou-tests@example.invalid")):
        subprocess.run(["git", "-C", str(repository), "config", key, value], check=True)
    (repository / "store" / "app.py").write_text(
        "class Store:\n    def checkout(self):\n        return 2\n", encoding="utf-8"
    )
    (repository / "tests" / "test_checkout.py").write_text(
        "def test_receipt(): pass\n", encoding="utf-8"
    )
    (repository / "loadtests" / "checkout.js").write_text(
        "export default function () {}\n", encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "good"], check=True)
    base = _git(repository, "rev-parse", "HEAD")
    (repository / "store" / "app.py").write_text(
        "class Store:\n    def checkout(self):\n        return 51\n", encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(repository), "commit", "-am", "candidate", "-q"], check=True)
    return repository, base, _git(repository, "rev-parse", "HEAD")


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments], check=True, capture_output=True, text=True
    ).stdout.strip()


def _request(
    repository: Path, base: str, candidate: str, configuration: dict[str, object] | None = None
) -> AnalysisRequest:
    return AnalysisRequest(
        repository_id="fixture-broken-store",
        repository_path=repository,
        base_commit_sha=base,
        candidate_commit_sha=candidate,
        configuration=configuration or {},
    )


def _observations(phase: str, sha: str, queries: float) -> PhaseObservations:
    output = CommandOutput("", 0, False, None, None)
    command = CommandResult(("pytest",), 0, 0, output, output, False, False, False, {})
    check = PhaseCheck(phase, sha, "checkout-pytest", ("pytest",), "passed", command)  # type: ignore[arg-type]
    sample = K6Sample(10, 20, 30, 4, 0, queries)
    load = K6Experiment(
        phase,  # type: ignore[arg-type]
        sha,
        "checkout-load",
        (sample,) * 5,
        (),
        {
            "p50_ms": 10,
            "p95_ms": 20,
            "p99_ms": 30,
            "throughput": 4,
            "error_rate": 0,
            "query_count": queries,
            "p95_variance_cv": 0.01,
            "repetitions": 5,
        },
        False,
    )
    return PhaseObservations((check,), load)


@dataclass
class _Runner:
    workspaces: list[Path] = field(default_factory=list)

    def run_phase(
        self,
        phase: str,
        *,
        repository: Path,
        commit_sha: str,
        selections: tuple[WorkloadSelection, ...],
        commands: dict[str, tuple[str, ...]],
        job: object,
        artifact_dir: Path,
    ) -> PhaseObservations:
        assert _git(repository, "rev-parse", "HEAD") == commit_sha
        assert [item.workload_id for item in selections] == ["checkout-pytest", "checkout-load"]
        assert commands["checkout-pytest"][:3] == ("python", "-m", "pytest")
        self.workspaces.append(repository)
        return _observations(phase, commit_sha, 2 if phase == "baseline" else 51)


def test_fixture_adapter_measures_disposable_worktrees_and_classifies_regression(
    fixture_repository: tuple[Path, str, str], tmp_path: Path
) -> None:
    repository, base, candidate = fixture_repository
    request = _request(repository, base, candidate)
    intelligence = FixtureRepositoryIntelligence()
    _, context = intelligence.inspect(request, "run-live")
    workloads = FixtureWorkloadSelector().select(context)
    runner = _Runner()
    verifier = FixtureVerificationAdapter(runner, tmp_path / "artifacts")

    baseline = verifier.measure_baseline(request, "run-live", workloads)
    result = verifier.measure_candidate(request, "run-live", workloads, baseline)

    assert baseline.result.status == "passed"
    assert result.result.status == "failed"
    assert result.result.metadata["classification"] == "runtime_regression"
    assert result.result.metrics["query_count_delta"] == 49
    assert len(result.findings) == 1
    assert all(not workspace.exists() for workspace in runner.workspaces)
    assert (tmp_path / "artifacts" / "run-live" / "comparison" / "comparison.json").is_file()


def test_fixture_decision_defaults_to_report_and_can_recommend_when_policy_qualifies(
    fixture_repository: tuple[Path, str, str], tmp_path: Path
) -> None:
    repository, base, candidate = fixture_repository
    request = _request(repository, base, candidate)
    workloads = FixtureWorkloadSelector().select(
        FixtureRepositoryIntelligence().inspect(request, "run-decision")[1]
    )
    verifier = FixtureVerificationAdapter(_Runner(), tmp_path / "artifacts")
    baseline = verifier.measure_baseline(request, "run-decision", workloads)
    measured = verifier.measure_candidate(request, "run-decision", workloads, baseline)

    assert (
        FixtureDecisionAdapter().decide(request, "run-decision", baseline, measured).action
        == "report"
    )

    qualified = _request(
        repository,
        base,
        candidate,
        {
            "debt_inputs": {
                "complexity": 0.8,
                "coverage_deficit": 0.8,
                "estimated_patch_size": 0.2,
                "churn": 0.5,
                "graph_centrality": 0.5,
                "path_criticality": 0.5,
            },
            "remediation_inputs": {
                "blast_radius": 0.1,
                "criticality": 0.1,
                "coverage": 1.0,
                "reversibility": 1.0,
                "verification_strength": 1.0,
                "patch_size": 0.1,
                "schema_migration_risk": 0.0,
                "data_migration_risk": 0.0,
                "context_completeness": 1.0,
                "evidence_confidence": 1.0,
            },
        },
    )

    decision = FixtureDecisionAdapter().decide(qualified, "run-decision", baseline, measured)
    assert decision.action == "recommend"
    assert decision.autonomy_level == 1
    assert decision.metadata["composition_action_cap"] == "recommend"

    candidate_test_failure = measured.__class__(
        measured.result.model_copy(
            update={
                "metadata": {"classification": "candidate_test_failure"},
                "findings": [],
            }
        ),
        (),
        measured.evidence,
    )
    assert (
        FixtureDecisionAdapter()
        .decide(qualified, "run-decision", baseline, candidate_test_failure)
        .action
        == "report"
    )

    clean = measured.__class__(
        measured.result.model_copy(
            update={"status": "passed", "metadata": {"classification": "clean"}}
        ),
        (),
        measured.evidence,
    )
    assert (
        FixtureDecisionAdapter().decide(qualified, "run-decision", baseline, clean).action
        == "report"
    )

    inconclusive = measured.__class__(
        measured.result.model_copy(
            update={"status": "inconclusive", "metadata": {"classification": "inconclusive"}}
        ),
        (),
        measured.evidence,
    )
    with pytest.raises(ValueError, match="inconclusive evidence"):
        FixtureDecisionAdapter().decide(qualified, "run-decision", baseline, inconclusive)
