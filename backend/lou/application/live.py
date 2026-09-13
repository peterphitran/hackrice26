"""Narrow local adapters for the Lou-owned broken-store fixture."""

from __future__ import annotations

import hashlib
import json
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
from lou.prediction import predict_impact
from lou.repository import (
    REGISTRY_REVISION,
    PlanningLimits,
    TraversalLimits,
    ValidationPlan,
    build_repository_context,
    build_repository_graph,
    fixture_workload_registry,
    parse_repository_changes,
    plan_validation_workloads,
    traverse_repository_impact,
)
from lou.repository import fixture_commands as fixture_commands
from lou.repository import fixture_workloads as fixture_workloads
from lou.repository.symbols import extract_changed_symbols
from lou.scoring import DebtInputs, RemediationInputs
from lou.scoring.observed import graph_debt_features
from lou.telemetry import build_telemetry
from lou.telemetry.correlation import correlation_summary
from lou.verification import PhaseObservations, compare_candidate, disposable_worktree

_REGISTRY_REVISION = REGISTRY_REVISION
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


@dataclass(frozen=True)
class FixtureRepositoryIntelligence:
    """Graph-backed repository context for the checked-in broken-store fixture only."""

    artifact_root: Path = Path(".lou/artifacts")
    traversal_limits: TraversalLimits = TraversalLimits()
    planning_limits: PlanningLimits = PlanningLimits()
    fallback_workload_ids: tuple[str, ...] = ()

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
        snapshot = build_repository_graph(
            repository_path=request.repository_path,
            change=change,
            analysis_run_id=run_id,
            artifact_root=self.artifact_root,
        )
        traversal = traverse_repository_impact(snapshot, limits=self.traversal_limits)
        context = build_repository_context(
            traversal,
            repository_id=change.repository_id,
            commit_sha=change.candidate_commit_sha,
        )
        fallback_workload_ids = self.fallback_workload_ids if snapshot.completeness < 1 else ()
        plan = plan_validation_workloads(
            context,
            fixture_workload_registry(),
            limits=self.planning_limits,
            fallback_workload_ids=fallback_workload_ids,
        )
        prediction = predict_impact(
            analysis_run_id=run_id,
            change=change,
            snapshot=snapshot,
            traversal=traversal,
            workloads=plan.selected,
            repository_root=request.repository_path,
        )
        context = context.model_copy(
            update={
                "selected_workload_ids": [item.workload_id for item in plan.selected],
                "selection_reasons": {
                    **context.selection_reasons,
                    **{item.workload_id: item.reason for item in plan.selected},
                },
                "metadata": {
                    **context.metadata,
                    "registry_revision": _REGISTRY_REVISION,
                    "analysis_run_id": run_id,
                    "graph_artifact_uri": snapshot.artifact_uri,
                    "fallback_workload_ids": list(fallback_workload_ids),
                    "impact_prediction": prediction.model_dump(mode="json"),
                    "validation_plan": plan.payload(),
                    "runtime_correlations": {
                        name: correlation_summary(name, snapshot)
                        for name in sorted(
                            {*context.changed_symbols, *context.affected_data_dependencies}
                        )
                    },
                },
            }
        )
        return change, context


@dataclass(frozen=True)
class FixtureWorkloadSelector:
    """Return only registry workloads selected by the fixture context."""

    planning_limits: PlanningLimits = PlanningLimits()

    def plan(self, context: RepositoryContext) -> ValidationPlan:
        """Return the full explainable plan while keeping ``select`` compatible."""

        raw_fallback_ids = context.metadata.get("fallback_workload_ids", [])
        if not isinstance(raw_fallback_ids, list) or not all(
            isinstance(item, str) for item in raw_fallback_ids
        ):
            raise ValueError("repository context fallback_workload_ids must be a list of strings")
        return plan_validation_workloads(
            context,
            fixture_workload_registry(),
            limits=self.planning_limits,
            fallback_workload_ids=tuple(raw_fallback_ids),
        )

    def select(self, context: RepositoryContext) -> tuple[WorkloadSelection, ...]:
        return self.plan(context).selected


@dataclass
class FixtureVerificationAdapter:
    """Measure immutable baseline/candidate worktrees and compare their evidence."""

    runner: PhaseRunner
    artifact_root: Path
    _baselines: dict[str, PhaseObservations] = field(default_factory=dict, init=False)
    _plans: dict[str, tuple[tuple[str, str, str], ...]] = field(default_factory=dict, init=False)

    def measure_baseline(
        self,
        request: AnalysisRequest,
        run_id: str,
        workloads: tuple[WorkloadSelection, ...],
    ) -> VerificationBundle:
        job = _analysis_job(request, run_id, workloads)
        self._plans[run_id] = _plan_identity(workloads)
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
        if self._plans.get(run_id) != _plan_identity(workloads):
            raise ValueError("candidate verification must reuse the finalized baseline plan")
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
        context: RepositoryContext | None = None,
    ) -> LouDecision:
        classification = str(candidate.result.metadata.get("classification", "clean"))
        if classification == "inconclusive":
            raise ValueError("inconclusive evidence cannot reach the decision stage")
        debt_values = _configured_values(request.configuration, "debt_inputs")
        debt_values.setdefault(
            "runtime_impact", 1.0 if classification == "runtime_regression" else 0.0
        )
        debt_values.setdefault("evidence_confidence", 1.0)
        if context is not None:
            for name, observed in graph_debt_features(context).items():
                debt_values.setdefault(name, observed)
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


def _plan_identity(
    workloads: tuple[WorkloadSelection, ...],
) -> tuple[tuple[str, str, str], ...]:
    """Bind all comparison phases to the same ordered registry definitions."""

    return tuple((item.workload_id, item.workload_type, item.definition_path) for item in workloads)


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
    load = observations.load
    command_failed = (load.command_failed if load is not None else False) or any(
        check.outcome == "command_failed" for check in observations.checks
    )
    test_failed = any(check.outcome == "test_failed" for check in observations.checks)
    status = "inconclusive" if command_failed else "failed" if test_failed else "passed"
    workload_id = (
        load.workload_id
        if load is not None
        else observations.checks[0].workload_id
        if len(observations.checks) == 1
        else None
    )
    metrics = dict(load.metrics) if load is not None else {}
    artifact_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "phase": phase,
        "commit_sha": commit_sha,
        "workload_id": workload_id,
        "status": status,
        "metrics": metrics,
        "checks": [
            {"workload_id": item.workload_id, "outcome": item.outcome}
            for item in observations.checks
        ],
        "runtime_comparison_eligible": load is not None,
        "load_workload_status": "measured" if load is not None else "not_selected",
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
        workload_id=workload_id,
        metrics=metrics,
        evidence_ids=[evidence_id],
        artifact_uri=evidence.artifact_uri,
        metadata={
            "classification": "inconclusive" if status == "inconclusive" else "clean",
            "registry_revision": _REGISTRY_REVISION,
            "runtime_comparison_performed": False,
            "load_workload_status": "measured" if load is not None else "not_selected",
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
        FixtureRepositoryIntelligence(settings.artifact_root),
        FixtureWorkloadSelector(),
        FixtureVerificationAdapter(
            DockerWorkloadRunner(database_url=settings.fixture_database_url),
            settings.artifact_root,
        ),
        FixtureDecisionAdapter(),
        build_telemetry(
            exporter=settings.telemetry_exporter,
            endpoint=settings.telemetry_otlp_endpoint,
            timeout_seconds=settings.telemetry_export_timeout_seconds,
            sample_rate=settings.telemetry_sample_rate,
            max_spans=settings.telemetry_max_spans_per_run,
        ),
    )
