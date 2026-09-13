import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
    InMemoryTraceLookup,
    InMemoryVerificationLookup,
    TraceFact,
    VerificationFact,
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
        evaluate_canary(
            window=_window(),
            observation=observation,
            policy=_policy(),
            now=now,
            verification_run_ids=("verification-1",),
            trace_ids=("0" * 32,),
        ).action
        == action
    )


@pytest.mark.parametrize(
    ("verification_run_ids", "trace_ids"),
    [((), ("0" * 32,)), (("verification-1",), ()), ((), ())],
)
def test_policy_pauses_when_asserted_health_is_not_linked_to_evidence(
    verification_run_ids: tuple[str, ...], trace_ids: tuple[str, ...]
) -> None:
    decision = evaluate_canary(
        window=_window(),
        observation=_observation(),
        policy=_policy(),
        now=NOW + timedelta(minutes=5),
        verification_run_ids=verification_run_ids,
        trace_ids=trace_ids,
    )

    assert decision.action == "pause"


def test_unlinked_caller_assertion_cannot_promote_through_the_service(tmp_path: Path) -> None:
    adapter = InMemoryDeploymentAdapter()
    service = DeploymentService(
        DeploymentJournal(tmp_path), adapter, clock=lambda: NOW + timedelta(minutes=5)
    )

    service.release(_release())
    result = service.observe(
        release_id="release-1", window=_window(), observation=_observation(), policy=_policy()
    )

    assert result.status == "paused"
    assert result.decision and result.decision.action == "pause"
    assert ("promote", "release-1") not in adapter.actions


def _lookup(**updates: str) -> InMemoryVerificationLookup:
    values = {
        "verification_run_id": "verification-1",
        "analysis_run_id": "run-1",
        "commit_sha": "a" * 40,
        "status": "passed",
    }
    values.update(updates)
    lookup = InMemoryVerificationLookup()
    lookup.record(VerificationFact(**values))
    return lookup


def _traces(**updates: object) -> InMemoryTraceLookup:
    values: dict[str, object] = {
        "trace_id": "0" * 32,
        "analysis_run_id": "run-1",
        "commit_sha": "a" * 40,
        "observation_count": 12,
    }
    values.update(updates)
    lookup = InMemoryTraceLookup()
    lookup.record(TraceFact(**values))  # type: ignore[arg-type]
    return lookup


def test_staging_release_promotes_and_writes_linked_immutable_evidence(tmp_path: Path) -> None:
    adapter = InMemoryDeploymentAdapter()
    service = DeploymentService(
        DeploymentJournal(tmp_path),
        adapter,
        clock=lambda: NOW + timedelta(minutes=5),
        verifications=_lookup(),
        traces=_traces(),
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


def test_release_and_rollback_are_idempotent(tmp_path: Path) -> None:
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


def test_failed_release_adapter_can_be_retried_without_stranding_journal(tmp_path: Path) -> None:
    class FailOnceAdapter(InMemoryDeploymentAdapter):
        attempts = 0

        def release(self, release: Release) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("controller unavailable")
            super().release(release)

    adapter = FailOnceAdapter()
    service = DeploymentService(DeploymentJournal(tmp_path), adapter, clock=lambda: NOW)

    with pytest.raises(RuntimeError, match="controller"):
        service.release(_release())
    retried = service.release(_release())

    assert adapter.attempts == 2
    assert retried.status == "released"
    assert [item.event for item in retried.evidence] == ["released"]


def test_release_id_cannot_be_reused_for_a_different_commit(tmp_path: Path) -> None:
    service = DeploymentService(
        DeploymentJournal(tmp_path), InMemoryDeploymentAdapter(), clock=lambda: NOW
    )
    service.release(_release())
    changed = _release().model_copy(update={"commit_sha": "b" * 40})

    with pytest.raises(DeploymentConflictError):
        service.release(changed)


def test_production_target_is_rejected(tmp_path: Path) -> None:
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


def test_int008_controller_logic_rehearses_promotion_and_rollback(tmp_path: Path) -> None:
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


@pytest.mark.parametrize(
    ("lookup", "reason"),
    [
        (InMemoryVerificationLookup(), "verification run does not exist"),
        (_lookup(status="failed"), "verification run did not pass"),
        (_lookup(analysis_run_id="other-run"), "verification belongs to another analysis run"),
        (_lookup(commit_sha="c" * 40), "verification measured a different commit"),
    ],
)
def test_promotion_requires_a_passing_verification_run_for_this_exact_release(
    tmp_path: Path, lookup: InMemoryVerificationLookup, reason: str
) -> None:
    adapter = InMemoryDeploymentAdapter()
    service = DeploymentService(
        DeploymentJournal(tmp_path),
        adapter,
        clock=lambda: NOW + timedelta(minutes=5),
        verifications=lookup,
        traces=_traces(),
    )

    service.release(_release())
    result = service.observe(
        release_id="release-1",
        window=_window(),
        observation=_observation(),
        policy=_policy(),
        verification_run_ids=("verification-1",),
        trace_ids=("0" * 32,),
    )

    assert result.status == "paused", reason
    assert ("promote", "release-1") not in adapter.actions


def test_a_service_without_a_verification_lookup_can_never_promote(tmp_path: Path) -> None:
    adapter = InMemoryDeploymentAdapter()
    service = DeploymentService(
        DeploymentJournal(tmp_path), adapter, clock=lambda: NOW + timedelta(minutes=5)
    )

    service.release(_release())
    result = service.observe(
        release_id="release-1",
        window=_window(),
        observation=_observation(),
        policy=_policy(),
        verification_run_ids=("verification-1",),
        trace_ids=("0" * 32,),
    )

    assert result.status == "paused"
    assert ("promote", "release-1") not in adapter.actions


def test_a_forged_promotion_appended_to_the_journal_is_rejected(tmp_path: Path) -> None:
    journal = DeploymentJournal(tmp_path)
    journal.create(_release())
    forged = DeploymentEvidence(
        evidence_id="forged-1",
        release_id="release-1",
        analysis_run_id="run-1",
        commit_sha="a" * 40,
        event="promoted",
        actor="attacker",
        collected_at=NOW,
    )
    path = tmp_path / "deployments" / "release-1.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"kind": "evidence", "evidence": forged.model_dump(mode="json")}))
        stream.write("\n")

    with pytest.raises(DeploymentConflictError, match="chain is broken"):
        journal.get("release-1")


def test_editing_a_recorded_release_is_detected(tmp_path: Path) -> None:
    journal = DeploymentJournal(tmp_path)
    journal.create(_release())
    path = tmp_path / "deployments" / "release-1.jsonl"
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    record["release"]["commit_sha"] = "c" * 40
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(DeploymentConflictError, match="was modified"):
        journal.get("release-1")


def test_the_decision_is_recorded_before_the_controller_is_called(tmp_path: Path) -> None:
    class FailingAdapter(InMemoryDeploymentAdapter):
        def promote(self, release: Release) -> None:
            raise RuntimeError("controller unavailable")

    service = DeploymentService(
        DeploymentJournal(tmp_path),
        FailingAdapter(),
        clock=lambda: NOW + timedelta(minutes=5),
        verifications=_lookup(),
        traces=_traces(),
    )
    service.release(_release())

    with pytest.raises(RuntimeError, match="controller"):
        service.observe(
            release_id="release-1",
            window=_window(),
            observation=_observation(),
            policy=_policy(),
            verification_run_ids=("verification-1",),
            trace_ids=("0" * 32,),
        )

    stranded = service.status("release-1")
    assert stranded is not None
    assert stranded.status == "released"
    assert stranded.decision is not None and stranded.decision.action == "promote"


def test_validated_evidence_records_only_confirmed_verification_runs(tmp_path: Path) -> None:
    service = DeploymentService(
        DeploymentJournal(tmp_path),
        InMemoryDeploymentAdapter(),
        clock=lambda: NOW + timedelta(minutes=5),
        verifications=_lookup(),
        traces=_traces(),
    )
    service.release(_release())

    result = service.observe(
        release_id="release-1",
        window=_window(),
        observation=_observation(),
        policy=_policy(),
        verification_run_ids=("verification-1", "fabricated"),
        trace_ids=("0" * 32,),
    )

    validated = next(item for item in result.evidence if item.event == "validated")
    assert validated.verification_run_ids == ("verification-1",)
    assert validated.metadata["verification_runs_claimed"] == 2
    assert validated.metadata["verification_runs_confirmed"] == 1


@pytest.mark.parametrize(
    ("traces", "reason"),
    [
        (InMemoryTraceLookup(), "trace was never recorded"),
        (_traces(analysis_run_id="other-run"), "trace belongs to another analysis run"),
        (_traces(commit_sha="c" * 40), "trace observed a different commit"),
        (_traces(observation_count=0), "trace carries no observation"),
    ],
)
def test_promotion_requires_a_trace_this_release_actually_recorded(
    tmp_path: Path, traces: InMemoryTraceLookup, reason: str
) -> None:
    adapter = InMemoryDeploymentAdapter()
    service = DeploymentService(
        DeploymentJournal(tmp_path),
        adapter,
        clock=lambda: NOW + timedelta(minutes=5),
        verifications=_lookup(),
        traces=traces,
    )

    service.release(_release())
    result = service.observe(
        release_id="release-1",
        window=_window(),
        observation=_observation(),
        policy=_policy(),
        verification_run_ids=("verification-1",),
        trace_ids=("0" * 32,),
    )

    assert result.status == "paused", reason
    assert ("promote", "release-1") not in adapter.actions


def test_a_service_without_a_trace_lookup_can_never_promote(tmp_path: Path) -> None:
    adapter = InMemoryDeploymentAdapter()
    service = DeploymentService(
        DeploymentJournal(tmp_path),
        adapter,
        clock=lambda: NOW + timedelta(minutes=5),
        verifications=_lookup(),
    )

    service.release(_release())
    result = service.observe(
        release_id="release-1",
        window=_window(),
        observation=_observation(),
        policy=_policy(),
        verification_run_ids=("verification-1",),
        trace_ids=("0" * 32,),
    )

    assert result.status == "paused"
    assert ("promote", "release-1") not in adapter.actions


def test_observed_evidence_records_only_confirmed_traces(tmp_path: Path) -> None:
    service = DeploymentService(
        DeploymentJournal(tmp_path),
        InMemoryDeploymentAdapter(),
        clock=lambda: NOW + timedelta(minutes=5),
        verifications=_lookup(),
        traces=_traces(),
    )
    service.release(_release())

    result = service.observe(
        release_id="release-1",
        window=_window(),
        observation=_observation(),
        policy=_policy(),
        verification_run_ids=("verification-1",),
        trace_ids=("0" * 32, "f" * 32),
    )

    observed = next(item for item in result.evidence if item.event == "observed")
    assert observed.trace_ids == ("0" * 32,)
    assert observed.metadata["traces_claimed"] == 2
    assert observed.metadata["traces_confirmed"] == 1
