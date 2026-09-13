from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from contracts import (
    AnalysisJob,
    Evidence,
    Finding,
    LouDecision,
    RepositoryChange,
    RepositoryContext,
    VerificationResult,
    WorkloadSelection,
)

FIXTURES_DIR = Path(__file__).parents[3] / "contracts" / "fixtures"


def test_contracts_serialize_with_schema_version() -> None:
    job = AnalysisJob(
        analysis_run_id="run-1",
        repository_id="repo-1",
        repository_path="fixtures/broken-store",
        base_commit_sha="good",
        candidate_commit_sha="n-plus-one",
    )

    assert job.model_dump(mode="json")["schema_version"] == "1"


def test_contract_metadata_is_explicit() -> None:
    change = RepositoryChange(
        repository_id="repo-1",
        base_commit_sha="good",
        candidate_commit_sha="candidate",
        metadata={"extractor": "python-ast"},
    )

    assert change.metadata["extractor"] == "python-ast"


def test_evidence_and_decision_support_hackathon_result() -> None:
    evidence = Evidence(
        evidence_id="evidence-1",
        analysis_run_id="run-1",
        phase="candidate",
        kind="k6-summary",
        source="k6",
        collected_at=datetime.now(UTC),
    )
    decision = LouDecision(
        decision_id="decision-1",
        analysis_run_id="run-1",
        debt_risk=0.8,
        remediation_risk=0.2,
        confidence=0.9,
        autonomy_level=2,
        action="generate_patch",
    )

    assert evidence.phase == "candidate"
    assert decision.action == "generate_patch"


def test_published_contract_fixtures_validate() -> None:
    fixtures: dict[str, type[BaseModel]] = {
        "analysis_job.json": AnalysisJob,
        "workload_selection.json": WorkloadSelection,
        "repository_context.json": RepositoryContext,
        "finding.json": Finding,
        "evidence.json": Evidence,
        "evidence_baseline.json": Evidence,
        "verification_result.json": VerificationResult,
        "verification_result_baseline.json": VerificationResult,
        "lou_decision.json": LouDecision,
    }

    for filename, model in fixtures.items():
        payload = (FIXTURES_DIR / filename).read_text()
        assert model.model_validate_json(payload).model_dump()["schema_version"] == "1"
