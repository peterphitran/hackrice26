"""Independent fix verification against the original selected workloads."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Literal, Protocol

from contracts import AnalysisJob, VerificationResult, WorkloadSelection
from lou.agents.orchestration import ValidatedPatch
from lou.agents.patch_validation import validate_patch
from lou.loadtest import K6Experiment
from lou.verification.checks import PhaseCheck
from lou.verification.compare import compare_candidate


@dataclass(frozen=True)
class PhaseObservations:
    checks: tuple[PhaseCheck, ...]
    load: K6Experiment


FixStatus = Literal["passed", "failed", "inconclusive"]


class WorkloadRunner(Protocol):
    """Run trusted selected commands against one exact worktree commit."""

    def run(
        self,
        *,
        repository: Path,
        commit_sha: str,
        selections: Sequence[WorkloadSelection],
        commands: Mapping[str, Sequence[str]],
        job: AnalysisJob,
        artifact_dir: Path,
    ) -> PhaseObservations: ...


def _git(
    repository: Path, *arguments: str, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _protected_path(path: str) -> bool:
    """Match test and workload definitions regardless of case or naming convention."""
    pure = PurePosixPath(path)
    parts = {part.lower() for part in pure.parts}
    name = pure.name.lower()
    return (
        bool(parts & {"test", "tests", "loadtest", "loadtests"})
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


class FixVerifier:
    """Drop-in AD-007 verifier; never accepts a provider's success claim."""

    def __init__(
        self,
        *,
        baseline: PhaseObservations,
        candidate: PhaseObservations,
        selections: Sequence[WorkloadSelection],
        commands: Mapping[str, Sequence[str]],
        runner: WorkloadRunner,
        artifact_root: Path,
    ) -> None:
        self.baseline = baseline
        self.candidate = candidate
        self.selections = tuple(selections)
        self.commands = commands
        self.runner = runner
        self.artifact_root = artifact_root
        self._cache: dict[tuple[str, str], dict[str, VerificationResult]] = {}

    def verify(self, patch: ValidatedPatch) -> VerificationResult:
        identity_reason = self._identity_reason(patch)
        if identity_reason is not None:
            results = self._result(patch, "inconclusive", identity_reason, None)
            if patch.workload_id in results:
                return results[patch.workload_id]
            representative = next(iter(results.values()))
            return representative.model_copy(
                update={
                    "verification_run_id": f"{patch.verification_attempt_id}:{patch.workload_id}",
                    "workload_id": patch.workload_id,
                }
            )
        key = (patch.verification_attempt_id, patch.patch_artifact.patch_sha256)
        if key not in self._cache:
            self._cache[key] = self._run_attempt(patch)

        return self._cache[key][patch.workload_id]

    def _identity_reason(self, patch: ValidatedPatch) -> str | None:
        """Reject a malformed verifier request before creating a worktree or runner call."""

        job = patch.analysis_job
        if patch.patch_artifact.analysis_run_id != job.analysis_run_id:
            return "analysis_run_id_mismatch"
        if patch.patch_artifact.base_commit_sha != job.candidate_commit_sha:
            return "base_commit_mismatch"
        actual_hash = sha256(patch.patch_diff.encode("utf-8")).hexdigest()
        if actual_hash != patch.patch_artifact.patch_sha256:
            return "patch_hash_mismatch"
        if patch.workload_id not in {item.workload_id for item in self.selections}:
            return "workload_id_mismatch"
        if patch.verification_attempt_id != f"{job.analysis_run_id}:attempt:{patch.attempt_count}":
            return "verification_attempt_id_mismatch"
        return None

    def _run_attempt(self, patch: ValidatedPatch) -> dict[str, VerificationResult]:
        job = patch.analysis_job
        patch_hash = sha256(patch.patch_diff.encode("utf-8")).hexdigest()
        ids = [item.workload_id for item in self.selections]
        planned = job.verification_plan.get("workloads")
        if (
            patch_hash != patch.patch_artifact.patch_sha256
            or patch.allowed_repository_root.resolve() != Path(job.repository_path).resolve()
            or not isinstance(planned, list)
            or set(ids) != set(planned)
            or len(ids) != len(set(ids))
            or sum(item.workload_type == "k6" for item in self.selections) != 1
            or any(item.workload_type not in {"pytest", "k6"} for item in self.selections)
        ):
            return self._result(patch, "inconclusive", "verification_plan_mismatch", None)
        expected_checks = {check.workload_id: check.arguments for check in self.candidate.checks}
        if (
            any(
                item.workload_type == "pytest"
                and tuple(self.commands.get(item.workload_id, ()))
                != expected_checks.get(item.workload_id)
                for item in self.selections
            )
            or self.candidate.load.workload_id
            != next(item.workload_id for item in self.selections if item.workload_type == "k6")
            or self.candidate.load.commit_sha != job.candidate_commit_sha
            or self.baseline.load.commit_sha != job.base_commit_sha
        ):
            return self._result(patch, "inconclusive", "workload_identity_mismatch", None)

        validation = validate_patch(
            patch.patch_diff,
            patch.patch_artifact,
            expected_base_commit_sha=job.candidate_commit_sha,
            allowed_repository_root=patch.allowed_repository_root,
        )
        if not validation.valid:
            return self._result(patch, "failed", "patch_rejected", None)
        if any(_protected_path(file.path) for file in validation.files):
            return self._result(patch, "failed", "verification_file_modified", None)

        source = Path(job.repository_path).resolve()
        attempt_dir = self.artifact_root / patch.verification_attempt_id.replace(":", "_")
        attempt_dir.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="lou-fix-") as temporary:
            worktree = Path(temporary) / "worktree"
            add = _git(
                source, "worktree", "add", "--detach", str(worktree), job.candidate_commit_sha
            )
            if add.returncode != 0:
                return self._result(patch, "inconclusive", "worktree_creation_failed", None)
            try:
                check = _git(worktree, "apply", "--check", "-", input_text=patch.patch_diff)
                if check.returncode != 0:
                    return self._result(patch, "failed", "patch_does_not_apply", None)
                apply = _git(worktree, "apply", "-", input_text=patch.patch_diff)
                if apply.returncode != 0:
                    return self._result(patch, "failed", "patch_apply_failed", None)
                paths = [file.path for file in validation.files]
                if _git(worktree, "add", "--", *paths).returncode != 0:
                    return self._result(patch, "inconclusive", "fix_commit_failed", None)
                commit = _git(
                    worktree,
                    "-c",
                    "user.name=Lou Verifier",
                    "-c",
                    "user.email=verifier@lou.local",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "commit",
                    "-qm",
                    "verified patch attempt",
                )
                if commit.returncode != 0:
                    return self._result(patch, "inconclusive", "fix_commit_failed", None)
                fix_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
                try:
                    observations = self.runner.run(
                        repository=worktree,
                        commit_sha=fix_sha,
                        selections=self.selections,
                        commands=self.commands,
                        job=job,
                        artifact_dir=attempt_dir,
                    )
                except Exception as error:
                    return self._result(
                        patch,
                        "inconclusive",
                        f"execution_failed:{type(error).__name__}",
                        fix_sha,
                        detail=str(error),
                    )
                try:
                    return self._compare(patch, observations, fix_sha, attempt_dir)
                except (OSError, ValueError, KeyError) as error:
                    return self._result(
                        patch,
                        "inconclusive",
                        f"comparison_failed:{type(error).__name__}",
                        fix_sha,
                        detail=str(error),
                    )
            finally:
                _git(source, "worktree", "remove", "--force", str(worktree))

    def _compare(
        self,
        patch: ValidatedPatch,
        fix: PhaseObservations,
        fix_sha: str,
        artifact_dir: Path,
    ) -> dict[str, VerificationResult]:
        job = patch.analysis_job
        if (
            fix.load.phase != "fix"
            or fix.load.commit_sha != fix_sha
            or {check.workload_id for check in fix.checks}
            != {item.workload_id for item in self.selections if item.workload_type == "pytest"}
            or any(check.phase != "fix" or check.commit_sha != fix_sha for check in fix.checks)
        ):
            return self._result(patch, "inconclusive", "fix_observation_mismatch", fix_sha)
        baseline_job = job.model_copy(update={"candidate_commit_sha": fix_sha})
        baseline_to_fix = compare_candidate(
            baseline_job,
            self.baseline.checks,
            fix.checks,
            self.baseline.load,
            fix.load,
            artifact_dir=artifact_dir / "baseline-to-fix",
        )
        fix_to_candidate = compare_candidate(
            job.model_copy(update={"base_commit_sha": fix_sha}),
            fix.checks,
            self.candidate.checks,
            fix.load,
            self.candidate.load,
            artifact_dir=artifact_dir / "fix-to-candidate",
        )
        status: FixStatus
        if "inconclusive" in {
            baseline_to_fix.verification.status,
            fix_to_candidate.verification.status,
        }:
            status, classification = "inconclusive", "inconclusive"
        elif baseline_to_fix.verification.status == "failed":
            status, classification = "failed", "regression"
        elif fix_to_candidate.verification.status == "failed":
            status, classification = "passed", "improvement"
        else:
            status, classification = "failed", "no_material_change"
        return self._result(
            patch,
            status,
            classification,
            fix_sha,
            metrics=baseline_to_fix.verification.metrics,
            artifact_uri=baseline_to_fix.verification.artifact_uri,
        )

    def _result(
        self,
        patch: ValidatedPatch,
        status: FixStatus,
        reason: str,
        fix_sha: str | None,
        *,
        metrics: dict[str, float] | None = None,
        artifact_uri: str | None = None,
        detail: str | None = None,
    ) -> dict[str, VerificationResult]:
        actual_hash = sha256(patch.patch_diff.encode("utf-8")).hexdigest()
        return {
            item.workload_id: VerificationResult(
                verification_run_id=f"{patch.verification_attempt_id}:{item.workload_id}",
                analysis_run_id=patch.analysis_job.analysis_run_id,
                phase="fix",
                commit_sha=fix_sha or patch.analysis_job.candidate_commit_sha,
                status=status,
                workload_id=item.workload_id,
                metrics=metrics or {},
                artifact_uri=artifact_uri,
                metadata={
                    "patch_sha256": actual_hash,
                    "verification_attempt_id": patch.verification_attempt_id,
                    "classification": reason,
                    "workloads_rerun": fix_sha is not None and bool(metrics),
                    # commit_sha must be a string, so it falls back to the candidate
                    # commit when no fix commit was ever created; this says which it is.
                    "fix_commit_sha": fix_sha,
                    "failure_detail": detail,
                },
            )
            for item in self.selections
        }
