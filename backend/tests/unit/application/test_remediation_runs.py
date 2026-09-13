"""Unit tests for the durable remediation execution boundary."""

from __future__ import annotations

from dataclasses import replace
from typing import cast
from uuid import UUID, uuid4

from lou.agents.orchestration import OrchestrationLimits, OrchestrationState
from lou.application.remediation_runs import RemediationExecutionService
from lou.persistence.interfaces import (
    PersistenceError,
    RemediationAttemptInput,
    RemediationAttemptView,
    RemediationRunInput,
    RemediationRunRepository,
    RemediationRunView,
    RemediationStage,
    RemediationStatus,
    RunConflictError,
)


class MemoryRemediationRuns:
    """Small test double that preserves the production persistence semantics."""

    def __init__(self) -> None:
        self._runs: dict[UUID, RemediationRunView] = {}
        self._keys: dict[str, UUID] = {}
        self._attempts: dict[UUID, list[RemediationAttemptView]] = {}

    def create_or_get(self, value: RemediationRunInput) -> tuple[RemediationRunView, bool]:
        existing_id = self._keys.get(value.deduplication_key)
        if existing_id is not None:
            existing = self._runs[existing_id]
            if existing.input != value:
                raise RunConflictError("remediation deduplication key conflicts")
            return existing, True
        run = RemediationRunView(
            id=uuid4(),
            input=value,
            status="queued",
            stage="context",
        )
        self._runs[run.id] = run
        self._keys[value.deduplication_key] = run.id
        self._attempts[run.id] = []
        return run, False

    def get(self, remediation_run_id: UUID) -> RemediationRunView | None:
        return self._runs.get(remediation_run_id)

    def save_snapshot(
        self,
        remediation_run_id: UUID,
        *,
        status: RemediationStatus,
        stage: RemediationStage,
        snapshot: dict[str, object],
        attempt_count: int,
        tokens_spent: int,
        estimated_cost_usd: float,
        termination_reason: str | None = None,
        error_message: str | None = None,
    ) -> RemediationRunView:
        run = self._require(remediation_run_id)
        saved = replace(
            run,
            status=status,
            stage=stage,
            snapshot=snapshot,
            attempt_count=attempt_count,
            tokens_spent=tokens_spent,
            estimated_cost_usd=estimated_cost_usd,
            termination_reason=termination_reason,
            error_message=error_message,
        )
        self._runs[remediation_run_id] = saved
        return saved

    def append_attempt(self, value: RemediationAttemptInput) -> tuple[RemediationAttemptView, bool]:
        attempts = self._attempts[value.remediation_run_id]
        existing = next(
            (item for item in attempts if item.value.attempt_key == value.attempt_key), None
        )
        if existing is not None:
            if existing.value != value:
                raise RunConflictError("remediation attempt key conflicts")
            return existing, True
        saved = RemediationAttemptView(id=uuid4(), value=value)
        attempts.append(saved)
        return saved, False

    def persist_stage(
        self,
        remediation_run_id: UUID,
        *,
        status: RemediationStatus,
        stage: RemediationStage,
        snapshot: dict[str, object],
        attempt_count: int,
        tokens_spent: int,
        estimated_cost_usd: float,
        attempt: RemediationAttemptInput,
        termination_reason: str | None = None,
        error_message: str | None = None,
    ) -> tuple[RemediationRunView, RemediationAttemptView, bool]:
        if attempt.remediation_run_id != remediation_run_id:
            raise PersistenceError("attempt run mismatch")
        saved = self.save_snapshot(
            remediation_run_id,
            status=status,
            stage=stage,
            snapshot=snapshot,
            attempt_count=attempt_count,
            tokens_spent=tokens_spent,
            estimated_cost_usd=estimated_cost_usd,
            termination_reason=termination_reason,
            error_message=error_message,
        )
        attempt_view, reused = self.append_attempt(attempt)
        return saved, attempt_view, reused

    def list_attempts(self, remediation_run_id: UUID) -> list[RemediationAttemptView]:
        return list(self._attempts[remediation_run_id])

    def _require(self, remediation_run_id: UUID) -> RemediationRunView:
        try:
            return self._runs[remediation_run_id]
        except KeyError as error:
            raise PersistenceError("remediation run was not found") from error


class AdvancingDriver:
    def __init__(self, analysis_run_id: UUID, fingerprint: str) -> None:
        self._states = [
            OrchestrationState(
                analysis_run_id=str(analysis_run_id),
                inputs_fingerprint=fingerprint,
                limits=OrchestrationLimits(max_attempts=1),
                started_at_epoch=0.0,
            ),
            OrchestrationState(
                analysis_run_id=str(analysis_run_id),
                inputs_fingerprint=fingerprint,
                limits=OrchestrationLimits(max_attempts=1),
                started_at_epoch=0.0,
                stage="diagnose",
                attempt_count=1,
            ),
            OrchestrationState(
                analysis_run_id=str(analysis_run_id),
                inputs_fingerprint=fingerprint,
                limits=OrchestrationLimits(max_attempts=1),
                started_at_epoch=0.0,
                stage="stopped",
                attempt_count=1,
                termination_reason="verified",
            ),
        ]
        self.step_calls = 0

    def start(self, limits: OrchestrationLimits | None = None) -> OrchestrationState:
        assert limits is not None
        return self._states[0].model_copy(update={"limits": limits})

    def step(self, previous: OrchestrationState) -> OrchestrationState:
        self.step_calls += 1
        return self._states[1] if previous.stage == "context" else self._states[2]


class FailingDriver(AdvancingDriver):
    def step(self, previous: OrchestrationState) -> OrchestrationState:
        raise RuntimeError("provider disconnected")


def test_execution_persists_each_stage_and_reuses_terminal_run() -> None:
    repository = MemoryRemediationRuns()
    service = RemediationExecutionService(cast(RemediationRunRepository, repository))
    analysis_run_id = uuid4()
    driver = AdvancingDriver(analysis_run_id, "f" * 64)

    first = service.execute(
        analysis_run_id=analysis_run_id,
        inputs_fingerprint="f" * 64,
        provider="mock",
        policy_revision="local-v1",
        driver=driver,
        limits=OrchestrationLimits(max_attempts=1),
    )
    repeated = service.execute(
        analysis_run_id=analysis_run_id,
        inputs_fingerprint="f" * 64,
        provider="mock",
        policy_revision="local-v1",
        driver=driver,
        limits=OrchestrationLimits(max_attempts=1),
    )

    assert first.reused is False
    assert first.run.status == "succeeded"
    assert first.state.termination_reason == "verified"
    assert [item.value.stage for item in repository.list_attempts(first.run.id)] == [
        "context",
        "diagnose",
    ]
    assert repeated.reused is True
    assert repeated.run.id == first.run.id
    assert driver.step_calls == 2


def test_execution_safely_records_step_failure() -> None:
    repository = MemoryRemediationRuns()
    service = RemediationExecutionService(cast(RemediationRunRepository, repository))
    analysis_run_id = uuid4()

    result = service.execute(
        analysis_run_id=analysis_run_id,
        inputs_fingerprint="e" * 64,
        provider="mock",
        policy_revision="local-v1",
        driver=FailingDriver(analysis_run_id, "e" * 64),
    )

    assert result.run.status == "failed"
    assert result.run.error_message == "orchestrator_step_failed:RuntimeError"
    assert repository.list_attempts(result.run.id)[0].value.outcome == "orchestrator_step_failed"


def test_changed_inputs_create_a_distinct_remediation_run() -> None:
    repository = MemoryRemediationRuns()
    service = RemediationExecutionService(cast(RemediationRunRepository, repository))
    analysis_run_id = uuid4()
    driver = AdvancingDriver(analysis_run_id, "d" * 64)
    first = service.execute(
        analysis_run_id=analysis_run_id,
        inputs_fingerprint="d" * 64,
        provider="mock",
        policy_revision="local-v1",
        driver=driver,
    )

    distinct = service.execute(
        analysis_run_id=analysis_run_id,
        inputs_fingerprint="c" * 64,
        provider="mock",
        policy_revision="local-v1",
        driver=AdvancingDriver(analysis_run_id, "c" * 64),
    )

    assert distinct.reused is False
    assert distinct.run.id != first.run.id
