from datetime import UTC, datetime
from pathlib import Path

import pytest

from contracts import (
    Evidence,
    Finding,
    LouDecision,
    RepositoryChange,
    RepositoryContext,
    VerificationResult,
    WorkloadSelection,
)
from lou.application.analysis import (
    AnalysisApplicationService,
    AnalysisRequest,
    AnalysisStatus,
    RunSnapshot,
    VerificationBundle,
)


class Store:
    def __init__(self, reused: bool = False, reused_status: str = "succeeded") -> None:
        self.reused = reused
        self.reused_status = reused_status
        self.calls: list[str] = []
        self.finished: list[str] = []

    def create_or_get(self, request: AnalysisRequest, deduplication_key: str) -> RunSnapshot:
        self.calls.append("initialize")
        status = self.reused_status if self.reused else "queued"
        return RunSnapshot("run-1", status, not self.reused)  # type: ignore[arg-type]

    def record_context(
        self,
        run_id: str,
        change: RepositoryChange,
        context: RepositoryContext,
        workloads: tuple[WorkloadSelection, ...],
    ) -> None:
        self.calls.append("context")

    def record_verification(self, run_id: str, bundle: VerificationBundle) -> None:
        self.calls.append(f"persist-{bundle.result.phase}")

    def record_decision(self, run_id: str, decision: LouDecision) -> None:
        self.calls.append("persist-decision")

    def finish(self, run_id: str, status: AnalysisStatus, message: str | None = None) -> None:
        self.calls.append("finalize")
        self.finished.append(status)


class Intelligence:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def inspect(
        self, request: AnalysisRequest, run_id: str
    ) -> tuple[RepositoryChange, RepositoryContext]:
        self.calls.append("inspect")
        return (
            RepositoryChange(
                repository_id=request.repository_id,
                base_commit_sha=request.base_commit_sha,
                candidate_commit_sha=request.candidate_commit_sha,
            ),
            RepositoryContext(
                repository_id=request.repository_id, commit_sha=request.candidate_commit_sha
            ),
        )


class Selector:
    def __init__(self, calls: list[str], workloads: tuple[WorkloadSelection, ...]) -> None:
        self.calls = calls
        self.workloads = workloads

    def select(self, context: RepositoryContext) -> tuple[WorkloadSelection, ...]:
        self.calls.append("select")
        return self.workloads


class Verification:
    def __init__(
        self,
        calls: list[str],
        baseline_status: str = "passed",
        candidate_status: str = "failed",
    ) -> None:
        self.calls = calls
        self.baseline_status = baseline_status
        self.candidate_status = candidate_status

    def measure_baseline(
        self,
        request: AnalysisRequest,
        run_id: str,
        workloads: tuple[WorkloadSelection, ...],
    ) -> VerificationBundle:
        self.calls.append("baseline")
        return _bundle(run_id, "baseline", request.base_commit_sha, self.baseline_status)

    def measure_candidate(
        self,
        request: AnalysisRequest,
        run_id: str,
        workloads: tuple[WorkloadSelection, ...],
        baseline: VerificationBundle,
    ) -> VerificationBundle:
        assert baseline.result.status == "passed"
        self.calls.append("candidate")
        return _bundle(run_id, "candidate", request.candidate_commit_sha, self.candidate_status)


class Decision:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def decide(
        self,
        request: AnalysisRequest,
        run_id: str,
        baseline: VerificationBundle,
        candidate: VerificationBundle,
        context: RepositoryContext,
    ) -> LouDecision:
        self.calls.append("decide")
        return LouDecision(
            decision_id=f"{run_id}-decision",
            analysis_run_id=run_id,
            debt_risk=0.8,
            remediation_risk=None,
            confidence=0.9,
            autonomy_level=1,
            action="recommend",
        )


def _request(tmp_path: Path) -> AnalysisRequest:
    return AnalysisRequest("repo-1", tmp_path, "a" * 40, "b" * 40)


def _workload() -> WorkloadSelection:
    return WorkloadSelection(
        workload_id="checkout",
        workload_type="pytest",
        definition_path="tests/test_checkout.py",
        phase="candidate",
        reason="changed code",
        confidence=1.0,
    )


def _bundle(run_id: str, phase: str, sha: str, status: str) -> VerificationBundle:
    result = VerificationResult(
        verification_run_id=f"{run_id}-{phase}",
        analysis_run_id=run_id,
        phase=phase,  # type: ignore[arg-type]
        commit_sha=sha,
        status=status,  # type: ignore[arg-type]
        workload_id="checkout",
    )
    if phase == "baseline":
        return VerificationBundle(result)
    finding = Finding(
        finding_id=f"{run_id}-finding",
        analysis_run_id=run_id,
        fingerprint="checkout:n-plus-one",
        source="test",
        category="runtime-regression",
        severity="high",
        confidence=1.0,
        phase="candidate",
        title="Candidate regression",
        message="Measured regression.",
    )
    evidence = Evidence(
        evidence_id=f"{run_id}-evidence",
        analysis_run_id=run_id,
        phase="comparison",
        kind="test",
        source="test",
        collected_at=datetime(2026, 9, 13, tzinfo=UTC),
    )
    return VerificationBundle(result, (finding,), (evidence,))


def _service(
    calls: list[str],
    store: Store,
    *,
    workloads: tuple[WorkloadSelection, ...] = (_workload(),),
    baseline_status: str = "passed",
    candidate_status: str = "failed",
) -> AnalysisApplicationService:
    return AnalysisApplicationService(
        store,
        Intelligence(calls),
        Selector(calls, workloads),
        Verification(calls, baseline_status, candidate_status),
        Decision(calls),
    )


def test_service_runs_all_seven_stages_in_order(tmp_path: Path) -> None:
    calls: list[str] = []
    store = Store()

    result = _service(calls, store).run(_request(tmp_path))

    assert result.status == "succeeded"
    assert result.stages == (
        "validate",
        "initialize",
        "inspect",
        "select",
        "verify",
        "decide",
        "finalize",
    )
    assert result.decision and result.decision.action == "recommend"
    assert calls == ["inspect", "select", "baseline", "candidate", "decide"]
    assert store.calls == [
        "initialize",
        "context",
        "persist-baseline",
        "persist-candidate",
        "persist-decision",
        "finalize",
    ]


def test_service_returns_existing_run_without_repeating_work(tmp_path: Path) -> None:
    calls: list[str] = []
    store = Store(reused=True)

    result = _service(calls, store).run(_request(tmp_path))

    assert result.reused is True
    assert result.stages == ("validate", "initialize")
    assert calls == []
    assert store.calls == ["initialize"]


@pytest.mark.parametrize("status", ["failed", "inconclusive", "running"])
def test_reused_run_preserves_actual_status(tmp_path: Path, status: str) -> None:
    calls: list[str] = []
    store = Store(reused=True, reused_status=status)

    result = _service(calls, store).run(_request(tmp_path))

    assert result.reused is True
    assert result.status == status
    assert calls == []


def test_no_workload_is_inconclusive_without_verification(tmp_path: Path) -> None:
    calls: list[str] = []
    store = Store()

    result = _service(calls, store, workloads=()).run(_request(tmp_path))

    assert result.status == "inconclusive"
    assert result.stages == ("validate", "initialize", "inspect", "select", "finalize")
    assert calls == ["inspect", "select"]
    assert store.finished == ["inconclusive"]


def test_unusable_baseline_is_inconclusive_and_skips_candidate_and_decision(tmp_path: Path) -> None:
    calls: list[str] = []
    store = Store()

    result = _service(calls, store, baseline_status="inconclusive").run(_request(tmp_path))

    assert result.status == "inconclusive"
    assert result.verification_results[0].phase == "baseline"
    assert calls == ["inspect", "select", "baseline"]
    assert "persist-candidate" not in store.calls
    assert "persist-decision" not in store.calls


def test_inconclusive_candidate_is_not_decided(tmp_path: Path) -> None:
    calls: list[str] = []
    store = Store()

    result = _service(calls, store, candidate_status="inconclusive").run(_request(tmp_path))

    assert result.status == "inconclusive"
    assert calls == ["inspect", "select", "baseline", "candidate"]
    assert "persist-decision" not in store.calls


def test_invalid_input_does_not_create_run(tmp_path: Path) -> None:
    calls: list[str] = []
    store = Store()

    with pytest.raises(ValueError, match="must differ"):
        _service(calls, store).run(AnalysisRequest("repo-1", tmp_path, "same", "same"))

    assert store.calls == []


def test_deduplication_key_is_stable_for_equivalent_nested_configuration(tmp_path: Path) -> None:
    first = AnalysisRequest(
        "repo-1",
        tmp_path,
        "a" * 40,
        "b" * 40,
        configuration={"limits": {"timeout": 120, "repetitions": 5}},
    )
    second = AnalysisRequest(
        "repo-1",
        tmp_path,
        "a" * 40,
        "b" * 40,
        configuration={"limits": {"repetitions": 5, "timeout": 120}},
    )

    assert first.deduplication_key() == second.deduplication_key()


def test_force_run_requires_a_visible_token(tmp_path: Path) -> None:
    calls: list[str] = []
    store = Store()
    request = AnalysisRequest("repo-1", tmp_path, "a" * 40, "b" * 40, force_new_run=True)

    with pytest.raises(ValueError, match="force_token"):
        _service(calls, store).run(request)

    assert store.calls == []
