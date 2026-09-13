"""Durable execution boundary for the bounded remediation state machine."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID

from lou.agents.orchestration import OrchestrationLimits, OrchestrationState
from lou.persistence.interfaces import (
    PersistenceError,
    RemediationAttemptInput,
    RemediationProvider,
    RemediationRunInput,
    RemediationRunRepository,
    RemediationRunView,
)


class OrchestrationDriver(Protocol):
    """The small resumable surface the durable runner needs."""

    def start(self, limits: OrchestrationLimits | None = None) -> OrchestrationState: ...

    def step(self, previous: OrchestrationState) -> OrchestrationState: ...


@dataclass(frozen=True)
class RemediationExecutionResult:
    """Public application result without leaking a database session."""

    run: RemediationRunView
    state: OrchestrationState
    reused: bool


class RemediationExecutionService:
    """Save every state-machine boundary and resume only matching inputs.

    The service does not select a model, apply a patch, or publish a branch.  Its
    responsibility is narrower: give an already-composed orchestrator a durable,
    idempotent lifecycle and an append-only audit trail.
    """

    def __init__(self, runs: RemediationRunRepository) -> None:
        self._runs = runs

    def execute(
        self,
        *,
        analysis_run_id: UUID,
        inputs_fingerprint: str,
        provider: RemediationProvider,
        policy_revision: str,
        driver: OrchestrationDriver,
        limits: OrchestrationLimits | None = None,
        force_token: str | None = None,
    ) -> RemediationExecutionResult:
        """Run or resume a remediation workflow through its terminal boundary."""

        active_limits = limits or OrchestrationLimits()
        value = RemediationRunInput(
            analysis_run_id=analysis_run_id,
            input_fingerprint=inputs_fingerprint,
            deduplication_key=_deduplication_key(
                analysis_run_id,
                inputs_fingerprint,
                provider,
                policy_revision,
                active_limits,
                force_token,
            ),
            provider=provider,
            policy_revision=policy_revision,
            limits=active_limits.model_dump(mode="json"),
        )
        run, existed = self._runs.create_or_get(value)
        state = _restore_state(run, inputs_fingerprint) if existed else driver.start(active_limits)
        if state.inputs_fingerprint != inputs_fingerprint:
            raise PersistenceError("saved remediation state has a different input fingerprint")
        if run.status in {"succeeded", "failed", "abandoned", "cancelled"}:
            return RemediationExecutionResult(run=run, state=state, reused=True)

        if not existed:
            run = self._runs.save_snapshot(
                run.id,
                status="running",
                stage=state.stage,
                snapshot=state.model_dump(mode="json"),
                attempt_count=state.attempt_count,
                tokens_spent=state.tokens_spent,
                estimated_cost_usd=state.estimated_cost_spent_usd,
            )

        while state.stage != "stopped":
            previous = state
            try:
                state = driver.step(previous)
            except Exception as error:
                run = self._save_failure(run, previous, type(error).__name__)
                return RemediationExecutionResult(run=run, state=previous, reused=existed)
            run = self._persist_transition(run, previous, state)

        return RemediationExecutionResult(run=run, state=state, reused=existed)

    def _persist_transition(
        self,
        run: RemediationRunView,
        previous: OrchestrationState,
        current: OrchestrationState,
    ) -> RemediationRunView:
        saved, _, _ = self._runs.persist_stage(
            run.id,
            status=_status_for(current),
            stage=current.stage,
            snapshot=current.model_dump(mode="json"),
            attempt_count=current.attempt_count,
            tokens_spent=current.tokens_spent,
            estimated_cost_usd=current.estimated_cost_spent_usd,
            termination_reason=current.termination_reason,
            attempt=_attempt_for(run.id, previous, current),
        )
        return saved

    def _save_failure(
        self,
        run: RemediationRunView,
        state: OrchestrationState,
        error_name: str,
    ) -> RemediationRunView:
        saved, _, _ = self._runs.persist_stage(
            run.id,
            status="failed",
            stage=state.stage,
            snapshot=state.model_dump(mode="json"),
            attempt_count=state.attempt_count,
            tokens_spent=state.tokens_spent,
            estimated_cost_usd=state.estimated_cost_spent_usd,
            error_message=f"orchestrator_step_failed:{error_name}",
            attempt=RemediationAttemptInput(
                remediation_run_id=run.id,
                attempt_key=f"failure:{state.attempt_count}:{state.stage}",
                attempt_number=max(1, state.attempt_count),
                stage=state.stage,
                outcome="orchestrator_step_failed",
                details={"error_name": error_name},
            ),
        )
        return saved


def _restore_state(run: RemediationRunView, inputs_fingerprint: str) -> OrchestrationState:
    if not run.snapshot:
        raise PersistenceError("existing remediation run has no resumable state snapshot")
    try:
        state = OrchestrationState.model_validate(run.snapshot)
    except ValueError as error:
        raise PersistenceError("saved remediation state is invalid") from error
    if state.inputs_fingerprint != inputs_fingerprint:
        raise PersistenceError("saved remediation state has a different input fingerprint")
    return state


def _deduplication_key(
    analysis_run_id: UUID,
    inputs_fingerprint: str,
    provider: RemediationProvider,
    policy_revision: str,
    limits: OrchestrationLimits,
    force_token: str | None,
) -> str:
    """Hash the complete immutable request and optionally create a deliberate new run."""

    value = {
        "analysis_run_id": str(analysis_run_id),
        "inputs_fingerprint": inputs_fingerprint,
        "provider": provider,
        "policy_revision": policy_revision,
        "limits": limits.model_dump(mode="json"),
        "force_token": force_token,
    }
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return sha256(serialized.encode("utf-8")).hexdigest()


def _status_for(state: OrchestrationState) -> Literal["running", "succeeded", "abandoned"]:
    if state.stage != "stopped":
        return "running"
    return "succeeded" if state.termination_reason == "verified" else "abandoned"


def _attempt_for(
    remediation_run_id: UUID,
    previous: OrchestrationState,
    current: OrchestrationState,
) -> RemediationAttemptInput:
    """Create a stable, secret-free audit record for one completed stage."""

    response = (
        current.current_patch_response
        if previous.stage == "patch"
        else current.current_diagnosis
        if previous.stage == "diagnose"
        else None
    )
    patch = (
        current.current_patch_response.patch_artifact
        if current.current_patch_response
        else None
    )
    key_material = {
        "attempt": current.attempt_count,
        "completed_stage": previous.stage,
        "next_stage": current.stage,
        "verification_cursor": current.verification_cursor,
        "outcomes": len(current.attempt_outcomes),
        "termination_reason": current.termination_reason,
        "last_failure_reason": current.last_failure_reason,
    }
    key = sha256(json.dumps(key_material, sort_keys=True).encode("utf-8")).hexdigest()
    outcome = (
        current.termination_reason
        or current.last_failure_reason
        or f"advanced_to_{current.stage}"
    )
    return RemediationAttemptInput(
        remediation_run_id=remediation_run_id,
        attempt_key=f"{current.attempt_count}:{previous.stage}:{key}",
        attempt_number=max(1, current.attempt_count),
        stage=previous.stage,
        outcome=outcome,
        patch_sha256=patch.patch_sha256 if patch is not None else None,
        agent_result=(response.result.model_dump(mode="json") if response is not None else {}),
        validation=(
            current.current_validation.model_dump(mode="json")
            if previous.stage == "validate" and current.current_validation is not None
            else {}
        ),
        details={
            "next_stage": current.stage,
            "verification_cursor": current.verification_cursor,
            "termination_reason": current.termination_reason,
        },
    )
