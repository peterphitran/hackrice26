from datetime import UTC, datetime
from typing import Any

import pytest

from lou.deployment.adapters import SqlAlchemyTraceLookup, SqlAlchemyVerificationLookup
from lou.persistence.interfaces import EvidenceInput
from lou.persistence.repositories import SqlAlchemyResultRepository

pytestmark = pytest.mark.integration

TRACE_ID = "0" * 32


def _observation(run_id: Any, trace_id: str, observation_id: str) -> EvidenceInput:
    return EvidenceInput(
        analysis_run_id=run_id,
        phase="candidate",
        kind="runtime-observation",
        source="lou.telemetry",
        contract_id=observation_id,
        collected_at=datetime.now(UTC),
        summary={"trace_id": trace_id, "observation_id": observation_id, "span_id": "a" * 16},
    )


def test_trace_lookup_resolves_a_recorded_trace_to_its_analysis_run(seeded_db: Any) -> None:
    session_factory, run, _ = seeded_db
    repository = SqlAlchemyResultRepository(session_factory)
    repository.append_evidence(_observation(run.id, TRACE_ID, "telemetry-1"))
    repository.append_evidence(_observation(run.id, TRACE_ID, "telemetry-2"))

    fact = SqlAlchemyTraceLookup(session_factory).get(TRACE_ID)

    assert fact is not None
    assert fact.analysis_run_id == str(run.id)
    assert fact.commit_sha == run.candidate_commit_sha
    assert fact.observation_count == 2


def test_trace_lookup_returns_nothing_for_an_unrecorded_trace(seeded_db: Any) -> None:
    session_factory, run, _ = seeded_db
    SqlAlchemyResultRepository(session_factory).append_evidence(
        _observation(run.id, TRACE_ID, "telemetry-1")
    )

    assert SqlAlchemyTraceLookup(session_factory).get("f" * 32) is None


def test_trace_lookup_ignores_evidence_that_is_not_a_runtime_observation(seeded_db: Any) -> None:
    session_factory, run, _ = seeded_db
    SqlAlchemyResultRepository(session_factory).append_evidence(
        EvidenceInput(
            analysis_run_id=run.id,
            phase="candidate",
            kind="phase-verification",
            source="lou.application.live",
            collected_at=datetime.now(UTC),
            summary={"trace_id": TRACE_ID},
        )
    )

    assert SqlAlchemyTraceLookup(session_factory).get(TRACE_ID) is None


def test_verification_lookup_returns_nothing_for_an_unrecorded_run(seeded_db: Any) -> None:
    session_factory, _, _ = seeded_db

    assert SqlAlchemyVerificationLookup(session_factory).get("verification-absent") is None
