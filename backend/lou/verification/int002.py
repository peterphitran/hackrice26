"""Honest INT-002 demo wiring around the shared application service."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from contracts import (
    AnalysisJob,
    Evidence,
    LouDecision,
    RepositoryChange,
    RepositoryContext,
    VerificationResult,
    WorkloadSelection,
)
from lou.application import AnalysisApplicationService, AnalysisRequest, VerificationBundle
from lou.application.persistence import SqlAlchemyAnalysisStore
from lou.core.settings import get_settings
from lou.persistence.database import create_database_engine, create_session_factory
from lou.persistence.models import AnalysisRunRecord, EvidenceRecord, FindingRecord
from lou.repository.changes import parse_repository_changes
from lou.verification.compare import compare_candidate
from lou.verification.fix import PhaseObservations
from lou.verification.runtime import DockerWorkloadRunner

_BACKEND = Path(__file__).resolve().parents[2]
_FIXTURE_SEED = _BACKEND / "fixtures/broken-store/scripts/seed_fixture_repo.py"
_MARKER = "__LOU_INT002_RESULT__"
_MAX_VARIANCE_CV = 0.5
_COMMANDS = {
    "checkout-pytest": (
        "python",
        "-m",
        "pytest",
        "-q",
        "tests/test_checkout.py::test_checkout_receipt_stays_correct",
    )
}


class StubGraphContextIntelligence:
    """Use the live diff while RI-003/004 graph context remains unavailable."""

    def inspect(
        self, request: AnalysisRequest, run_id: str
    ) -> tuple[RepositoryChange, RepositoryContext]:
        change = parse_repository_changes(
            repository_id=request.repository_id,
            repository_path=request.repository_path,
            base_revision=request.base_commit_sha,
            candidate_revision=request.candidate_commit_sha,
        )
        return change, RepositoryContext(
            repository_id=request.repository_id,
            commit_sha=request.candidate_commit_sha,
            changed_symbols=["store.app.Store.checkout"],
            affected_tests=["tests/test_checkout.py::test_checkout_receipt_stays_correct"],
            affected_endpoints=["POST /checkout"],
            affected_data_dependencies=["broken_store.cart_items", "broken_store.products"],
            selected_workload_ids=["checkout-pytest", "checkout-k6"],
            selection_reasons={
                "checkout-pytest": "[STUB: RI-003/004] Known fixture checkout test.",
                "checkout-k6": "[STUB: RI-003/004] Known fixture POST /checkout scenario.",
            },
            unresolved_relationships=["[STUB: RI-003/004] Repository graph is not available."],
            completeness=0.25,
            metadata={"stub": True, "replaced_by": "Steven's RepositoryIntelligencePort"},
        )


class StubCheckoutWorkloadSelector:
    """Temporary RI-005 replacement using the checked-in fixture workloads."""

    def select(self, context: RepositoryContext) -> tuple[WorkloadSelection, ...]:
        return (
            WorkloadSelection(
                workload_id="checkout-pytest",
                workload_type="pytest",
                definition_path="tests/test_checkout.py",
                phase="candidate",
                reason="[STUB: RI-005] Fixed checkout unit workload.",
                confidence=0.5,
                metadata={"stub": True, "selected_from_context": context.commit_sha},
            ),
            WorkloadSelection(
                workload_id="checkout-k6",
                workload_type="k6",
                definition_path="loadtests/checkout.js",
                phase="candidate",
                reason="[STUB: RI-005] Fixed checkout runtime workload.",
                confidence=0.5,
                metadata={"stub": True, "selected_from_context": context.commit_sha},
            ),
        )


class Int002VerificationPort:
    """Run live baseline and candidate observations through EV-004/005/006."""

    def __init__(self, artifact_root: Path, database_url: str) -> None:
        self.artifact_root = artifact_root
        self.runner = DockerWorkloadRunner(database_url=database_url)
        self._baseline: dict[str, PhaseObservations] = {}

    @staticmethod
    def _job(request: AnalysisRequest, run_id: str) -> AnalysisJob:
        return AnalysisJob(
            analysis_run_id=run_id,
            repository_id=request.repository_id,
            repository_path=str(request.repository_path),
            base_commit_sha=request.base_commit_sha,
            candidate_commit_sha=request.candidate_commit_sha,
            verification_plan={
                "workloads": ["checkout-pytest", "checkout-k6"],
                "minimum_p95_regression_ratio": 2,
                "minimum_query_count_increase": 10,
                "minimum_query_count_ratio": 5,
                "maximum_variance_cv": _MAX_VARIANCE_CV,
                "unit_tests_must_pass": True,
            },
            resource_limits={
                "cpus": 1,
                "memory_mb": 512,
                "pids": 128,
                "disk_mb": 64,
                "timeout_seconds": 120,
                "k6_repetitions": 5,
            },
        )

    def _observe(
        self,
        phase: Literal["baseline", "candidate"],
        request: AnalysisRequest,
        run_id: str,
        workloads: tuple[WorkloadSelection, ...],
    ) -> PhaseObservations:
        commit = request.base_commit_sha if phase == "baseline" else request.candidate_commit_sha
        with TemporaryDirectory(prefix=f"lou-int002-{phase}-") as directory:
            worktree = Path(directory) / "worktree"
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(request.repository_path),
                    "worktree",
                    "add",
                    "--detach",
                    str(worktree),
                    commit,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            try:
                return self.runner.run_phase(
                    phase,
                    repository=worktree,
                    commit_sha=commit,
                    selections=workloads,
                    commands=_COMMANDS,
                    job=self._job(request, run_id),
                    artifact_dir=self.artifact_root / run_id / phase,
                )
            finally:
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(request.repository_path),
                        "worktree",
                        "remove",
                        "--force",
                        str(worktree),
                    ],
                    check=False,
                    capture_output=True,
                )

    def measure_baseline(
        self,
        request: AnalysisRequest,
        run_id: str,
        workloads: tuple[WorkloadSelection, ...],
    ) -> VerificationBundle:
        observations = self._observe("baseline", request, run_id, workloads)
        self._baseline[run_id] = observations
        failed = observations.load.command_failed or any(
            check.outcome != "passed" for check in observations.checks
        )
        noisy = observations.load.metrics.get("p95_variance_cv", 0) > _MAX_VARIANCE_CV
        status: Literal["passed", "inconclusive"] = "inconclusive" if failed or noisy else "passed"
        artifact = self.artifact_root / run_id / "baseline/load/aggregate.json"
        evidence = Evidence(
            evidence_id=f"evidence_{run_id}_baseline",
            analysis_run_id=run_id,
            phase="baseline",
            kind="k6-summary",
            source="lou.verification",
            collected_at=datetime.now(UTC),
            summary={
                "metrics": dict(observations.load.metrics),
                "checks": {check.workload_id: check.outcome for check in observations.checks},
            },
            artifact_uri=f"file://{artifact.as_posix()}",
            artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
        )
        result = VerificationResult(
            verification_run_id=f"verify_{run_id}_baseline",
            analysis_run_id=run_id,
            phase="baseline",
            commit_sha=request.base_commit_sha,
            status=status,
            workload_id="checkout-k6",
            metrics=dict(observations.load.metrics),
            evidence_ids=[evidence.evidence_id],
            artifact_uri=evidence.artifact_uri,
            metadata={"checks": evidence.summary["checks"], "source": "[LIVE] EV-004/005"},
        )
        return VerificationBundle(result, evidence=(evidence,))

    def measure_candidate(
        self,
        request: AnalysisRequest,
        run_id: str,
        workloads: tuple[WorkloadSelection, ...],
        baseline: VerificationBundle,
    ) -> VerificationBundle:
        del baseline
        before = self._baseline.pop(run_id)
        after = self._observe("candidate", request, run_id, workloads)
        comparison = compare_candidate(
            self._job(request, run_id),
            before.checks,
            after.checks,
            before.load,
            after.load,
            artifact_dir=self.artifact_root / run_id / "comparison",
        )
        findings = (comparison.finding,) if comparison.finding else ()
        return VerificationBundle(comparison.verification, findings, (comparison.evidence,))


class StubReportDecision:
    """Keep PF-005 visibly blocked while allowing the application run to finalize."""

    def decide(
        self,
        request: AnalysisRequest,
        run_id: str,
        baseline: VerificationBundle,
        candidate: VerificationBundle,
    ) -> LouDecision:
        del request, baseline
        return LouDecision(
            decision_id=f"decision_{run_id}_int002",
            analysis_run_id=run_id,
            debt_risk=1 if candidate.result.status == "failed" else 0,
            remediation_risk=None,
            confidence=1,
            autonomy_level=0,
            action="report",
            rationale={
                "summary": "[STUB: PF-005] Runtime result detected; report renderer unavailable."
            },
            metadata={"stub": True, "blocked_by": "PF-005"},
        )


def _worker(repository: Path, base: str, candidate: str) -> int:
    settings = get_settings()
    invocation_id = uuid4().hex
    artifact_root = (
        settings.artifact_root
        if settings.artifact_root.is_absolute()
        else (_BACKEND / settings.artifact_root).resolve()
    )
    print("[SUBSTITUTION: apps/cli] Owned module entrypoint; Peter coordination required")
    print("[LIVE] AnalysisApplicationService + PostgreSQL persistence")
    print("[LIVE] RI-001 diff parsing")
    print("[STUB: RI-003/004] Fixture graph context; completeness=0.25")
    print("[STUB: RI-005] checkout-pytest + checkout-k6 workload selection")
    print("[LIVE] EV-004/005/006 Docker verification and differential verdict")
    print("[STUB: PF-005] report-only decision; no real report renderer")
    request = AnalysisRequest(
        repository_id=f"broken-store-int002-{invocation_id}",
        repository_path=repository,
        base_commit_sha=base,
        candidate_commit_sha=candidate,
        force_new_run=True,
        force_token=invocation_id,
        configuration={
            "substitutions": {
                "entrypoint": "[SUBSTITUTION: apps/cli] owned integration module",
                "repository_context": "[STUB: RI-003/004] fixture-known relationships",
                "workload_selection": "[STUB: RI-005] fixed checkout workloads",
                "decision": "[STUB: PF-005] report-only decision",
            }
        },
    )
    service = AnalysisApplicationService(
        SqlAlchemyAnalysisStore(create_session_factory(settings)),
        StubGraphContextIntelligence(),
        StubCheckoutWorkloadSelector(),
        Int002VerificationPort(
            artifact_root,
            "postgresql://lou:lou@lou-int002-postgres-1:5432/lou",
        ),
        StubReportDecision(),
    )
    result = service.run(request)
    candidate_result = next(
        (item for item in result.verification_results if item.phase == "candidate"), None
    )
    if candidate_result:
        metrics = candidate_result.metrics
        print(
            "[LIVE RESULT] queries "
            f"{metrics.get('baseline_query_count', 0):g} -> "
            f"{metrics.get('candidate_query_count', 0):g}; "
            f"throughput {metrics.get('baseline_throughput', 0):.1f} -> "
            f"{metrics.get('candidate_throughput', 0):.1f}/s; "
            f"verdict={candidate_result.status}"
        )
    print("[BLOCKED: PF-005] No persisted human-readable report exists yet.")
    print(_MARKER + json.dumps({"run_id": result.analysis_run_id, "status": result.status}))
    return 0 if result.status == "succeeded" and candidate_result else 1


def _read_back(run_id: str) -> int:
    engine = create_database_engine()
    with Session(engine) as session:
        identifier = UUID(run_id)
        run = session.get(AnalysisRunRecord, identifier)
        evidence = session.scalar(
            select(func.count())
            .select_from(EvidenceRecord)
            .where(EvidenceRecord.analysis_run_id == identifier)
        )
        findings = session.scalar(
            select(func.count())
            .select_from(FindingRecord)
            .where(FindingRecord.analysis_run_id == identifier)
        )
    engine.dispose()
    if run is None:
        print(f"[READ-BACK FAILED] analysis run {run_id} was not found")
        return 1
    print(
        f"[LIVE READ-BACK AFTER WORKER EXIT] run={run.id} status={run.status} "
        f"evidence={evidence} findings={findings}"
    )
    return 0 if run.status == "succeeded" and evidence and findings else 1


def _controller() -> int:
    port = os.environ.get("LOU_INT002_POSTGRES_PORT", "55432")
    environment = os.environ.copy()
    environment.update(
        {
            "LOU_INT002_POSTGRES_PORT": port,
            "LOU_POSTGRES_PORT": port,
            "LOU_MIGRATION_DATABASE_URL": (
                f"postgresql+psycopg://lou_migrator:lou_migrator@localhost:{port}/lou"
            ),
            "LOU_DATABASE_URL": f"postgresql+psycopg://lou:lou@localhost:{port}/lou",
        }
    )
    subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            "lou-int002",
            "-f",
            str(_BACKEND / "infra/compose.yaml"),
            "up",
            "-d",
            "--wait",
        ],
        env=environment,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from alembic import command; from alembic.config import Config; "
            "config=Config(sys.argv[1]); config.set_main_option('script_location', sys.argv[2]); "
            "command.upgrade(config, 'head')",
            str(_BACKEND / "alembic.ini"),
            str(_BACKEND / "alembic"),
        ],
        cwd=_BACKEND.parent,
        env=environment,
        check=True,
    )
    with TemporaryDirectory(prefix="lou-int002-fixture-") as directory:
        repository = Path(directory) / "broken-store"
        subprocess.run([sys.executable, str(_FIXTURE_SEED), str(repository)], check=True)
        base = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "good"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        candidate = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "n-plus-one"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        worker = subprocess.run(
            [
                sys.executable,
                "-m",
                "lou.verification.int002",
                "worker",
                "--repo",
                str(repository),
                "--base",
                base,
                "--candidate",
                candidate,
            ],
            cwd=_BACKEND,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        print(worker.stdout, end="", flush=True)
        if worker.stderr:
            print(worker.stderr, file=sys.stderr, end="")
        if worker.returncode:
            return worker.returncode
        result_line = next(line for line in worker.stdout.splitlines() if line.startswith(_MARKER))
        run_id = json.loads(result_line.removeprefix(_MARKER))["run_id"]
        return subprocess.run(
            [sys.executable, "-m", "lou.verification.int002", "read-back", run_id],
            cwd=_BACKEND,
            env=environment,
            check=False,
        ).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the labelled INT-002 fixture demo.")
    subparsers = parser.add_subparsers(dest="mode")
    worker = subparsers.add_parser("worker")
    worker.add_argument("--repo", type=Path, required=True)
    worker.add_argument("--base", required=True)
    worker.add_argument("--candidate", required=True)
    read_back = subparsers.add_parser("read-back")
    read_back.add_argument("run_id")
    args = parser.parse_args()
    if args.mode == "worker":
        return _worker(args.repo, args.base, args.candidate)
    if args.mode == "read-back":
        return _read_back(args.run_id)
    return _controller()


if __name__ == "__main__":
    raise SystemExit(main())
