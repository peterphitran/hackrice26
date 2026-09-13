"""Repeat a selected k6 workload and retain raw and aggregate evidence."""

from __future__ import annotations

import json
import statistics
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from contracts import WorkloadSelection
from lou.execution import CommandResult, ResourceValue, run_command

CommandExecutor = Callable[..., CommandResult]


@dataclass(frozen=True)
class K6Sample:
    p50_ms: float
    p95_ms: float
    p99_ms: float
    throughput: float
    error_rate: float
    query_count: float


@dataclass(frozen=True)
class K6Experiment:
    phase: Literal["baseline", "candidate", "fix"]
    commit_sha: str
    workload_id: str
    samples: tuple[K6Sample, ...]
    results: tuple[CommandResult, ...]
    metrics: Mapping[str, float]
    command_failed: bool


def parse_k6_summary(path: Path) -> K6Sample:
    metrics = json.loads(path.read_text(encoding="utf-8"))["metrics"]

    def value(metric: str, key: str) -> float:
        return float(metrics[metric]["values"][key])

    return K6Sample(
        p50_ms=value("http_req_duration", "med"),
        p95_ms=value("http_req_duration", "p(95)"),
        p99_ms=value("http_req_duration", "p(99)"),
        throughput=value("http_reqs", "rate"),
        error_rate=value("http_req_failed", "rate"),
        query_count=value("database_queries", "med"),
    )


def aggregate(samples: tuple[K6Sample, ...]) -> dict[str, float]:
    if not samples:
        return {}
    metrics = {
        field: statistics.median(getattr(sample, field) for sample in samples)
        for field in K6Sample.__dataclass_fields__
    }
    p95_values = [sample.p95_ms for sample in samples]
    p95_mean = statistics.mean(p95_values)
    metrics["repetitions"] = float(len(samples))
    metrics["p95_variance_cv"] = statistics.pstdev(p95_values) / p95_mean if p95_mean else 0.0
    return metrics


def run_k6_experiment(
    phase: Literal["baseline", "candidate", "fix"],
    commit_sha: str,
    selection: WorkloadSelection,
    *,
    repository: Path,
    artifact_dir: Path,
    base_url: str,
    repetitions: int = 5,
    timeout_seconds: float = 120,
    resource_metadata: Mapping[str, ResourceValue] | None = None,
    executor: CommandExecutor = run_command,
) -> K6Experiment:
    if selection.workload_type != "k6":
        raise ValueError("selection must be a k6 workload")
    if len(commit_sha) != 40:
        raise ValueError("commit_sha must be the exact 40-character SHA")
    if repetitions < 1:
        raise ValueError("repetitions must be positive")

    artifact_dir.mkdir(parents=True, exist_ok=True)
    scenario = repository / selection.definition_path
    results: list[CommandResult] = []
    samples: list[K6Sample] = []
    for attempt in range(repetitions + 1):
        warmup = attempt == 0
        summary = artifact_dir / ("warmup.json" if warmup else f"run-{attempt}.json")
        arguments = [
            "k6",
            "run",
            "--quiet",
            "--summary-export",
            str(summary),
            "-e",
            f"BASE_URL={base_url}",
        ]
        if warmup:
            arguments.extend(["-e", "WARMUP=1"])
        arguments.append(str(scenario))
        result = executor(
            arguments,
            artifact_dir=artifact_dir / ("warmup" if warmup else f"run-{attempt}"),
            timeout_seconds=timeout_seconds,
            cwd=repository,
            resource_metadata=resource_metadata,
        )
        results.append(result)
        if result.tool_not_found or result.timed_out or result.cancelled or result.exit_code != 0:
            break
        if not warmup:
            samples.append(parse_k6_summary(summary))

    sample_tuple = tuple(samples)
    metrics = aggregate(sample_tuple)
    (artifact_dir / "aggregate.json").write_text(
        json.dumps(
            {
                "phase": phase,
                "commit_sha": commit_sha,
                "workload_id": selection.workload_id,
                "metrics": metrics,
                "samples": [asdict(sample) for sample in sample_tuple],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return K6Experiment(
        phase,
        commit_sha,
        selection.workload_id,
        sample_tuple,
        tuple(results),
        metrics,
        len(sample_tuple) != repetitions,
    )
