from __future__ import annotations

import threading
from pathlib import Path

import pytest

from lou.execution import CommandOutput, CommandResult
from lou.sandbox import SandboxLimits, run_sandbox
from lou.sandbox import docker as docker_module


def _result(arguments: list[str], *, exit_code: int = 0, **states: bool) -> CommandResult:
    output = CommandOutput("", 0, False, None, None)
    return CommandResult(
        tuple(arguments),
        exit_code,
        0.0,
        output,
        output,
        states.get("timed_out", False),
        states.get("cancelled", False),
        False,
        {},
    )


@pytest.mark.parametrize("terminal_state", ["success", "failure", "timeout", "cancellation"])
def test_sandbox_is_hardened_and_always_destroyed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, terminal_state: str
) -> None:
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **_: object) -> CommandResult:
        calls.append(arguments)
        if arguments[1] == "start":
            return _result(
                arguments,
                exit_code=0 if terminal_state == "success" else 1,
                timed_out=terminal_state == "timeout",
                cancelled=terminal_state == "cancellation",
            )
        return _result(arguments)

    monkeypatch.setattr(docker_module, "run_command", fake_run)
    result = run_sandbox(
        "broken-store:test",
        ["python", "-m", "pytest"],
        artifact_dir=tmp_path,
        network="lou-fixture",
        limits=SandboxLimits(cpus=0.5, memory_mb=256, pids=32, disk_mb=16),
        cancellation_event=threading.Event(),
    )

    create = calls[0]
    assert create[:2] == ["docker", "create"]
    assert ["--user", "65532:65532"] == create[create.index("--user") : create.index("--user") + 2]
    assert ["--network", "lou-fixture"] == create[
        create.index("--network") : create.index("--network") + 2
    ]
    assert "--cpus" in create and "--memory" in create and "--pids-limit" in create
    assert "--read-only" in create and "--tmpfs" in create
    assert "--cap-drop" in create and "no-new-privileges" in create
    assert not any("docker.sock" in argument for argument in create)
    assert calls[-1] == ["docker", "rm", "--force", result.name]


def test_create_failure_still_attempts_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **_: object) -> CommandResult:
        calls.append(arguments)
        return _result(arguments, exit_code=1 if arguments[1] == "create" else 0)

    monkeypatch.setattr(docker_module, "run_command", fake_run)
    result = run_sandbox("broken-store:test", artifact_dir=tmp_path)

    assert result.execute is None
    assert result.collect is None
    assert [arguments[1] for arguments in calls] == ["create", "rm"]


def test_limits_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        SandboxLimits(memory_mb=0)


@pytest.mark.parametrize("network", ["", "bridge", "host", "container:postgres", "internet"])
def test_non_fixture_networks_are_rejected(tmp_path: Path, network: str) -> None:
    with pytest.raises(ValueError, match="lou-fixture"):
        run_sandbox("broken-store:test", artifact_dir=tmp_path, network=network)


def test_image_cannot_be_parsed_as_a_docker_option(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not an option"):
        run_sandbox("--user=0", ["alpine", "id"], artifact_dir=tmp_path)


def test_fixture_network_is_internal() -> None:
    compose = (Path(__file__).parents[3] / "infra" / "compose.yaml").read_text()

    assert "name: lou-fixture" in compose
    assert "internal: true" in compose
