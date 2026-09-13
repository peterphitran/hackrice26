"""Adversarial patch proposals are rejected before any patch is applied."""

from difflib import unified_diff
from hashlib import sha256
from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel

from contracts import (
    AnalysisJob,
    Finding,
    PatchArtifact,
    RepositoryContext,
    VerificationResult,
    WorkloadSelection,
)
from lou.agents import AgentAdapter, ProviderRequest, build_context_bundle
from lou.agents.patch_validation import PatchValidationLimits, validate_patch

FIXTURES = Path(__file__).parents[3] / "contracts" / "fixtures" / "demo_checkout"
ModelT = TypeVar("ModelT", bound=BaseModel)
BASE_SHA = "b" * 40


def _load(name: str, model: type[ModelT]) -> ModelT:
    return model.model_validate_json((FIXTURES / name).read_text())


def _diff(path: str, before: list[str], after: list[str], *, new_file: bool = False) -> str:
    old_header = "/dev/null" if new_file else f"a/{path}"
    lines = list(
        unified_diff(
            [] if new_file else [f"{line}\n" for line in before],
            [f"{line}\n" for line in after],
            fromfile=old_header,
            tofile=f"b/{path}",
        )
    )
    return f"diff --git a/{path} b/{path}\n" + "".join(lines)


def _artifact(
    diff: str,
    *,
    base_sha: str = BASE_SHA,
    patch_sha256: str | None = None,
    files_changed: int | None = None,
) -> PatchArtifact:
    added = sum(
        line.startswith("+") and not line.startswith("+++ b/") and line != "+++ /dev/null"
        for line in diff.splitlines()
    )
    deleted = sum(
        line.startswith("-") and not line.startswith("--- a/") and line != "--- /dev/null"
        for line in diff.splitlines()
    )
    return PatchArtifact(
        patch_id="proposed-patch",
        analysis_run_id="run-1",
        base_commit_sha=base_sha,
        patch_sha256=patch_sha256 or sha256(diff.encode()).hexdigest(),
        artifact_uri="mock://run-1/proposed.diff",
        files_changed=files_changed if files_changed is not None else diff.count("diff --git "),
        lines_added=added,
        lines_deleted=deleted,
    )


def _codes(diff: str, root: Path, **overrides: object) -> set[str]:
    base_sha = str(overrides.get("base_sha", BASE_SHA))
    stored_hash = overrides.get("patch_sha256")
    limits = overrides.get("limits")
    files_changed = overrides.get("files_changed")
    artifact = _artifact(
        diff,
        base_sha=base_sha,
        patch_sha256=str(stored_hash) if stored_hash is not None else None,
        files_changed=files_changed if isinstance(files_changed, int) else None,
    )
    result = validate_patch(
        diff,
        artifact,
        expected_base_commit_sha=BASE_SHA,
        allowed_repository_root=root,
        limits=limits if isinstance(limits, PatchValidationLimits) else None,
    )
    assert result.valid is (not result.reasons)
    return {reason.code for reason in result.reasons}


_SIMPLE = _diff("checkout/service.py", ["old = 1"], ["new = 1"])
_TWO_FILES = _SIMPLE + _diff("checkout/other.py", ["old = 1"], ["new = 1"])


@pytest.mark.parametrize(
    ("name", "diff", "expected", "overrides"),
    [
        ("parent traversal", _diff("../outside.py", ["x = 0"], ["x = 1"]), "path_outside_root", {}),
        ("absolute path", _diff("/tmp/outside.py", ["x = 0"], ["x = 1"]), "path_outside_root", {}),
        (
            "binary content",
            "diff --git a/image.png b/image.png\nBinary files a/image.png and b/image.png differ\n",
            "binary_change",
            {},
        ),
        (
            "empty textual hunk",
            "diff --git a/empty.py b/empty.py\n--- a/empty.py\n+++ b/empty.py\n@@ -0,0 +0,0 @@\n",
            "binary_change",
            {},
        ),
        ("workflow", _diff(".github/workflows/ci.yml", ["x"], ["y"]), "workflow_file", {}),
        ("requirements", _diff("requirements-dev.txt", ["x"], ["y"]), "dependency_manifest", {}),
        ("pyproject", _diff("pyproject.toml", ["x"], ["y"]), "dependency_manifest", {}),
        ("package", _diff("package.json", ["x"], ["y"]), "dependency_manifest", {}),
        ("poetry", _diff("poetry.lock", ["x"], ["y"]), "dependency_manifest", {}),
        ("uv lock", _diff("uv.lock", ["x"], ["y"]), "dependency_manifest", {}),
        (
            "secret",
            _diff("checkout/service.py", ["key = None"], ["API_KEY = 'sk-" + "A" * 30 + "'"]),
            "secret_pattern",
            {},
        ),
        (
            "file budget",
            _TWO_FILES,
            "file_budget_exceeded",
            {"limits": PatchValidationLimits(max_files=1)},
        ),
        (
            "line budget",
            _SIMPLE,
            "line_budget_exceeded",
            {"limits": PatchValidationLimits(max_changed_lines=1)},
        ),
        (
            "invalid hunk counts",
            _SIMPLE.replace("@@ -1 +1 @@", "@@ -1,2 +1,2 @@"),
            "invalid_unified_diff",
            {},
        ),
        (
            "missing old file header",
            _SIMPLE.replace("--- a/checkout/service.py\n", ""),
            "invalid_unified_diff",
            {},
        ),
        ("wrong base", _SIMPLE, "base_commit_mismatch", {"base_sha": "c" * 40}),
        ("wrong hash", _SIMPLE, "patch_hash_mismatch", {"patch_sha256": "0" * 64}),
        ("wrong counts", _SIMPLE, "artifact_summary_mismatch", {"files_changed": 2}),
        (
            "deleted test",
            "diff --git a/tests/test_checkout.py b/tests/test_checkout.py\n"
            "--- a/tests/test_checkout.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-def test_checkout():\n",
            "test_file_deleted",
            {},
        ),
        (
            "emptied test",
            "diff --git a/tests/test_checkout.py b/tests/test_checkout.py\n"
            "--- a/tests/test_checkout.py\n+++ b/tests/test_checkout.py\n"
            "@@ -1 +0,0 @@\n-assert True\n",
            "test_file_emptied",
            {},
        ),
        (
            "removed test function",
            _diff(
                "tests/test_checkout.py", ["def test_checkout():", "    assert True"], ["# removed"]
            ),
            "test_function_removed",
            {},
        ),
        (
            "pytest skip",
            _diff(
                "tests/test_checkout.py",
                ["def test_checkout():"],
                ["@pytest.mark.skip", "def test_checkout():"],
            ),
            "test_skip_added",
            {},
        ),
        (
            "pytest xfail",
            _diff(
                "tests/test_checkout.py",
                ["def test_checkout():"],
                ["@pytest.mark.xfail", "def test_checkout():"],
            ),
            "test_skip_added",
            {},
        ),
        (
            "pytest skip call",
            _diff("tests/test_checkout.py", ["assert True"], ["pytest.skip('later')"]),
            "test_skip_added",
            {},
        ),
        (
            "unittest skip",
            _diff(
                "tests/test_checkout.py",
                ["def test_checkout():"],
                ["@unittest.skip('later')", "def test_checkout():"],
            ),
            "test_skip_added",
            {},
        ),
        (
            "k6 threshold",
            _diff(
                "loadtests/checkout.js", ["thresholds: ['p95<100']"], ["thresholds: ['p95<9999']"]
            ),
            "k6_scenario_modified",
            {},
        ),
        (
            "verification fixture",
            _diff("fixtures/checkout.json", ["count: 10"], ["count: 1"]),
            "verification_fixture_modified",
            {},
        ),
    ],
)
def test_rejection_rules_are_distinct_and_deterministic(
    name: str, diff: str, expected: str, overrides: dict[str, object], tmp_path: Path
) -> None:
    assert expected in _codes(diff, tmp_path, **overrides), name


def test_symlink_target_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "patch-validation-outside.py"
    (tmp_path / "linked.py").symlink_to(outside)
    assert "symlink_target" in _codes(_diff("linked.py", ["x = 0"], ["x = 1"]), tmp_path)


def test_symlink_mode_is_rejected(tmp_path: Path) -> None:
    diff = _SIMPLE.replace(
        "--- a/checkout/service.py", "new file mode 120000\n--- a/checkout/service.py"
    )
    assert "symlink_target" in _codes(diff, tmp_path)


def test_valid_small_patch_and_nonweakening_test_addition_pass(tmp_path: Path) -> None:
    test_addition = _diff(
        "tests/test_checkout.py",
        ["def test_old():", "    assert True"],
        ["def test_old():", "    assert True", "", "def test_new():", "    assert True"],
    )
    test_signature_edit = _diff(
        "tests/test_checkout.py",
        ["def test_checkout():", "    assert True"],
        ["def test_checkout(self):", "    assert True"],
    )
    header_looking_content = _diff("checkout/service.py", ["-- old comment"], ["++ new comment"])
    for diff in (_SIMPLE, test_addition, test_signature_edit, header_looking_content):
        artifact = _artifact(diff)
        result = validate_patch(
            diff,
            artifact,
            expected_base_commit_sha=BASE_SHA,
            allowed_repository_root=tmp_path,
        )
        assert result.valid is True
        assert result.reasons == []
        assert result.files[0].path in {"checkout/service.py", "tests/test_checkout.py"}


def test_independent_failures_are_all_reported(tmp_path: Path) -> None:
    diff = _diff("../outside.py", ["x = 0"], ["API_KEY = 'sk-" + "A" * 30 + "'"])
    codes = _codes(diff, tmp_path, base_sha="c" * 40, patch_sha256="0" * 64)
    assert {
        "path_outside_root",
        "secret_pattern",
        "base_commit_mismatch",
        "patch_hash_mismatch",
    } <= codes


def test_actual_mock_proposal_passes_validator(tmp_path: Path) -> None:
    job = _load("analysis_job.json", AnalysisJob)
    bundle = build_context_bundle(
        job,
        _load("repository_context.json", RepositoryContext),
        _load("finding.json", Finding),
        _load("verification_candidate.json", VerificationResult),
        [
            _load("workload_pytest.json", WorkloadSelection),
            _load("workload_k6.json", WorkloadSelection),
        ],
        _load("patch_artifact.json", PatchArtifact),
    )
    response = AgentAdapter().call(ProviderRequest(operation="patch", bundle=bundle))
    assert response.patch_diff is not None
    assert response.patch_artifact is not None

    result = validate_patch(
        response.patch_diff,
        response.patch_artifact,
        expected_base_commit_sha=job.candidate_commit_sha,
        allowed_repository_root=tmp_path,
    )
    assert result.valid is True
    assert result.files[0].path == "checkout/service.py"
