from pathlib import Path

from typer.testing import CliRunner

from apps.cli.main import app


def test_version_prints_application_version() -> None:
    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_analyze_is_explicitly_not_implemented() -> None:
    result = CliRunner().invoke(
        app,
        ["analyze", "--repo", str(Path.cwd()), "--base", "good", "--candidate", "candidate"],
    )

    assert result.exit_code == 2
    assert "not wired yet" in result.stdout
