"""Validate the shared, illustrative SH-001 checkout demo records."""

from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel

from contracts import (
    AnalysisJob,
    Finding,
    PatchArtifact,
    RepositoryContext,
    VerificationResult,
    WorkloadSelection,
)

FIXTURES_DIR = Path(__file__).parents[3] / "contracts" / "fixtures" / "demo_checkout"
ModelT = TypeVar("ModelT", bound=BaseModel)

FIXTURE_MODELS: dict[str, type[BaseModel]] = {
    "analysis_job.json": AnalysisJob,
    "repository_context.json": RepositoryContext,
    "workload_pytest.json": WorkloadSelection,
    "workload_k6.json": WorkloadSelection,
    "finding.json": Finding,
    "verification_baseline.json": VerificationResult,
    "verification_candidate.json": VerificationResult,
    "verification_fix.json": VerificationResult,
    "patch_artifact.json": PatchArtifact,
    "boundary_context_incomplete.json": RepositoryContext,
    "boundary_verification_inconclusive.json": VerificationResult,
}


def _load(filename: str, model: type[ModelT]) -> ModelT:
    return model.model_validate_json((FIXTURES_DIR / filename).read_text())


@pytest.mark.parametrize(("filename", "model"), FIXTURE_MODELS.items())
def test_every_checkout_fixture_validates(filename: str, model: type[BaseModel]) -> None:
    assert (
        model.model_validate_json((FIXTURES_DIR / filename).read_text()).model_dump()[
            "schema_version"
        ]
        == "1"
    )


def test_checkout_fixture_set_is_complete() -> None:
    assert {path.name for path in FIXTURES_DIR.glob("*.json")} == FIXTURE_MODELS.keys()


def test_checkout_records_describe_one_consistent_regression_and_fix() -> None:
    job = _load("analysis_job.json", AnalysisJob)
    context = _load("repository_context.json", RepositoryContext)
    finding = _load("finding.json", Finding)
    pytest_workload = _load("workload_pytest.json", WorkloadSelection)
    k6_workload = _load("workload_k6.json", WorkloadSelection)
    patch = _load("patch_artifact.json", PatchArtifact)
    results = {
        phase: _load(f"verification_{phase}.json", VerificationResult)
        for phase in ("baseline", "candidate", "fix")
    }
    baseline, candidate, fix = (results[phase] for phase in ("baseline", "candidate", "fix"))

    # Placeholder commit identities encode the intended baseline -> candidate -> fix chain.
    commits = (baseline.commit_sha, candidate.commit_sha, fix.commit_sha)
    assert len(set(commits)) == 3
    assert all(
        len(sha) == 40 and all(char in "0123456789abcdef" for char in sha) for sha in commits
    )
    assert job.base_commit_sha == baseline.commit_sha
    assert job.candidate_commit_sha == candidate.commit_sha == context.commit_sha
    assert candidate.metadata["parent_commit_sha"] == baseline.commit_sha
    assert fix.metadata["parent_commit_sha"] == candidate.commit_sha
    assert patch.base_commit_sha == candidate.commit_sha
    assert patch.metadata["expected_fix_commit_sha"] == fix.commit_sha

    workload_ids = {pytest_workload.workload_id, k6_workload.workload_id}
    assert pytest_workload.workload_type == "pytest"
    assert k6_workload.workload_type == "k6"
    assert pytest_workload.phase == k6_workload.phase == "candidate"
    assert set(job.verification_plan["workloads"]) == workload_ids
    assert set(context.selected_workload_ids) == workload_ids
    assert set(context.selection_reasons) == workload_ids
    assert all(context.selection_reasons.values())
    assert pytest_workload.metadata["test_nodeid"] in context.affected_tests
    assert k6_workload.metadata["endpoint"] in context.affected_endpoints
    assert finding.symbol_key in context.changed_symbols
    assert "checkout.service.checkout" in context.changed_symbols
    assert context.completeness > 0.9 and not context.unresolved_relationships

    assert finding.analysis_run_id == patch.analysis_run_id == job.analysis_run_id
    assert finding.phase == "candidate"
    assert finding.category == "database-query-regression"
    assert patch.files_changed == 1
    assert patch.lines_added + patch.lines_deleted <= 12
    assert "Batch-load" in patch.metadata["expected_change"]
    for phase, result in results.items():
        assert result.phase == phase
        assert result.analysis_run_id == job.analysis_run_id
        assert set(result.metadata["workload_ids"]) == workload_ids
        assert result.metadata["pytest_status"] == "passed"
        assert result.metrics["p50_ms"] <= result.metrics["p95_ms"] <= result.metrics["p99_ms"]
        assert (
            result.metadata["latency_variance_cv"] <= job.verification_plan["maximum_variance_cv"]
        )

    assert (baseline.status, candidate.status, fix.status) == ("passed", "failed", "passed")
    assert candidate.metadata["k6_status"] == "failed"
    assert baseline.metadata["k6_status"] == fix.metadata["k6_status"] == "passed"
    assert candidate.findings == [finding.finding_id]
    assert not baseline.findings and not fix.findings
    assert candidate.metrics["p95_ms"] >= (
        baseline.metrics["p95_ms"] * job.verification_plan["minimum_p95_regression_ratio"]
    )
    query_increase = (
        candidate.metrics["queries_per_checkout"] - baseline.metrics["queries_per_checkout"]
    )
    assert query_increase >= job.verification_plan["minimum_query_count_increase"]
    assert finding.metadata["query_count_delta"] == query_increase
    assert finding.metadata["p95_regression_ratio"] == pytest.approx(
        candidate.metrics["p95_ms"] / baseline.metrics["p95_ms"], abs=0.01
    )
    assert fix.metrics["p95_ms"] <= baseline.metrics["p95_ms"] * 1.2
    assert fix.metrics["queries_per_checkout"] == baseline.metrics["queries_per_checkout"]


def test_checkout_boundary_records_preserve_uncertainty() -> None:
    job = _load("analysis_job.json", AnalysisJob)
    incomplete = _load("boundary_context_incomplete.json", RepositoryContext)
    inconclusive = _load("boundary_verification_inconclusive.json", VerificationResult)

    assert incomplete.repository_id == job.repository_id
    assert incomplete.commit_sha == job.candidate_commit_sha
    assert incomplete.completeness < 0.5
    assert incomplete.unresolved_relationships
    assert not incomplete.selected_workload_ids
    assert not incomplete.selection_reasons

    assert inconclusive.analysis_run_id == job.analysis_run_id
    assert inconclusive.commit_sha == job.candidate_commit_sha
    assert inconclusive.phase == "candidate"
    assert inconclusive.status == "inconclusive"
    assert inconclusive.workload_id == "checkout-k6"
    assert (
        inconclusive.metadata["latency_variance_cv"] > job.verification_plan["maximum_variance_cv"]
    )
    assert inconclusive.metadata["inconclusive_reason"]
    assert not inconclusive.findings
