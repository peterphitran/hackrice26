"""Bounded, static symbol extraction from the immutable RI-001 comparison."""

from __future__ import annotations

import ast
import io
import re
import tokenize
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import quote

from contracts import RepositoryChange
from lou.core.errors import GitExecutionError, InvalidCommitError
from lou.repository.changes import (
    _OBJECT_ID_PATTERN,
    _decode_path,
    _decode_stderr,
    _discover_repository_root,
    _execute_git,
    _resolve_commit,
    _validate_path,
)

MAX_BLOB_BYTES = 1024 * 1024
MAX_FILE_SNAPSHOTS = 200
_HUNK = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
_Phase = Literal["baseline", "candidate"]


@dataclass(frozen=True)
class _Symbol:
    key: str
    kind: str
    phase: _Phase
    path: str
    start_line: int
    end_line: int


@dataclass
class _Snapshot:
    blob: str
    symbols: list[_Symbol]
    owners: list[_Symbol]


class _Incomplete(Exception):
    """A safe diagnostic code for a snapshot that cannot be extracted."""


def extract_changed_symbols(
    *, repository_path: str | Path, change: RepositoryChange
) -> RepositoryChange:
    """Enrich an RI-001 result without modifying it or accessing worktree source."""
    root = _discover_repository_root(_validate_path(repository_path))
    commits: tuple[tuple[str, Literal["base", "candidate"]], ...] = (
        (change.base_commit_sha, "base"),
        (change.candidate_commit_sha, "candidate"),
    )
    for sha, role in commits:
        if _OBJECT_ID_PATTERN.fullmatch(sha) is None:
            raise InvalidCommitError(root, sha, role)
        _resolve_commit(root, sha, role)

    pairs: list[tuple[str | None, str | None]] = [
        *((None, path) for path in change.added_files),
        *((path, path) for path in change.modified_files),
        *((path, None) for path in change.deleted_files),
        *change.renamed_files.items(),
    ]
    pairs = sorted(set(pairs), key=lambda pair: (pair[0] or "", pair[1] or ""))
    for pair in pairs:
        for path in pair:
            if path is not None:
                try:
                    _decode_path(path.encode("utf-8"))
                except UnicodeError as error:
                    raise GitExecutionError(
                        "validate source path", None, "Invalid UTF-8"
                    ) from error
    expected = sum(path is not None for pair in pairs for path in pair)
    attempted = completed = 0
    selected: set[_Symbol] = set()
    diagnostics: list[dict[str, str]] = []

    for old_path, new_path in pairs:
        snapshots: list[_Snapshot | None] = []
        sides: tuple[tuple[str | None, str, _Phase], ...] = (
            (old_path, change.base_commit_sha, "baseline"),
            (new_path, change.candidate_commit_sha, "candidate"),
        )
        for path, sha, phase in sides:
            snapshot = None
            if path is not None:
                attempted += 1
                try:
                    if attempted > MAX_FILE_SNAPSHOTS:
                        raise _Incomplete("snapshot_limit")
                    snapshot = _read_snapshot(root, sha, path, phase)
                    completed += 1
                except _Incomplete as error:
                    diagnostics.append({"path": path, "phase": phase, "code": str(error)})
            snapshots.append(snapshot)

        old, new = snapshots
        if old is not None and new is not None and old_path == new_path:
            old_lines, new_lines = _changed_lines(root, old.blob, new.blob)
            for snapshot, intervals in ((old, old_lines), (new, new_lines)):
                for start, count in intervals:
                    if count and (start < 1 or start + count > len(snapshot.owners)):
                        raise GitExecutionError("parse source diff", None, "Invalid hunk range")
                    selected.update(snapshot.owners[start : start + count])
        else:
            # Whole-file changes and unavailable counterparts conservatively select all scopes.
            for snapshot in snapshots:
                if snapshot is not None:
                    selected.update(snapshot.symbols)
                    selected.update(snapshot.owners[1:])

    result = change.model_copy(deep=True)
    result.changed_symbols = sorted({symbol.key for symbol in selected})
    result.completeness = change.completeness * (completed / expected if expected else 1.0)
    result.metadata["symbol_extraction"] = {
        "schema_version": "1",
        "extractor": "python-ast",
        "symbols": [
            asdict(symbol)
            for symbol in sorted(
                selected, key=lambda item: (item.path, item.phase, item.start_line, item.key)
            )
        ],
        "diagnostics": diagnostics,
        "expected_snapshots": expected,
        "completed_snapshots": completed,
        "max_blob_bytes": MAX_BLOB_BYTES,
        "max_file_snapshots": MAX_FILE_SNAPSHOTS,
    }
    return result


def _git(root: Path, arguments: list[str], operation: str) -> bytes:
    result = _execute_git(root, arguments, operation=operation)
    if result.returncode:
        raise GitExecutionError(operation, result.returncode, _decode_stderr(result.stderr))
    return result.stdout


def _read_snapshot(root: Path, sha: str, path: str, phase: _Phase) -> _Snapshot:
    entry = _git(root, ["ls-tree", "-z", sha, "--", f":(literal){path}"], "inspect source")
    if not entry:
        raise _Incomplete("missing_path")
    header, _, returned_path = entry.removesuffix(b"\0").partition(b"\t")
    fields = header.split()
    if len(fields) != 3 or returned_path != path.encode("utf-8"):
        raise GitExecutionError("inspect source", None, "Invalid tree entry")
    mode, kind, object_id = fields
    if mode not in {b"100644", b"100755"} or kind != b"blob":
        raise _Incomplete("unsupported_object_type")
    blob = object_id.decode("ascii")
    if _OBJECT_ID_PATTERN.fullmatch(blob) is None:
        raise GitExecutionError("inspect source", None, "Invalid blob ID")
    size = int(_git(root, ["cat-file", "-s", blob], "inspect source size"))
    if size > MAX_BLOB_BYTES:
        raise _Incomplete("blob_size_limit")
    source_bytes = _git(root, ["cat-file", "blob", blob], "read source")
    if len(source_bytes) != size:
        raise GitExecutionError("read source", None, "Unexpected blob size")
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(source_bytes).readline)
        source = source_bytes.decode(encoding).replace("\r\n", "\n")
    except (SyntaxError, UnicodeError, LookupError) as error:
        raise _Incomplete("source_encoding_error") from error
    if "\r" in source:
        # Git counts LF lines; bare CR line endings cannot use those hunk coordinates.
        raise _Incomplete("unsupported_line_endings")
    if source.count("\n") != source_bytes.count(b"\n"):
        raise _Incomplete("unsupported_encoding_line_mapping")
    try:
        tree = ast.parse(source)
        symbols, owners = _index_symbols(tree, source, path, phase)
    except (SyntaxError, ValueError, RecursionError) as error:
        raise _Incomplete("source_parse_error") from error
    return _Snapshot(blob, symbols, owners)


def _index_symbols(
    tree: ast.Module, source: str, path: str, phase: _Phase
) -> tuple[list[_Symbol], list[_Symbol]]:
    # Escape literal dots in path components so a.b.py cannot collide with a/b.py.
    module = ".".join(
        quote(part, safe="").replace(".", "%2E")
        for part in PurePosixPath(path).with_suffix("").parts
    )
    line_count = source.count("\n") + bool(source and not source.endswith("\n"))
    module_symbol = _Symbol(f"{module}.<module>", "module", phase, path, 1, max(1, line_count))
    owners = [module_symbol] * (line_count + 1)
    source_lines = source.split("\n")
    symbols: list[_Symbol] = []
    counts: Counter[str] = Counter()
    pending: list[tuple[ast.AST, str, bool]] = [(tree, module, False)]
    while pending:
        node, scope, in_class = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            key = f"{scope}.{node.name}"
            counts[key] += 1
            if counts[key] > 1:
                key = f"{key}#{counts[key]}"
            kind = (
                "class" if isinstance(node, ast.ClassDef) else "method" if in_class else "function"
            )
            start = node.lineno
            if node.decorator_list:
                start = node.decorator_list[0].lineno
                # Parenthesized decorators may start before their AST expression.
                while start > 1 and not source_lines[start - 1].lstrip().startswith("@"):
                    start -= 1
            end = node.end_lineno or node.lineno
            symbol = _Symbol(key, kind, phase, path, start, end)
            symbols.append(symbol)
            owners[start : end + 1] = [symbol] * (end - start + 1)
            scope, in_class = key, isinstance(node, ast.ClassDef)
        pending.extend(
            (child, scope, in_class) for child in reversed(list(ast.iter_child_nodes(node)))
        )
    return symbols, owners


def _changed_lines(
    root: Path, old_blob: str, new_blob: str
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    if old_blob == new_blob:
        return [], []
    output = _git(
        root,
        [
            "--no-pager",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--text",
            "--no-color",
            "--unified=0",
            "--inter-hunk-context=0",
            "--diff-algorithm=myers",
            "--no-indent-heuristic",
            old_blob,
            new_blob,
            "--",
        ],
        "diff source blobs",
    )
    old, new = [], []
    for match in _HUNK.finditer(output):
        old.append((int(match[1]), int(match[2]) if match[2] is not None else 1))
        new.append((int(match[3]), int(match[4]) if match[4] is not None else 1))
    return old, new
