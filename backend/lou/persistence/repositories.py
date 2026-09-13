"""SQLAlchemy adapters for persistence interfaces."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from lou.persistence.in_memory import _ALLOWED
from lou.persistence.interfaces import (
    AnalysisRunInput,
    AnalysisRunRepository,
    AnalysisRunView,
    InvalidRunTransitionError,
    PersistenceError,
    RunConflictError,
    RunStatus,
)
from lou.persistence.models import AnalysisRunRecord


class SqlAlchemyAnalysisRunRepository(AnalysisRunRepository):
    """Transactional analysis-run adapter; sessions remain an implementation detail."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_or_get(self, value: AnalysisRunInput) -> tuple[AnalysisRunView, bool]:
        with self._session_factory.begin() as session:
            existing = session.scalar(
                select(AnalysisRunRecord).where(
                    AnalysisRunRecord.deduplication_key == value.deduplication_key
                )
            )
            if existing is not None:
                view = _to_view(existing)
                if view.input != value:
                    raise RunConflictError("deduplication key conflicts with immutable run inputs")
                return view, True

            record = AnalysisRunRecord(
                repository_id=value.repository_id,
                trigger_type=value.trigger_type,
                base_commit_sha=value.base_commit_sha,
                candidate_commit_sha=value.candidate_commit_sha,
                deduplication_key=value.deduplication_key,
                configuration=value.configuration,
                toolchain_revision=value.toolchain_revision,
                policy_revision=value.policy_revision,
            )
            session.add(record)
            try:
                session.flush()
            except IntegrityError as error:
                # Another process may have won the unique-key race. Re-read its row
                # before translating unrelated integrity failures.
                session.rollback()
                existing = session.scalar(
                    select(AnalysisRunRecord).where(
                        AnalysisRunRecord.deduplication_key == value.deduplication_key
                    )
                )
                if existing is not None:
                    view = _to_view(existing)
                    if view.input != value:
                        raise RunConflictError(
                            "deduplication key conflicts with immutable run inputs"
                        ) from error
                    return view, True
                raise PersistenceError("analysis run could not be created") from error
            return _to_view(record), False

    def get(self, run_id: UUID) -> AnalysisRunView | None:
        with self._session_factory() as session:
            record = session.get(AnalysisRunRecord, run_id)
            return _to_view(record) if record is not None else None

    def transition(self, run_id: UUID, status: RunStatus) -> AnalysisRunView:
        with self._session_factory.begin() as session:
            record = session.get(AnalysisRunRecord, run_id)
            if record is None:
                raise PersistenceError(f"analysis run {run_id} was not found")
            current_status = cast(RunStatus, record.status)
            if status not in _ALLOWED[current_status]:
                raise InvalidRunTransitionError(f"cannot transition {record.status} to {status}")
            now = datetime.now(UTC)
            record.status = status
            if status == "running" and record.started_at is None:
                record.started_at = now
            if status in {"succeeded", "failed", "cancelled", "inconclusive"}:
                record.completed_at = now
            session.flush()
            return _to_view(record)

    def mark_failed(self, run_id: UUID, error_code: str, error_message: str) -> AnalysisRunView:
        with self._session_factory.begin() as session:
            record = session.get(AnalysisRunRecord, run_id)
            if record is None or record.status != "running":
                raise InvalidRunTransitionError("only running analysis runs can be failed")
            record.error_code = error_code[:100]
            record.error_message = error_message[:2000]
            record.status = "failed"
            record.completed_at = datetime.now(UTC)
            session.flush()
            return _to_view(record)


def _to_view(record: AnalysisRunRecord) -> AnalysisRunView:
    value = AnalysisRunInput(
        repository_id=record.repository_id,
        base_commit_sha=record.base_commit_sha,
        candidate_commit_sha=record.candidate_commit_sha,
        trigger_type=record.trigger_type,  # type: ignore[arg-type]
        deduplication_key=record.deduplication_key,
        configuration=dict(record.configuration),
        toolchain_revision=record.toolchain_revision,
        policy_revision=record.policy_revision,
    )
    return AnalysisRunView(
        id=record.id,
        input=value,
        status=record.status,  # type: ignore[arg-type]
        fix_commit_sha=record.fix_commit_sha,
        error_code=record.error_code,
        error_message=record.error_message,
        started_at=record.started_at,
        completed_at=record.completed_at,
        created_at=record.created_at,
    )
