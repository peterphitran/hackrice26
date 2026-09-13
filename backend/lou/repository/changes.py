"""Deterministic parsing of Python file changes between Git commits."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from contracts import RepositoryChange
from lou.core.errors import GitExecutionError, InvalidCommitError, InvalidRepositoryError

_OBJECT_ID_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_RENAME_STATUS_PATTERN = re.compile(r"R([0-9]{1,3})")
_RENAME_SIMILARITY_THRESHOLD = 50
_GIT_TIMEOUT_SECONDS = 30.0
_GIT_REPOSITORY_ENVIRONMENT_VARIABLES = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_SYSTEM",
    "GIT_DIR",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_DIFF_OPTS",
    "GIT_INDEX_FILE",
    "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_REPLACE_REF_BASE",
    "GIT_SHALLOW_FILE",
    "GIT_WORK_TREE",
)
_GIT_REPOSITORY_ENVIRONMENT_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


@dataclass(frozen=True)
class _ParsedChanges:
    added_files: tuple[str, ...]
    modified_files: tuple[str, ...]
    deleted_files: tuple[str, ...]
    renamed_files: tuple[tuple[str, str], ...]


def parse_repository_changes(
    *,
    repository_id: str,
    repository_path: str | Path,
    base_revision: str,
    candidate_revision: str,
) -> RepositoryChange:
    """Return Python file changes between two exact commits in a local repository."""

    supplied_path = _validate_path(repository_path)
    repository_root = _discover_repository_root(supplied_path)
    base_commit_sha = _resolve_commit(repository_root, base_revision, "base")
    candidate_commit_sha = _resolve_commit(repository_root, candidate_revision, "candidate")

    result = _execute_git(
        repository_root,
        [
            "--no-pager",
            "diff",
            "--no-ext-diff",
            "--name-status",
            "-z",
            f"--find-renames={_RENAME_SIMILARITY_THRESHOLD}%",
            "--diff-filter=AMDRT",
            base_commit_sha,
            candidate_commit_sha,
            "--",
            "*.py",
        ],
        operation="diff commits",
    )
    if result.returncode != 0:
        raise GitExecutionError(
            "diff commits",
            result.returncode,
            _decode_stderr(result.stderr),
        )

    parsed = _parse_name_status_z(result.stdout)
    return RepositoryChange(
        repository_id=repository_id,
        base_commit_sha=base_commit_sha,
        candidate_commit_sha=candidate_commit_sha,
        added_files=list(parsed.added_files),
        modified_files=list(parsed.modified_files),
        deleted_files=list(parsed.deleted_files),
        renamed_files=dict(parsed.renamed_files),
        changed_symbols=[],
        completeness=1.0,
        metadata={
            "extractor": "git",
            "original_base_revision": base_revision,
            "original_candidate_revision": candidate_revision,
            "rename_similarity_threshold": _RENAME_SIMILARITY_THRESHOLD,
        },
    )


def _validate_path(repository_path: str | Path) -> Path:
    path = Path(repository_path)
    try:
        if not path.exists():
            raise InvalidRepositoryError(path, "path does not exist")
        if not path.is_dir():
            raise InvalidRepositoryError(path, "path is not a directory")
        return path.resolve(strict=True)
    except OSError as error:
        raise InvalidRepositoryError(path, str(error)) from error


def _discover_repository_root(path: Path) -> Path:
    result = _execute_git(
        path,
        ["rev-parse", "--show-toplevel"],
        operation="repository validation",
    )
    if result.returncode != 0:
        reason = _decode_stderr(result.stderr) or "path is not inside a Git working tree"
        raise InvalidRepositoryError(path, reason)

    try:
        root_text = result.stdout.removesuffix(b"\n").removesuffix(b"\r").decode("utf-8")
        if not root_text:
            raise ValueError("Git returned an empty working-tree root")
        root = Path(root_text).resolve(strict=True)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise InvalidRepositoryError(path, f"invalid Git working-tree root: {error}") from error

    if not root.is_dir():
        raise InvalidRepositoryError(path, "Git working-tree root is not a directory")
    if not path.is_relative_to(root):
        raise InvalidRepositoryError(
            path,
            "Git working-tree root does not contain the supplied path",
        )
    return root


def _resolve_commit(
    repository_root: Path,
    revision: str,
    role: Literal["base", "candidate"],
) -> str:
    if not revision or "\0" in revision:
        raise InvalidCommitError(repository_root, revision, role)

    result = _execute_git(
        repository_root,
        ["rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"],
        operation=f"resolve {role} revision",
    )
    if result.returncode != 0:
        raise InvalidCommitError(repository_root, revision, role)

    try:
        object_id = result.stdout.strip().decode("ascii").lower()
    except UnicodeDecodeError as error:
        raise GitExecutionError(
            f"resolve {role} revision",
            result.returncode,
            "Git returned a non-ASCII object ID",
        ) from error

    if _OBJECT_ID_PATTERN.fullmatch(object_id) is None:
        raise GitExecutionError(
            f"resolve {role} revision",
            result.returncode,
            "Git returned an invalid commit object ID",
        )
    return object_id


def _execute_git(
    repository_path: Path,
    arguments: Sequence[str],
    *,
    operation: str,
) -> subprocess.CompletedProcess[bytes]:
    environment = os.environ.copy()
    for variable in _GIT_REPOSITORY_ENVIRONMENT_VARIABLES:
        environment.pop(variable, None)
    for variable in tuple(environment):
        if variable.startswith(_GIT_REPOSITORY_ENVIRONMENT_PREFIXES):
            environment.pop(variable)
    environment["LC_ALL"] = "C"
    environment["LANG"] = "C"
    try:
        return subprocess.run(
            ["git", "--no-replace-objects", "-C", str(repository_path), *arguments],
            check=False,
            capture_output=True,
            shell=False,
            env=environment,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise GitExecutionError(
            operation,
            None,
            _decode_stderr(error.stderr),
            timed_out=True,
            timeout_seconds=_GIT_TIMEOUT_SECONDS,
        ) from error
    except OSError as error:
        raise GitExecutionError(operation, None, str(error)) from error


def _parse_name_status_z(output: bytes) -> _ParsedChanges:
    if not output:
        return _ParsedChanges((), (), (), ())
    if not output.endswith(b"\0"):
        raise GitExecutionError("parse diff output", None, "NUL-delimited output is truncated")

    fields = output[:-1].split(b"\0")
    added: set[str] = set()
    modified: set[str] = set()
    deleted: set[str] = set()
    renamed: dict[str, str] = {}

    index = 0
    while index < len(fields):
        status = _decode_status(fields[index])
        index += 1

        if status in {"A", "M", "D", "T"}:
            if index >= len(fields):
                raise GitExecutionError(
                    "parse diff output", None, f"status {status} is missing its path"
                )
            path = _decode_path(fields[index])
            index += 1
            if not _is_python_path(path):
                continue
            if status == "A":
                added.add(path)
            elif status in {"M", "T"}:
                modified.add(path)
            else:
                deleted.add(path)
            continue

        rename_match = _RENAME_STATUS_PATTERN.fullmatch(status)
        if rename_match is None or int(rename_match.group(1)) > 100:
            raise GitExecutionError(
                "parse diff output", None, f"unsupported name-status value {status!r}"
            )
        if index + 1 >= len(fields):
            raise GitExecutionError("parse diff output", None, f"status {status} is missing paths")

        old_path = _decode_path(fields[index])
        new_path = _decode_path(fields[index + 1])
        index += 2
        old_is_python = _is_python_path(old_path)
        new_is_python = _is_python_path(new_path)
        if old_is_python and new_is_python:
            renamed[old_path] = new_path
        elif old_is_python:
            deleted.add(old_path)
        elif new_is_python:
            added.add(new_path)

    return _ParsedChanges(
        tuple(sorted(added)),
        tuple(sorted(modified)),
        tuple(sorted(deleted)),
        tuple(sorted(renamed.items())),
    )


def _decode_status(value: bytes) -> str:
    try:
        return value.decode("ascii")
    except UnicodeDecodeError as error:
        raise GitExecutionError(
            "parse diff output", None, "Git returned a non-ASCII status"
        ) from error


def _decode_path(value: bytes) -> str:
    if not value or b"\0" in value:
        raise GitExecutionError("parse diff output", None, "Git returned an empty path")
    try:
        path = value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GitExecutionError(
            "parse diff output", None, "Git returned a path that is not valid UTF-8"
        ) from error

    normalized = PurePosixPath(path)
    if normalized.is_absolute() or ".." in normalized.parts or normalized.as_posix() != path:
        raise GitExecutionError(
            "parse diff output", None, f"Git returned an unsafe repository path {path!r}"
        )
    return path


def _decode_stderr(stderr: bytes | str | None) -> str:
    if isinstance(stderr, bytes):
        return stderr.decode("utf-8", errors="replace").strip()
    return stderr.strip() if stderr is not None else ""


def _is_python_path(path: str) -> bool:
    return PurePosixPath(path).suffix == ".py"
