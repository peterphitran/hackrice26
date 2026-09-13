from pathlib import Path
from typing import Literal

import pytest

from contracts import WorkloadSelection
from lou.execution import CommandOutput, CommandResult
from lou.verification import run_phase_checks


def _selection(
    workload_id: str,
    workload_type: Literal["pytest", "k6", "semgrep", "custom"] = "pytest",
) -> WorkloadSelection:
    return WorkloadSelection(
        workload_id=workload_id,
        workload_type=workload_type,
        definition_path="tests/test_checkout.py",
        phase="candidate",
        reason="selected by graph",
        confidence=1,
    )


def _result(arguments: tuple[str, ...], exit_code: int | None = 0, **states: bool) -> CommandResult:
    output = CommandOutput("", 0, False, None, None)
    return CommandResult(
        arguments,
        exit_code,
        0,
        output,
        output,
        states.get("timed_out", False),
        states.get("cancelled", False),
        states.get("tool_not_found", False),
        {},
    )


def test_runs_identical_registry_command_with_phase_and_sha(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def execute(arguments: tuple[str, ...], **_: object) -> CommandResult:
        calls.append(arguments)
        return _result(arguments)

    commands = {"checkout-pytest": ("python", "-m", "pytest", "tests/test_checkout.py")}
    selection = _selection("checkout-pytest")
    baseline = run_phase_checks(
        "baseline",
        "a" * 40,
        [selection],
        commands,
        repository=tmp_path,
        artifact_dir=tmp_path,
        executor=execute,
    )
    candidate = run_phase_checks(
        "candidate",
        "b" * 40,
        [selection],
        commands,
        repository=tmp_path,
        artifact_dir=tmp_path,
        executor=execute,
    )

    assert calls == [commands["checkout-pytest"], commands["checkout-pytest"]]
    assert (baseline[0].phase, baseline[0].commit_sha, baseline[0].outcome) == (
        "baseline",
        "a" * 40,
        "passed",
    )
    assert candidate[0].phase == "candidate"


@pytest.mark.parametrize(
    ("result", "outcome"),
    [
        (_result(("pytest",), 1), "test_failed"),
        (_result(("pytest",), None, timed_out=True), "command_failed"),
    ],
)
def test_separates_test_and_command_failures(
    tmp_path: Path, result: CommandResult, outcome: str
) -> None:
    checks = run_phase_checks(
        "candidate",
        "b" * 40,
        [_selection("tests")],
        {"tests": ("pytest",)},
        repository=tmp_path,
        artifact_dir=tmp_path,
        executor=lambda *_args, **_kwargs: result,
    )
    assert checks[0].outcome == outcome


def test_skips_k6_for_the_repeated_experiment(tmp_path: Path) -> None:
    checks = run_phase_checks(
        "fix",
        "c" * 40,
        [_selection("checkout-k6", "k6")],
        {},
        repository=tmp_path,
        artifact_dir=tmp_path,
        executor=lambda *_args, **_kwargs: _result(("k6",)),
    )
    assert checks == ()


def test_rejects_unregistered_commands(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown workload"):
        run_phase_checks(
            "candidate",
            "b" * 40,
            [_selection("invented")],
            {},
            repository=tmp_path,
            artifact_dir=tmp_path,
        )
