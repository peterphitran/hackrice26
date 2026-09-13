from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from lou.persistence.interfaces import (
    EvidenceInput,
    FindingInput,
    PersistenceError,
    VerificationRunInput,
)
from lou.persistence.models import EvidenceRecord, VerificationRunRecord
from lou.persistence.repositories import SqlAlchemyResultRepository

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
