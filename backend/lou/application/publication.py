"""Trusted publication planning; dry runs never require a GitHub credential."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from contracts import PatchArtifact, PublicationPlan, PublicationResult
from lou.agents.orchestration import OrchestrationState
from lou.persistence.models import (
    AgentRunRecord,
    AnalysisRunRecord,
    PublicationAttemptRecord,
    RepositoryRecord,
)
from lou.reporting import EvidenceReportReader


class PublicationError(ValueError):
    """Safe publication-policy or composition failure."""


class GitHubPublisher(Protocol):
    """Trusted adapter: receives a validated plan, never an agent prompt."""

    def publish(self, plan: PublicationPlan, patch_diff: str) -> str: ...


class GitHubCliPublisher:
    """Explicit local publisher using an already-authenticated GitHub CLI session."""

    def publish(self, plan: PublicationPlan, patch_diff: str) -> str:
        # The patch itself is intentionally not passed to gh: a trusted future adapter
        # must create the branch from the verifier-owned fix commit first.
        del patch_diff
        command = [
            "gh",
            "pr",
            "create",
            "--repo",
            plan.repository,
            "--base",
            plan.base_branch,
            "--head",
            plan.branch_name,
            "--title",
            plan.title,
            "--body",
            plan.body,
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
        if result.returncode != 0 or not result.stdout.strip():
            raise PublicationError("GitHub publication failed")
        return result.stdout.strip()


@dataclass(frozen=True)
class PublicationService:
    session_factory: sessionmaker[Session]
    report_reader: EvidenceReportReader

    def dry_run(self, agent_run_id: UUID) -> PublicationResult:
        plan, _, dry_run_allowed, _ = self._plan(agent_run_id)
        if not dry_run_allowed:
            return self._save(plan, "denied", message="Verified patch evidence is unavailable.")
        return self._save(plan, "dry_run", message="No network action was requested.")

    def publish(
        self,
        agent_run_id: UUID,
        *,
        acknowledgement: str,
        publisher: GitHubPublisher,
    ) -> PublicationResult:
        if acknowledgement != "PUBLISH_VERIFIED_REMEDIATION":
            raise PublicationError("explicit publication acknowledgement is required")
        plan, patch_diff, _, publish_allowed = self._plan(agent_run_id)
        if not publish_allowed:
            return self._save(
                plan, "denied", message="Verified A3 publication evidence is unavailable."
            )
        existing = self._existing(plan.publication_plan_id)
        if existing is not None:
            return existing
        try:
            reference = publisher.publish(plan, patch_diff)
        except (OSError, subprocess.SubprocessError, PublicationError):
            return self._save(plan, "failed", message="Trusted publisher failed safely.")
        return self._save(plan, "published", provider_reference=reference)

    def _plan(self, agent_run_id: UUID) -> tuple[PublicationPlan, str, bool, bool]:
        with self.session_factory() as session:
            run = session.get(AgentRunRecord, agent_run_id)
            if run is None:
                raise PublicationError("remediation run was not found")
            analysis = session.get(AnalysisRunRecord, run.analysis_run_id)
            if analysis is None:
                raise PublicationError("analysis run was not found")
            repository = session.scalar(
                select(RepositoryRecord).where(RepositoryRecord.id == analysis.repository_id)
            )
        if repository is None:
            raise PublicationError("remediation repository was not found")
        try:
            state = OrchestrationState.model_validate(run.snapshot)
        except ValueError as error:
            raise PublicationError("remediation run has no valid completed state") from error
        patch_response = state.current_patch_response
        patch: PatchArtifact | None = (
            patch_response.patch_artifact if patch_response is not None else None
        )
        patch_diff = patch_response.patch_diff if patch_response is not None else None
        decision = state.decision
        evidence_hash = hashlib.sha256(
            self.report_reader.read(str(run.analysis_run_id)).render_json().encode("utf-8")
        ).hexdigest()
        plan_id = hashlib.sha256(f"m2-v2:{agent_run_id}:{evidence_hash}".encode()).hexdigest()
        plan = PublicationPlan(
            publication_plan_id=plan_id,
            agent_run_id=str(agent_run_id),
            analysis_run_id=str(run.analysis_run_id),
            decision_id=decision.decision_id if decision is not None else "unavailable",
            repository=_repository_name(repository),
            base_branch=repository.default_branch,
            branch_name=f"lou/remediation-{str(agent_run_id)[:8]}",
            title="Lou verified remediation",
            body=(
                "Generated only after independent verification.\n\n"
                f"Evidence report SHA-256: `{evidence_hash}`"
            ),
            patch_sha256=patch.patch_sha256 if patch is not None else "0" * 64,
            evidence_report_sha256=evidence_hash,
        )
        dry_run_allowed = (
            state.termination_reason == "verified"
            and decision is not None
            and patch is not None
            and patch_diff is not None
        )
        publish_allowed = (
            dry_run_allowed and decision is not None and decision.action == "open_pr"
            and decision.autonomy_level >= 3
        )
        return plan, patch_diff or "", dry_run_allowed, publish_allowed

    def _existing(self, plan_id: str) -> PublicationResult | None:
        with self.session_factory() as session:
            record = session.scalar(
                select(PublicationAttemptRecord).where(
                    PublicationAttemptRecord.publication_plan_id == plan_id
                )
            )
        return _result(record) if record is not None else None

    def _save(
        self,
        plan: PublicationPlan,
        status: str,
        *,
        provider_reference: str | None = None,
        message: str | None = None,
    ) -> PublicationResult:
        with self.session_factory.begin() as session:
            record = session.scalar(
                select(PublicationAttemptRecord).where(
                    PublicationAttemptRecord.publication_plan_id == plan.publication_plan_id
                )
            )
            if record is None:
                record = PublicationAttemptRecord(
                    agent_run_id=UUID(plan.agent_run_id),
                    publication_plan_id=plan.publication_plan_id,
                    plan=plan.model_dump(mode="json"),
                    status=status,
                    provider_reference=provider_reference,
                    message=message,
                )
                session.add(record)
                session.flush()
            return _result(record)


def _repository_name(repository: RepositoryRecord) -> str:
    return (
        f"{repository.owner_name}/{repository.repository_name}"
        if repository.owner_name
        else repository.repository_name
    )


def _result(record: PublicationAttemptRecord) -> PublicationResult:
    plan = record.plan
    return PublicationResult(
        publication_plan_id=record.publication_plan_id,
        agent_run_id=str(record.agent_run_id),
        status=record.status,  # type: ignore[arg-type]
        provider_reference=record.provider_reference,
        decision_id=str(plan.get("decision_id")) if plan.get("decision_id") else None,
        message=record.message,
        metadata={"created_at": record.created_at.isoformat() if record.created_at else None},
    )
