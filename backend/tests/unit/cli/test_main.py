import subprocess
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from apps.cli import main as cli
from contracts import VerificationResult
from lou.application.analysis import AnalysisRequest, AnalysisResult

app = cli.app


def test_version_prints_application_version() -> None:
    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_analyze_maps_resolved_revisions_to_the_application_service(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "lou-tests@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Lou Tests"], check=True)
    (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "base"], check=True)
    base = _git(repository, "rev-parse", "HEAD")
    (repository / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "commit", "-am", "candidate", "-q"], check=True)
    candidate = _git(repository, "rev-parse", "HEAD")
    service = _Service()
    monkeypatch.setattr(cli, "_build_service", lambda: service)

    result = CliRunner().invoke(
        app,
        ["analyze", "--repo", str(repository), "--base", base, "--candidate", candidate],
    )

    assert result.exit_code == 0
    assert service.request and service.request.base_commit_sha == base
    assert service.request.candidate_commit_sha == candidate
    assert "run: run-cli" in result.stdout


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments], check=True, capture_output=True, text=True
    ).stdout.strip()


class _Service:
    request: AnalysisRequest | None = None

    def run(self, request: AnalysisRequest) -> AnalysisResult:
        self.request = request
        return AnalysisResult("run-cli", "succeeded", False, ("validate", "finalize"))


def test_ci_exit_codes_follow_candidate_measurement() -> None:
    candidate = VerificationResult(
        verification_run_id="verify-run-candidate",
        analysis_run_id="run-cli",
        phase="candidate",
        commit_sha="a" * 40,
        status="failed",
    )
    assert (
        cli._exit_code(
            AnalysisResult("run-cli", "succeeded", False, (), verification_results=(candidate,)),
            True,
        )
        == 1
    )
    assert (
        cli._exit_code(
            AnalysisResult("run-cli", "inconclusive", False, (), verification_results=(candidate,)),
            True,
        )
        == 2
    )
    assert cli._exit_code(AnalysisResult("run-cli", "failed", False, ()), True) == 3
