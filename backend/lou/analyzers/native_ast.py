"""Offline AST rule for database execute calls inside Python loops."""

from __future__ import annotations

import ast
import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from contracts import Evidence, Finding

Phase = Literal["baseline", "candidate", "fix"]
Status = Literal["succeeded", "failed", "disabled"]
RULE_ID = "database-execute-in-loop-v1"
CATEGORY = "database-query-regression"
SOURCE = "native-ast"
_RUN_ID = re.compile(r"[A-Za-z0-9_-]+\Z")
_RECEIVERS = frozenset({"cursor", "connection", "conn", "db_cursor", "db_connection"})
_METHODS = frozenset({"execute", "executemany"})


class AnalyzerError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    file_path: str | None = None
    message: str
    line: int | None = None


class StaticAnalysisResult(BaseModel):
    """Exit status is independent of whether any findings were detected."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Status
    tool_exit_status: int | None
    completeness: float = Field(ge=0, le=1)
    findings: tuple[Finding, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    errors: tuple[AnalyzerError, ...] = ()
    artifact_uri: str | None = None
    artifact_sha256: str | None = None


def _is_database_execute(call: ast.Call) -> bool:
    method = call.func
    if not isinstance(method, ast.Attribute) or method.attr not in _METHODS:
        return False
    receiver = method.value
    return (isinstance(receiver, ast.Name) and receiver.id in _RECEIVERS) or (
        isinstance(receiver, ast.Attribute) and receiver.attr in _RECEIVERS
    )


class _LoopQueryVisitor(ast.NodeVisitor):
    def __init__(self, module_key: str) -> None:
        self.module_key = module_key
        self.classes: list[str] = []
        self.functions: list[str] = []
        self.loop_depth = 0
        self.matches: dict[str, list[int]] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.classes.append(node.name)
        for child in node.body:
            self.visit(child)
        self.classes.pop()

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        previous_depth = self.loop_depth
        self.loop_depth = 0
        self.functions.append(node.name)
        for child in node.body:
            self.visit(child)
        self.functions.pop()
        self.loop_depth = previous_depth

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def _loop(self, body: list[ast.stmt], orelse: list[ast.stmt]) -> None:
        self.loop_depth += 1
        for child in body:
            self.visit(child)
        self.loop_depth -= 1
        for child in orelse:
            self.visit(child)

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        self._loop(node.body, node.orelse)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit(node.iter)
        self._loop(node.body, node.orelse)

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        self._loop(node.body, node.orelse)

    def visit_Call(self, node: ast.Call) -> None:
        if self.functions and self.loop_depth and _is_database_execute(node):
            symbol = ".".join((self.module_key, *self.classes, *self.functions))
            self.matches.setdefault(symbol, []).append(node.lineno)
        self.generic_visit(node)


def _source_path(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if (
        not relative
        or pure.is_absolute()
        or ".." in pure.parts
        or "\\" in relative
        or "\x00" in relative
        or pure.suffix != ".py"
    ):
        raise ValueError("Expected a repository-relative Python source path")
    path = root.joinpath(*pure.parts)
    if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
        raise ValueError("Source path resolves outside the repository or through a symlink")
    if not path.is_file():
        raise ValueError("Source path is not a regular file")
    return path


def _artifact_path(root: Path, analysis_run_id: str, phase: Phase) -> tuple[Path, str]:
    if _RUN_ID.fullmatch(analysis_run_id) is None:
        raise ValueError("Analysis run ID is not safe for an artifact path")
    relative = PurePosixPath(".lou", "artifacts", analysis_run_id, phase, "static-analyzer.json")
    directory = root.joinpath(*relative.parts[:-1])
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("Artifact path passes through a symlink")
    if not directory.resolve(strict=False).is_relative_to(root):
        raise ValueError("Artifact path resolves outside the repository")
    return root.joinpath(*relative.parts), relative.as_posix()


def _write_artifact(path: Path, content: bytes) -> None:
    """Create an immutable artifact, allowing an identical retry to be idempotent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as artifact:
            artifact.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise ValueError("Refusing to overwrite an existing analyzer artifact") from None


def analyze_python_sources(
    *,
    repository_root: Path,
    file_paths: list[str] | tuple[str, ...],
    analysis_run_id: str,
    phase: Phase,
    enabled: bool = True,
) -> StaticAnalysisResult:
    """Parse source as data and emit contract records plus deterministic raw output."""
    if not enabled:
        return StaticAnalysisResult(status="disabled", tool_exit_status=None, completeness=0.0)

    errors: list[AnalyzerError] = []
    raw_files: list[dict[str, object]] = []
    matches: list[tuple[str, str, list[int]]] = []
    requested = sorted(set(file_paths))
    try:
        root = repository_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Repository root is not a directory")
    except (OSError, ValueError) as error:
        return StaticAnalysisResult(
            status="failed",
            tool_exit_status=1,
            completeness=0.0,
            errors=(AnalyzerError(code="repository_error", message=str(error)),),
        )

    if not requested:
        errors.append(AnalyzerError(code="no_files", message="No Python files were supplied"))
    successful = 0
    for relative in requested:
        try:
            source = _source_path(root, relative).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=relative)
            module_key = ".".join(PurePosixPath(relative).with_suffix("").parts)
            visitor = _LoopQueryVisitor(module_key)
            visitor.visit(tree)
        except SyntaxError as error:
            item = AnalyzerError(
                code="syntax_error",
                file_path=relative,
                message=error.msg,
                line=error.lineno,
            )
            errors.append(item)
            raw_files.append(
                {"file_path": relative, "status": "failed", "error": item.model_dump()}
            )
            continue
        except (OSError, UnicodeError, ValueError, RecursionError) as error:
            item = AnalyzerError(
                code="analysis_error",
                file_path=relative,
                message=f"{type(error).__name__}: {error}",
            )
            errors.append(item)
            raw_files.append(
                {"file_path": relative, "status": "failed", "error": item.model_dump()}
            )
            continue
        except Exception as error:
            item = AnalyzerError(
                code="analyzer_crash",
                file_path=relative,
                message=f"{type(error).__name__}: {error}",
            )
            errors.append(item)
            raw_files.append(
                {"file_path": relative, "status": "failed", "error": item.model_dump()}
            )
            continue

        successful += 1
        file_matches: list[dict[str, object]] = []
        for symbol, lines in sorted(visitor.matches.items()):
            line_numbers = sorted(set(lines))
            matches.append((relative, symbol, line_numbers))
            file_matches.append({"symbol_key": symbol, "line_numbers": line_numbers})
        raw_files.append({"file_path": relative, "status": "succeeded", "matches": file_matches})

    completeness = successful / len(requested) if requested else 0.0
    status: Literal["succeeded", "failed"] = "failed" if errors else "succeeded"
    raw = {
        "analyzer": SOURCE,
        "rule_id": RULE_ID,
        "phase": phase,
        "status": status,
        "tool_exit_status": 1 if errors else 0,
        "completeness": completeness,
        "files": raw_files,
        "errors": [item.model_dump() for item in errors],
    }
    raw_bytes = (json.dumps(raw, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        artifact, artifact_uri = _artifact_path(root, analysis_run_id, phase)
        _write_artifact(artifact, raw_bytes)
    except (OSError, ValueError) as error:
        return StaticAnalysisResult(
            status="failed",
            tool_exit_status=1,
            completeness=0.0,
            errors=tuple(errors)
            + (AnalyzerError(code="artifact_write_error", message=str(error)),),
        )

    artifact_sha256 = sha256(raw_bytes).hexdigest()
    findings: list[Finding] = []
    for relative, symbol, line_numbers in matches:
        fingerprint = f"{symbol}:{CATEGORY}"
        identifier = sha256(f"{analysis_run_id}:{phase}:{fingerprint}".encode()).hexdigest()[:16]
        findings.append(
            Finding(
                finding_id=f"finding_{identifier}",
                analysis_run_id=analysis_run_id,
                fingerprint=fingerprint,
                source=SOURCE,
                category=CATEGORY,
                severity="medium",
                confidence=0.85,
                phase=phase,
                title="Database call inside a loop may cause N+1 queries",
                message=(
                    "A cursor or connection execute call appears inside a loop body. "
                    "Static analysis cannot confirm runtime query counts."
                ),
                file_path=relative,
                symbol_key=symbol,
                metadata={"rule_id": RULE_ID, "line_numbers": line_numbers},
            )
        )

    evidence: list[Evidence] = []
    collected_at = datetime.now(UTC)
    for file_result in raw_files:
        relative = str(file_result["file_path"])
        identifier = sha256(f"{analysis_run_id}:{phase}:{relative}".encode()).hexdigest()[:16]
        evidence.append(
            Evidence(
                evidence_id=f"evidence_{identifier}",
                analysis_run_id=analysis_run_id,
                phase=phase,
                kind="static-analysis",
                source=SOURCE,
                collected_at=collected_at,
                summary={
                    "status": file_result["status"],
                    "finding_fingerprints": [
                        finding.fingerprint for finding in findings if finding.file_path == relative
                    ],
                },
                artifact_uri=artifact_uri,
                artifact_sha256=artifact_sha256,
                metadata={"rule_id": RULE_ID, "file_path": relative},
            )
        )
    return StaticAnalysisResult(
        status=status,
        tool_exit_status=1 if errors else 0,
        completeness=completeness,
        findings=tuple(findings),
        evidence=tuple(evidence),
        errors=tuple(errors),
        artifact_uri=artifact_uri,
        artifact_sha256=artifact_sha256,
    )
