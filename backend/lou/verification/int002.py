"""INT-002 controller using the real local Lou application composition."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lou.application import AnalysisRequest, build_fixture_service
from lou.core.settings import get_settings
from lou.persistence.database import create_database_engine
from lou.persistence.models import AnalysisRunRecord, EvidenceRecord, FindingRecord
from lou.repository import resolve_repository_revisions

_BACKEND = Path(__file__).resolve().parents[2]
_SEED = _BACKEND / "fixtures/broken-store/scripts/seed_fixture_repo.py"
_MARKER = "__LOU_INT002_RESULT__"


def _worker(repository: Path, base: str, candidate: str) -> int:
    """Run actual graph selection, Docker verification, persistence, and decision code."""

    revisions = resolve_repository_revisions(
        repository_path=repository, base_revision=base, candidate_revision=candidate
    )
    token = uuid4().hex
    result = build_fixture_service(get_settings()).run(
        AnalysisRequest(
            repository_id=f"broken-store-int002-{token}",
            repository_path=revisions.repository_root,
            base_commit_sha=revisions.base_commit_sha,
            candidate_commit_sha=revisions.candidate_commit_sha,
            force_new_run=True,
            force_token=token,
        )
    )
    candidate_result = next(
        (item for item in result.verification_results if item.phase == "candidate"), None
    )
    if candidate_result is not None:
        metrics = candidate_result.metrics
        print(
            "[LIVE RESULT] queries "
            f"{metrics.get('baseline_query_count', 0):g} -> "
            f"{metrics.get('candidate_query_count', 0):g}; verdict={candidate_result.status}"
        )
    print(_MARKER + json.dumps({"run_id": result.analysis_run_id, "status": result.status}))
    return 0 if result.status == "succeeded" and candidate_result is not None else 1


def _read_back(run_id: str) -> int:
    engine = create_database_engine()
    try:
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
    except ValueError:
        return 1
    finally:
        engine.dispose()
    print(
        f"[LIVE READ-BACK] run={getattr(run, 'id', None)} evidence={evidence} findings={findings}"
    )
    return 0 if run is not None and run.status == "succeeded" and evidence and findings else 1


def _seed(target: Path) -> None:
    spec = importlib.util.spec_from_file_location("lou_int002_seed", _SEED)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.seed(target)


def _controller() -> int:
    port = os.environ.get("LOU_INT002_POSTGRES_PORT", "55432")
    environment = os.environ | {
        "LOU_POSTGRES_PORT": port,
        "LOU_MIGRATION_DATABASE_URL": f"postgresql+psycopg://lou_migrator:lou_migrator@localhost:{port}/lou",
        "LOU_DATABASE_URL": f"postgresql+psycopg://lou:lou@localhost:{port}/lou",
        "LOU_FIXTURE_DATABASE_URL": "postgresql://lou_migrator:lou_migrator@postgres:5432/lou",
    }
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
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_BACKEND,
        env=environment,
        check=True,
    )
    with TemporaryDirectory(prefix="lou-int002-fixture-") as directory:
        repository = Path(directory) / "broken-store"
        _seed(repository)
        revisions = resolve_repository_revisions(
            repository_path=repository, base_revision="good", candidate_revision="n-plus-one"
        )
        worker = subprocess.run(
            [
                sys.executable,
                "-m",
                "lou.verification.int002",
                "worker",
                "--repo",
                str(repository),
                "--base",
                revisions.base_commit_sha,
                "--candidate",
                revisions.candidate_commit_sha,
            ],
            cwd=_BACKEND,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        print(worker.stdout, end="")
        if worker.returncode:
            return worker.returncode
        marker = next(line for line in worker.stdout.splitlines() if line.startswith(_MARKER))
        run_id = json.loads(marker.removeprefix(_MARKER))["run_id"]
        return subprocess.run(
            [sys.executable, "-m", "lou.verification.int002", "read-back", run_id],
            cwd=_BACKEND,
            env=environment,
        ).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the real INT-002 fixture demo.")
    subs = parser.add_subparsers(dest="mode")
    worker = subs.add_parser("worker")
    worker.add_argument("--repo", type=Path, required=True)
    worker.add_argument("--base", required=True)
    worker.add_argument("--candidate", required=True)
    reader = subs.add_parser("read-back")
    reader.add_argument("run_id")
    args = parser.parse_args()
    if args.mode == "worker":
        return _worker(args.repo, args.base, args.candidate)
    if args.mode == "read-back":
        return _read_back(args.run_id)
    return _controller()


if __name__ == "__main__":
    raise SystemExit(main())
