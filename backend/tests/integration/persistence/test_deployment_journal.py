from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from contracts import DeploymentEvidence, DeploymentTarget, Release
from lou.deployment import DeploymentConflictError, SqlAlchemyDeploymentJournal
from lou.persistence.models import DeploymentJournalRecord

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 13, 12, tzinfo=UTC)
COMMIT_SHA = "a" * 40


@pytest.fixture()
def release_id() -> str:
    """A fresh ID per test: the journal is append-only and never cleaned up."""

    return f"release-{uuid4().hex}"


@pytest.fixture()
def journal(session_factory: Any) -> SqlAlchemyDeploymentJournal:
    return SqlAlchemyDeploymentJournal(session_factory)


def _release(release_id: str) -> Release:
    return Release(
        release_id=release_id,
        repository_id="demo",
        commit_sha=COMMIT_SHA,
        analysis_run_id="run-db",
        target=DeploymentTarget(
            environment="staging", adapter="local", namespace="lou", service="demo"
        ),
        requested_by="tester",
    )


def _evidence(release_id: str, evidence_id: str, event: str = "released") -> DeploymentEvidence:
    return DeploymentEvidence(
        evidence_id=evidence_id,
        release_id=release_id,
        analysis_run_id="run-db",
        commit_sha=COMMIT_SHA,
        event=event,  # type: ignore[arg-type]
        actor="tester",
        collected_at=NOW,
    )


def test_journal_round_trips_a_release_and_its_evidence(
    journal: SqlAlchemyDeploymentJournal, release_id: str
) -> None:
    journal.create(_release(release_id))
    journal.append(_evidence(release_id, "evidence-1"))
    result = journal.append(_evidence(release_id, "evidence-2", "promoted"))

    assert result.release == _release(release_id)
    assert [item.evidence_id for item in result.evidence] == ["evidence-1", "evidence-2"]
    assert result.status == "promoted"


def test_an_absent_release_reads_as_nothing(journal: SqlAlchemyDeploymentJournal) -> None:
    assert journal.get(f"never-{uuid4().hex}") is None


def test_stored_rows_carry_a_linked_hash_chain(
    journal: SqlAlchemyDeploymentJournal, release_id: str, session_factory: Any
) -> None:
    journal.create(_release(release_id))
    journal.append(_evidence(release_id, "evidence-1"))

    with session_factory() as session:
        rows = session.execute(
            select(
                DeploymentJournalRecord.sequence,
                DeploymentJournalRecord.kind,
                DeploymentJournalRecord.previous_hash,
                DeploymentJournalRecord.record_hash,
            )
            .where(DeploymentJournalRecord.release_id == release_id)
            .order_by(DeploymentJournalRecord.sequence)
        ).all()

    assert [row.sequence for row in rows] == [0, 1]
    assert [row.kind for row in rows] == ["release", "evidence"]
    assert rows[0].previous_hash == ""
    assert rows[1].previous_hash == rows[0].record_hash


def test_creating_and_appending_are_idempotent(
    journal: SqlAlchemyDeploymentJournal, release_id: str
) -> None:
    first = journal.create(_release(release_id))
    replay = journal.create(_release(release_id))
    journal.append(_evidence(release_id, "evidence-1"))
    repeated = journal.append(_evidence(release_id, "evidence-1"))

    assert not first.reused and replay.reused
    assert [item.evidence_id for item in repeated.evidence] == ["evidence-1"]


def test_a_release_id_cannot_be_reused_for_different_inputs(
    journal: SqlAlchemyDeploymentJournal, release_id: str
) -> None:
    journal.create(_release(release_id))

    with pytest.raises(DeploymentConflictError, match="different release inputs"):
        journal.create(_release(release_id).model_copy(update={"commit_sha": "c" * 40}))


def test_evidence_must_belong_to_its_release(
    journal: SqlAlchemyDeploymentJournal, release_id: str
) -> None:
    journal.create(_release(release_id))
    foreign = _evidence(release_id, "evidence-1").model_copy(update={"commit_sha": "c" * 40})

    with pytest.raises(DeploymentConflictError, match="does not match the release"):
        journal.append(foreign)


def test_a_second_writer_cannot_claim_a_taken_chain_position(
    journal: SqlAlchemyDeploymentJournal, release_id: str
) -> None:
    """A racing writer that read the same length must lose, not interleave silently."""

    journal.create(_release(release_id))
    journal.append(_evidence(release_id, "evidence-1"))
    racing: dict[str, object] = {
        "kind": "evidence",
        "evidence": _evidence(release_id, "evidence-racer").model_dump(mode="json"),
        "previous_hash": "",
        "record_hash": "f" * 64,
    }

    with pytest.raises(DeploymentConflictError, match="concurrently"):
        journal._write(release_id, 1, racing)


def test_the_application_role_cannot_delete_journal_rows(
    journal: SqlAlchemyDeploymentJournal, release_id: str, session_factory: Any
) -> None:
    """Append-only is enforced by grants, not only detected by the hash chain."""

    journal.create(_release(release_id))

    with pytest.raises(Exception, match="permission denied"):  # noqa: B017 - driver-specific
        with session_factory.begin() as session:
            session.execute(
                delete(DeploymentJournalRecord).where(
                    DeploymentJournalRecord.release_id == release_id
                )
            )
