"""PostgreSQL acceptance tests for durable remediation orchestration state."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from lou.agents.orchestration import OrchestrationLimits, OrchestrationState
from lou.application.remediation_runs import RemediationExecutionService
from lou.persistence.interfaces import (
    InvalidRunTransitionError,
    RemediationAttemptInput,
    RemediationRunInput,
    RunConflictError,
)
from lou.persistence.models import AgentRunRecord, RemediationAttemptRecord
from lou.persistence.repositories import SqlAlchemyRemediationRunRepository

pytestmark = pytest.mark.integration


class _TwoStepDriver:
    """A deterministic state driver for the database composition acceptance test."""

    def __init__(self, analysis_run_id: str, fingerprint: str) -> None:
        self._analysis_run_id = analysis_run_id
        self._fingerprint = fingerprint
        self.calls = 0

    def start(self, limits: OrchestrationLimits | None = None) -> OrchestrationState:
        assert limits is not None
        return OrchestrationState(
            analysis_run_id=self._analysis_run_id,
            inputs_fingerprint=self._fingerprint,
            limits=limits,
            started_at_epoch=0.0,
        )

    def step(self, previous: OrchestrationState) -> OrchestrationState:
        self.calls += 1
        if previous.stage == "context":
            return previous.model_copy(update={"stage": "diagnose", "attempt_count": 1})
        return previous.model_copy(
            update={"stage": "stopped", "termination_reason": "verified"}
        )


def _run_input(analysis_run_id: Any, *, deduplication_key: str) -> RemediationRunInput:
    return RemediationRunInput(
        analysis_run_id=analysis_run_id,
        input_fingerprint="a" * 64,
        deduplication_key=deduplication_key,
        provider="mock",
        policy_revision="local-v1",
        limits={"max_attempts": 3, "max_tokens": 20_000},
    )


def test_remediation_run_is_deduplicated_and_snapshot_is_durable(seeded_db: Any) -> None:
    session_factory, analysis_run, _ = seeded_db
    repository = SqlAlchemyRemediationRunRepository(session_factory)
    value = _run_input(analysis_run.id, deduplication_key=f"remediation-{uuid4()}")

    created, existed = repository.create_or_get(value)
    reused, existed_again = repository.create_or_get(value)
    saved = repository.save_snapshot(
        created.id,
        status="running",
        stage="diagnose",
        snapshot={"state": "diagnose", "changed_symbols": ["store.checkout"]},
        attempt_count=1,
        tokens_spent=54,
        estimated_cost_usd=0.0,
    )

    assert existed is False
    assert existed_again is True
    assert reused.id == created.id
    assert saved.status == "running"
    assert saved.stage == "diagnose"
    assert saved.snapshot["changed_symbols"] == ["store.checkout"]
    assert saved.started_at is not None

    with session_factory() as session:
        record = session.get(AgentRunRecord, created.id)
        assert record is not None
        assert record.input_fingerprint == "a" * 64
        assert record.tokens_spent == 54


def test_remediation_attempts_are_append_only_and_idempotent(seeded_db: Any) -> None:
    session_factory, analysis_run, _ = seeded_db
    repository = SqlAlchemyRemediationRunRepository(session_factory)
    run, _ = repository.create_or_get(
        _run_input(analysis_run.id, deduplication_key=f"remediation-{uuid4()}")
    )
    first = RemediationAttemptInput(
        remediation_run_id=run.id,
        attempt_key="diagnose-1",
        attempt_number=1,
        stage="diagnose",
        outcome="candidate identified",
        agent_result={"diagnosis": "N+1 query"},
    )
    second = RemediationAttemptInput(
        remediation_run_id=run.id,
        attempt_key="validate-1",
        attempt_number=2,
        stage="validate",
        outcome="patch is safe",
        patch_sha256="b" * 64,
        validation={"status": "passed"},
    )

    persisted, existed = repository.append_attempt(first)
    replayed, replayed_existing = repository.append_attempt(first)
    repository.append_attempt(second)

    assert existed is False
    assert replayed_existing is True
    assert replayed.id == persisted.id
    assert [item.value.attempt_key for item in repository.list_attempts(run.id)] == [
        "diagnose-1",
        "validate-1",
    ]

    with pytest.raises(RunConflictError):
        repository.append_attempt(
            RemediationAttemptInput(
                remediation_run_id=run.id,
                attempt_key="diagnose-1",
                attempt_number=1,
                stage="diagnose",
                outcome="a different result",
            )
        )

    with session_factory() as session:
        records = session.scalars(
            select(RemediationAttemptRecord).where(RemediationAttemptRecord.agent_run_id == run.id)
        ).all()
        assert len(records) == 2


def test_completed_remediation_cannot_be_reopened(seeded_db: Any) -> None:
    session_factory, analysis_run, _ = seeded_db
    repository = SqlAlchemyRemediationRunRepository(session_factory)
    run, _ = repository.create_or_get(
        _run_input(analysis_run.id, deduplication_key=f"remediation-{uuid4()}")
    )
    completed = repository.save_snapshot(
        run.id,
        status="abandoned",
        stage="stopped",
        snapshot={"state": "stopped"},
        attempt_count=3,
        tokens_spent=200,
        estimated_cost_usd=0.0,
        termination_reason="attempt_limit",
    )

    assert completed.completed_at is not None
    with pytest.raises(InvalidRunTransitionError):
        repository.save_snapshot(
            run.id,
            status="running",
            stage="diagnose",
            snapshot={},
            attempt_count=3,
            tokens_spent=200,
            estimated_cost_usd=0.0,
        )


def test_stage_snapshot_and_attempt_rollback_together(seeded_db: Any) -> None:
    session_factory, analysis_run, _ = seeded_db
    repository = SqlAlchemyRemediationRunRepository(session_factory)
    run, _ = repository.create_or_get(
        _run_input(analysis_run.id, deduplication_key=f"remediation-{uuid4()}")
    )
    initial = RemediationAttemptInput(
        remediation_run_id=run.id,
        attempt_key="diagnose-1",
        attempt_number=1,
        stage="diagnose",
        outcome="recorded",
    )
    repository.persist_stage(
        run.id,
        status="running",
        stage="diagnose",
        snapshot={"state": "diagnose"},
        attempt_count=1,
        tokens_spent=1,
        estimated_cost_usd=0.0,
        attempt=initial,
    )

    with pytest.raises(RunConflictError):
        repository.persist_stage(
            run.id,
            status="running",
            stage="patch",
            snapshot={"state": "patch"},
            attempt_count=1,
            tokens_spent=2,
            estimated_cost_usd=0.0,
            attempt=RemediationAttemptInput(
                remediation_run_id=run.id,
                attempt_key="diagnose-1",
                attempt_number=1,
                stage="diagnose",
                outcome="conflicting replay",
            ),
        )

    reloaded = repository.get(run.id)
    assert reloaded is not None
    assert reloaded.stage == "diagnose"
    assert reloaded.snapshot == {"state": "diagnose"}
    assert reloaded.tokens_spent == 1


def test_execution_service_persists_and_reuses_real_remediation_run(seeded_db: Any) -> None:
    session_factory, analysis_run, _ = seeded_db
    repository = SqlAlchemyRemediationRunRepository(session_factory)
    service = RemediationExecutionService(repository)
    fingerprint = "c" * 64
    driver = _TwoStepDriver(str(analysis_run.id), fingerprint)

    completed = service.execute(
        analysis_run_id=analysis_run.id,
        inputs_fingerprint=fingerprint,
        provider="mock",
        policy_revision="local-v1",
        driver=driver,
        limits=OrchestrationLimits(max_attempts=1),
    )
    replayed = service.execute(
        analysis_run_id=analysis_run.id,
        inputs_fingerprint=fingerprint,
        provider="mock",
        policy_revision="local-v1",
        driver=driver,
        limits=OrchestrationLimits(max_attempts=1),
    )

    assert completed.run.status == "succeeded"
    assert replayed.reused is True
    assert replayed.run.id == completed.run.id
    assert driver.calls == 2
    assert [item.value.stage for item in repository.list_attempts(completed.run.id)] == [
        "context",
        "diagnose",
    ]
