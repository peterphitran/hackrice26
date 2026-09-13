"""Recorded checkout inputs and temporary Git fixture for the demo runner."""

from __future__ import annotations

import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from contracts import (
    AnalysisJob,
    Finding,
    PatchArtifact,
    RepositoryContext,
    VerificationResult,
    WorkloadSelection,
)
from lou.agents import DeterministicMockProvider, OrchestrationInputs, ValidatedPatch
from lou.agents.provider import ProviderRequest, ProviderResponse
from lou.policies import AutonomyPolicy
from lou.scoring import DebtInputs, RemediationInputs

BACKEND = Path(__file__).resolve().parents[1]
RECORDS = BACKEND / "contracts" / "fixtures" / "demo_checkout"
BROKEN_STORE = BACKEND / "fixtures" / "broken-store"
ModelT = TypeVar("ModelT", bound=BaseModel)


def _record(name: str, model: type[ModelT]) -> ModelT:
    return model.model_validate_json((RECORDS / name).read_text(encoding="utf-8"))


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _seed(repo: Path) -> tuple[str, str, str]:
    subprocess.run(
        [sys.executable, str(BROKEN_STORE / "scripts" / "seed_fixture_repo.py"), str(repo)],
        check=True,
        capture_output=True,
        text=True,
    )
    base = _git(repo, "rev-parse", "good")
    candidate = _git(repo, "rev-parse", "n-plus-one")
    good_tree = _git(repo, "rev-parse", "good^{tree}")
    fix = _git(
        repo,
        "-c",
        "user.name=Lou Demo",
        "-c",
        "user.email=demo@lou.local",
        "commit-tree",
        good_tree,
        "-p",
        candidate,
        "-m",
        "restore batched checkout",
    )
    return base, candidate, fix


def _deleted_test_diff(repo: Path) -> str:
    path = "tests/test_checkout.py"
    lines = (repo / path).read_text(encoding="utf-8").splitlines()
    return (
        f"diff --git a/{path} b/{path}\n"
        "deleted file mode 100644\n"
        f"--- a/{path}\n+++ /dev/null\n"
        f"@@ -1,{len(lines)} +0,0 @@\n" + "".join(f"-{line}\n" for line in lines)
    )


class DemoProvider:
    """Use the real offline mock, substituting one harmful proposal for the refusal case."""

    def __init__(self, deleted_test_diff: str | None = None) -> None:
        self.mock = DeterministicMockProvider()
        self.deleted_test_diff = deleted_test_diff

    def call(self, request: ProviderRequest) -> ProviderResponse:
        response = self.mock.call(request)
        if request.operation != "patch" or self.deleted_test_diff is None:
            return response
        assert response.patch_artifact is not None
        diff = self.deleted_test_diff
        artifact = response.patch_artifact.model_copy(
            update={
                "patch_sha256": sha256(diff.encode("utf-8")).hexdigest(),
                "files_changed": 1,
                "lines_added": 0,
                "lines_deleted": sum(line.startswith("-") for line in diff.splitlines()[5:]),
            }
        )
        return response.model_copy(update={"patch_diff": diff, "patch_artifact": artifact})


class RecordedFixVerifier:
    """EV-007 slot: replay recorded metrics; never claim to run workloads."""

    def __init__(self, fix_sha: str, *, wrong_hash: bool = False) -> None:
        self.template = _record("verification_fix.json", VerificationResult)
        self.fix_sha = fix_sha
        self.wrong_hash = wrong_hash
        self.calls = 0

    def verify(self, patch: ValidatedPatch) -> VerificationResult:
        self.calls += 1
        patch_hash = (
            sha256(b"different verified bytes").hexdigest()
            if self.wrong_hash
            else patch.patch_artifact.patch_sha256
        )
        return self.template.model_copy(
            update={
                "verification_run_id": f"{patch.verification_attempt_id}:{patch.workload_id}",
                "analysis_run_id": patch.analysis_job.analysis_run_id,
                "commit_sha": self.fix_sha,
                "workload_id": patch.workload_id,
                "metadata": self.template.metadata
                | {
                    "patch_sha256": patch_hash,
                    "verification_attempt_id": patch.verification_attempt_id,
                    "source": "recorded_fixture_ev007_slot",
                },
            }
        )


def _inputs(
    repo: Path, base: str, candidate: str, run_id: str, finding: Finding
) -> OrchestrationInputs:
    scoring = json.loads((BACKEND / "scripts" / "demo_scoring.json").read_text())
    job = _record("analysis_job.json", AnalysisJob).model_copy(
        update={
            "analysis_run_id": run_id,
            "repository_path": str(repo),
            "base_commit_sha": base,
            "candidate_commit_sha": candidate,
        }
    )
    context = _record("repository_context.json", RepositoryContext).model_copy(
        update={"commit_sha": candidate}
    )
    verification = _record("verification_candidate.json", VerificationResult).model_copy(
        update={"analysis_run_id": run_id, "commit_sha": candidate}
    )
    patch = _record("patch_artifact.json", PatchArtifact).model_copy(
        update={"analysis_run_id": run_id, "base_commit_sha": candidate}
    )
    return OrchestrationInputs(
        job=job,
        context=context,
        finding=finding,
        candidate_verification=verification,
        workloads=(
            _record("workload_pytest.json", WorkloadSelection),
            _record("workload_k6.json", WorkloadSelection),
        ),
        expected_patch=patch,
        debt_inputs=DebtInputs.model_validate(scoring["debt"]),
        remediation_inputs=RemediationInputs.model_validate(scoring["remediation"]),
        policy=AutonomyPolicy(max_autonomy=3),
        allowed_repository_root=repo,
    )
