"""Structured subprocess execution without shell interpolation."""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO, TypeAlias
from uuid import uuid4

ResourceValue: TypeAlias = str | int | float | bool | None


@dataclass(frozen=True)
class CommandOutput:
    """Bounded text plus an optional reference to the complete byte stream."""

    text: str
    byte_count: int
    truncated: bool
    artifact_path: Path | None
    artifact_sha256: str | None


@dataclass(frozen=True)
class CommandResult:
    """One complete, unambiguous command attempt."""

    arguments: tuple[str, ...]
    exit_code: int | None
    duration_seconds: float
    stdout: CommandOutput
    stderr: CommandOutput
    timed_out: bool
    cancelled: bool
    tool_not_found: bool
    resource_metadata: Mapping[str, ResourceValue]


class _OutputCollector:
    def __init__(self, limit: int, artifact_dir: Path, name: str) -> None:
        self._limit = limit
        self._artifact_dir = artifact_dir
        self._name = name
        self._buffer = bytearray()
        self._byte_count = 0
        self._digest = hashlib.sha256()
        self._artifact: BinaryIO | None = None
        self._artifact_path: Path | None = None

    def read(self, stream: BinaryIO) -> None:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            self._byte_count += len(chunk)
            self._digest.update(chunk)
            remaining = self._limit - len(self._buffer)
            if remaining > 0:
                self._buffer.extend(chunk[:remaining])
            if self._artifact is None and self._byte_count > self._limit:
                self._artifact_path = self._artifact_dir / self._name
                self._artifact = self._artifact_path.open("xb")
                self._artifact.write(self._buffer)
                self._artifact.write(chunk[remaining:] if remaining > 0 else chunk)
            elif self._artifact is not None:
                self._artifact.write(chunk)

    def finish(self) -> CommandOutput:
        if self._artifact is not None:
            self._artifact.close()
        truncated = self._byte_count > self._limit
        return CommandOutput(
            text=self._buffer.decode("utf-8", errors="replace"),
            byte_count=self._byte_count,
            truncated=truncated,
            artifact_path=self._artifact_path,
            artifact_sha256=self._digest.hexdigest() if truncated else None,
        )


def _empty_output() -> CommandOutput:
    return CommandOutput("", 0, False, None, None)


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        try:
            getattr(os, "killpg")(process.pid, getattr(signal, "SIGKILL"))
        except ProcessLookupError:
            pass

    if process.poll() is None:
        process.kill()
    process.wait()


def run_command(
    arguments: Sequence[str],
    *,
    artifact_dir: Path,
    timeout_seconds: float | None = None,
    max_output_bytes: int = 64 * 1024,
    cancellation_event: threading.Event | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    resource_metadata: Mapping[str, ResourceValue] | None = None,
) -> CommandResult:
    """Run an argument array and return its output and terminal state."""
    if isinstance(arguments, (str, bytes)) or not arguments:
        raise ValueError("arguments must be a non-empty sequence of strings")
    if not all(isinstance(argument, str) for argument in arguments):
        raise TypeError("every command argument must be a string")
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_output_bytes < 0:
        raise ValueError("max_output_bytes cannot be negative")

    command = tuple(arguments)
    metadata = MappingProxyType(dict(resource_metadata or {}))
    started = time.monotonic()
    if cancellation_event is not None and cancellation_event.is_set():
        return CommandResult(
            command,
            None,
            time.monotonic() - started,
            _empty_output(),
            _empty_output(),
            False,
            True,
            False,
            metadata,
        )

    artifact_dir.mkdir(parents=True, exist_ok=True)
    identifier = uuid4().hex
    stdout_collector = _OutputCollector(max_output_bytes, artifact_dir, f"{identifier}-stdout.log")
    stderr_collector = _OutputCollector(max_output_bytes, artifact_dir, f"{identifier}-stderr.log")
    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0

    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=os.name != "nt",
            creationflags=creation_flags,
        )
    except FileNotFoundError:
        return CommandResult(
            command,
            None,
            time.monotonic() - started,
            _empty_output(),
            _empty_output(),
            False,
            False,
            True,
            metadata,
        )

    assert process.stdout is not None
    assert process.stderr is not None
    readers = [
        threading.Thread(target=stdout_collector.read, args=(process.stdout,)),
        threading.Thread(target=stderr_collector.read, args=(process.stderr,)),
    ]
    for reader in readers:
        reader.start()

    timed_out = False
    cancelled = False
    deadline = started + timeout_seconds if timeout_seconds is not None else None
    while process.poll() is None:
        if cancellation_event is not None and cancellation_event.is_set():
            cancelled = True
            _terminate_process_tree(process)
            break
        if deadline is not None and time.monotonic() >= deadline:
            timed_out = True
            _terminate_process_tree(process)
            break
        time.sleep(0.01)

    for reader in readers:
        reader.join()

    return CommandResult(
        arguments=command,
        exit_code=process.returncode,
        duration_seconds=time.monotonic() - started,
        stdout=stdout_collector.finish(),
        stderr=stderr_collector.finish(),
        timed_out=timed_out,
        cancelled=cancelled,
        tool_not_found=False,
        resource_metadata=metadata,
    )
