from datetime import UTC, datetime

from typer.testing import CliRunner

from apps.cli import main as cli
from lou.deployment import DeploymentJournal, DeploymentService, InMemoryDeploymentAdapter


def test_deploy_command_outputs_a_promoted_staging_release(tmp_path, monkeypatch) -> None:
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
            "release-cli",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert '"status": "promoted"' in result.stdout


def test_deploy_command_pauses_when_telemetry_is_missing(tmp_path, monkeypatch) -> None:
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
