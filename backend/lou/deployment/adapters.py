"""Local rehearsal and narrowly-scoped Argo Rollouts adapters."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from contracts import Release
from lou.deployment.ports import (
    DeploymentPort,
    TraceFact,
    TraceLookupPort,
    VerificationFact,
    VerificationLookupPort,
)
from lou.deployment.service import ChainedJournal, DeploymentConflictError
from lou.persistence.models import (
    AnalysisRunRecord,
    DeploymentJournalRecord,
    EvidenceRecord,
    VerificationRunRecord,
)

RUNTIME_OBSERVATION_KIND = "runtime-observation"


@dataclass
class InMemoryVerificationLookup(VerificationLookupPort):
    """Deterministic verification lookup for tests and local rehearsal."""

    facts: dict[str, VerificationFact] = field(default_factory=dict)

    def record(self, fact: VerificationFact) -> None:
        self.facts[fact.verification_run_id] = fact

    def get(self, verification_run_id: str) -> VerificationFact | None:
        return self.facts.get(verification_run_id)


@dataclass(frozen=True)
class SqlAlchemyVerificationLookup(VerificationLookupPort):
    """Read verification runs from the durable store by contract ID."""

    session_factory: sessionmaker[Session]

    def get(self, verification_run_id: str) -> VerificationFact | None:
        try:
            with self.session_factory() as session:
                record = session.execute(
                    select(VerificationRunRecord).where(
                        VerificationRunRecord.contract_id == verification_run_id
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError:
            # An unreadable store cannot confirm health, so the canary pauses.
            return None
        if record is None:
            return None
        return VerificationFact(
            verification_run_id=verification_run_id,
            analysis_run_id=str(record.analysis_run_id),
            commit_sha=record.commit_sha,
            status=record.status,
        )


@dataclass
class InMemoryTraceLookup(TraceLookupPort):
    """Deterministic trace lookup for tests and local rehearsal."""

    facts: dict[str, TraceFact] = field(default_factory=dict)

    def record(self, fact: TraceFact) -> None:
        self.facts[fact.trace_id] = fact

    def get(self, trace_id: str) -> TraceFact | None:
        return self.facts.get(trace_id)


@dataclass(frozen=True)
class SqlAlchemyTraceLookup(TraceLookupPort):
    """Resolve a trace to the analysis run whose telemetry actually recorded it."""

    session_factory: sessionmaker[Session]

    def get(self, trace_id: str) -> TraceFact | None:
        statement = (
            select(
                AnalysisRunRecord.id,
                AnalysisRunRecord.candidate_commit_sha,
                func.count(EvidenceRecord.id),
            )
            .join(EvidenceRecord, EvidenceRecord.analysis_run_id == AnalysisRunRecord.id)
            .where(
                EvidenceRecord.kind == RUNTIME_OBSERVATION_KIND,
                EvidenceRecord.summary["trace_id"].astext == trace_id,
            )
            .group_by(AnalysisRunRecord.id, AnalysisRunRecord.candidate_commit_sha)
        )
        try:
            with self.session_factory() as session:
                rows = session.execute(statement).all()
        except SQLAlchemyError:
            # An unreadable store cannot confirm a trace, so the canary pauses.
            return None
        if len(rows) != 1:
            # No recorded trace, or one claimed by several runs: neither is confirmable.
            return None
        analysis_run_id, commit_sha, observation_count = rows[0]
        return TraceFact(
            trace_id=trace_id,
            analysis_run_id=str(analysis_run_id),
            commit_sha=commit_sha,
            observation_count=int(observation_count),
        )


class SqlAlchemyDeploymentJournal(ChainedJournal):
    """The durable deployment journal: hash-chained, append-only PostgreSQL rows."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def _read(self, release_id: str) -> list[dict[str, object]]:
        statement = (
            select(
                DeploymentJournalRecord.body,
                DeploymentJournalRecord.previous_hash,
                DeploymentJournalRecord.record_hash,
            )
            .where(DeploymentJournalRecord.release_id == release_id)
            .order_by(DeploymentJournalRecord.sequence)
        )
        with self._session_factory() as session:
            rows = session.execute(statement).all()
        return [
            {**body, "previous_hash": previous_hash, "record_hash": record_hash}
            for body, previous_hash, record_hash in rows
        ]

    def _write(self, release_id: str, sequence: int, record: dict[str, object]) -> None:
        body = self.body_of(record)
        try:
            with self._session_factory.begin() as session:
                session.add(
                    DeploymentJournalRecord(
                        release_id=release_id,
                        sequence=sequence,
                        kind=str(body["kind"]),
                        body=body,
                        previous_hash=str(record["previous_hash"]),
                        record_hash=str(record["record_hash"]),
                    )
                )
        except IntegrityError as error:
            # Another writer already claimed this position in the chain.
            raise DeploymentConflictError("deployment journal was appended concurrently") from error


@dataclass
class InMemoryDeploymentAdapter(DeploymentPort):
    """Deterministic staging adapter used by tests and local rehearsal."""

    actions: list[tuple[str, str]] = field(default_factory=list)

    def release(self, release: Release) -> None:
        self._record("release", release)

    def promote(self, release: Release) -> None:
        self._record("promote", release)

    def pause(self, release: Release) -> None:
        self._record("pause", release)

    def rollback(self, release: Release) -> None:
        self._record("rollback", release)

    def _record(self, action: str, release: Release) -> None:
        if release.target.environment != "staging":
            raise ValueError("the local deployment adapter only permits staging")
        item = (action, release.release_id)
        if item not in self.actions:
            self.actions.append(item)


@dataclass(frozen=True)
class ArgoRolloutsAdapter(DeploymentPort):
    """Thin trusted adapter; it never stores or returns controller credentials."""

    kubectl: str = "kubectl"
    timeout_seconds: int = 30

    def release(self, release: Release) -> None:
        self._run("restart", release)

    def promote(self, release: Release) -> None:
        self._run("promote", release)

    def pause(self, release: Release) -> None:
        self._run("pause", release)

    def rollback(self, release: Release) -> None:
        self._run("abort", release)

    def _run(self, action: str, release: Release) -> None:
        if release.target.environment != "staging":
            raise ValueError("M8 does not permit production deployment")
        command = [
            self.kubectl,
            "argo",
            "rollouts",
            action,
            release.target.service,
            "--namespace",
            release.target.namespace,
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=self.timeout_seconds, check=False
        )
        if result.returncode:
            raise RuntimeError("Argo Rollouts command failed")
