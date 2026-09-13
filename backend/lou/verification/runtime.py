"""Run the selected fixture workloads in containers built from an exact worktree."""

from __future__ import annotations

import json
import re
import shlex
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import uuid4

from contracts import AnalysisJob, WorkloadSelection
from lou.execution import CommandResult, run_command
from lou.loadtest import run_k6_experiment
from lou.sandbox import SandboxLimits, run_sandbox
from lou.sandbox.docker import FIXTURE_NETWORK
from lou.verification.checks import run_phase_checks
from lou.verification.fix import PhaseObservations

_IMAGE = "lou-ev007-runtime:v1"
_SUMMARY_MARKER = "__LOU_K6_SUMMARY__"
_SAFE_URL = re.compile(r"[A-Za-z0-9:/@._%?=&+-]+\Z")
_BASE_DOCKERFILE = """\
FROM python:3.13-slim
COPY --from=grafana/k6:0.54.0 /usr/bin/k6 /usr/local/bin/k6
RUN python -m pip install --no-cache-dir \
  'fastapi>=0.115,<1.0' 'psycopg[binary]>=3.2,<4.0' \
  'uvicorn>=0.30,<1.0' 'httpx>=0.27,<1.0' 'pytest>=8.2,<9.0'
"""
_SOURCE_DOCKERFILE = """\
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
WORKDIR /app
COPY store /app/store
COPY tests /app/tests
COPY loadtests /app/loadtests
USER 65532:65532
"""


def _docker_build(
    context: Path, dockerfile: Path, tag: str, *, build_arg: str | None = None
) -> None:
    command = ["docker", "build", "--quiet", "--tag", tag, "--file", str(dockerfile)]
    if build_arg is not None:
        command.extend(["--build-arg", f"BASE_IMAGE={build_arg}"])
    result = run_command(
        command + [str(context)], artifact_dir=dockerfile.parent / "build", timeout_seconds=300
    )
    if result.tool_not_found or result.timed_out or result.exit_code != 0:
        raise RuntimeError(f"Docker image build failed: {result.stderr.text[:400]}")


def _container_result(
    image: str,
    arguments: Sequence[str],
    artifact_dir: Path,
    limits: SandboxLimits,
    *,
    network: str = "none",
) -> CommandResult:
    outcome = run_sandbox(
        image, arguments, artifact_dir=artifact_dir, network=network, limits=limits
    )
    if outcome.execute is None or outcome.create.exit_code != 0:
        return replace(outcome.create, tool_not_found=True)
    return outcome.execute


def _remove_app(name: str, artifact_dir: Path) -> None:
    """Retain application logs before removing an ephemeral runtime container."""

    run_command(["docker", "logs", name], artifact_dir=artifact_dir / "logs", timeout_seconds=30)
    run_command(
        ["docker", "rm", "--force", name],
        artifact_dir=artifact_dir / "cleanup",
        timeout_seconds=30,
    )


def _is_running(name: str, artifact_dir: Path) -> bool:
    inspected = run_command(
        ["docker", "inspect", "--format", "{{.State.Running}}", name],
        artifact_dir=artifact_dir,
        timeout_seconds=5,
    )
    return inspected.exit_code == 0 and inspected.stdout.text.strip() == "true"


def _start_app(
    image: str, name: str, limits: SandboxLimits, database_url: str, artifact_dir: Path
) -> None:
    start = run_command(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            name,
            "--network",
            FIXTURE_NETWORK,
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
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--env",
            f"BROKEN_STORE_DATABASE_URL={database_url}",
            image,
            "python",
            "-m",
            "uvicorn",
            "store.app:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ],
        artifact_dir=artifact_dir / "start",
        timeout_seconds=30,
    )
    if start.tool_not_found or start.exit_code != 0:
        raise RuntimeError(f"Fix app could not start: {start.stderr.text[:400]}")
    for attempt in range(30):
        ready = run_command(
            [
                "docker",
                "exec",
                name,
                "python",
                "-c",
                'import socket; socket.create_connection(("127.0.0.1", 8000), timeout=1).close()',
            ],
            artifact_dir=artifact_dir / "ready" / str(attempt),
            timeout_seconds=5,
        )
        if ready.exit_code == 0:
            return
        if not _is_running(name, artifact_dir / "inspect" / str(attempt)):
            raise RuntimeError("Fix app exited before becoming ready; see app/logs artifacts")
        time.sleep(0.2)
    raise RuntimeError("Fix app did not become ready; see app/logs artifacts")


class DockerWorkloadRunner:
    """Build only trusted Dockerfiles; source is copied without running build hooks."""

    def __init__(self, *, database_url: str) -> None:
        if _SAFE_URL.fullmatch(database_url) is None or not database_url.startswith(
            "postgresql://"
        ):
            raise ValueError("database_url must be a plain PostgreSQL URL")
        self.database_url = database_url

    def run(
        self,
        *,
        repository: Path,
        commit_sha: str,
        selections: Sequence[WorkloadSelection],
        commands: Mapping[str, Sequence[str]],
        job: AnalysisJob,
        artifact_dir: Path,
    ) -> PhaseObservations:
        return self.run_phase(
            "fix",
            repository=repository,
            commit_sha=commit_sha,
            selections=selections,
            commands=commands,
            job=job,
            artifact_dir=artifact_dir,
        )

    def run_phase(
        self,
        phase: Literal["baseline", "candidate", "fix"],
        *,
        repository: Path,
        commit_sha: str,
        selections: Sequence[WorkloadSelection],
        commands: Mapping[str, Sequence[str]],
        job: AnalysisJob,
        artifact_dir: Path,
    ) -> PhaseObservations:
        timeout = float(job.resource_limits.get("timeout_seconds", 120))
        limits = SandboxLimits(
            cpus=float(job.resource_limits.get("cpus", 1)),
            memory_mb=int(job.resource_limits.get("memory_mb", 512)),
            pids=int(job.resource_limits.get("pids", 128)),
            disk_mb=int(job.resource_limits.get("disk_mb", 64)),
            timeout_seconds=timeout,
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="lou-runtime-") as temporary:
            build = Path(temporary)
            base = build / "base.Dockerfile"
            base.write_text(_BASE_DOCKERFILE, encoding="utf-8")
            _docker_build(build, base, _IMAGE)
            source = build / "source.Dockerfile"
            source.write_text(_SOURCE_DOCKERFILE, encoding="utf-8")
            image = f"lou-ev007-fix:{commit_sha[:16]}"
            _docker_build(repository, source, image, build_arg=_IMAGE)
            try:

                def execute_check(
                    arguments: Sequence[str],
                    *,
                    artifact_dir: Path,
                    timeout_seconds: float,
                    cwd: Path,
                ) -> CommandResult:
                    return _container_result(image, arguments, artifact_dir, limits)

                checks = run_phase_checks(
                    phase,
                    commit_sha,
                    selections,
                    commands,
                    repository=repository,
                    artifact_dir=artifact_dir / "checks",
                    timeout_seconds=timeout,
                    executor=execute_check,
                )
                load_selections = [item for item in selections if item.workload_type == "k6"]
                if len(load_selections) > 1:
                    raise ValueError("only one k6 workload is supported per finalized plan")
                if not load_selections:
                    return PhaseObservations(checks, None)
                load_selection = load_selections[0]
                app_name = f"lou-ev007-app-{uuid4().hex[:12]}"
                _start_app(image, app_name, limits, self.database_url, artifact_dir / "app")

                def execute_k6(
                    arguments: Sequence[str],
                    *,
                    artifact_dir: Path,
                    timeout_seconds: float,
                    cwd: Path,
                    resource_metadata: object = None,
                ) -> CommandResult:
                    supplied = list(arguments)
                    summary_index = supplied.index("--summary-export") + 1
                    summary_path = Path(supplied[summary_index])
                    supplied[summary_index] = "/tmp/lou-summary.json"
                    supplied[-1] = f"/app/{load_selection.definition_path}"
                    command = shlex.join(supplied)
                    launch = (
                        f"{command}; result=$?; "
                        f"printf '\\n{_SUMMARY_MARKER}\\n'; "
                        "cat /tmp/lou-summary.json 2>/dev/null || true; exit $result"
                    )
                    result = replace(
                        _container_result(
                            image,
                            ["sh", "-c", launch],
                            artifact_dir,
                            limits,
                            network=FIXTURE_NETWORK,
                        ),
                        arguments=tuple(arguments),
                    )
                    if result.exit_code == 0 and not result.tool_not_found:
                        output_path = result.stdout.artifact_path
                        output = output_path.read_text() if output_path else result.stdout.text
                        if _SUMMARY_MARKER not in output:
                            return replace(result, tool_not_found=True)
                        try:
                            summary = json.loads(output.split(_SUMMARY_MARKER, 1)[1])
                        except json.JSONDecodeError:
                            return replace(result, tool_not_found=True)
                        (artifact_dir / "k6-raw-summary.json").write_text(
                            json.dumps(summary, sort_keys=True), encoding="utf-8"
                        )
                        if "WARMUP=1" in supplied:
                            return result
                        metrics = summary.get("metrics")
                        required = (
                            "http_req_duration",
                            "http_reqs",
                            "http_req_failed",
                            "database_queries",
                        )
                        if not isinstance(metrics, dict) or any(
                            key not in metrics or not isinstance(metrics[key], dict)
                            for key in required
                        ):
                            return replace(result, tool_not_found=True)
                        for key in required:
                            metric = metrics[key]
                            if "values" not in metric:
                                values = dict(metric)
                                if key == "http_req_failed":
                                    values["rate"] = values.get("value")
                                metric["values"] = values
                        summary_path.write_text(json.dumps(summary), encoding="utf-8")
                    return result

                try:
                    load = run_k6_experiment(
                        phase,
                        commit_sha,
                        load_selection,
                        repository=repository,
                        artifact_dir=artifact_dir / "load",
                        base_url=f"http://{app_name}:8000",
                        repetitions=int(job.resource_limits.get("k6_repetitions", 5)),
                        timeout_seconds=timeout,
                        executor=execute_k6,
                    )
                    return PhaseObservations(checks, load)
                finally:
                    _remove_app(app_name, artifact_dir / "app")
            finally:
                run_command(
                    ["docker", "image", "rm", "--force", image],
                    artifact_dir=artifact_dir / "cleanup",
                    timeout_seconds=30,
                )
