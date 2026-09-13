from uuid import uuid4

import pytest

from lou.persistence import (
    AnalysisRunInput,
    InMemoryAnalysisRunRepository,
    InvalidRunTransitionError,
    RunConflictError,
)


def _input(key: str = "repo:base:candidate") -> AnalysisRunInput:
    return AnalysisRunInput(
        repository_id=uuid4(),
        base_commit_sha="base",
        candidate_commit_sha="candidate",
        trigger_type="fixture",
        deduplication_key=key,
    )


def test_create_or_get_is_idempotent() -> None:
    repository = InMemoryAnalysisRunRepository()
    value = _input()

    first, existing = repository.create_or_get(value)
    second, existing_again = repository.create_or_get(value)

    assert existing is False
    assert existing_again is True
    assert second.id == first.id
    assert second.status == "queued"


def test_deduplication_conflict_does_not_mutate_run() -> None:
    repository = InMemoryAnalysisRunRepository()
    repository.create_or_get(_input())

    conflicting = _input()
    with pytest.raises(RunConflictError):
        repository.create_or_get(conflicting)


def test_lifecycle_allows_running_then_success_and_rejects_reopen() -> None:
    repository = InMemoryAnalysisRunRepository()
    run, _ = repository.create_or_get(_input())

    running = repository.transition(run.id, "running")
    completed = repository.transition(run.id, "succeeded")

    assert running.started_at is not None
    assert completed.completed_at is not None
    with pytest.raises(InvalidRunTransitionError):
        repository.transition(run.id, "running")


def test_failure_is_bounded_and_terminal() -> None:
    repository = InMemoryAnalysisRunRepository()
    run, _ = repository.create_or_get(_input())
    repository.transition(run.id, "running")

    failed = repository.mark_failed(run.id, "X" * 200, "Y" * 3000)

    assert failed.status == "failed"
    assert failed.error_code == "X" * 100
    assert failed.error_message == "Y" * 2000
