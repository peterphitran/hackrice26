from pathlib import Path

import pytest

from contracts import RepositoryChange, RepositoryContext, WorkloadSelection
from lou.application.analysis import AnalysisApplicationService, AnalysisRequest, AnalysisStatus


class Store:
    def __init__(self, reused: bool = False) -> None:
        self.reused = reused
        self.context_calls = 0
        self.finished: list[str] = []

    def create_or_get(self, request: AnalysisRequest, deduplication_key: str) -> tuple[str, bool]:
        return "run-1", self.reused

    def record_context(
        self,
        run_id: str,
        change: RepositoryChange,
        context: RepositoryContext,
        workloads: tuple[WorkloadSelection, ...],
    ) -> None:
        self.context_calls += 1

    def finish(self, run_id: str, status: AnalysisStatus, message: str | None = None) -> None:
        self.finished.append(status)


class Intelligence:
    def inspect(
        self, request: AnalysisRequest, run_id: str
    ) -> tuple[RepositoryChange, RepositoryContext]:
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
    def __init__(self, workloads: tuple[WorkloadSelection, ...]) -> None:
        self.workloads = workloads

    def select(self, context: RepositoryContext) -> tuple[WorkloadSelection, ...]:
        return self.workloads


def _request(tmp_path: Path) -> AnalysisRequest:
    return AnalysisRequest("repo-1", tmp_path, "base", "candidate")


def _workload() -> WorkloadSelection:
    return WorkloadSelection(
        workload_id="checkout",
        workload_type="pytest",
        definition_path="tests/test_checkout.py",
        phase="candidate",
        reason="changed code",
        confidence=1.0,
    )


def test_service_coordinates_inspection_and_selection(tmp_path: Path) -> None:
    store = Store()
    service = AnalysisApplicationService(store, Intelligence(), Selector((_workload(),)))

    result = service.run(_request(tmp_path))

    assert result.status == "succeeded"
    assert result.stages == ("initialize", "inspect", "select")
    assert store.context_calls == 1


def test_service_returns_existing_run_without_repeating_work(tmp_path: Path) -> None:
    store = Store(reused=True)
    service = AnalysisApplicationService(store, Intelligence(), Selector((_workload(),)))

    result = service.run(_request(tmp_path))

    assert result.reused is True
    assert result.stages == ("initialize",)
    assert store.context_calls == 0


def test_no_workload_is_inconclusive(tmp_path: Path) -> None:
    store = Store()
    service = AnalysisApplicationService(store, Intelligence(), Selector(()))

    result = service.run(_request(tmp_path))

    assert result.status == "inconclusive"
    assert store.finished == ["inconclusive"]


def test_invalid_input_does_not_create_run(tmp_path: Path) -> None:
    store = Store()
    service = AnalysisApplicationService(store, Intelligence(), Selector((_workload(),)))

    with pytest.raises(ValueError, match="must differ"):
        service.run(AnalysisRequest("repo-1", tmp_path, "same", "same"))
