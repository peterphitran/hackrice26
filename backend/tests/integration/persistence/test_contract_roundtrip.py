import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from contracts import Evidence, Finding, VerificationResult
from lou.persistence.interfaces import EvidenceInput, FindingInput, VerificationRunInput
from lou.persistence.models import EvidenceRecord, FindingRecord, VerificationRunRecord
from lou.persistence.repositories import SqlAlchemyResultRepository

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parents[3] / "contracts" / "fixtures"


def test_shared_contract_fixtures_round_trip(seeded_db: Any) -> None:
    session_factory, run, workload_id = seeded_db
    finding_payload = json.loads((FIXTURES / "finding.json").read_text())
    evidence_payload = json.loads((FIXTURES / "evidence.json").read_text())
    evidence_baseline_payload = json.loads((FIXTURES / "evidence_baseline.json").read_text())
    verification_payload = json.loads((FIXTURES / "verification_result.json").read_text())
    verification_baseline_payload = json.loads(
        (FIXTURES / "verification_result_baseline.json").read_text()
    )
    finding_contract = Finding.model_validate(finding_payload)
    evidence_contract = Evidence.model_validate(evidence_payload)
    evidence_baseline_contract = Evidence.model_validate(evidence_baseline_payload)
    verification_contract = VerificationResult.model_validate(verification_payload)
    verification_baseline_contract = VerificationResult.model_validate(
        verification_baseline_payload
    )
    repository = SqlAlchemyResultRepository(session_factory)

    finding_id, _ = repository.add_finding(
        FindingInput(
            analysis_run_id=run.id,
            fingerprint=finding_contract.fingerprint,
            source=finding_contract.source,
            category=finding_contract.category,
            severity=finding_contract.severity,
            confidence=finding_contract.confidence,
            phase=finding_contract.phase,
            title=finding_contract.title,
            message=finding_contract.message,
            file_path=finding_contract.file_path,
            symbol_key=finding_contract.symbol_key,
            metadata=finding_contract.metadata,
        )
    )
    verification_id = repository.append_verification(
        VerificationRunInput(
            analysis_run_id=run.id,
            phase=verification_contract.phase,
            commit_sha=verification_contract.commit_sha,
            status="queued",
            workload_id=workload_id,
            metrics=dict(verification_contract.metrics),
            artifact_uri=verification_contract.artifact_uri,
            artifact_sha256="roundtrip-sha256",
        )
    )
    baseline_verification_id = repository.append_verification(
        VerificationRunInput(
            analysis_run_id=run.id,
            phase=verification_baseline_contract.phase,
            commit_sha=verification_baseline_contract.commit_sha,
            status="queued",
            workload_id=workload_id,
            metrics=dict(verification_baseline_contract.metrics),
        )
    )
    repository.complete_verification(
        baseline_verification_id, verification_baseline_contract.status
    )
    evidence_id = repository.append_evidence(
        EvidenceInput(
            analysis_run_id=run.id,
            finding_id=finding_id,
            phase=evidence_contract.phase,
            kind=evidence_contract.kind,
            source=evidence_contract.source,
            collected_at=evidence_contract.collected_at,
            summary=evidence_contract.summary,
            artifact_uri=evidence_contract.artifact_uri,
            artifact_sha256=evidence_contract.artifact_sha256,
        )
    )
    baseline_evidence_id = repository.append_evidence(
        EvidenceInput(
            analysis_run_id=run.id,
            phase=evidence_baseline_contract.phase,
            kind=evidence_baseline_contract.kind,
            source=evidence_baseline_contract.source,
            collected_at=evidence_baseline_contract.collected_at,
            summary=evidence_baseline_contract.summary,
            artifact_uri=evidence_baseline_contract.artifact_uri,
            artifact_sha256=evidence_baseline_contract.artifact_sha256,
        )
    )

    with session_factory() as session:
        stored_finding = session.get(FindingRecord, finding_id)
        stored_verification = session.get(VerificationRunRecord, verification_id)
        stored_evidence = session.get(EvidenceRecord, evidence_id)
        assert stored_finding is not None
        assert stored_finding.confidence == finding_contract.confidence
        assert stored_finding.details == finding_contract.metadata
        assert stored_verification is not None
        assert stored_verification.aggregate_metrics == verification_contract.metrics
        assert session.get(VerificationRunRecord, baseline_verification_id) is not None
        assert stored_evidence is not None
        assert stored_evidence.summary == evidence_contract.summary
        assert stored_evidence.artifact_uri == evidence_contract.artifact_uri
        assert session.get(EvidenceRecord, baseline_evidence_id) is not None
        assert isinstance(
            datetime.fromisoformat(evidence_contract.collected_at.isoformat()), datetime
        )
