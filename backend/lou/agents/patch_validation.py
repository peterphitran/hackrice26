"""Reject unsafe model-proposed diffs before application or verification."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from contracts import PatchArtifact

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)$")
_TEST_FUNCTION = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)\s*\(")
_SKIP = re.compile(
    r"@pytest\.mark\.(?:skip|skipif|xfail)\b|pytest\.skip\s*\(|"
    r"@?unittest\.skip(?:If|Unless)?\b|@?skip(?:If|Unless)?\s*\("
)
_SECRET = re.compile(
    r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{30,}|"
    r"sk-[A-Za-z0-9_-]{20,})\b|"
    r"\b(?:api[_-]?key|access[_-]?token|secret[_-]?key)\s*[:=]\s*"
    r"['\"]?[A-Za-z0-9_./+=-]{12,}",
    re.IGNORECASE,
)
_DEPENDENCY_NAMES = frozenset({"pyproject.toml", "package.json", "poetry.lock", "uv.lock"})
_VERIFICATION_DIRS = frozenset({"fixture", "fixtures", "loadtest", "loadtests"})


class PatchValidationLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_files: int = Field(default=5, ge=1)
    max_changed_lines: int = Field(default=100, ge=1)


class PatchRejection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    detail: str


class ParsedPatchFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    old_path: str | None
    new_path: str | None
    lines_added: int
    lines_deleted: int
    hunk_count: int
    binary: bool


class PatchValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    reasons: list[PatchRejection]
    files: list[ParsedPatchFile]


@dataclass
class _FileState:
    git_old: str | None = None
    git_new: str | None = None
    old_path: str | None = None
    new_path: str | None = None
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)
    hunk_count: int = 0
    binary: bool = False
    symlink_mode: bool = False
    old_remaining: int = 0
    new_remaining: int = 0
    in_hunk: bool = False
    old_header_seen: bool = False
    new_header_seen: bool = False

    def path(self) -> str:
        return self.new_path or self.old_path or self.git_new or self.git_old or ""


def _header_path(value: str) -> str | None:
    if value == "/dev/null":
        return None
    if value.startswith(("a/", "b/")):
        return value[2:]
    return value


def _parse(diff: str) -> tuple[list[_FileState], list[str]]:
    files: list[_FileState] = []
    errors: list[str] = []
    current: _FileState | None = None

    def finish() -> None:
        nonlocal current
        if current is None:
            return
        if current.old_remaining or current.new_remaining:
            errors.append(f"Hunk line counts do not match for {current.path()}.")
        if not current.old_header_seen or not current.new_header_seen:
            errors.append(f"Missing file headers for {current.path()}.")
        if current.old_path is None and current.new_path is None:
            errors.append(f"Both file headers are /dev/null for {current.path()}.")
        if current.hunk_count == 0 and not current.binary:
            errors.append(f"No textual hunk for {current.path()}.")
        if current.git_old and current.old_path and current.git_old != current.old_path:
            errors.append(f"Old file header disagrees with git header for {current.path()}.")
        if current.git_new and current.new_path and current.git_new != current.new_path:
            errors.append(f"New file header disagrees with git header for {current.path()}.")
        files.append(current)
        current = None

    if not diff or not diff.endswith("\n"):
        errors.append("Diff is empty or lacks a final newline.")
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            finish()
            pieces = line.split(" ")
            if len(pieces) != 4 or not pieces[2].startswith("a/") or not pieces[3].startswith("b/"):
                errors.append("Invalid diff --git header.")
                current = _FileState()
            else:
                current = _FileState(git_old=pieces[2][2:], git_new=pieces[3][2:])
            continue
        if current is None and line.startswith("--- "):
            current = _FileState()
        if current is None:
            errors.append(f"Unexpected line before file header: {line[:80]}")
            continue
        if current.binary:
            continue
        if current.in_hunk and line[:1] in {" ", "+", "-"}:
            kind = line[0]
            if kind == " ":
                current.context.append(line[1:])
                current.old_remaining -= 1
                current.new_remaining -= 1
            elif kind == "+":
                current.added.append(line[1:])
                current.new_remaining -= 1
            else:
                current.removed.append(line[1:])
                current.old_remaining -= 1
            if current.old_remaining < 0 or current.new_remaining < 0:
                errors.append(f"Hunk contains more lines than declared for {current.path()}.")
            if current.old_remaining == current.new_remaining == 0:
                current.in_hunk = False
            continue
        if line.startswith(("GIT binary patch", "Binary files ")):
            current.binary = True
            continue
        if line.startswith(
            ("index ", "new file mode ", "deleted file mode ", "old mode ", "new mode ")
        ):
            if "120000" in line:
                current.symlink_mode = True
            continue
        if line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            errors.append(f"Rename/copy metadata is not supported for {current.path()}.")
            continue
        if line.startswith("--- "):
            if current.hunk_count and not current.in_hunk:
                finish()
                current = _FileState()
            current.old_path = _header_path(line[4:])
            current.old_header_seen = True
            continue
        if line.startswith("+++ "):
            if not current.old_header_seen:
                errors.append("New file header appeared without an old file header.")
            current.new_path = _header_path(line[4:])
            current.new_header_seen = True
            continue
        if line.startswith("@@"):
            match = _HUNK.fullmatch(line)
            if match is None or current.hunk_count and current.in_hunk:
                errors.append(f"Invalid or overlapping hunk header for {current.path()}.")
                continue
            if not current.old_header_seen or not current.new_header_seen:
                errors.append(f"Hunk appeared before file headers for {current.path()}.")
            if current.old_remaining or current.new_remaining:
                errors.append(f"Previous hunk line counts do not match for {current.path()}.")
            current.old_remaining = int(match.group(2) or 1)
            current.new_remaining = int(match.group(4) or 1)
            current.hunk_count += 1
            current.in_hunk = True
            continue
        if line == r"\ No newline at end of file":
            continue
        errors.append(f"Unexpected diff line for {current.path()}: {line[:80]}")
    finish()
    if not files:
        errors.append("No changed file was parsed.")
    return files, errors


def _is_test_file(path: str) -> bool:
    pure = PurePosixPath(path)
    return "tests" in pure.parts or pure.name.startswith("test_") and pure.suffix == ".py"


def _is_k6_scenario(path: str, lines: list[str]) -> bool:
    pure = PurePosixPath(path)
    if pure.suffix not in {".js", ".ts"}:
        return False
    return bool(_VERIFICATION_DIRS.intersection(pure.parts)) or any(
        re.search(r"\b(?:thresholds|scenarios|stages|k6|http\.(?:get|post))\b", line)
        for line in lines
    )


def _check_path(path: str, root: Path) -> tuple[bool, bool]:
    pure = PurePosixPath(path)
    if not path or pure.is_absolute() or ".." in pure.parts or "\\" in path or "\x00" in path:
        return False, False
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            return True, True
    return True, False


def validate_patch(
    patch_diff: str,
    patch_artifact: PatchArtifact,
    *,
    expected_base_commit_sha: str,
    allowed_repository_root: Path,
    limits: PatchValidationLimits | None = None,
) -> PatchValidationResult:
    """Validate a proposed textual diff; never apply it or infer test outcomes."""
    budget = limits or PatchValidationLimits()
    reasons: list[PatchRejection] = []

    def reject(code: str, message: str, detail: str) -> None:
        if not any(item.code == code and item.detail == detail for item in reasons):
            reasons.append(PatchRejection(code=code, message=message, detail=detail))

    if patch_artifact.base_commit_sha != expected_base_commit_sha:
        reject(
            "base_commit_mismatch",
            "Patch base commit differs from the expected commit.",
            patch_artifact.base_commit_sha,
        )
    try:
        actual_hash = sha256(patch_diff.encode("utf-8")).hexdigest()
    except UnicodeError:
        reject("invalid_unified_diff", "Patch is not a valid unified diff.", "Invalid UTF-8 text.")
    else:
        if patch_artifact.patch_sha256 != actual_hash:
            reject(
                "patch_hash_mismatch", "Patch hash does not match the supplied bytes.", actual_hash
            )
    states, parse_errors = _parse(patch_diff)
    for error in parse_errors:
        reject("invalid_unified_diff", "Patch is not a valid unified diff.", error)
    if len(states) > budget.max_files:
        reject("file_budget_exceeded", "Patch changes too many files.", str(len(states)))
    changed_lines = sum(len(state.added) + len(state.removed) for state in states)
    if changed_lines > budget.max_changed_lines:
        reject("line_budget_exceeded", "Patch changes too many lines.", str(changed_lines))
    if patch_artifact.files_changed != len(states) or (
        patch_artifact.lines_added != sum(len(state.added) for state in states)
        or patch_artifact.lines_deleted != sum(len(state.removed) for state in states)
    ):
        reject(
            "artifact_summary_mismatch",
            "Patch artifact counts differ from parsed changes.",
            patch_artifact.patch_id,
        )
    if _SECRET.search(patch_diff):
        reject("secret_pattern", "Patch contains a common credential pattern.", "diff content")

    try:
        root = allowed_repository_root.resolve(strict=True)
        root_ok = root.is_dir()
    except OSError:
        root = allowed_repository_root
        root_ok = False
    if not root_ok:
        reject(
            "invalid_repository_root",
            "Allowed repository root is not a directory.",
            str(allowed_repository_root),
        )

    parsed: list[ParsedPatchFile] = []
    for state in states:
        path = state.path()
        paths = {
            part for part in (state.git_old, state.git_new, state.old_path, state.new_path) if part
        }
        for candidate_path in sorted(paths):
            safe, symlink = _check_path(candidate_path, root)
            if not safe:
                reject(
                    "path_outside_root",
                    "Patch path is outside the repository root.",
                    candidate_path,
                )
            if symlink or state.symlink_mode:
                reject("symlink_target", "Patch targets a symlink or creates one.", candidate_path)
            pure = PurePosixPath(candidate_path)
            if ".github" in pure.parts:
                reject("workflow_file", "Patch changes a CI or workflow file.", candidate_path)
            if pure.name in _DEPENDENCY_NAMES or fnmatch.fnmatch(pure.name, "requirements*.txt"):
                reject(
                    "dependency_manifest", "Patch changes a dependency manifest.", candidate_path
                )
            if _VERIFICATION_DIRS.intersection(pure.parts):
                reject(
                    "verification_fixture_modified",
                    "Patch changes verification fixtures or loadtests.",
                    candidate_path,
                )
        if state.binary or not (state.added or state.removed or state.context):
            reject("binary_change", "Patch has no textual hunk content.", path)
        if _is_test_file(path):
            if state.old_path is not None and state.new_path is None:
                reject("test_file_deleted", "Patch deletes an existing test file.", path)
            if state.old_path is not None and not state.added and not state.context:
                reject("test_file_emptied", "Patch removes all visible test content.", path)
            removed_tests = {
                match.group(1)
                for line in state.removed
                if (match := _TEST_FUNCTION.match(line)) is not None
            }
            added_tests = {
                match.group(1)
                for line in state.added
                if (match := _TEST_FUNCTION.match(line)) is not None
            }
            if removed_tests - added_tests:
                reject("test_function_removed", "Patch removes a test function or method.", path)
            if state.old_path is not None and any(_SKIP.search(line) for line in state.added):
                reject("test_skip_added", "Patch adds a skip or xfail to an existing test.", path)
        if _is_k6_scenario(path, state.added + state.removed + state.context):
            reject(
                "k6_scenario_modified", "Patch changes a k6 threshold or scenario definition.", path
            )
        parsed.append(
            ParsedPatchFile(
                path=path,
                old_path=state.old_path,
                new_path=state.new_path,
                lines_added=len(state.added),
                lines_deleted=len(state.removed),
                hunk_count=state.hunk_count,
                binary=state.binary,
            )
        )
    return PatchValidationResult(valid=not reasons, reasons=reasons, files=parsed)
