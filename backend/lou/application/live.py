"""Narrow local adapters for the Lou-owned broken-store fixture."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from contracts import (
    AnalysisJob,
    Evidence,
    LouDecision,
    RepositoryChange,
    RepositoryContext,
    VerificationResult,
    WorkloadSelection,
)
from lou.application.analysis import AnalysisApplicationService, AnalysisRequest, VerificationBundle
from lou.core.settings import Settings
from lou.decision.autonomy import decide_autonomy
from lou.policies import AutonomyPolicy
from lou.repository import parse_repository_changes
from lou.repository.symbols import extract_changed_symbols
from lou.scoring import DebtInputs, RemediationInputs
from lou.verification import PhaseObservations, compare_candidate, disposable_worktree

_REGISTRY_REVISION = "broken-store-v1"
_PYTEST_ID = "checkout-pytest"
_K6_ID = "checkout-load"
_PYTEST_PATH = "tests/test_checkout.py"
_K6_PATH = "loadtests/checkout.js"
_PYTEST_COMMAND = (
    "python",
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:cacheprovider",
    _PYTEST_PATH,
)
_PHASE = Literal["baseline", "candidate"]


class PhaseRunner(Protocol):
    """Runtime surface required by the live verification adapter."""

    def run_phase(
        self,
        phase: _PHASE,
        *,
        repository: Path,
        commit_sha: str,
        selections: tuple[WorkloadSelection, ...],
        commands: dict[str, tuple[str, ...]],
        job: AnalysisJob,
        artifact_dir: Path,
    ) -> PhaseObservations: ...


def fixture_workloads() -> tuple[WorkloadSelection, ...]:
    """Return the only two workloads allowed in the first local slice."""

    return (
        WorkloadSelection(
            workload_id=_PYTEST_ID,
            workload_type="pytest",
            definition_path=_PYTEST_PATH,
            phase="candidate",
            reason="Fixture checkout correctness gate.",
            confidence=1.0,
            metadata={"registry_revision": _REGISTRY_REVISION},
        ),
        WorkloadSelection(
            workload_id=_K6_ID,
            workload_type="k6",
            definition_path=_K6_PATH,
            phase="candidate",
            reason="Fixture checkout query-count workload.",
            confidence=1.0,
            metadata={"registry_revision": _REGISTRY_REVISION},
        ),
    )


def fixture_commands() -> dict[str, tuple[str, ...]]:
    """Return trusted command arrays, never executable CLI input."""

    return {_PYTEST_ID: _PYTEST_COMMAND}


class FixtureRepositoryIntelligence:
    """Static repository context for the checked-in broken-store fixture only."""

    def inspect(
        self, request: AnalysisRequest, run_id: str
    ) -> tuple[RepositoryChange, RepositoryContext]:
        change = parse_repository_changes(
            repository_id=request.repository_id,
            repository_path=request.repository_path,
            base_revision=request.base_commit_sha,
            candidate_revision=request.candidate_commit_sha,
        )
        change = extract_changed_symbols(repository_path=request.repository_path, change=change)
        selected = (
            fixture_workloads()
            if _has_fixture_layout(request.repository_path, request.candidate_commit_sha)
            else ()
        )
        checkout_changed = any(
            symbol.endswith("Store.checkout") for symbol in change.changed_symbols
        )
        context = RepositoryContext(
            repository_id=request.repository_id,
            commit_sha=request.candidate_commit_sha,
            changed_symbols=change.changed_symbols,
            affected_symbols=change.changed_symbols,
            affected_tests=[_PYTEST_PATH] if checkout_changed and selected else [],
            affected_endpoints=["POST /checkout"] if checkout_changed and selected else [],
            affected_data_dependencies=(
                ["broken_store.cart_items", "broken_store.products"]
                if checkout_changed and selected
                else []
            ),
            selected_workload_ids=[item.workload_id for item in selected],
            selection_reasons={item.workload_id: item.reason for item in selected},
            unresolved_relationships=(
                []
                if selected
                else ["No approved broken-store fixture workload is available for this repository."]
            ),
            completeness=1.0 if selected else 0.0,
            metadata={"registry_revision": _REGISTRY_REVISION, "analysis_run_id": run_id},
        )
        return change, context


class FixtureWorkloadSelector:
    """Return only registry workloads selected by the fixture context."""

    def select(self, context: RepositoryContext) -> tuple[WorkloadSelection, ...]:
        available = {item.workload_id: item for item in fixture_workloads()}
        return tuple(available[item] for item in context.selected_workload_ids if item in available)


@dataclass
class FixtureVerificationAdapter:
    """Measure immutable baseline/candidate worktrees and compare their evidence."""

    runner: PhaseRunner
    artifact_root: Path
    _baselines: dict[str, PhaseObservations] = field(default_factory=dict, init=False)

    def measure_baseline(
        self,
        request: AnalysisRequest,
        run_id: str,
        workloads: tuple[WorkloadSelection, ...],
    ) -> VerificationBundle:
        job = _analysis_job(request, run_id, workloads)
        observations = self._measure("baseline", request, job, workloads)
        self._baselines[run_id] = observations
        return _phase_bundle(
            "baseline",
            request.base_commit_sha,
            job,
            observations,
            self._phase_dir(run_id, "baseline"),
        )

    def measure_candidate(
        self,
        request: AnalysisRequest,
        run_id: str,
        workloads: tuple[WorkloadSelection, ...],
        baseline: VerificationBundle,
    ) -> VerificationBundle:
        job = _analysis_job(request, run_id, workloads)
        baseline_observations = self._baselines.get(run_id)
        if baseline_observations is None:
            raise ValueError("candidate comparison requires its baseline measurement")
        observations = self._measure("candidate", request, job, workloads)
        compared = compare_candidate(
            job,
            baseline_observations.checks,
            observations.checks,
            baseline_observations.load,
            observations.load,
            artifact_dir=self._phase_dir(run_id, "comparison"),
        )
        classification = (
            "inconclusive"
            if compared.verification.status == "inconclusive"
            else "runtime_regression"
            if compared.finding is not None
            else "candidate_test_failure"
            if compared.verification.status == "failed"
            else "clean"
        )
        verification = compared.verification.model_copy(
            update={
                "metadata": {
                    **compared.verification.metadata,
                    "classification": classification,
                    "registry_revision": _REGISTRY_REVISION,
                }
            }
        )
        return VerificationBundle(
            verification,
            (compared.finding,) if compared.finding is not None else (),
            (compared.evidence,),
        )

    def _measure(
        self,
        phase: _PHASE,
        request: AnalysisRequest,
        job: AnalysisJob,
        workloads: tuple[WorkloadSelection, ...],
    ) -> PhaseObservations:
        commit_sha = (
            request.base_commit_sha if phase == "baseline" else request.candidate_commit_sha
        )
        with disposable_worktree(request.repository_path, commit_sha) as workspace:
            return self.runner.run_phase(
                phase,
                repository=workspace,
                commit_sha=commit_sha,
                selections=workloads,
                commands=fixture_commands(),
                job=job,
                artifact_dir=self._phase_dir(job.analysis_run_id, phase),
            )

    def _phase_dir(self, run_id: str, phase: str) -> Path:
        return self.artifact_root / run_id / phase


@dataclass(frozen=True)
class FixtureDecisionAdapter:
    """Apply deterministic report/recommend policy to measured fixture evidence."""

    policy: AutonomyPolicy = AutonomyPolicy(revision="fixture-local-v1", max_autonomy=1)

    def decide(
        self,
        request: AnalysisRequest,
        run_id: str,
        baseline: VerificationBundle,
        candidate: VerificationBundle,
    ) -> LouDecision:
        classification = str(candidate.result.metadata.get("classification", "clean"))
        if classification == "inconclusive":
            raise ValueError("inconclusive evidence cannot reach the decision stage")
        debt_values = _configured_values(request.configuration, "debt_inputs")
        debt_values.setdefault(
            "runtime_impact", 1.0 if classification == "runtime_regression" else 0.0
        )
        debt_values.setdefault("evidence_confidence", 1.0)
        remediation_values = _configured_values(request.configuration, "remediation_inputs")
        decision = decide_autonomy(
            decision_id=f"decision_{run_id}",
            analysis_run_id=run_id,
            debt_inputs=DebtInputs.model_validate(debt_values),
            remediation_inputs=(
                RemediationInputs.model_validate(remediation_values) if remediation_values else None
            ),
            policy=AutonomyPolicy(
                revision=self.policy.revision, max_autonomy=min(self.policy.max_autonomy, 1)
            ),
            candidate_regression=candidate.result,
        )
        rationale = dict(decision.rationale)
        if classification != "runtime_regression" and decision.action != "report":
            rationale["composition_cap_reason"] = (
                "Only a measured runtime regression may receive a recommendation in this slice."
            )
            decision = decision.model_copy(update={"action": "report", "autonomy_level": 0})
        rationale["outcome_classification"] = classification
        rationale["outcome_rule"] = (
            "Measured regression meets the local recommendation policy."
            if decision.action == "recommend"
            else "Measured evidence is retained as a report; Lou will not generate a patch."
        )
        return decision.model_copy(
            update={
                "rationale": rationale,
                "metadata": {
                    **decision.metadata,
                    "outcome_classification": classification,
                    "composition_action_cap": "recommend",
                },
            }
        )


def _has_fixture_layout(repository: Path, candidate_sha: str) -> bool:
    for relative_path in (_PYTEST_PATH, _K6_PATH, "store/app.py"):
        result = subprocess.run(
            ["git", "-C", str(repository), "cat-file", "-e", f"{candidate_sha}:{relative_path}"],
            capture_output=True,
            check=False,
            timeout=10,
        )
        if result.returncode != 0:
            return False
    return True


def _analysis_job(
    request: AnalysisRequest, run_id: str, workloads: tuple[WorkloadSelection, ...]
) -> AnalysisJob:
    return AnalysisJob(
        analysis_run_id=run_id,
        repository_id=request.repository_id,
        repository_path=str(request.repository_path),
        base_commit_sha=request.base_commit_sha,
        candidate_commit_sha=request.candidate_commit_sha,
        policy_revision=request.policy_revision,
        toolchain_revision=request.toolchain_revision,
        verification_plan={
            "workloads": [item.workload_id for item in workloads],
            "minimum_query_count_increase": 10,
            "minimum_query_count_ratio": 5,
            "minimum_p95_regression_ratio": 2,
            "maximum_variance_cv": 0.2,
            "registry_revision": _REGISTRY_REVISION,
        },
        resource_limits={"k6_repetitions": 5, "timeout_seconds": 120, "cpus": 1, "memory_mb": 512},
    )


def _phase_bundle(
    phase: _PHASE,
    commit_sha: str,
    job: AnalysisJob,
    observations: PhaseObservations,
    artifact_dir: Path,
) -> VerificationBundle:
    command_failed = observations.load.command_failed or any(
        check.outcome == "command_failed" for check in observations.checks
    )
    test_failed = any(check.outcome == "test_failed" for check in observations.checks)
    status = "inconclusive" if command_failed else "failed" if test_failed else "passed"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "phase": phase,
        "commit_sha": commit_sha,
        "workload_id": observations.load.workload_id,
        "status": status,
        "metrics": dict(observations.load.metrics),
        "checks": [
            {"workload_id": item.workload_id, "outcome": item.outcome}
            for item in observations.checks
        ],
        "registry_revision": _REGISTRY_REVISION,
    }
    artifact = artifact_dir / "phase-summary.json"
    raw = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
    artifact.write_bytes(raw)
    evidence_id = f"evidence_{job.analysis_run_id}_{phase}"
    evidence = Evidence(
        evidence_id=evidence_id,
        analysis_run_id=job.analysis_run_id,
        phase=phase,
        kind="phase-verification",
        source="lou.application.live",
        collected_at=datetime.now(UTC),
        summary=payload,
        artifact_uri=f"file://{artifact.as_posix()}",
        artifact_sha256=hashlib.sha256(raw).hexdigest(),
    )
    result = VerificationResult(
        verification_run_id=f"verify_{job.analysis_run_id}_{phase}",
        analysis_run_id=job.analysis_run_id,
        phase=phase,
        commit_sha=commit_sha,
        status=cast(Any, status),
        workload_id=observations.load.workload_id,
        metrics=dict(observations.load.metrics),
        evidence_ids=[evidence_id],
        artifact_uri=evidence.artifact_uri,
        metadata={
            "classification": "inconclusive" if status == "inconclusive" else "clean",
            "registry_revision": _REGISTRY_REVISION,
        },
    )
    return VerificationBundle(result, (), (evidence,))


def _configured_values(configuration: dict[str, object], key: str) -> dict[str, object]:
    values = configuration.get(key, {})
    if not isinstance(values, dict) or not all(isinstance(name, str) for name in values):
        raise ValueError(f"configuration.{key} must be an object")
    return dict(values)


def build_fixture_service(settings: Settings) -> AnalysisApplicationService:
    """Compose the first local-only service without exposing persistence to the CLI."""

    from lou.application.persistence import SqlAlchemyAnalysisStore
    from lou.persistence import create_session_factory
    from lou.verification.runtime import DockerWorkloadRunner

    return AnalysisApplicationService(
        SqlAlchemyAnalysisStore(create_session_factory(settings)),
        FixtureRepositoryIntelligence(),
        FixtureWorkloadSelector(),
        FixtureVerificationAdapter(
            DockerWorkloadRunner(database_url=settings.fixture_database_url),
            settings.artifact_root,
        ),
        FixtureDecisionAdapter(),
    )
