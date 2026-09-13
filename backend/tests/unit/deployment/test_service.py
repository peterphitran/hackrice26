from datetime import UTC, datetime, timedelta

import pytest

from contracts import (
    CanaryObservation,
    CanaryWindow,
    DeploymentEvidence,
    DeploymentTarget,
    Release,
    SLOPolicy,
)
from lou.deployment import (
    DeploymentConflictError,
    DeploymentJournal,
    DeploymentService,
    InMemoryDeploymentAdapter,
    evaluate_canary,
)
from lou.deployment.int008 import _rehearse

NOW = datetime(2026, 9, 13, 12, tzinfo=UTC)


def _release(release_id: str = "release-1") -> Release:
    return Release(
        release_id=release_id,
        repository_id="demo",
        commit_sha="a" * 40,
        analysis_run_id="run-1",
        target=DeploymentTarget(
            environment="staging", adapter="local", namespace="lou", service="demo"
        ),
        requested_by="tester",
    )


def _window() -> CanaryWindow:
    return CanaryWindow(
        release_id="release-1",
        started_at=NOW,
        deadline_at=NOW + timedelta(minutes=5),
        minimum_samples=5,
    )


def _policy() -> SLOPolicy:
    return SLOPolicy(
        policy_revision="test-v1",
        max_error_rate=0.05,
        max_latency_ms=300,
        minimum_samples=5,
        telemetry_max_age_seconds=60,
    )


def _observation(**updates: object) -> CanaryObservation:
    values: dict[str, object] = {
        "release_id": "release-1",
        "observed_at": NOW + timedelta(minutes=5),
        "sample_count": 5,
        "error_rate": 0.01,
        "latency_ms": 120,
        "telemetry_available": True,
        "telemetry_age_seconds": 5,
        "validation_passed": True,
    }
    values.update(updates)
    return CanaryObservation.model_validate(values)


@pytest.mark.parametrize(
    ("observation", "now", "action"),
    [
        (_observation(), NOW + timedelta(minutes=5), "promote"),
        (_observation(telemetry_available=False), NOW + timedelta(minutes=5), "pause"),
        (_observation(error_rate=0.2), NOW + timedelta(minutes=5), "rollback"),
        (_observation(), NOW + timedelta(minutes=1), "pause"),
        (_observation(validation_passed=False), NOW + timedelta(minutes=5), "rollback"),
    ],
)
def test_policy_never_promotes_missing_or_bad_evidence(
    observation: CanaryObservation, now: datetime, action: str
) -> None:
    assert (
        evaluate_canary(window=_window(), observation=observation, policy=_policy(), now=now).action
        == action
    )


def test_staging_release_promotes_and_writes_linked_immutable_evidence(tmp_path) -> None:
    adapter = InMemoryDeploymentAdapter()
    service = DeploymentService(
        DeploymentJournal(tmp_path), adapter, clock=lambda: NOW + timedelta(minutes=5)
    )

    released = service.release(_release())
    result = service.observe(
        release_id="release-1",
        window=_window(),
        observation=_observation(),
        policy=_policy(),
        verification_run_ids=("verification-1",),
        trace_ids=("0" * 32,),
    )

    assert released.status == "released"
    assert result.status == "promoted"
    assert result.decision and result.decision.action == "promote"
    assert {item.event for item in result.evidence} == {
        "released",
        "validated",
        "observed",
        "promoted",
    }
    assert any(item.trace_ids == ("0" * 32,) for item in result.evidence)
    assert adapter.actions == [("release", "release-1"), ("promote", "release-1")]
    report = service.report("release-1")
    assert report and report["release"]["commit_sha"] == "a" * 40  # type: ignore[index]
    assert len(report["evidence"]) == 4  # type: ignore[arg-type]


def test_release_and_rollback_are_idempotent(tmp_path) -> None:
    adapter = InMemoryDeploymentAdapter()
    service = DeploymentService(DeploymentJournal(tmp_path), adapter, clock=lambda: NOW)

    first = service.release(_release())
    replay = service.release(_release())
    rollback = service.rollback("release-1", actor="operator", reason="demo rollback")
    repeated = service.rollback("release-1", actor="operator", reason="demo rollback")

    assert not first.reused and replay.reused
    assert rollback.status == repeated.status == "rolled_back"
    assert len(repeated.evidence) == len(rollback.evidence)
    assert adapter.actions == [("release", "release-1"), ("rollback", "release-1")]


def test_release_id_cannot_be_reused_for_a_different_commit(tmp_path) -> None:
    service = DeploymentService(
        DeploymentJournal(tmp_path), InMemoryDeploymentAdapter(), clock=lambda: NOW
    )
    service.release(_release())
    changed = _release().model_copy(update={"commit_sha": "b" * 40})

    with pytest.raises(DeploymentConflictError):
        service.release(changed)


def test_production_target_is_rejected(tmp_path) -> None:
    service = DeploymentService(
        DeploymentJournal(tmp_path), InMemoryDeploymentAdapter(), clock=lambda: NOW
    )
    production = _release().model_copy(
        update={
            "target": DeploymentTarget(
                environment="production", adapter="local", namespace="lou", service="demo"
            )
        }
    )

    with pytest.raises(ValueError, match="staging"):
        service.release(production)


def test_int008_controller_logic_rehearses_promotion_and_rollback(tmp_path) -> None:
    result = _rehearse(tmp_path)

    assert result["healthy_promoted"] is True
    assert result["bad_rolled_back"] is True
    assert result["good_evidence_count"] == result["bad_evidence_count"] == 4


def test_deployment_evidence_rejects_secret_like_metadata() -> None:
    with pytest.raises(ValueError, match="secret-like"):
        DeploymentEvidence(
            evidence_id="evidence-1",
            release_id="release-1",
            analysis_run_id="run-1",
            commit_sha="a" * 40,
            event="released",
            actor="tester",
            collected_at=NOW,
            metadata={"api_token": "do-not-store"},
        )


def test_release_rejects_credential_bearing_artifact_uri() -> None:
    with pytest.raises(ValueError, match="credentials"):
        Release.model_validate(
            _release().model_dump() | {"artifact_uri": "https://user:password@example.test/app"}
        )
