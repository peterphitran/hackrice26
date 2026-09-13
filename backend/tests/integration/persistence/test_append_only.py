from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from lou.persistence.interfaces import EvidenceInput, FindingInput, PersistenceError
from lou.persistence.models import EvidenceRecord
from lou.persistence.repositories import SqlAlchemyResultRepository

pytestmark = pytest.mark.integration


def test_evidence_has_no_update_or_delete_boundary(seeded_db: Any) -> None:
    session_factory, run, _ = seeded_db
    repository = SqlAlchemyResultRepository(session_factory)
    evidence_id = repository.append_evidence(
        EvidenceInput(run.id, "candidate", "test", "pytest", datetime.now(UTC))
    )

    assert evidence_id
    assert not hasattr(repository, "update_evidence")
    assert not hasattr(repository, "delete_evidence")
    with session_factory() as session:
        stored = session.get(EvidenceRecord, evidence_id)
        assert stored is not None and stored.source == "pytest"


def test_evidence_rejects_missing_and_cross_run_findings(seeded_db: Any) -> None:
    session_factory, run, _ = seeded_db
    repository = SqlAlchemyResultRepository(session_factory)
    finding_id, _ = repository.add_finding(
        FindingInput(
            analysis_run_id=run.id,
            fingerprint="sha256:cross-run",
            source="test",
            category="test",
            severity="low",
            confidence=0.5,
            phase="candidate",
            title="cross-run",
            message="cross-run",
        )
    )

    with pytest.raises(PersistenceError):
        repository.append_evidence(
            EvidenceInput(
                uuid4(), "candidate", "test", "pytest", datetime.now(UTC), finding_id=finding_id
            )
        )
    with pytest.raises(PersistenceError):
        repository.append_evidence(
            EvidenceInput(
                run.id, "candidate", "test", "pytest", datetime.now(UTC), finding_id=uuid4()
            )
        )
