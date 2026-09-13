"""INT-003 controller: offline patch proposals with independent live verification."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from sqlalchemy import select

from contracts import (
    AgentResult,
    Finding,
    PatchArtifact,
    RepositoryContext,
    VerificationResult,
    WorkloadSelection,
)
from lou.agents import OrchestrationInputs, OrchestrationLimits
from lou.agents.provider import ProviderRequest, ProviderResponse
from lou.application import (
    AnalysisRequest,
    FixtureWorkloadSelector,
    build_fixture_service,
    build_remediation_orchestrator,
    fixture_commands,
)
from lou.application.persistence import SqlAlchemyAnalysisStore
from lou.core.settings import Settings, get_settings
from lou.persistence import create_session_factory
from lou.persistence.models import EvidenceRecord
from lou.policies import AutonomyPolicy
from lou.scoring import DebtInputs, RemediationInputs
from lou.verification import DockerWorkloadRunner, PhaseObservations, disposable_worktree
from lou.verification.int002 import _seed

_MARKER = "__LOU_INT003_RESULT__"


class RecordedPatchProvider:
    """Offline provider that returns one reviewed, immutable fixture patch."""

    def __init__(self, patch_diff: str, *, case: str) -> None:
        self._patch_diff = patch_diff
        self._case = case

    def call(self, request: ProviderRequest) -> ProviderResponse:
        diagnosis = AgentResult(
            agent_run_id=f"{request.bundle.analysis_run_id}:{request.operation}:recorded",
            analysis_run_id=request.bundle.analysis_run_id,
            status="succeeded",
            diagnosis="The candidate makes one product query per checkout item.",
            plan="Restore the batched product lookup and rerun the selected workloads.",
            confidence=1.0,
            metadata={"provider": "recorded-fixture", "case": self._case},
        )
        if request.operation == "diagnose":
            return ProviderResponse(
                result=diagnosis,
                timeout_seconds=request.timeout_seconds,
                retry_count=0,
                tokens_used=0,
                estimated_cost_usd=0.0,
                elapsed_ms=0.0,
            )
        patch = PatchArtifact(
            patch_id=f"recorded-{self._case}-patch",
            analysis_run_id=request.bundle.analysis_run_id,
            base_commit_sha=request.bundle.candidate_commit_sha,
            patch_sha256=sha256(self._patch_diff.encode()).hexdigest(),
            artifact_uri=f"recorded://broken-store/{self._case}.diff",
            files_changed=1,
            lines_added=sum(line.startswith("+") for line in self._patch_diff.splitlines()[5:]),
            lines_deleted=sum(line.startswith("-") for line in self._patch_diff.splitlines()[5:]),
            metadata={"provider": "recorded-fixture", "case": self._case},
        )
        return ProviderResponse(
            result=diagnosis,
            patch_diff=self._patch_diff,
            patch_artifact=patch,
            timeout_seconds=request.timeout_seconds,
            retry_count=0,
            tokens_used=0,
            estimated_cost_usd=0.0,
            elapsed_ms=0.0,
        )


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments], check=True, capture_output=True, text=True
    )
    return result.stdout


def _patches(repository: Path, base: str, candidate: str) -> tuple[str, str]:
    """Return the actual repair and an applying-but-ineffective control patch."""

    good = _git(repository, "diff", candidate, base, "--", "store/app.py")
    source = repository / "store/app.py"
    original = source.read_text(encoding="utf-8")
    source.write_text(
        original + "\n# Recorded control patch: leaves the N+1 query path unchanged.\n"
    )
    try:
        bad = _git(repository, "diff", "--", "store/app.py")
    finally:
        source.write_text(original, encoding="utf-8")
    if not good or not bad:
        raise RuntimeError("fixture patches could not be constructed")
    return good, bad


def _inputs(
    *,
    settings: Settings,
    repository: Path,
    base: str,
    candidate: str,
    run_id: str,
    candidate_result: VerificationResult,
) -> tuple[
    OrchestrationInputs,
    DockerWorkloadRunner,
    tuple[PhaseObservations, PhaseObservations],
    tuple[WorkloadSelection, ...],
]:
    with create_session_factory(settings)() as session:
        evidence = session.scalar(
            select(EvidenceRecord).where(
                EvidenceRecord.analysis_run_id == run_id,
                EvidenceRecord.kind == "repository-context",
            )
        )
    if evidence is None or not isinstance(evidence.summary.get("context"), dict):
        raise RuntimeError("initial analysis did not persist repository context")
    context = RepositoryContext.model_validate(evidence.summary["context"])
    request = AnalysisRequest(
        repository_id=context.repository_id,
        repository_path=repository,
        base_commit_sha=base,
        candidate_commit_sha=candidate,
    )
    workloads = FixtureWorkloadSelector().select(context)
    from lou.application.live import _analysis_job

    job = _analysis_job(request, run_id, workloads)
    finding = Finding(
        finding_id=f"finding-{run_id}",
        analysis_run_id=run_id,
        fingerprint=f"runtime-regression:{candidate}",
        source="lou.verification",
        category="runtime-regression",
        severity="high",
        confidence=1.0,
        phase="candidate",
        title="Candidate checkout query regression",
        message="Checkout changed from a batched lookup to one query per item.",
        file_path="store/app.py",
        symbol_key="store.app.Store.checkout",
    )
    expected = PatchArtifact(
        patch_id=f"expected-{run_id}",
        analysis_run_id=run_id,
        base_commit_sha=candidate,
        patch_sha256="0" * 64,
        artifact_uri="recorded://broken-store/expected.diff",
        files_changed=1,
        lines_added=1,
        lines_deleted=1,
    )
    inputs = OrchestrationInputs(
        job=job,
        context=context,
        finding=finding,
        candidate_verification=candidate_result,
        workloads=workloads,
        expected_patch=expected,
        debt_inputs=DebtInputs(
            complexity=0.8,
            coverage_deficit=0.4,
            estimated_patch_size=0.2,
            churn=0.6,
            graph_centrality=0.7,
            runtime_impact=0.9,
            path_criticality=1.0,
            evidence_confidence=1.0,
        ),
        remediation_inputs=RemediationInputs(
            blast_radius=0.1,
            criticality=0.1,
            coverage=0.9,
            reversibility=1.0,
            verification_strength=1.0,
            patch_size=0.1,
            schema_migration_risk=0.0,
            data_migration_risk=0.0,
            context_completeness=1.0,
            evidence_confidence=1.0,
        ),
        policy=AutonomyPolicy(revision="1", max_autonomy=3),
        allowed_repository_root=repository,
        live_sources=("candidate_change", "selected_graph_context", "candidate_verification"),
    )
    runner = DockerWorkloadRunner(database_url=settings.fixture_database_url)
    with disposable_worktree(repository, base) as baseline_worktree:
        baseline = runner.run_phase(
            "baseline",
            repository=baseline_worktree,
            commit_sha=base,
            selections=workloads,
            commands=fixture_commands(),
            job=job,
            artifact_dir=settings.artifact_root / run_id / "remediation-baseline",
        )
    with disposable_worktree(repository, candidate) as candidate_worktree:
        observed_candidate = runner.run_phase(
            "candidate",
            repository=candidate_worktree,
            commit_sha=candidate,
            selections=workloads,
            commands=fixture_commands(),
            job=job,
            artifact_dir=settings.artifact_root / run_id / "remediation-candidate",
        )
    return inputs, runner, (baseline, observed_candidate), workloads


def _run_case(repository: Path, base: str, candidate: str, case: str) -> dict[str, object]:
    settings = get_settings()
    token = uuid4().hex
    analysis = build_fixture_service(settings).run(
        AnalysisRequest(
            repository_id=f"broken-store-int003-{case}-{token}",
            repository_path=repository,
            base_commit_sha=base,
            candidate_commit_sha=candidate,
            force_new_run=True,
            force_token=token,
        )
    )
    candidate_result = next(
        (item for item in analysis.verification_results if item.phase == "candidate"), None
    )
    if candidate_result is None:
        raise RuntimeError(
            "initial analysis did not yield candidate evidence: "
            f"status={analysis.status}, message={analysis.message}"
        )
    inputs, runner, observations, _ = _inputs(
        settings=settings,
        repository=repository,
        base=base,
        candidate=candidate,
        run_id=analysis.analysis_run_id,
        candidate_result=candidate_result,
    )
    good, bad = _patches(repository, base, candidate)
    orchestrator = build_remediation_orchestrator(
        inputs,
        store=SqlAlchemyAnalysisStore(create_session_factory(settings)),
        baseline=observations[0],
        candidate=observations[1],
        commands=fixture_commands(),
        runner=runner,
        artifact_root=settings.artifact_root / analysis.analysis_run_id / "fix",
        provider=RecordedPatchProvider(good if case == "good" else bad, case=case),
    )
    state = orchestrator.run(limits=OrchestrationLimits(max_attempts=1, max_wall_seconds=300))
    return {
        "run_id": analysis.analysis_run_id,
        "case": case,
        "termination_reason": state.termination_reason,
        "fix_statuses": [result.status for result in state.verification_results],
        "decision": state.decision.action if state.decision else None,
        "decision_level": state.decision.autonomy_level if state.decision else None,
    }


def _controller() -> int:
    from lou.verification.int002 import _controller as int002_controller

    # INT-002 passes its isolated database settings only to child processes.
    # This controller also runs composition in-process, so it must use the
    # same disposable Postgres instance rather than a developer's default DB.
    port = os.environ.get("LOU_INT002_POSTGRES_PORT", "55432")
    os.environ.update(
        {
            "LOU_MIGRATION_DATABASE_URL": (
                f"postgresql+psycopg://lou_migrator:lou_migrator@localhost:{port}/lou"
            ),
            "LOU_DATABASE_URL": f"postgresql+psycopg://lou:lou@localhost:{port}/lou",
            "LOU_FIXTURE_DATABASE_URL": "postgresql://lou_migrator:lou_migrator@postgres:5432/lou",
        }
    )
    get_settings.cache_clear()
    if int002_controller() != 0:
        return 1
    with TemporaryDirectory(prefix="lou-int003-fixture-") as directory:
        repository = Path(directory) / "broken-store"
        _seed(repository)
        base = _git(repository, "rev-parse", "good").strip()
        candidate = _git(repository, "rev-parse", "n-plus-one").strip()
        results = [_run_case(repository, base, candidate, case) for case in ("good", "bad")]
    print(_MARKER + json.dumps(results, sort_keys=True))
    good, bad = results
    return int(
        not (
            good["fix_statuses"] == ["passed", "passed"]
            and bad["fix_statuses"] == ["failed", "failed"]
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local INT-003 remediation rehearsal.")
    parser.parse_args()
    return _controller()


if __name__ == "__main__":
    raise SystemExit(main())
