from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts import WorkloadSelection
from lou.execution import CommandOutput, CommandResult
from lou.loadtest import K6Sample, aggregate, parse_k6_summary, run_k6_experiment


def _summary(path: Path, p95: float = 20, queries: float = 2) -> None:
    path.write_text(
        json.dumps(
            {
                "metrics": {
                    "http_req_duration": {"values": {"med": 10, "p(95)": p95, "p(99)": 30}},
                    "http_reqs": {"values": {"rate": 4}},
                    "http_req_failed": {"values": {"rate": 0}},
                    "database_queries": {"values": {"med": queries}},
                }
            }
        ),
        encoding="utf-8",
    )


def _result(arguments: list[str], *, failed: bool = False) -> CommandResult:
    output = CommandOutput("", 0, False, None, None)
    return CommandResult(
        tuple(arguments), 1 if failed else 0, 0, output, output, False, False, False, {}
    )


def _selection() -> WorkloadSelection:
    return WorkloadSelection(
        workload_id="checkout-k6",
        workload_type="k6",
        definition_path="loadtests/checkout.js",
        phase="candidate",
        reason="selected by graph",
        confidence=1,
    )


def test_parse_and_aggregate_k6_summaries(tmp_path: Path) -> None:
    summary = tmp_path / "summary.json"
    _summary(summary, p95=20, queries=51)
    sample = parse_k6_summary(summary)

    assert sample == K6Sample(10, 20, 30, 4, 0, 51)
    metrics = aggregate((sample, K6Sample(10, 30, 30, 4, 0, 51)))
    assert metrics["p95_ms"] == 25
    assert metrics["query_count"] == 51
    assert metrics["repetitions"] == 2
    assert metrics["p95_variance_cv"] == pytest.approx(0.2)


def test_warms_up_then_runs_selected_workload_repeatedly(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def execute(arguments: list[str], **_: object) -> CommandResult:
        calls.append(arguments)
        summary = Path(arguments[arguments.index("--summary-export") + 1])
        _summary(summary, p95=20 + len(calls), queries=51)
        return _result(arguments)

    experiment = run_k6_experiment(
        "candidate",
        "b" * 40,
        _selection(),
        repository=tmp_path,
        artifact_dir=tmp_path / "artifacts",
        base_url="http://fixture:8000",
        repetitions=3,
        executor=execute,
    )

    assert len(calls) == 4
    assert "WARMUP=1" in calls[0] and all("WARMUP=1" not in call for call in calls[1:])
    assert all(call[-1] == str(tmp_path / "loadtests/checkout.js") for call in calls)
    assert experiment.command_failed is False
    assert experiment.metrics["repetitions"] == 3
    assert json.loads((tmp_path / "artifacts/aggregate.json").read_text())["commit_sha"] == (
        "b" * 40
    )


def test_command_failure_stops_without_fabricating_samples(tmp_path: Path) -> None:
    experiment = run_k6_experiment(
        "baseline",
        "a" * 40,
        _selection(),
        repository=tmp_path,
        artifact_dir=tmp_path / "artifacts",
        base_url="http://fixture:8000",
        executor=lambda arguments, **_: _result(arguments, failed=True),
    )

    assert experiment.command_failed is True
    assert experiment.samples == ()
    assert experiment.metrics == {}
