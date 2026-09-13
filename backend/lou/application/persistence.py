"""SQLAlchemy-backed application store without session leakage to orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from contracts import LouDecision, RepositoryChange, RepositoryContext, WorkloadSelection
from lou.application.analysis import (
    AnalysisRequest,
    AnalysisStatus,
    RunSnapshot,
    VerificationBundle,
)
from lou.persistence.interfaces import (
    AnalysisRunInput,
    DecisionInput,
    EvidenceInput,
    FindingInput,
    PersistenceError,
    VerificationRunInput,
)
from lou.persistence.models import RepositoryRecord, WorkloadRecord
from lou.persistence.repositories import (
    SqlAlchemyAnalysisRunRepository,
    SqlAlchemyDecisionRepository,
    SqlAlchemyResultRepository,
)


class SqlAlchemyAnalysisStore:
    """Application-facing store backed by PF-003 persistence repositories."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._runs = SqlAlchemyAnalysisRunRepository(session_factory)
        self._results = SqlAlchemyResultRepository(session_factory)
        self._decisions = SqlAlchemyDecisionRepository(session_factory)

    def create_or_get(self, request: AnalysisRequest, deduplication_key: str) -> RunSnapshot:
        repository_id = self._register_local_repository(request)
        run, reused = self._runs.create_or_get(
            AnalysisRunInput(
                repository_id=repository_id,
                base_commit_sha=request.base_commit_sha,
                candidate_commit_sha=request.candidate_commit_sha,
                trigger_type=request.trigger_type,
                deduplication_key=deduplication_key,
                configuration=request.configuration,
                toolchain_revision=request.toolchain_revision,
                policy_revision=request.policy_revision,
            )
        )
        if reused:
            return RunSnapshot(str(run.id), run.status, False, run.error_message)
        running = self._runs.transition(run.id, "running")
        return RunSnapshot(str(running.id), running.status, True)

    def record_context(
        self,
        run_id: str,
        change: RepositoryChange,
        context: RepositoryContext,
        workloads: tuple[WorkloadSelection, ...],
    ) -> None:
        """Register selected workload definitions and retain context as immutable evidence."""

        analysis_run_id = _uuid(run_id)
        repository_id, repository_path = self._repository_for_run(analysis_run_id)
        for selection in workloads:
            self._register_workload(repository_id, repository_path, selection)
        self._results.append_evidence(
            EvidenceInput(
                analysis_run_id=analysis_run_id,
                phase="comparison",
                kind="repository-context",
                source="lou.application",
                contract_id=f"context:{run_id}",
                collected_at=_utc_now(),
                summary={
                    "change": change.model_dump(mode="json"),
                    "context": context.model_dump(mode="json"),
                    "workload_ids": [selection.workload_id for selection in workloads],
                },
            )
        )

    def record_verification(self, run_id: str, bundle: VerificationBundle) -> None:
        analysis_run_id = _uuid(run_id)
        if bundle.result.analysis_run_id != run_id:
            raise PersistenceError("verification result does not belong to the analysis run")
        repository_id, _ = self._repository_for_run(analysis_run_id)
        workload_id = self._workload_id(repository_id, bundle.result.workload_id)
        verification = VerificationRunInput(
            analysis_run_id=analysis_run_id,
            phase=bundle.result.phase,
            commit_sha=bundle.result.commit_sha,
            status=bundle.result.status,
            workload_id=workload_id,
            metrics=cast(dict[str, object], bundle.result.metrics),
            contract_id=bundle.result.verification_run_id,
        )
        findings = [
            FindingInput(
                analysis_run_id=analysis_run_id,
                contract_id=finding.finding_id,
                fingerprint=finding.fingerprint,
                source=finding.source,
                category=finding.category,
                severity=finding.severity,
                confidence=finding.confidence,
                phase=finding.phase,
                title=finding.title,
                message=finding.message,
                file_path=finding.file_path,
                symbol_key=finding.symbol_key,
                metadata=cast(dict[str, object], finding.metadata),
            )
            for finding in bundle.findings
        ]
        evidence = [
            EvidenceInput(
                analysis_run_id=analysis_run_id,
                contract_id=item.evidence_id,
                phase=item.phase,
                kind=item.kind,
                source=item.source,
                collected_at=item.collected_at,
                summary=cast(dict[str, object], item.summary),
                artifact_uri=item.artifact_uri,
                artifact_sha256=item.artifact_sha256,
            )
            for item in bundle.evidence
        ]
        self._results.persist_verification_bundle(verification, findings, evidence)

    def record_decision(self, run_id: str, decision: LouDecision) -> None:
        analysis_run_id = _uuid(run_id)
        if decision.analysis_run_id != run_id:
            raise PersistenceError("decision does not belong to the analysis run")
        self._decisions.save(
            DecisionInput(
                analysis_run_id=analysis_run_id,
                decision_id=decision.decision_id,
                action=decision.action,
                debt_risk=decision.debt_risk,
                remediation_risk=decision.remediation_risk,
                confidence=decision.confidence,
                autonomy_level=decision.autonomy_level,
                rationale=cast(dict[str, object], decision.rationale),
                metadata=cast(dict[str, object], decision.metadata),
            )
        )

    def finish(self, run_id: str, status: AnalysisStatus, message: str | None = None) -> None:
        if status not in {"succeeded", "failed", "inconclusive", "cancelled"}:
            raise PersistenceError("only terminal analysis statuses can be finalized")
        analysis_run_id = _uuid(run_id)
        current = self._runs.get(analysis_run_id)
        if current is None:
            raise PersistenceError("analysis run was not found")
        if current.status == status:
            return
        if current.status != "running":
            raise PersistenceError("only a running analysis run can be finalized")
        if status == "failed":
            self._runs.mark_failed(analysis_run_id, "analysis_stage_failed", message or "")
        else:
            self._runs.transition(analysis_run_id, status)

    def _register_local_repository(self, request: AnalysisRequest) -> UUID:
        local_path = str(request.repository_path.resolve(strict=True))
        with self._session_factory.begin() as session:
            existing = session.scalar(
                select(RepositoryRecord).where(
                    RepositoryRecord.provider == "local",
                    RepositoryRecord.provider_repository_id == request.repository_id,
                )
            )
            if existing is not None:
                return existing.id
            try:
                # As with the analysis-run key, a savepoint makes the unique
                # repository registration safe when identical API calls arrive
                # concurrently.
                with session.begin_nested():
                    record = RepositoryRecord(
                        provider="local",
                        provider_repository_id=request.repository_id,
                        repository_name=request.repository_path.name,
                        local_path=local_path,
                    )
                    session.add(record)
                    session.flush()
            except IntegrityError:
                existing = session.scalar(
                    select(RepositoryRecord).where(
                        RepositoryRecord.provider == "local",
                        RepositoryRecord.provider_repository_id == request.repository_id,
                    )
                )
                if existing is not None:
                    return existing.id
                raise
            return record.id

    def _repository_for_run(self, analysis_run_id: UUID) -> tuple[UUID, Path]:
        run = self._runs.get(analysis_run_id)
        if run is None:
            raise PersistenceError("analysis run was not found")
        with self._session_factory() as session:
            repository = session.get(RepositoryRecord, run.input.repository_id)
            if repository is None or repository.local_path is None:
                raise PersistenceError("analysis repository has no local path")
            return repository.id, Path(repository.local_path).resolve(strict=True)

    def _register_workload(
        self,
        repository_id: UUID,
        repository_path: Path,
        selection: WorkloadSelection,
    ) -> UUID:
        definition_path = _inside_repository(repository_path, selection.definition_path)
        definition_sha256 = sha256(definition_path.read_bytes()).hexdigest()
        with self._session_factory.begin() as session:
            existing = session.scalar(
                select(WorkloadRecord).where(
                    WorkloadRecord.repository_id == repository_id,
                    WorkloadRecord.name == selection.workload_id,
                )
            )
            if existing is not None:
                if (
                    existing.definition_path != selection.definition_path
                    or existing.definition_sha256 != definition_sha256
                    or existing.workload_type != selection.workload_type
                ):
                    raise PersistenceError(
                        "selected workload conflicts with its registered definition"
                    )
                return existing.id
            record = WorkloadRecord(
                repository_id=repository_id,
                name=selection.workload_id,
                workload_type=selection.workload_type,
                definition_path=selection.definition_path,
                definition_sha256=definition_sha256,
                selectors={
                    "reason": selection.reason,
                    "confidence": selection.confidence,
                    "metadata": selection.metadata,
                },
            )
            session.add(record)
            session.flush()
            return record.id

    def _workload_id(self, repository_id: UUID, workload_name: str | None) -> UUID | None:
        if workload_name is None:
            return None
        with self._session_factory() as session:
            workload = session.scalar(
                select(WorkloadRecord).where(
                    WorkloadRecord.repository_id == repository_id,
                    WorkloadRecord.name == workload_name,
                )
            )
            if workload is None:
                raise PersistenceError("verification workload was not registered")
            return workload.id


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise PersistenceError("analysis run ID is invalid") from error


def _inside_repository(repository_path: Path, definition_path: str) -> Path:
    pure = PurePosixPath(definition_path)
    if not definition_path or pure.is_absolute() or ".." in pure.parts or "\\" in definition_path:
        raise PersistenceError("workload definition path must be repository-relative")
    path = repository_path.joinpath(*pure.parts)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise PersistenceError("workload definition path is unavailable") from error
    if (
        path.is_symlink()
        or not resolved.is_relative_to(repository_path)
        or not resolved.is_file()
    ):
        raise PersistenceError("workload definition path is outside the repository")
    return resolved


def _utc_now() -> datetime:
    return datetime.now(UTC)
