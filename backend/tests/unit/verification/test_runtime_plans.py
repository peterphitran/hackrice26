"""Runtime execution supports finalized plans with or without load checks."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import pytest

from contracts import AnalysisJob, WorkloadSelection
from lou.execution import CommandOutput, CommandResult
from lou.loadtest import K6Experiment
from lou.verification import runtime
from lou.verification.runtime import DockerWorkloadRunner


def _result(arguments: Sequence[str] = ()) -> CommandResult:
    output = CommandOutput("", 0, False, None, None)
    return CommandResult(tuple(arguments), 0, 0, output, output, False, False, False, {})


def _job() -> AnalysisJob:
    return AnalysisJob(
        analysis_run_id="adaptive-runtime",
        repository_id="fixture",
        repository_path="fixture",
        base_commit_sha="a" * 40,
        candidate_commit_sha="b" * 40,
        verification_plan={"workloads": []},
        resource_limits={"k6_repetitions": 5},
    )


def _selection(workload_id: str, workload_type: Literal["pytest", "k6"]) -> WorkloadSelection:
    return WorkloadSelection(
        workload_id=workload_id,
        workload_type=workload_type,
        definition_path=(
            "tests/test_checkout.py" if workload_type == "pytest" else "loadtests/checkout.js"
        ),
        phase="candidate",
        reason="selected by finalized graph plan",
        confidence=1,
    )


def _disable_container_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_docker_build", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "run_command", lambda *_args, **_kwargs: _result())


def test_pytest_only_plan_does_not_start_load_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_container_builds(monkeypatch)
    monkeypatch.setattr(
        runtime,
        "_container_result",
        lambda _image, arguments, _artifact_dir, _limits: _result(arguments),
    )

    def unexpected_start(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("pytest-only plan attempted to start a load-test application")

    monkeypatch.setattr(runtime, "_start_app", unexpected_start)
    selection = _selection("checkout-pytest", "pytest")
    observed = DockerWorkloadRunner(database_url="postgresql://lou@postgres/lou").run_phase(
        "candidate",
        repository=tmp_path,
        commit_sha="b" * 40,
        selections=(selection,),
        commands={"checkout-pytest": ("python", "-m", "pytest")},
        job=_job(),
        artifact_dir=tmp_path / "artifacts",
    )

    assert [check.workload_id for check in observed.checks] == ["checkout-pytest"]
    assert observed.load is None


def test_load_only_plan_runs_without_assuming_pytest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_container_builds(monkeypatch)
    monkeypatch.setattr(runtime, "_start_app", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "_remove_app", lambda *_args, **_kwargs: None)

    def fake_k6(
        phase: Literal["baseline", "candidate", "fix"],
        commit_sha: str,
        selection: WorkloadSelection,
        **_kwargs: object,
    ) -> K6Experiment:
        return K6Experiment(phase, commit_sha, selection.workload_id, (), (), {}, False)

    monkeypatch.setattr(runtime, "run_k6_experiment", fake_k6)
    selection = _selection("checkout-k6", "k6")
    observed = DockerWorkloadRunner(database_url="postgresql://lou@postgres/lou").run_phase(
        "candidate",
        repository=tmp_path,
        commit_sha="b" * 40,
        selections=(selection,),
        commands={},
        job=_job(),
        artifact_dir=tmp_path / "artifacts",
    )

    assert observed.checks == ()
    assert observed.load is not None
    assert observed.load.workload_id == "checkout-k6"
