from __future__ import annotations

from pathlib import Path

from contracts import AnalysisJob
from lou.execution import CommandOutput, CommandResult
from lou.loadtest import K6Experiment, K6Sample
from lou.verification import PhaseCheck, compare_candidate


def _job(**plan: float) -> AnalysisJob:
    return AnalysisJob(
        analysis_run_id="run-1",
        repository_id="fixture",
        repository_path="fixture",
        base_commit_sha="a" * 40,
        candidate_commit_sha="b" * 40,
        verification_plan={
            "minimum_query_count_increase": 10,
            "minimum_query_count_ratio": 5,
            "minimum_p95_regression_ratio": 2,
            "maximum_variance_cv": 0.2,
            **plan,
        },
        resource_limits={"k6_repetitions": 5},
    )


def _experiment(
    phase: str,
    sha: str,
    *,
    queries: float,
    p95: float,
    variance: float = 0.05,
    count: int = 5,
    failed: bool = False,
) -> K6Experiment:
    sample = K6Sample(10, p95, 30, 4, 0, queries)
    return K6Experiment(
        phase,  # type: ignore[arg-type]
        sha,
        "checkout-k6",
        (sample,) * count,
        (),
        {
            "p50_ms": 10,
            "p95_ms": p95,
            "p99_ms": 30,
            "throughput": 4,
            "error_rate": 0,
            "query_count": queries,
            "p95_variance_cv": variance,
            "repetitions": count,
        },
        failed,
    )


def _check(phase: str, sha: str, outcome: str = "passed") -> PhaseCheck:
    output = CommandOutput("", 0, False, None, None)
    command = CommandResult(("pytest",), 0, 0, output, output, False, False, False, {})
    return PhaseCheck(
        phase,  # type: ignore[arg-type]
        sha,
        "checkout-pytest",
        ("pytest",),
        outcome,  # type: ignore[arg-type]
        command,
    )


def test_reports_query_regression_with_absolute_delta_and_thresholds(tmp_path: Path) -> None:
    result = compare_candidate(
        _job(),
        (_check("baseline", "a" * 40),),
        (_check("candidate", "b" * 40),),
        _experiment("baseline", "a" * 40, queries=2, p95=20),
        _experiment("candidate", "b" * 40, queries=51, p95=25),
        artifact_dir=tmp_path,
    )

    assert result.verification.status == "failed"
    assert result.verification.metrics["query_count_delta"] == 49
    assert result.verification.metrics["query_count_ratio"] == 25.5
    assert result.verification.metadata["thresholds"]["minimum_query_count_ratio"] == 5
    assert result.finding and result.finding.category == "runtime-regression"
    assert result.evidence.artifact_sha256
    assert (tmp_path / "comparison.json").is_file()


def test_classifies_candidate_only_and_shared_test_failures(tmp_path: Path) -> None:
    baseline = (_check("baseline", "a" * 40, "test_failed"),)
    candidate = (_check("candidate", "b" * 40, "test_failed"),)
    result = compare_candidate(
        _job(),
        baseline,
        candidate,
        _experiment("baseline", "a" * 40, queries=2, p95=20),
        _experiment("candidate", "b" * 40, queries=2, p95=20),
        artifact_dir=tmp_path,
    )
    assert result.verification.status == "passed"
    assert result.verification.metadata["failure_classification"] == {
        "baseline_only": [],
        "candidate_only": [],
        "shared": ["checkout-pytest"],
    }

    candidate_only = compare_candidate(
        _job(),
        (_check("baseline", "a" * 40),),
        candidate,
        _experiment("baseline", "a" * 40, queries=2, p95=20),
        _experiment("candidate", "b" * 40, queries=2, p95=20),
        artifact_dir=tmp_path / "candidate-only",
    )
    assert candidate_only.verification.status == "failed"


def test_excessive_variance_or_tool_failure_is_inconclusive(tmp_path: Path) -> None:
    noisy = compare_candidate(
        _job(),
        (),
        (),
        _experiment("baseline", "a" * 40, queries=2, p95=20),
        _experiment("candidate", "b" * 40, queries=51, p95=50, variance=0.3),
        artifact_dir=tmp_path / "noisy",
    )
    failed_tool = compare_candidate(
        _job(),
        (),
        (),
        _experiment("baseline", "a" * 40, queries=2, p95=20),
        _experiment("candidate", "b" * 40, queries=51, p95=50, failed=True),
        artifact_dir=tmp_path / "tool-failure",
    )

    assert noisy.verification.status == "inconclusive"
    assert failed_tool.verification.status == "inconclusive"
