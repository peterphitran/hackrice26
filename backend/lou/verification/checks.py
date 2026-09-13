"""Phase-aware execution of checked-in verification workloads."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from contracts import WorkloadSelection
from lou.execution import CommandResult, run_command

CheckOutcome = Literal["passed", "test_failed", "command_failed"]
CommandExecutor = Callable[..., CommandResult]


@dataclass(frozen=True)
class PhaseCheck:
    phase: Literal["baseline", "candidate", "fix"]
    commit_sha: str
    workload_id: str
    arguments: tuple[str, ...]
    outcome: CheckOutcome
    result: CommandResult


def run_phase_checks(
    phase: Literal["baseline", "candidate", "fix"],
    commit_sha: str,
    selections: Sequence[WorkloadSelection],
    commands: Mapping[str, Sequence[str]],
    *,
    repository: Path,
    artifact_dir: Path,
    timeout_seconds: float = 120,
    executor: CommandExecutor = run_command,
) -> tuple[PhaseCheck, ...]:
    """Run the registry command selected for each non-load workload."""
    if len(commit_sha) != 40:
        raise ValueError("commit_sha must be the exact 40-character SHA")

    checks: list[PhaseCheck] = []
    for selection in selections:
        if selection.workload_type == "k6":
            continue
        try:
            arguments = tuple(commands[selection.workload_id])
        except KeyError as error:
            raise ValueError(f"unknown workload: {selection.workload_id}") from error
        result = executor(
            arguments,
            artifact_dir=artifact_dir / selection.workload_id,
            timeout_seconds=timeout_seconds,
            cwd=repository,
        )
        command_failed = result.tool_not_found or result.timed_out or result.cancelled
        outcome: CheckOutcome = (
            "command_failed"
            if command_failed
            else "passed"
            if result.exit_code == 0
            else "test_failed"
        )
        checks.append(
            PhaseCheck(phase, commit_sha, selection.workload_id, arguments, outcome, result)
        )
    return tuple(checks)
