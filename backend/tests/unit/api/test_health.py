from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.main import RunStatusView, create_app
from lou.application import AnalysisResult
from lou.persistence.interfaces import AnalysisRunInput, AnalysisRunView
from lou.reporting import EvidenceReport, ReportError


class FakeService:
    def __init__(self, result: AnalysisResult) -> None:
        self.result = result
        self.requests: list[object] = []

    def run(self, request: object) -> AnalysisResult:
        self.requests.append(request)
        return self.result


class FakeReportReader:
    def __init__(self, report: EvidenceReport | Exception) -> None:
        self.report = report

    def read(self, _: str) -> EvidenceReport:
        if isinstance(self.report, Exception):
            raise self.report
        return self.report


def test_health_returns_application_identity() -> None:
    response = TestClient(create_app()).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "lou", "version": "0.1.0"}


def test_create_analysis_preflights_git_and_uses_injected_service(tmp_path: Path) -> None:
    base, candidate = _repository_revisions(tmp_path)
    run_id = str(uuid4())
    service = FakeService(AnalysisResult(run_id, "succeeded", False, ("validate", "finalize")))
    client = TestClient(create_app(service_factory=lambda: service))

    response = client.post(
        "/analysis",
        json={
            "schema_version": "1",
            "repository_path": str(tmp_path),
            "base_revision": base,
            "candidate_revision": candidate,
        },
    )

    assert response.status_code == 202
    assert response.json() == {
        "schema_version": "1",
        "analysis_run_id": run_id,
        "status": "succeeded",
        "reused": False,
        "stages": ["validate", "finalize"],
        "message": None,
        "decision_id": None,
    }
    assert len(service.requests) == 1


def test_invalid_analysis_input_does_not_invoke_service() -> None:
    service = FakeService(AnalysisResult(str(uuid4()), "succeeded", False, ()))
    client = TestClient(create_app(service_factory=lambda: service))

    response = client.post(
        "/analysis",
        json={
            "schema_version": "1",
            "repository_path": "/does/not/exist",
            "base_revision": "good",
            "candidate_revision": "candidate",
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_analysis_request"
    assert service.requests == []


def test_force_request_requires_a_token_before_service_is_called(tmp_path: Path) -> None:
    base, candidate = _repository_revisions(tmp_path)
    service = FakeService(AnalysisResult(str(uuid4()), "succeeded", False, ()))
    client = TestClient(create_app(service_factory=lambda: service))

    response = client.post(
        "/analysis",
        json={
            "schema_version": "1",
            "repository_path": str(tmp_path),
            "base_revision": base,
            "candidate_revision": candidate,
            "force_new_run": True,
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_analysis_request"
    assert service.requests == []


def test_analysis_status_and_report_are_read_only() -> None:
    run_id = uuid4()
    timestamp = datetime(2026, 9, 13, tzinfo=UTC)
    run = AnalysisRunView(
        id=run_id,
        input=AnalysisRunInput(uuid4(), "a" * 40, "b" * 40, "api", "key"),
        status="succeeded",
        started_at=timestamp,
        completed_at=timestamp,
    )
    report = EvidenceReport(
        {
            "schema_version": "1",
            "run": {
                "analysis_run_id": str(run_id),
                "status": "succeeded",
                "base_commit_sha": "a" * 40,
                "candidate_commit_sha": "b" * 40,
            },
            "repository": {"name": "fixture", "provider": "local"},
            "context": {},
            "workloads": [],
            "verification_runs": [],
            "findings": [],
            "evidence": [],
            "decision": None,
        }
    )
    client = TestClient(
        create_app(
            run_status_reader=lambda identifier: RunStatusView(run, "decision-1")
            if identifier == run_id
            else None,
            report_reader_factory=lambda: FakeReportReader(report),
        )
    )

    status_response = client.get(f"/analysis/{run_id}")
    report_response = client.get(f"/analysis/{run_id}/report")
    markdown_response = client.get(f"/analysis/{run_id}/report?format=markdown")

    assert status_response.status_code == 200
    assert status_response.json()["decision_id"] == "decision-1"
    assert status_response.json()["status"] == "succeeded"
    assert report_response.status_code == 200
    assert report_response.text == report.render_json()
    assert markdown_response.status_code == 200
    assert markdown_response.text == report.render_markdown()


def test_missing_or_tampered_report_has_a_safe_error() -> None:
    run_id = uuid4()
    missing = TestClient(
        create_app(
            report_reader_factory=lambda: FakeReportReader(
                ReportError("analysis run was not found")
            )
        )
    )
    tampered = TestClient(
        create_app(
            report_reader_factory=lambda: FakeReportReader(ReportError("artifact SHA-256 mismatch"))
        )
    )

    assert missing.get(f"/analysis/{run_id}/report").status_code == 404
    response = tampered.get(f"/analysis/{run_id}/report")
    assert response.status_code == 409
    assert response.json()["code"] == "report_artifact_unavailable"


def _repository_revisions(repository: Path) -> tuple[str, str]:
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test User")
    source = repository / "module.py"
    source.write_text("value = 1\n", encoding="utf-8")
    _git(repository, "add", "module.py")
    _git(repository, "commit", "-m", "base")
    base = _git(repository, "rev-parse", "HEAD")
    source.write_text("value = 2\n", encoding="utf-8")
    _git(repository, "commit", "-am", "candidate")
    return base, _git(repository, "rev-parse", "HEAD")


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repository, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()
