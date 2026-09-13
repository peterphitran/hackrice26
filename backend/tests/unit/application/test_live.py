"""Tests for the first live fixture adapter set without invoking Docker."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from contracts import RepositoryContext, WorkloadSelection
from lou.application.analysis import AnalysisRequest
from lou.application.live import (
    FixtureDecisionAdapter,
    FixtureRepositoryIntelligence,
    FixtureVerificationAdapter,
    FixtureWorkloadSelector,
)
from lou.execution import CommandOutput, CommandResult
from lou.loadtest import K6Experiment, K6Sample
from lou.repository import build_repository_graph, fixture_workload_registry
from lou.verification import PhaseCheck, PhaseObservations

FIXTURE_MANIFEST = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "broken-store"
    / "template"
    / ".lou"
    / "workloads.json"
)


def write_workload_manifest(repository: Path) -> None:
    """Give the repository the same workload manifest the shipped fixture declares.

    Copying rather than restating it keeps this fixture honest: if the manifest the
    demo relies on stops loading, these tests stop passing too.
    """

    target = repository / ".lou"
    target.mkdir(parents=True, exist_ok=True)
    (target / "workloads.json").write_text(
        FIXTURE_MANIFEST.read_text(encoding="utf-8"), encoding="utf-8"
    )


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
        """from fastapi import FastAPI

class Store:
    def checkout(self, cursor):
        cursor.execute("SELECT id FROM broken_store.products")
        return 2

store = Store()
app = FastAPI()

@app.post("/checkout")
def checkout():
    return store.checkout(None)
""",
        encoding="utf-8",
    )
    (repository / "tests" / "test_checkout.py").write_text(
        """import store.app as store_app

def test_receipt():
    return store_app.Store().checkout(None)
""",
        encoding="utf-8",
    )
    (repository / "loadtests" / "checkout.js").write_text(
        'import http from "k6/http";\nhttp.post(`${__ENV.BASE_URL}/checkout`);\n',
        encoding="utf-8",
    )
    write_workload_manifest(repository)
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "good"], check=True)
    base = _git(repository, "rev-parse", "HEAD")
    (repository / "store" / "app.py").write_text(
        """from fastapi import FastAPI

class Store:
    def checkout(self, cursor):
        cursor.execute("SELECT id FROM broken_store.products WHERE id = 1")
        return 51

store = Store()
app = FastAPI()

@app.post("/checkout")
def checkout():
    return store.checkout(None)
""",
        encoding="utf-8",
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
        "checkout-k6",
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
        assert [item.workload_id for item in selections] == ["checkout-pytest", "checkout-k6"]
        assert commands["checkout-pytest"][:3] == ("python", "-m", "pytest")
        self.workspaces.append(repository)
        return _observations(phase, commit_sha, 2 if phase == "baseline" else 51)


@dataclass
class _AdaptiveRunner:
    plans: list[tuple[str, ...]] = field(default_factory=list)

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
        del repository, commands, job, artifact_dir
        self.plans.append(tuple(item.workload_id for item in selections))
        complete = _observations(phase, commit_sha, 2 if phase == "baseline" else 51)
        includes_pytest = any(item.workload_type == "pytest" for item in selections)
        checks = complete.checks if includes_pytest else ()
        load = complete.load if any(item.workload_type == "k6" for item in selections) else None
        return PhaseObservations(checks, load)


def test_fixture_adapter_measures_disposable_worktrees_and_classifies_regression(
    fixture_repository: tuple[Path, str, str], tmp_path: Path
) -> None:
    repository, base, candidate = fixture_repository
    request = _request(repository, base, candidate)
    intelligence = FixtureRepositoryIntelligence(tmp_path / "graph-artifacts")
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


@pytest.mark.parametrize("selected_type", ["pytest", "k6"])
def test_fixture_adapter_supports_single_type_finalized_plans(
    fixture_repository: tuple[Path, str, str], tmp_path: Path, selected_type: str
) -> None:
    repository, base, candidate = fixture_repository
    request = _request(repository, base, candidate)
    context = RepositoryContext(
        repository_id="fixture",
        commit_sha=candidate,
        selected_workload_ids=["checkout-pytest", "checkout-k6"],
        selection_reasons={
            "checkout-pytest": "graph test path",
            "checkout-k6": "graph load path",
        },
        completeness=1,
        metadata={"repository_path": str(repository)},
    )
    all_workloads = FixtureWorkloadSelector().select(context)
    workloads = tuple(item for item in all_workloads if item.workload_type == selected_type)
    runner = _AdaptiveRunner()
    verifier = FixtureVerificationAdapter(runner, tmp_path / selected_type)

    baseline = verifier.measure_baseline(request, f"run-{selected_type}", workloads)
    candidate_result = verifier.measure_candidate(
        request, f"run-{selected_type}", workloads, baseline
    )

    expected_ids = tuple(item.workload_id for item in workloads)
    assert runner.plans == [expected_ids, expected_ids]
    assert baseline.result.status == "passed"
    if selected_type == "pytest":
        assert candidate_result.result.status == "passed"
        assert candidate_result.result.metadata["runtime_comparison_performed"] is False
        assert candidate_result.result.metadata["load_workload_status"] == "not_selected"
    else:
        assert candidate_result.result.status == "failed"
        assert candidate_result.result.metadata["runtime_comparison_performed"] is True


def test_candidate_rejects_a_different_plan_than_baseline(
    fixture_repository: tuple[Path, str, str], tmp_path: Path
) -> None:
    repository, base, candidate = fixture_repository
    request = _request(repository, base, candidate)
    context = RepositoryContext(
        repository_id="fixture",
        commit_sha=candidate,
        selected_workload_ids=["checkout-pytest", "checkout-k6"],
        selection_reasons={
            "checkout-pytest": "graph test path",
            "checkout-k6": "graph load path",
        },
        completeness=1,
        metadata={"repository_path": str(repository)},
    )
    workloads = FixtureWorkloadSelector().select(context)
    verifier = FixtureVerificationAdapter(_AdaptiveRunner(), tmp_path / "same-plan")
    baseline = verifier.measure_baseline(request, "run-same-plan", workloads)

    with pytest.raises(ValueError, match="reuse the finalized baseline plan"):
        verifier.measure_candidate(request, "run-same-plan", workloads[:1], baseline)


def test_fixture_intelligence_selects_only_graph_reached_registry_workloads(
    fixture_repository: tuple[Path, str, str], tmp_path: Path
) -> None:
    repository, base, candidate = fixture_repository
    _, context = FixtureRepositoryIntelligence(tmp_path / "graph-artifacts").inspect(
        _request(repository, base, candidate), "run-graph-context"
    )

    assert context.selected_workload_ids == ["checkout-pytest", "checkout-k6"]
    assert context.affected_tests == ["tests/test_checkout.py::test_receipt"]
    assert context.affected_endpoints == ["POST /checkout"]
    assert context.affected_data_dependencies == ["broken_store.products"]
    assert "reachable graph evidence" in context.selection_reasons["checkout-k6"]
    assert context.metadata["impact_traversal"]["nodes"]


def test_selector_allows_only_known_configured_fallback_workloads() -> None:
    context = RepositoryContext(
        repository_id="fixture-broken-store",
        commit_sha="a" * 40,
        selected_workload_ids=["checkout-pytest"],
        selection_reasons={"checkout-pytest": "configured fallback"},
        completeness=0.5,
        metadata={"fallback_workload_ids": ["checkout-pytest"]},
    )

    selector = FixtureWorkloadSelector(registry=fixture_workload_registry())

    assert [item.workload_id for item in selector.select(context)] == ["checkout-pytest"]

    unknown = context.model_copy(update={"selected_workload_ids": ["unknown"]})
    plan = selector.plan(unknown)
    assert [item.workload_id for item in plan.selected] == ["checkout-pytest"]
    unknown_omitted = any(
        item.workload_id == "unknown" and item.category == "unregistered" for item in plan.omitted
    )
    assert unknown_omitted


def test_fixture_decision_defaults_to_report_and_can_recommend_when_policy_qualifies(
    fixture_repository: tuple[Path, str, str], tmp_path: Path
) -> None:
    repository, base, candidate = fixture_repository
    request = _request(repository, base, candidate)
    workloads = FixtureWorkloadSelector().select(
        FixtureRepositoryIntelligence(tmp_path / "graph-artifacts").inspect(
            request, "run-decision"
        )[1]
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


def test_a_declared_fallback_runs_when_graph_extraction_is_incomplete(
    fixture_repository: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial graph must not silently validate nothing.

    No real repository reaches full completeness, so a manifest's fallback_eligible
    flag has to be what authorizes a workload to run on partial evidence. Without
    this the planner omits every workload as incomplete_graph.
    """

    repository, base, candidate = fixture_repository
    intelligence = FixtureRepositoryIntelligence(tmp_path / "artifacts")
    real = build_repository_graph

    def partial(**kwargs: object) -> object:
        snapshot = real(**kwargs)  # type: ignore[arg-type]
        return replace(snapshot, completeness=0.9)

    monkeypatch.setattr("lou.application.live.build_repository_graph", partial)
    _, context = intelligence.inspect(_request(repository, base, candidate), "run-fallback")

    plan = context.metadata["validation_plan"]
    assert plan["state"] == "incomplete_with_fallback"
    assert [item["workload_id"] for item in plan["selected"]] == ["checkout-pytest", "checkout-k6"]
    assert plan["confidence"] < 1.0
