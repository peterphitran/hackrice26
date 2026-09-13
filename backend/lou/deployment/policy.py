"""Deterministic canary policy; absence of evidence is never healthy."""

from __future__ import annotations

from datetime import datetime

from contracts import CanaryObservation, CanaryWindow, DeploymentDecision, SLOPolicy


def evaluate_canary(
    *,
    window: CanaryWindow,
    observation: CanaryObservation,
    policy: SLOPolicy,
    now: datetime,
    verification_run_ids: tuple[str, ...],
    trace_ids: tuple[str, ...],
) -> DeploymentDecision:
    """Return the sole allowed transition from bounded staging evidence.

    A caller-asserted measurement is only usable when it is linked to a recorded
    verification run and trace; an unlinked claim of health can never promote.
    """

    if observation.release_id != window.release_id:
        raise ValueError("observation does not belong to the canary window")
    if not observation.validation_passed:
        return _decision(window.release_id, "rollback", policy, now, "staging validation failed")
    if not observation.telemetry_available:
        return _decision(window.release_id, "pause", policy, now, "telemetry unavailable")
    if (
        observation.telemetry_age_seconds is None
        or observation.telemetry_age_seconds > policy.telemetry_max_age_seconds
    ):
        return _decision(window.release_id, "pause", policy, now, "telemetry is stale")
    if (
        observation.sample_count < policy.minimum_samples
        or observation.sample_count < window.minimum_samples
    ):
        return _decision(window.release_id, "pause", policy, now, "insufficient telemetry samples")
    if observation.error_rate is None or observation.latency_ms is None:
        return _decision(
            window.release_id, "pause", policy, now, "telemetry metrics are incomplete"
        )
    if observation.error_rate > policy.max_error_rate:
        return _decision(window.release_id, "rollback", policy, now, "error-rate SLO breached")
    if observation.latency_ms > policy.max_latency_ms:
        return _decision(window.release_id, "rollback", policy, now, "latency SLO breached")
    if not verification_run_ids:
        return _decision(
            window.release_id,
            "pause",
            policy,
            now,
            "validation is not linked to a verification run",
        )
    if not trace_ids:
        return _decision(
            window.release_id, "pause", policy, now, "telemetry is not linked to a recorded trace"
        )
    if now < window.deadline_at:
        return _decision(
            window.release_id, "pause", policy, now, "canary window is still observing"
        )
    return _decision(
        window.release_id, "promote", policy, now, "validation passed", "canary window complete"
    )


def _decision(
    release_id: str, action: str, policy: SLOPolicy, now: datetime, *reasons: str
) -> DeploymentDecision:
    return DeploymentDecision(
        release_id=release_id,
        action=action,  # type: ignore[arg-type]
        policy_revision=policy.policy_revision,
        reasons=reasons,
        decided_at=now,
    )
