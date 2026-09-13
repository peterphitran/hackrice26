"""Deterministic baseline-to-candidate verification verdicts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from contracts import AnalysisJob, Evidence, Finding, VerificationResult
from lou.loadtest import K6Experiment
from lou.verification.checks import PhaseCheck


@dataclass(frozen=True)
class DifferentialResult:
    verification: VerificationResult
    evidence: Evidence
    finding: Finding | None


def _ratio(candidate: float, baseline: float) -> float:
    if baseline:
        return candidate / baseline
    return 1.0 if candidate == 0 else 1e308


def _failure_classes(
    baseline: tuple[PhaseCheck, ...], candidate: tuple[PhaseCheck, ...]
) -> dict[str, list[str]]:
    before = {check.workload_id: check.outcome for check in baseline}
    after = {check.workload_id: check.outcome for check in candidate}
    classes: dict[str, list[str]] = {
        "baseline_only": [],
        "candidate_only": [],
        "shared": [],
    }
    for workload_id in sorted(before.keys() | after.keys()):
        baseline_failed = before.get(workload_id, "passed") != "passed"
        candidate_failed = after.get(workload_id, "passed") != "passed"
        if baseline_failed and candidate_failed:
            classes["shared"].append(workload_id)
        elif baseline_failed:
            classes["baseline_only"].append(workload_id)
        elif candidate_failed:
            classes["candidate_only"].append(workload_id)
    return classes


def compare_candidate(
    job: AnalysisJob,
    baseline_checks: tuple[PhaseCheck, ...],
    candidate_checks: tuple[PhaseCheck, ...],
    baseline_load: K6Experiment | None,
    candidate_load: K6Experiment | None,
    *,
    artifact_dir: Path,
) -> DifferentialResult:
    """Compare equivalent runs and return contract-compatible evidence."""
    if (baseline_load is None) != (candidate_load is None):
        raise ValueError("baseline and candidate must use the same finalized load plan")
    runtime_comparison_performed = baseline_load is not None
    if baseline_load is not None and candidate_load is not None:
        if baseline_load.commit_sha != job.base_commit_sha:
            raise ValueError("baseline experiment commit does not match the analysis job")
        if candidate_load.commit_sha != job.candidate_commit_sha:
            raise ValueError("candidate experiment commit does not match the analysis job")
        if baseline_load.workload_id != candidate_load.workload_id:
            raise ValueError("baseline and candidate must use the same workload")

    plan = job.verification_plan
    expected_repetitions = int(job.resource_limits.get("k6_repetitions", 5))
    max_variance = float(plan.get("maximum_variance_cv", 0.2))
    min_query_increase = float(plan.get("minimum_query_count_increase", 10))
    min_query_ratio = float(plan.get("minimum_query_count_ratio", 5))
    min_p95_ratio = float(plan.get("minimum_p95_regression_ratio", 2))
    baseline_metrics = baseline_load.metrics if baseline_load is not None else {}
    candidate_metrics = candidate_load.metrics if candidate_load is not None else {}
    classes = _failure_classes(baseline_checks, candidate_checks)

    tool_failure = (
        any(check.outcome == "command_failed" for check in baseline_checks + candidate_checks)
        or (baseline_load.command_failed if baseline_load is not None else False)
        or (candidate_load.command_failed if candidate_load is not None else False)
    )
    enough_samples = not runtime_comparison_performed or (
        baseline_load is not None
        and candidate_load is not None
        and len(baseline_load.samples) == expected_repetitions
        and len(candidate_load.samples) == expected_repetitions
    )
    noisy = any(
        metrics.get("p95_variance_cv", 0) > max_variance
        for metrics in (baseline_metrics, candidate_metrics)
    )

    baseline_queries = float(baseline_metrics.get("query_count", 0))
    candidate_queries = float(candidate_metrics.get("query_count", 0))
    baseline_p95 = float(baseline_metrics.get("p95_ms", 0))
    candidate_p95 = float(candidate_metrics.get("p95_ms", 0))
    query_delta = candidate_queries - baseline_queries
    query_ratio = _ratio(candidate_queries, baseline_queries)
    p95_ratio = _ratio(candidate_p95, baseline_p95)
    query_regression = (
        runtime_comparison_performed
        and query_delta >= min_query_increase
        and query_ratio >= min_query_ratio
    )
    latency_regression = runtime_comparison_performed and p95_ratio >= min_p95_ratio
    regression = query_regression or latency_regression

    status: Literal["passed", "failed", "inconclusive"]
    if tool_failure or not enough_samples:
        status = "inconclusive"
    elif classes["candidate_only"] or query_regression:
        status = "failed"
    elif noisy:
        status = "inconclusive"
    elif latency_regression:
        status = "failed"
    else:
        status = "passed"

    metrics = (
        {
            "baseline_p50_ms": float(baseline_metrics.get("p50_ms", 0)),
            "candidate_p50_ms": float(candidate_metrics.get("p50_ms", 0)),
            "baseline_p95_ms": baseline_p95,
            "candidate_p95_ms": candidate_p95,
            "baseline_p99_ms": float(baseline_metrics.get("p99_ms", 0)),
            "candidate_p99_ms": float(candidate_metrics.get("p99_ms", 0)),
            "baseline_throughput": float(baseline_metrics.get("throughput", 0)),
            "candidate_throughput": float(candidate_metrics.get("throughput", 0)),
            "baseline_error_rate": float(baseline_metrics.get("error_rate", 0)),
            "candidate_error_rate": float(candidate_metrics.get("error_rate", 0)),
            "baseline_query_count": baseline_queries,
            "candidate_query_count": candidate_queries,
            "query_count_delta": query_delta,
            "query_count_ratio": query_ratio,
            "p95_regression_ratio": p95_ratio,
            "baseline_variance_cv": float(baseline_metrics.get("p95_variance_cv", 0)),
            "candidate_variance_cv": float(candidate_metrics.get("p95_variance_cv", 0)),
            "baseline_repetitions": float(len(baseline_load.samples)),
            "candidate_repetitions": float(len(candidate_load.samples)),
        }
        if baseline_load is not None and candidate_load is not None
        else {}
    )
    thresholds = {
        "minimum_query_count_increase": min_query_increase,
        "minimum_query_count_ratio": min_query_ratio,
        "minimum_p95_regression_ratio": min_p95_ratio,
        "maximum_variance_cv": max_variance,
        "required_repetitions": expected_repetitions,
    }
    raw = {
        "analysis_run_id": job.analysis_run_id,
        "baseline_commit_sha": job.base_commit_sha,
        "candidate_commit_sha": job.candidate_commit_sha,
        "status": status,
        "metrics": metrics,
        "thresholds": thresholds,
        "failure_classification": classes,
        "tool_failure": tool_failure,
        "excessive_variance": noisy,
        "runtime_comparison_performed": runtime_comparison_performed,
        "load_workload_status": "compared" if runtime_comparison_performed else "not_selected",
    }
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / "comparison.json"
    artifact_bytes = (json.dumps(raw, indent=2, sort_keys=True) + "\n").encode()
    artifact_path.write_bytes(artifact_bytes)
    artifact_uri = f"file://{artifact_path.as_posix()}"
    artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()

    finding = None
    if regression and status == "failed" and candidate_load is not None:
        finding = Finding(
            finding_id=f"finding_{job.analysis_run_id}_runtime_regression",
            analysis_run_id=job.analysis_run_id,
            fingerprint=hashlib.sha256(
                f"{job.candidate_commit_sha}:{candidate_load.workload_id}:runtime".encode()
            ).hexdigest(),
            source="differential-verification",
            category="runtime-regression",
            severity="high",
            confidence=1.0,
            phase="candidate",
            title="Candidate runtime regression",
            message="Candidate exceeds the configured runtime regression threshold.",
            metadata={
                "query_regression": query_regression,
                "latency_regression": latency_regression,
            },
        )

    evidence = Evidence(
        evidence_id=f"evidence_{job.analysis_run_id}_comparison",
        analysis_run_id=job.analysis_run_id,
        phase="comparison",
        kind="differential-verification",
        source="lou.verification",
        collected_at=datetime.now(UTC),
        summary=raw,
        artifact_uri=artifact_uri,
        artifact_sha256=artifact_hash,
    )
    verification = VerificationResult(
        verification_run_id=f"verify_{job.analysis_run_id}_candidate",
        analysis_run_id=job.analysis_run_id,
        phase="candidate",
        commit_sha=job.candidate_commit_sha,
        status=status,
        workload_id=(
            candidate_load.workload_id
            if candidate_load is not None
            else candidate_checks[0].workload_id
            if len(candidate_checks) == 1
            else None
        ),
        metrics=metrics,
        findings=[finding.finding_id] if finding else [],
        evidence_ids=[evidence.evidence_id],
        artifact_uri=artifact_uri,
        metadata={
            "thresholds": thresholds,
            "failure_classification": classes,
            "excessive_variance": noisy,
            "runtime_comparison_performed": runtime_comparison_performed,
            "load_workload_status": (
                "compared" if runtime_comparison_performed else "not_selected"
            ),
        },
    )
    return DifferentialResult(verification, evidence, finding)
