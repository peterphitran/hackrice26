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
    DecisionInput,
    DecisionRepository,
    DecisionView,
    EvidenceInput,
    FindingInput,
    InvalidRunTransitionError,
    PersistedVerificationBundle,
    PersistenceError,
    ResultRepository,
    RunConflictError,
    RunStatus,
    VerificationRunInput,
    VerificationStatus,
)
from lou.persistence.models import (
    AnalysisRunRecord,
    EvidenceRecord,
    FindingRecord,
    LouDecisionRecord,
    VerificationRunRecord,
)


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


class SqlAlchemyResultRepository(ResultRepository):
    """Transactional adapter for verification attempts, findings, and evidence."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def append_verification(self, value: VerificationRunInput) -> UUID:
        _validate_artifact_pair(value.artifact_uri, value.artifact_sha256)
        with self._session_factory.begin() as session:
            record = VerificationRunRecord(
                contract_id=value.contract_id,
                analysis_run_id=value.analysis_run_id,
                workload_id=value.workload_id,
                phase=value.phase,
                attempt=value.attempt,
                status=value.status,
                commit_sha=value.commit_sha,
                aggregate_metrics=value.metrics,
                artifact_uri=value.artifact_uri,
                artifact_sha256=value.artifact_sha256,
            )
            session.add(record)
            session.flush()
            return record.id

    def complete_verification(self, verification_id: UUID, status: VerificationStatus) -> None:
        if status not in {"passed", "failed", "inconclusive"}:
            raise PersistenceError("verification completion requires a terminal status")
        with self._session_factory.begin() as session:
            record = session.get(VerificationRunRecord, verification_id)
            if record is None or record.status not in {"queued", "running"}:
                raise PersistenceError("verification attempt cannot be completed")
            record.status = status
            record.completed_at = datetime.now(UTC)

    def complete_with_evidence(
        self,
        verification_id: UUID,
        status: VerificationStatus,
        evidence: list[EvidenceInput],
    ) -> list[UUID]:
        """Complete an attempt and append evidence in one transaction."""

        if status not in {"passed", "failed", "inconclusive"}:
            raise PersistenceError("verification completion requires a terminal status")
        with self._session_factory.begin() as session:
            verification = session.get(VerificationRunRecord, verification_id)
            if verification is None or verification.status not in {"queued", "running"}:
                raise PersistenceError("verification attempt cannot be completed")
            records: list[EvidenceRecord] = []
            for value in evidence:
                _validate_artifact_pair(value.artifact_uri, value.artifact_sha256)
                if value.finding_id is not None:
                    finding = session.get(FindingRecord, value.finding_id)
                    if finding is None or finding.analysis_run_id != value.analysis_run_id:
                        raise PersistenceError("evidence finding must belong to the analysis run")
                records.append(
                    EvidenceRecord(
                        contract_id=value.contract_id,
                        analysis_run_id=value.analysis_run_id,
                        finding_id=value.finding_id,
                        phase=value.phase,
                        kind=value.kind,
                        source=value.source,
                        collected_at=value.collected_at,
                        summary=value.summary,
                        artifact_uri=value.artifact_uri,
                        artifact_sha256=value.artifact_sha256,
                    )
                )
            verification.status = status
            verification.completed_at = datetime.now(UTC)
            session.add_all(records)
            session.flush()
            return [record.id for record in records]

    def add_finding(self, value: FindingInput) -> tuple[UUID, bool]:
        with self._session_factory.begin() as session:
            existing = session.scalar(
                select(FindingRecord).where(
                    FindingRecord.analysis_run_id == value.analysis_run_id,
                    FindingRecord.phase == value.phase,
                    FindingRecord.fingerprint == value.fingerprint,
                )
            )
            if existing is not None:
                return existing.id, True
            record = FindingRecord(
                contract_id=value.contract_id,
                analysis_run_id=value.analysis_run_id,
                fingerprint=value.fingerprint,
                source=value.source,
                category=value.category,
                severity=value.severity,
                confidence=value.confidence,
                phase=value.phase,
                title=value.title,
                message=value.message,
                file_path=value.file_path,
                symbol_key=value.symbol_key,
                details=value.metadata,
            )
            session.add(record)
            session.flush()
            return record.id, False

    def append_evidence(self, value: EvidenceInput) -> UUID:
        _validate_artifact_pair(value.artifact_uri, value.artifact_sha256)
        with self._session_factory.begin() as session:
            if value.finding_id is not None:
                finding = session.get(FindingRecord, value.finding_id)
                if finding is None or finding.analysis_run_id != value.analysis_run_id:
                    raise PersistenceError("evidence finding must belong to the analysis run")
            record = EvidenceRecord(
                contract_id=value.contract_id,
                analysis_run_id=value.analysis_run_id,
                finding_id=value.finding_id,
                phase=value.phase,
                kind=value.kind,
                source=value.source,
                collected_at=value.collected_at,
                summary=value.summary,
                artifact_uri=value.artifact_uri,
                artifact_sha256=value.artifact_sha256,
            )
            session.add(record)
            session.flush()
            return record.id

    def persist_verification_bundle(
        self,
        verification: VerificationRunInput,
        findings: list[FindingInput],
        evidence: list[EvidenceInput],
    ) -> PersistedVerificationBundle:
        """Atomically persist one terminal verification and all derived observations."""

        _validate_artifact_pair(verification.artifact_uri, verification.artifact_sha256)
        if verification.status not in {"passed", "failed", "inconclusive"}:
            raise PersistenceError("bundle verification requires a terminal status")
        for finding_input in findings:
            if finding_input.analysis_run_id != verification.analysis_run_id:
                raise PersistenceError(
                    "bundle records must belong to the verification analysis run"
                )
        for evidence_input in evidence:
            if evidence_input.analysis_run_id != verification.analysis_run_id:
                raise PersistenceError(
                    "bundle records must belong to the verification analysis run"
                )
            _validate_artifact_pair(evidence_input.artifact_uri, evidence_input.artifact_sha256)

        with self._session_factory.begin() as session:
            verification_record = VerificationRunRecord(
                contract_id=verification.contract_id,
                analysis_run_id=verification.analysis_run_id,
                workload_id=verification.workload_id,
                phase=verification.phase,
                attempt=verification.attempt,
                status=verification.status,
                commit_sha=verification.commit_sha,
                aggregate_metrics=verification.metrics,
                artifact_uri=verification.artifact_uri,
                artifact_sha256=verification.artifact_sha256,
                completed_at=datetime.now(UTC),
            )
            session.add(verification_record)
            session.flush()

            finding_ids: dict[str, UUID] = {}
            for finding_input in findings:
                finding_id = self._add_finding_in_session(session, finding_input)
                if finding_input.contract_id is not None:
                    finding_ids[finding_input.contract_id] = finding_id

            evidence_ids: dict[str, UUID] = {}
            for evidence_input in evidence:
                if evidence_input.finding_id is not None:
                    finding = session.get(FindingRecord, evidence_input.finding_id)
                    if finding is None or finding.analysis_run_id != evidence_input.analysis_run_id:
                        raise PersistenceError("evidence finding must belong to the analysis run")
                record = EvidenceRecord(
                    contract_id=evidence_input.contract_id,
                    analysis_run_id=evidence_input.analysis_run_id,
                    finding_id=evidence_input.finding_id,
                    phase=evidence_input.phase,
                    kind=evidence_input.kind,
                    source=evidence_input.source,
                    collected_at=evidence_input.collected_at,
                    summary=evidence_input.summary,
                    artifact_uri=evidence_input.artifact_uri,
                    artifact_sha256=evidence_input.artifact_sha256,
                )
                session.add(record)
                session.flush()
                if evidence_input.contract_id is not None:
                    evidence_ids[evidence_input.contract_id] = record.id

            return PersistedVerificationBundle(
                verification_id=verification_record.id,
                finding_ids=finding_ids,
                evidence_ids=evidence_ids,
            )

    @staticmethod
    def _add_finding_in_session(session: Session, value: FindingInput) -> UUID:
        existing = session.scalar(
            select(FindingRecord).where(
                FindingRecord.analysis_run_id == value.analysis_run_id,
                FindingRecord.phase == value.phase,
                FindingRecord.fingerprint == value.fingerprint,
            )
        )
        if existing is not None:
            if value.contract_id and existing.contract_id not in {None, value.contract_id}:
                raise PersistenceError("finding contract ID conflicts with the existing finding")
            if value.contract_id and existing.contract_id is None:
                existing.contract_id = value.contract_id
            return existing.id
        record = FindingRecord(
            contract_id=value.contract_id,
            analysis_run_id=value.analysis_run_id,
            fingerprint=value.fingerprint,
            source=value.source,
            category=value.category,
            severity=value.severity,
            confidence=value.confidence,
            phase=value.phase,
            title=value.title,
            message=value.message,
            file_path=value.file_path,
            symbol_key=value.symbol_key,
            details=value.metadata,
        )
        session.add(record)
        session.flush()
        return record.id


class SqlAlchemyDecisionRepository(DecisionRepository):
    """Idempotent persistence adapter for one decision per analysis run."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save(self, value: DecisionInput) -> tuple[DecisionView, bool]:
        with self._session_factory.begin() as session:
            existing = session.scalar(
                select(LouDecisionRecord).where(
                    LouDecisionRecord.analysis_run_id == value.analysis_run_id
                )
            )
            if existing is not None:
                existing_view = _to_decision_view(existing)
                if existing_view.value != value:
                    raise PersistenceError("analysis run already has a different decision")
                return existing_view, True
            record = LouDecisionRecord(
                analysis_run_id=value.analysis_run_id,
                decision_id=value.decision_id,
                action=value.action,
                debt_risk=value.debt_risk,
                remediation_risk=value.remediation_risk,
                confidence=value.confidence,
                autonomy_level=value.autonomy_level,
                rationale=value.rationale,
                details=value.metadata,
            )
            session.add(record)
            session.flush()
            return _to_decision_view(record), False

    def get_for_run(self, analysis_run_id: UUID) -> DecisionView | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(LouDecisionRecord).where(
                    LouDecisionRecord.analysis_run_id == analysis_run_id
                )
            )
            return _to_decision_view(record) if record is not None else None


def _to_decision_view(record: LouDecisionRecord) -> DecisionView:
    return DecisionView(
        id=record.id,
        value=DecisionInput(
            analysis_run_id=record.analysis_run_id,
            decision_id=record.decision_id,
            action=record.action,  # type: ignore[arg-type]
            debt_risk=record.debt_risk,
            remediation_risk=record.remediation_risk,
            confidence=record.confidence,
            autonomy_level=record.autonomy_level,
            rationale=dict(record.rationale),
            metadata=dict(record.details),
        ),
    )


def _validate_artifact_pair(uri: str | None, digest: str | None) -> None:
    if (uri is None) != (digest is None):
        raise PersistenceError("artifact URI and SHA-256 must be supplied together")
