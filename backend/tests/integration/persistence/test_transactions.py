from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from lou.persistence.interfaces import (
    DecisionInput,
    EvidenceInput,
    FindingInput,
    PersistenceError,
    VerificationRunInput,
)
from lou.persistence.models import EvidenceRecord, FindingRecord, VerificationRunRecord
from lou.persistence.repositories import SqlAlchemyDecisionRepository, SqlAlchemyResultRepository

pytestmark = pytest.mark.integration


def test_verification_and_evidence_commit_together(seeded_db: Any) -> None:
    session_factory, run, workload_id = seeded_db
    results = SqlAlchemyResultRepository(session_factory)
    finding_id, _ = results.add_finding(
        FindingInput(
            analysis_run_id=run.id,
            fingerprint="sha256:transaction-finding",
            source="test",
            category="test",
            severity="high",
            confidence=0.9,
            phase="candidate",
            title="transaction finding",
            message="transaction evidence",
        )
    )
    verification_id = results.append_verification(
        VerificationRunInput(run.id, "candidate", "candidate-persistence", workload_id=workload_id)
    )
    evidence_ids = results.complete_with_evidence(
        verification_id,
        "failed",
        [
            EvidenceInput(
                analysis_run_id=run.id,
                finding_id=finding_id,
                phase="candidate",
                kind="test",
                source="pytest",
                collected_at=datetime.now(UTC),
                summary={"query_count": 2},
            )
        ],
    )

    with session_factory() as session:
        verification = session.get(VerificationRunRecord, verification_id)
        evidence = session.get(EvidenceRecord, evidence_ids[0])
        assert verification is not None and verification.status == "failed"
        assert evidence is not None and evidence.finding_id == finding_id


def test_invalid_evidence_rolls_back_verification_completion(seeded_db: Any) -> None:
    session_factory, run, _ = seeded_db
    results = SqlAlchemyResultRepository(session_factory)
    verification_id = results.append_verification(
        VerificationRunInput(run.id, "candidate", "candidate-persistence")
    )

    with pytest.raises(PersistenceError):
        results.complete_with_evidence(
            verification_id,
            "passed",
            [
                EvidenceInput(
                    analysis_run_id=run.id,
                    finding_id=uuid4(),
                    phase="candidate",
                    kind="test",
                    source="pytest",
                    collected_at=datetime.now(UTC),
                )
            ],
        )

    with session_factory() as session:
        verification = session.get(VerificationRunRecord, verification_id)
        evidence = session.scalars(
            select(EvidenceRecord).where(EvidenceRecord.analysis_run_id == run.id)
        ).all()
        assert verification is not None and verification.status == "queued"
        assert evidence == []


def test_bundle_persists_verification_finding_and_evidence_together(seeded_db: Any) -> None:
    session_factory, run, workload_id = seeded_db
    results = SqlAlchemyResultRepository(session_factory)

    persisted = results.persist_verification_bundle(
        VerificationRunInput(
            analysis_run_id=run.id,
            phase="candidate",
            commit_sha="candidate-persistence",
            status="failed",
            workload_id=workload_id,
            contract_id="verify-contract-1",
            metrics={"query_count_delta": 49},
        ),
        [
            FindingInput(
                analysis_run_id=run.id,
                fingerprint="sha256:bundle-finding",
                contract_id="finding-contract-1",
                source="test",
                category="runtime-regression",
                severity="high",
                confidence=1.0,
                phase="candidate",
                title="bundle finding",
                message="bundle evidence",
            )
        ],
        [
            EvidenceInput(
                analysis_run_id=run.id,
                phase="comparison",
                kind="differential-verification",
                source="test",
                contract_id="evidence-contract-1",
                collected_at=datetime.now(UTC),
                summary={"query_count_delta": 49},
            )
        ],
    )

    with session_factory() as session:
        verification = session.get(VerificationRunRecord, persisted.verification_id)
        finding = session.get(FindingRecord, persisted.finding_ids["finding-contract-1"])
        evidence = session.get(EvidenceRecord, persisted.evidence_ids["evidence-contract-1"])
        assert verification is not None and verification.contract_id == "verify-contract-1"
        assert finding is not None and finding.contract_id == "finding-contract-1"
        assert evidence is not None and evidence.contract_id == "evidence-contract-1"


def test_invalid_bundle_rolls_back_verification_finding_and_evidence(seeded_db: Any) -> None:
    session_factory, run, _ = seeded_db
    results = SqlAlchemyResultRepository(session_factory)

    with pytest.raises(PersistenceError):
        results.persist_verification_bundle(
            VerificationRunInput(
                analysis_run_id=run.id,
                phase="candidate",
                commit_sha="candidate-persistence",
                status="failed",
                contract_id="verify-contract-rollback",
            ),
            [
                FindingInput(
                    analysis_run_id=run.id,
                    fingerprint="sha256:rollback-finding",
                    source="test",
                    category="runtime-regression",
                    severity="high",
                    confidence=1.0,
                    phase="candidate",
                    title="rollback finding",
                    message="invalid artifact follows",
                )
            ],
            [
                EvidenceInput(
                    analysis_run_id=run.id,
                    phase="comparison",
                    kind="differential-verification",
                    source="test",
                    collected_at=datetime.now(UTC),
                    artifact_uri="file://missing-sha.json",
                )
            ],
        )

    with session_factory() as session:
        assert session.scalars(
            select(VerificationRunRecord).where(VerificationRunRecord.analysis_run_id == run.id)
        ).all() == []
        assert session.scalars(
            select(FindingRecord).where(FindingRecord.analysis_run_id == run.id)
        ).all() == []
        assert session.scalars(
            select(EvidenceRecord).where(EvidenceRecord.analysis_run_id == run.id)
        ).all() == []


def test_decision_is_durable_and_idempotent(seeded_db: Any) -> None:
    session_factory, run, _ = seeded_db
    decisions = SqlAlchemyDecisionRepository(session_factory)
    value = DecisionInput(
        analysis_run_id=run.id,
        decision_id="decision-contract-1",
        action="report",
        debt_risk=0.4,
        remediation_risk=None,
        confidence=0.5,
        autonomy_level=0,
        metadata={"policy_revision": "local-v1"},
    )

    created, existed = decisions.save(value)
    reused, existed_again = decisions.save(value)

    assert existed is False
    assert existed_again is True
    assert reused.id == created.id
    assert decisions.get_for_run(run.id) == created
