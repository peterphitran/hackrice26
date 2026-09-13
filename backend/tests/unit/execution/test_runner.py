from __future__ import annotations

import hashlib
import sys
import threading
import time
from pathlib import Path

import pytest

from lou.execution import run_command


def test_command_must_be_an_argument_array(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="sequence of strings"):
        run_command("echo unsafe", artifact_dir=tmp_path)


def test_timeout_terminates_process_tree(tmp_path: Path) -> None:
    marker = tmp_path / "child-survived"
    child = "import pathlib,sys,time;time.sleep(1);pathlib.Path(sys.argv[1]).touch()"
    parent = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]);"
        "time.sleep(10)"
    )

    result = run_command(
        [sys.executable, "-c", parent, child, str(marker)],
        artifact_dir=tmp_path,
        timeout_seconds=0.1,
    )
    time.sleep(1.1)

    assert result.timed_out is True
    assert result.cancelled is False
    assert not marker.exists()


def test_large_output_is_truncated_and_preserved(tmp_path: Path) -> None:
    output = b"x" * 10_000

    result = run_command(
        [sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'x'*10000)"],
        artifact_dir=tmp_path,
        max_output_bytes=100,
    )

    assert result.stdout.text == "x" * 100
    assert result.stdout.byte_count == len(output)
    assert result.stdout.truncated is True
    assert result.stdout.artifact_path is not None
    assert result.stdout.artifact_path.read_bytes() == output
    assert result.stdout.artifact_sha256 == hashlib.sha256(output).hexdigest()


def test_result_captures_both_streams_and_metadata(tmp_path: Path) -> None:
    result = run_command(
        [
            sys.executable,
            "-c",
            "import sys;print('out');print('err', file=sys.stderr)",
        ],
        artifact_dir=tmp_path,
        resource_metadata={"cpus": 1},
    )

    assert result.arguments[0] == sys.executable
    assert result.exit_code == 0
    assert result.duration_seconds >= 0
    assert result.stdout.text.splitlines() == ["out"]
    assert result.stderr.text.splitlines() == ["err"]
    assert result.resource_metadata == {"cpus": 1}


def test_missing_tool_is_distinct_from_nonzero_exit(tmp_path: Path) -> None:
    missing = run_command(
        ["lou-command-that-does-not-exist"],
        artifact_dir=tmp_path,
    )
    failed = run_command(
        [sys.executable, "-c", "raise SystemExit(7)"],
        artifact_dir=tmp_path,
    )

    assert missing.tool_not_found is True
    assert missing.exit_code is None
    assert failed.tool_not_found is False
    assert failed.exit_code == 7


def test_cancellation_terminates_command(tmp_path: Path) -> None:
    cancellation = threading.Event()
    timer = threading.Timer(0.1, cancellation.set)
    timer.start()
    try:
        result = run_command(
            [sys.executable, "-c", "import time;time.sleep(10)"],
            artifact_dir=tmp_path,
            cancellation_event=cancellation,
        )
    finally:
        timer.cancel()

    assert result.cancelled is True
    assert result.timed_out is False
