from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from apps.cli import main as cli
from lou.deployment import (
    DeploymentJournal,
    DeploymentService,
    InMemoryDeploymentAdapter,
    InMemoryVerificationLookup,
    VerificationFact,
)


def test_deploy_command_outputs_a_promoted_staging_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = InMemoryVerificationLookup()
    lookup.record(VerificationFact("verification-1", "run-1", "a" * 40, "passed"))
    service = DeploymentService(
        DeploymentJournal(tmp_path),
        InMemoryDeploymentAdapter(),
        clock=lambda: datetime(2026, 9, 13, tzinfo=UTC),
        verifications=lookup,
    )
    monkeypatch.setattr(cli, "_deployment_service", lambda: service)

    result = CliRunner().invoke(
        cli.app,
        [
            "deploy",
            "--run",
            "run-1",
            "--commit",
            "a" * 40,
            "--release",
            "release-cli",
            "--verification-run",
            "verification-1",
            "--trace-id",
            "0" * 32,
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert '"status": "promoted"' in result.stdout


def test_deploy_command_pauses_when_health_is_asserted_without_linked_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = DeploymentService(
        DeploymentJournal(tmp_path),
        InMemoryDeploymentAdapter(),
        clock=lambda: datetime(2026, 9, 13, tzinfo=UTC),
    )
    monkeypatch.setattr(cli, "_deployment_service", lambda: service)

    result = CliRunner().invoke(
        cli.app,
        [
            "deploy",
            "--run",
            "run-1",
            "--commit",
            "a" * 40,
            "--release",
            "unlinked",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert '"status": "paused"' in result.stdout


def test_deploy_command_pauses_when_telemetry_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = DeploymentService(
        DeploymentJournal(tmp_path),
        InMemoryDeploymentAdapter(),
        clock=lambda: datetime(2026, 9, 13, tzinfo=UTC),
    )
    monkeypatch.setattr(cli, "_deployment_service", lambda: service)

    result = CliRunner().invoke(
        cli.app,
        [
            "deploy",
            "--run",
            "run-1",
            "--commit",
            "a" * 40,
            "--release",
            "release-pause",
            "--telemetry-missing",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert '"status": "paused"' in result.stdout
