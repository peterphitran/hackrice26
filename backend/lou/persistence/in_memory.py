"""In-memory persistence fake used for fast domain tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from lou.persistence.interfaces import (
    AnalysisRunInput,
    AnalysisRunRepository,
    AnalysisRunView,
    InvalidRunTransitionError,
    PersistenceError,
    RunConflictError,
    RunStatus,
)

_ALLOWED: dict[RunStatus, frozenset[RunStatus]] = {
    "queued": frozenset({"running", "cancelled"}),
    "running": frozenset({"succeeded", "failed", "cancelled", "inconclusive"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "inconclusive": frozenset(),
}


class InMemoryAnalysisRunRepository(AnalysisRunRepository):
    """Reference behavior for idempotency and lifecycle tests."""

    def __init__(self) -> None:
        self._runs: dict[UUID, AnalysisRunView] = {}
        self._keys: dict[str, UUID] = {}

    def create_or_get(self, value: AnalysisRunInput) -> tuple[AnalysisRunView, bool]:
        existing_id = self._keys.get(value.deduplication_key)
        if existing_id is not None:
            existing = self._runs[existing_id]
            if existing.input != value:
                raise RunConflictError("deduplication key conflicts with immutable run inputs")
            return existing, True

        now = datetime.now(UTC)
        run = AnalysisRunView(uuid4(), value, "queued", created_at=now)
        self._runs[run.id] = run
        self._keys[value.deduplication_key] = run.id
        return run, False

    def get(self, run_id: UUID) -> AnalysisRunView | None:
        return self._runs.get(run_id)

    def transition(self, run_id: UUID, status: RunStatus) -> AnalysisRunView:
        current = self._require(run_id)
        if status not in _ALLOWED[current.status]:
            raise InvalidRunTransitionError(f"cannot transition {current.status} to {status}")
        now = datetime.now(UTC)
        updated = replace(
            current,
            status=status,
            started_at=current.started_at or (now if status == "running" else None),
            completed_at=now
            if status in {"succeeded", "failed", "cancelled", "inconclusive"}
            else None,
        )
        self._runs[run_id] = updated
        return updated

    def mark_failed(self, run_id: UUID, error_code: str, error_message: str) -> AnalysisRunView:
        current = self._require(run_id)
        if current.status != "running":
            raise InvalidRunTransitionError(f"cannot fail {current.status} run")
        updated = replace(current, error_code=error_code[:100], error_message=error_message[:2000])
        self._runs[run_id] = updated
        return self.transition(run_id, "failed")

    def _require(self, run_id: UUID) -> AnalysisRunView:
        run = self.get(run_id)
        if run is None:
            raise PersistenceError(f"analysis run {run_id} was not found")
        return run
