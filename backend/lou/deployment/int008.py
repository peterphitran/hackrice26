"""M8 staging-only Docker rehearsal for deterministic promotion and rollback."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.request import urlopen

from contracts import CanaryObservation, CanaryWindow, DeploymentTarget, Release, SLOPolicy
from lou.deployment import (
    DeploymentJournal,
    DeploymentService,
    InMemoryDeploymentAdapter,
    InMemoryTraceLookup,
    InMemoryVerificationLookup,
    TraceFact,
    VerificationFact,
)

_BACKEND = Path(__file__).resolve().parents[2]
_MARKER = "__LOU_INT008_RESULT__"
_NOW = datetime(2026, 9, 13, 12, tzinfo=UTC)


def _healthy_staging() -> bool:
    try:
        with urlopen("http://127.0.0.1:8088/", timeout=2) as response:  # noqa: S310 - fixed local rehearsal URL
            return int(response.status) == 200
    except (URLError, OSError):
        return False


def _rehearse(root: Path) -> dict[str, object]:
    adapter = InMemoryDeploymentAdapter()
    lookup = InMemoryVerificationLookup()
    traces = InMemoryTraceLookup()
    service = DeploymentService(
        DeploymentJournal(root),
        adapter,
        clock=lambda: _NOW,
        verifications=lookup,
        traces=traces,
    )
    policy = SLOPolicy(
        policy_revision="int008-v1",
        max_error_rate=0.05,
        max_latency_ms=300,
        minimum_samples=5,
        telemetry_max_age_seconds=60,
    )

    def release(release_id: str) -> Release:
        return Release(
            release_id=release_id,
            repository_id="broken-store",
            commit_sha="a" * 40,
            analysis_run_id="int008-analysis",
            target=DeploymentTarget(
                environment="staging", adapter="local", namespace="lou", service="staging-demo"
            ),
            requested_by="int008",
        )

    def window(release_id: str) -> CanaryWindow:
        return CanaryWindow(
            release_id=release_id,
            started_at=_NOW - timedelta(minutes=5),
            deadline_at=_NOW,
            minimum_samples=5,
        )

    good = release("int008-good")
    lookup.record(
        VerificationFact("verification-good", good.analysis_run_id, good.commit_sha, "passed")
    )
    traces.record(TraceFact("0" * 32, good.analysis_run_id, good.commit_sha, 12))
    service.release(good)
    promoted = service.observe(
        release_id=good.release_id,
        window=window(good.release_id),
        observation=CanaryObservation(
            release_id=good.release_id,
            observed_at=_NOW,
            sample_count=5,
            error_rate=0.01,
            latency_ms=100,
            telemetry_available=True,
            telemetry_age_seconds=1,
            validation_passed=True,
        ),
        policy=policy,
        verification_run_ids=("verification-good",),
        trace_ids=("0" * 32,),
    )
    bad = release("int008-bad")
    lookup.record(
        VerificationFact("verification-bad", bad.analysis_run_id, bad.commit_sha, "passed")
    )
    traces.record(TraceFact("1" * 32, bad.analysis_run_id, bad.commit_sha, 12))
    service.release(bad)
    rolled_back = service.observe(
        release_id=bad.release_id,
        window=window(bad.release_id),
        observation=CanaryObservation(
            release_id=bad.release_id,
            observed_at=_NOW,
            sample_count=5,
            error_rate=0.25,
            latency_ms=100,
            telemetry_available=True,
            telemetry_age_seconds=1,
            validation_passed=True,
        ),
        policy=policy,
        verification_run_ids=("verification-bad",),
        trace_ids=("1" * 32,),
    )
    return {
        "healthy_promoted": promoted.status == "promoted",
        "bad_rolled_back": rolled_back.status == "rolled_back",
        "good_evidence_count": len(promoted.evidence),
        "bad_evidence_count": len(rolled_back.evidence),
        "actions": adapter.actions,
    }


def main() -> int:
    command = [
        "docker",
        "compose",
        "-p",
        "lou-int008",
        "-f",
        str(_BACKEND / "infra/compose.yaml"),
        "--profile",
        "staging",
        "up",
        "-d",
        "--wait",
        "staging-demo",
    ]
    try:
        subprocess.run(command, cwd=_BACKEND, check=True)
        with TemporaryDirectory(prefix="lou-int008-") as directory:
            payload = {"staging_available": _healthy_staging(), **_rehearse(Path(directory))}
    finally:
        subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                "lou-int008",
                "-f",
                str(_BACKEND / "infra/compose.yaml"),
                "--profile",
                "staging",
                "down",
                "--volumes",
            ],
            cwd=_BACKEND,
            check=False,
        )
    print(_MARKER + " " + json.dumps(payload, sort_keys=True))
    return (
        0
        if all(payload[key] for key in ("staging_available", "healthy_promoted", "bad_rolled_back"))
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
