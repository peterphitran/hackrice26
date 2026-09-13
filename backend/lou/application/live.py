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
    AutonomyDecision,
    CostEstimates,
    Evidence,
    LouDecision,
    RepositoryChange,
    RepositoryContext,
    RiskSignals,
    VerificationResult,
    WorkloadSelection,
)
from lou.application.analysis import AnalysisApplicationService, AnalysisRequest, VerificationBundle
from lou.core.settings import Settings
from lou.decision import decide_m5
from lou.policies import AutonomyPolicy
from lou.policies.engine import LocalPolicy
from lou.prediction import predict_impact
from lou.repository import (
    REGISTRY_REVISION,
    TraversalLimits,
    build_repository_context,
    build_repository_graph,
    parse_repository_changes,
    select_fixture_workloads,
    traverse_repository_impact,
)
from lou.repository import fixture_commands as fixture_commands
from lou.repository import fixture_workloads as fixture_workloads
from lou.repository.symbols import extract_changed_symbols
from lou.scoring import DebtInputs, RemediationInputs
from lou.telemetry import build_telemetry
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
        selected = _select_fixture_workloads(
            context,
            fallback_workload_ids=fallback_workload_ids,
        )
        prediction = predict_impact(
            analysis_run_id=run_id,
            change=change,
            snapshot=snapshot,
            traversal=traversal,
            workloads=selected,
            repository_root=request.repository_path,
        )
        context = context.model_copy(
            update={
                "selected_workload_ids": [item.workload_id for item in selected],
                "selection_reasons": {
                    **context.selection_reasons,
                    **{item.workload_id: item.reason for item in selected},
                },
                "metadata": {
                    **context.metadata,
                    "registry_revision": _REGISTRY_REVISION,
                    "analysis_run_id": run_id,
                    "graph_artifact_uri": snapshot.artifact_uri,
                    "fallback_workload_ids": list(fallback_workload_ids),
                    "impact_prediction": prediction.model_dump(mode="json"),
                },
            }
        )
        return change, context


class FixtureWorkloadSelector:
    """Return only registry workloads selected by the fixture context."""

    def select(self, context: RepositoryContext) -> tuple[WorkloadSelection, ...]:
        raw_fallback_ids = context.metadata.get("fallback_workload_ids", [])
        if not isinstance(raw_fallback_ids, list) or not all(
            isinstance(item, str) for item in raw_fallback_ids
        ):
            raise ValueError("repository context fallback_workload_ids must be a list of strings")
        return _select_fixture_workloads(
            context,
            fallback_workload_ids=tuple(raw_fallback_ids),
        )


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

    policy: AutonomyPolicy = AutonomyPolicy(revision="1", max_autonomy=1)

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
            for name, observed in _observed_debt_inputs(context).items():
                debt_values.setdefault(name, observed)
        remediation_values = _configured_values(request.configuration, "remediation_inputs")
        risk_values = _configured_values(request.configuration, "risk_signals")
        cost_values = _configured_values(request.configuration, "cost_estimates")
        decision = decide_m5(
            decision_id=f"decision_{run_id}",
            analysis_run_id=run_id,
            debt_inputs=DebtInputs.model_validate(debt_values),
            remediation_inputs=(
                RemediationInputs.model_validate(remediation_values) if remediation_values else None
            ),
            risk_signals=RiskSignals.model_validate(risk_values) if risk_values else None,
            cost_estimates=CostEstimates.model_validate(cost_values) if cost_values else None,
            policy_revision=request.policy_revision,
            policy_evaluator=LocalPolicy(
                revision=self.policy.revision, max_autonomy=min(self.policy.max_autonomy, 1)
            ),
            candidate_regression=candidate.result,
        )
        rationale = dict(decision.rationale)
        if classification != "runtime_regression" and decision.action != "report":
            rationale["composition_cap_reason"] = (
                "Only a measured runtime regression may receive a recommendation in this slice."
            )
            rationale["summary"] = "A0: report."
            rationale["reasons"] = [*rationale.get("reasons", []), "no_measured_runtime_regression"]
            rationale["m5_reasons"] = [
                *rationale.get("m5_reasons", []),
                "no_measured_runtime_regression",
            ]
            rationale["declined"] = True
            m5 = AutonomyDecision.model_validate(decision.metadata["m5"])
            capped = AutonomyDecision.model_validate(
                {
                    **m5.model_dump(mode="json"),
                    "permitted_level": 0,
                    "action": "report",
                    "denied_reasons": [*m5.denied_reasons, "no_measured_runtime_regression"],
                }
            )
            decision = decision.model_copy(
                update={
                    "action": "report",
                    "autonomy_level": 0,
                    "metadata": {**decision.metadata, "m5": capped.model_dump(mode="json")},
                }
            )
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


def _observed_debt_inputs(context: RepositoryContext) -> dict[str, float]:
    """Derive the debt features this slice actually measures; omit the rest.

    Only graph-backed observations are returned. complexity and coverage_deficit are
    deliberately absent because nothing here measures them — the scorer treats missing
    features as unknown and lowers confidence, which is the honest outcome.
    """
    reached = len(context.affected_symbols)
    changed = max(len(context.changed_symbols), 1)
    return {
        # How far the change reaches through the call graph, saturating at 10 symbols.
        "graph_centrality": min(reached / 10.0, 1.0),
        # A change on a served endpoint sits on a user-facing path.
        "path_criticality": 1.0 if context.affected_endpoints else 0.4,
        # Symbols touched relative to a 5-symbol repair budget.
        "estimated_patch_size": min(changed / 5.0, 1.0),
    }


def _select_fixture_workloads(
    context: RepositoryContext,
    *,
    fallback_workload_ids: tuple[str, ...] = (),
) -> tuple[WorkloadSelection, ...]:
    """Delegate registry resolution to repository intelligence."""

    return select_fixture_workloads(context, fallback_workload_ids=fallback_workload_ids)


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
