"""Controlled lifecycle for local Docker fixture containers."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from lou.execution import CommandResult, run_command

FIXTURE_NETWORK = "lou-fixture"


@dataclass(frozen=True)
class SandboxLimits:
    cpus: float = 1.0
    memory_mb: int = 512
    pids: int = 128
    disk_mb: int = 64
    timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if min(self.cpus, self.memory_mb, self.pids, self.disk_mb, self.timeout_seconds) <= 0:
            raise ValueError("sandbox limits must be positive")


@dataclass(frozen=True)
class SandboxResult:
    name: str
    create: CommandResult
    execute: CommandResult | None
    collect: CommandResult | None
    destroy: CommandResult


def run_sandbox(
    image: str,
    command: Sequence[str] = (),
    *,
    artifact_dir: Path,
    network: str = "none",
    limits: SandboxLimits = SandboxLimits(),
    cancellation_event: threading.Event | None = None,
) -> SandboxResult:
    """Create, execute, inspect, and always destroy one hardened container."""
    if not image or image.startswith("-"):
        raise ValueError("image must be a Docker image reference, not an option")
    if isinstance(command, (str, bytes)) or not all(isinstance(item, str) for item in command):
        raise TypeError("command must be a sequence of strings")
    if network not in {"none", FIXTURE_NETWORK}:
        raise ValueError(f"network must be 'none' or {FIXTURE_NETWORK!r}")

    name = f"lou-sandbox-{uuid4().hex}"
    metadata: dict[str, str | int | float] = {
        "cpus": limits.cpus,
        "memory_mb": limits.memory_mb,
        "pids": limits.pids,
        "disk_mb": limits.disk_mb,
        "network": network,
    }
    create = run_command(
        [
            "docker",
            "create",
            "--name",
            name,
            "--label",
            "lou.sandbox=true",
            "--user",
            "65532:65532",
            "--cpus",
            str(limits.cpus),
            "--memory",
            f"{limits.memory_mb}m",
            "--pids-limit",
            str(limits.pids),
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,size={limits.disk_mb}m",
            "--network",
            network,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            image,
            *command,
        ],
        artifact_dir=artifact_dir,
        timeout_seconds=30,
        resource_metadata=metadata,
    )
    execute = None
    collect = None
    try:
        if create.exit_code == 0:
            execute = run_command(
                ["docker", "start", "--attach", name],
                artifact_dir=artifact_dir,
                timeout_seconds=limits.timeout_seconds,
                cancellation_event=cancellation_event,
                resource_metadata=metadata,
            )
            collect = run_command(
                [
                    "docker",
                    "inspect",
                    "--size",
                    "--format",
                    "{{json .State}} {{.SizeRw}}",
                    name,
                ],
                artifact_dir=artifact_dir,
                timeout_seconds=30,
                resource_metadata=metadata,
            )
    finally:
        destroy = run_command(
            ["docker", "rm", "--force", name],
            artifact_dir=artifact_dir,
            timeout_seconds=30,
            resource_metadata=metadata,
        )

    return SandboxResult(name, create, execute, collect, destroy)
