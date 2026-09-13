"""Local rehearsal and narrowly-scoped Argo Rollouts adapters."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from contracts import Release
from lou.deployment.ports import DeploymentPort, VerificationFact, VerificationLookupPort
from lou.persistence.models import VerificationRunRecord


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
