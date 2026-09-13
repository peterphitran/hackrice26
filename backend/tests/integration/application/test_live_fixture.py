"""Opt-in Docker/PostgreSQL proof for the live `lou analyze` composition."""

from __future__ import annotations

import importlib.util
import os
from hashlib import sha256
from pathlib import Path

import pytest
from pytest import MonkeyPatch
from sqlalchemy import select
from typer.testing import CliRunner

from apps.cli.main import app
from lou.application import AnalysisRequest, build_fixture_service
from lou.core.settings import Settings, get_settings
from lou.persistence.database import create_session_factory
from lou.persistence.models import (
    AnalysisRunRecord,
    EvidenceRecord,
    FindingRecord,
    LouDecisionRecord,
    VerificationRunRecord,
)
from lou.reporting import EvidenceReportReader
from lou.repository import resolve_repository_revisions

pytestmark = pytest.mark.integration

FIXTURE_ROOT = Path(__file__).parents[3] / "fixtures" / "broken-store"
SEED_SCRIPT = FIXTURE_ROOT / "scripts" / "seed_fixture_repo.py"


@pytest.mark.skipif(
    os.getenv("RUN_LOU_LIVE_E2E") != "1",
    reason="set RUN_LOU_LIVE_E2E=1 with Docker, lou-fixture, and migrated lou_test",
)
def test_live_fixture_persists_two_phase_regression(tmp_path: Path) -> None:
    test_url = os.environ["LOU_TEST_DATABASE_URL"]
    fixture_url = os.environ.get(
        "LOU_FIXTURE_DATABASE_URL",
        "postgresql://lou_migrator:lou_migrator@postgres:5432/lou_test",
    )
    repository = tmp_path / "broken-store"
    _seed(repository)
    revisions = resolve_repository_revisions(
        repository_path=repository,
        base_revision="good",
        candidate_revision="n-plus-one",
    )
    settings = Settings(
        database_url=test_url,
        fixture_database_url=fixture_url,
        artifact_root=tmp_path / "artifacts",
    )
    service = build_fixture_service(settings)
    result = service.run(
        AnalysisRequest(
            repository_id=f"fixture-live-e2e-{sha256(str(repository).encode()).hexdigest()[:12]}",
            repository_path=revisions.repository_root,
            base_commit_sha=revisions.base_commit_sha,
            candidate_commit_sha=revisions.candidate_commit_sha,
        )
    )

    assert result.status == "succeeded"
    assert result.decision and result.decision.action == "report"
    candidate = next(item for item in result.verification_results if item.phase == "candidate")
    assert candidate.status == "failed"
    assert candidate.metrics["baseline_query_count"] == 2
    assert candidate.metrics["candidate_query_count"] == 51
    assert candidate.metrics["query_count_delta"] == 49

    # A new reader models process restart: it reads only persisted rows and
    # hashed artifacts, never the application service's in-memory state.
    report = EvidenceReportReader(create_session_factory(settings), settings.artifact_root).read(
        result.analysis_run_id
    )
    rendered = report.render_json()
    assert '"query_count_delta": 49.0' in rendered
    assert '"runtime-regression"' in rendered
    assert report.render_json() == rendered

    factory = create_session_factory(settings)
    with factory() as session:
        run = session.scalar(
            select(AnalysisRunRecord).where(AnalysisRunRecord.id == result.analysis_run_id)
        )
        assert run and run.status == "succeeded"
        assert session.scalars(
            select(VerificationRunRecord).where(VerificationRunRecord.analysis_run_id == run.id)
        ).all()
        assert session.scalars(
            select(FindingRecord).where(FindingRecord.analysis_run_id == run.id)
        ).all()
        assert session.scalars(
            select(EvidenceRecord).where(EvidenceRecord.analysis_run_id == run.id)
        ).all()
        assert session.scalar(
            select(LouDecisionRecord).where(LouDecisionRecord.analysis_run_id == run.id)
        )


@pytest.mark.skipif(
    os.getenv("RUN_LOU_LIVE_E2E") != "1",
    reason="set RUN_LOU_LIVE_E2E=1 with Docker, lou-fixture, and migrated lou_test",
)
def test_live_cli_prints_a_safe_measured_summary(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    repository = tmp_path / "broken-store"
    _seed(repository)
    monkeypatch.setenv("LOU_DATABASE_URL", os.environ["LOU_TEST_DATABASE_URL"])
    monkeypatch.setenv(
        "LOU_FIXTURE_DATABASE_URL",
        os.environ.get(
            "LOU_FIXTURE_DATABASE_URL",
            "postgresql://lou_migrator:lou_migrator@postgres:5432/lou_test",
        ),
    )
    monkeypatch.setenv("LOU_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(
            app,
            [
                "analyze",
                "--repo",
                str(repository),
                "--base",
                "good",
                "--candidate",
                "n-plus-one",
                "--output",
                "json",
            ],
        )
    finally:
        get_settings.cache_clear()

    assert result.exit_code == 0, result.stdout
    assert '"analysis_run_id"' in result.stdout
    assert '"query_count_delta": 49.0' in result.stdout
    assert "lou_migrator" not in result.stdout


def _seed(target: Path) -> None:
    module_spec = importlib.util.spec_from_file_location("seed_fixture_repo", SEED_SCRIPT)
    assert module_spec and module_spec.loader
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    module.seed(target)
