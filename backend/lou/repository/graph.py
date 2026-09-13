"""Bounded, deterministic repository graph extraction from immutable Git objects."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import tempfile
import tokenize
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from stat import S_ISREG
from typing import Literal, TypeAlias, cast
from urllib.parse import urlsplit

import networkx as nx  # type: ignore[import-untyped]

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
from lou.repository.symbols import canonical_python_module

NodeType: TypeAlias = Literal[
    "FILE", "FUNCTION", "CLASS", "TEST", "ENDPOINT", "DATABASE_TABLE", "LOAD_SCENARIO"
]
EdgeType: TypeAlias = Literal[
    "DEFINES",
    "CALLS",
    "IMPORTS",
    "TESTED_BY",
    "SERVES_ENDPOINT",
    "READS_FROM",
    "WRITES_TO",
    "VALIDATED_BY",
]

_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_HTTP_METHODS = frozenset({"delete", "get", "head", "options", "patch", "post", "put", "trace"})
_LOAD_INVOCATION = re.compile(r"http\.(delete|get|head|options|patch|post|put|trace)\s*\(")
_LOAD_LITERAL = re.compile(
    r"\s*([`\"'])(.*?)\1",
    re.DOTALL,
)
_BASE_URL_EXPRESSION = re.compile(r"^\$\{[^{}]*\bBASE_URL\b[^{}]*\}")
_TABLE_PATTERNS: tuple[tuple[EdgeType, re.Pattern[str]], ...] = (
    ("READS_FROM", re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)", re.I)),
    (
        "WRITES_TO",
        re.compile(
            r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?|DROP\s+TABLE(?:\s+IF\s+EXISTS)?)\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)",
            re.I,
        ),
    ),
)


class RepositoryGraphError(Exception):
    """Graph construction failed at a trusted identity or artifact boundary."""


@dataclass(frozen=True)
class GraphLimits:
    max_files: int = 500
    max_blob_bytes: int = 1024 * 1024
    max_total_bytes: int = 10 * 1024 * 1024
    max_ast_nodes: int = 100_000
    max_diagnostics: int = 500

    def __post_init__(self) -> None:
        if (
            min(
                self.max_files,
                self.max_blob_bytes,
                self.max_total_bytes,
                self.max_ast_nodes,
                self.max_diagnostics,
            )
            <= 0
        ):
            raise ValueError("graph limits must be positive")


@dataclass(frozen=True, order=True)
class GraphDiagnostic:
    code: str
    path: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class RepositoryGraphSnapshot:
    graph: nx.MultiDiGraph
    repository_id: str
    commit_sha: str
    completeness: float
    diagnostics: tuple[GraphDiagnostic, ...]
    artifact_path: Path
    artifact_uri: str
    artifact_sha256: str


@dataclass(frozen=True)
class _TreeEntry:
    mode: str
    kind: str
    object_id: str
    path: str


@dataclass
class _PythonUnit:
    path: str
    module: str
    import_module: str
    is_package: bool
    tree: ast.Module
    source: str
    imports: dict[str, str]
    import_targets: set[str]
    instances: dict[str, str]
    definitions: dict[str, str]
    runtime_definitions: dict[str, str]
    node_by_ast_id: dict[int, str]
    key_by_ast_id: dict[int, str]
    class_by_ast_id: dict[int, str]
    endpoint_apps: dict[str, str]


@dataclass
class _Progress:
    eligible_files: int = 0
    parsed_files: int = 0
    relationship_attempts: int = 0
    relationship_resolved: int = 0
    total_bytes: int = 0


def _node_id(node_type: NodeType, key: str) -> str:
    prefix = "table" if node_type == "DATABASE_TABLE" else node_type.lower()
    return f"{prefix}:{key}"


def _import_module_name(path: str) -> str:
    pure = PurePosixPath(path)
    parts = list(pure.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_test_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return "tests" in pure.parts or pure.name.startswith("test_")


def _is_load_path(path: str) -> bool:
    return bool({"loadtest", "loadtests"}.intersection(PurePosixPath(path).parts))


def _add_node(
    graph: nx.MultiDiGraph,
    node_type: NodeType,
    key: str,
    *,
    path: str | None,
    line: int | None,
    confidence: float,
    evidence: str,
    changed: bool = False,
) -> str:
    identifier = _node_id(node_type, key)
    if identifier in graph:
        attributes = graph.nodes[identifier]
        if attributes["node_type"] != node_type or attributes["key"] != key:
            raise RepositoryGraphError(f"conflicting graph node identity: {identifier}")
        attributes["confidence"] = max(float(attributes["confidence"]), confidence)
        attributes["changed"] = bool(attributes["changed"] or changed)
        items = set(cast(list[str], attributes["evidence"]))
        items.add(evidence)
        attributes["evidence"] = sorted(items)
        locations = set(cast(list[str], attributes["locations"]))
        if path is not None:
            locations.add(f"{path}:{line or 1}")
        attributes["locations"] = sorted(locations)
        first = attributes["locations"][0] if attributes["locations"] else None
        if first is not None:
            first_path, _, first_line = first.rpartition(":")
            attributes["path"] = first_path
            attributes["line"] = int(first_line)
        return identifier
    initial_locations = [f"{path}:{line or 1}"] if path is not None else []
    graph.add_node(
        identifier,
        node_type=node_type,
        key=key,
        path=path,
        line=line,
        confidence=confidence,
        evidence=[evidence],
        locations=initial_locations,
        changed=changed,
    )
    return identifier


def _add_edge(
    graph: nx.MultiDiGraph,
    source: str,
    target: str,
    edge_type: EdgeType,
    *,
    confidence: float,
    evidence: str,
) -> None:
    if graph.has_edge(source, target, key=edge_type):
        data = graph[source][target][edge_type]
        data["confidence"] = max(float(data["confidence"]), confidence)
        items = set(cast(list[str], data["evidence"]))
        items.add(evidence)
        data["evidence"] = sorted(items)
        return
    graph.add_edge(
        source,
        target,
        key=edge_type,
        edge_type=edge_type,
        confidence=confidence,
        evidence=[evidence],
    )


def _diagnose(
    diagnostics: list[GraphDiagnostic],
    limits: GraphLimits,
    code: str,
    path: str | None,
    detail: str,
) -> None:
    if len(diagnostics) < limits.max_diagnostics:
        diagnostics.append(GraphDiagnostic(code, path, detail[:500]))


def _validate_identity(root: Path, change: RepositoryChange) -> None:
    if (
        not change.repository_id
        or len(change.repository_id) > 256
        or any(ord(character) < 32 for character in change.repository_id)
    ):
        raise RepositoryGraphError("repository_id is invalid")
    roles: tuple[tuple[str, Literal["base", "candidate"]], ...] = (
        (change.base_commit_sha, "base"),
        (change.candidate_commit_sha, "candidate"),
    )
    for sha, role in roles:
        if _OBJECT_ID_PATTERN.fullmatch(sha) is None or _resolve_commit(root, sha, role) != sha:
            raise InvalidCommitError(root, sha, role)


def _validate_symbol_provenance(change: RepositoryChange) -> None:
    if not change.changed_symbols:
        return
    metadata = change.metadata.get("symbol_extraction")
    if not isinstance(metadata, dict):
        raise RepositoryGraphError("changed symbols require RI-002 provenance")
    if metadata.get("schema_version") != "1" or metadata.get("extractor") != "python-ast":
        raise RepositoryGraphError("changed symbols have unsupported RI-002 provenance")
    symbols = metadata.get("symbols")
    if not isinstance(symbols, list):
        raise RepositoryGraphError("changed symbols have malformed RI-002 provenance")
    evidenced = {
        item.get("key")
        for item in symbols
        if isinstance(item, dict)
        and isinstance(item.get("key"), str)
        and item.get("phase") in {"baseline", "candidate"}
        and isinstance(item.get("path"), str)
    }
    if not set(change.changed_symbols).issubset(evidenced):
        raise RepositoryGraphError("changed symbols are not supported by RI-002 evidence")


def _tree_entries(root: Path, sha: str) -> list[_TreeEntry]:
    result = _execute_git(
        root,
        ["ls-tree", "-r", "-z", "--full-tree", sha, "--"],
        operation="enumerate repository graph inputs",
    )
    if result.returncode:
        raise GitExecutionError(
            "enumerate repository graph inputs", result.returncode, _decode_stderr(result.stderr)
        )
    if result.stdout and not result.stdout.endswith(b"\0"):
        raise GitExecutionError("parse repository tree", None, "Truncated ls-tree output")
    entries: list[_TreeEntry] = []
    for raw in result.stdout.removesuffix(b"\0").split(b"\0") if result.stdout else ():
        header, separator, raw_path = raw.partition(b"\t")
        fields = header.split()
        if not separator or len(fields) != 3:
            raise GitExecutionError("parse repository tree", None, "Invalid ls-tree entry")
        try:
            mode, kind, object_id = (field.decode("ascii") for field in fields)
        except UnicodeDecodeError as error:
            raise GitExecutionError(
                "parse repository tree", None, "Invalid ls-tree header"
            ) from error
        path = _decode_path(raw_path)
        if _OBJECT_ID_PATTERN.fullmatch(object_id) is None:
            raise GitExecutionError("parse repository tree", None, "Invalid Git object ID")
        entries.append(_TreeEntry(mode, kind, object_id, path))
    return sorted(entries, key=lambda item: item.path)


def _read_blob(root: Path, entry: _TreeEntry, limits: GraphLimits, progress: _Progress) -> str:
    size_result = _execute_git(
        root, ["cat-file", "-s", entry.object_id], operation="size graph blob"
    )
    if size_result.returncode:
        raise GitExecutionError(
            "size graph blob", size_result.returncode, _decode_stderr(size_result.stderr)
        )
    try:
        size = int(size_result.stdout)
    except ValueError as error:
        raise GitExecutionError("size graph blob", None, "Invalid blob size") from error
    if size > limits.max_blob_bytes:
        raise ValueError("blob_size_limit")
    if progress.total_bytes + size > limits.max_total_bytes:
        raise ValueError("total_size_limit")
    content = _execute_git(root, ["cat-file", "blob", entry.object_id], operation="read graph blob")
    if content.returncode:
        raise GitExecutionError(
            "read graph blob", content.returncode, _decode_stderr(content.stderr)
        )
    if len(content.stdout) != size:
        raise GitExecutionError("read graph blob", None, "Unexpected blob size")
    progress.total_bytes += size
    try:
        encoding, _ = tokenize.detect_encoding(
            iter(content.stdout.splitlines(keepends=True)).__next__
        )
        return content.stdout.decode(encoding).replace("\r\n", "\n")
    except (StopIteration, SyntaxError, UnicodeError, LookupError) as error:
        if not content.stdout:
            return ""
        raise ValueError("source_encoding_error") from error


def _relative_import_base(module: str, is_package: bool, level: int) -> str | None:
    package = module if is_package else module.rpartition(".")[0]
    parts = package.split(".") if package else []
    ascend = level - 1
    if ascend > len(parts):
        return None
    return ".".join(parts[: len(parts) - ascend])


def _collect_imports(
    tree: ast.Module, module: str, is_package: bool
) -> tuple[dict[str, str], set[str]]:
    imports: dict[str, str] = {}
    targets: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                binding = alias.asname or alias.name.split(".")[0]
                imports[binding] = alias.name if alias.asname else binding
                targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _relative_import_base(module, is_package, node.level)
                if base is None:
                    continue
                imported_module = ".".join(filter(None, (base, node.module or "")))
            elif node.module:
                imported_module = node.module
            else:
                continue
            targets.add(imported_module)
            for alias in node.names:
                if alias.name != "*":
                    target = ".".join(filter(None, (imported_module, alias.name)))
                    imports[alias.asname or alias.name] = target
                    targets.add(target)
    return imports, targets


def _canonical_reference(reference: str, module_keys: dict[str, str]) -> str:
    for module in sorted(module_keys, key=len, reverse=True):
        if reference == module or reference.startswith(f"{module}."):
            return f"{module_keys[module]}{reference[len(module) :]}"
    return reference


def _expression_reference(expression: ast.expr, imports: dict[str, str]) -> str | None:
    if isinstance(expression, ast.Name):
        return imports.get(expression.id, expression.id)
    if isinstance(expression, ast.Attribute):
        base = _expression_reference(expression.value, imports)
        return f"{base}.{expression.attr}" if base else None
    return None


def _literal_prefix(call: ast.Call) -> tuple[str, bool]:
    for keyword in call.keywords:
        if keyword.arg == "prefix":
            if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                value = keyword.value.value
                return (value, value == "" or value.startswith("/"))
            return ("", False)
    return ("", True)


def _endpoint_bindings(
    tree: ast.Module,
    path: str,
    progress: _Progress,
    diagnostics: list[GraphDiagnostic],
    limits: GraphLimits,
) -> dict[str, str]:
    bindings: dict[str, str] = {}
    active_imports: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                binding = alias.asname or alias.name.split(".")[0]
                active_imports[binding] = alias.name if alias.asname else binding
            continue
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                if alias.name != "*":
                    active_imports[alias.asname or alias.name] = f"{node.module}.{alias.name}"
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            active_imports.pop(node.name, None)
            continue
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not isinstance(
            node.value, ast.Call
        ):
            continue
        constructor = _expression_reference(node.value.func, active_imports)
        if constructor not in {"fastapi.FastAPI", "fastapi.APIRouter"}:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        prefix, valid = _literal_prefix(node.value)
        if not valid:
            progress.relationship_attempts += 1
            _diagnose(diagnostics, limits, "dynamic_router_prefix", path, f"line {node.lineno}")
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                bindings[target.id] = prefix if constructor.endswith("APIRouter") else ""
                active_imports.pop(target.id, None)

    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not isinstance(call.func, ast.Attribute) or call.func.attr != "include_router":
            continue
        if not isinstance(call.func.value, ast.Name) or call.func.value.id not in bindings:
            continue
        if not call.args or not isinstance(call.args[0], ast.Name):
            continue
        router = call.args[0].id
        if router not in bindings:
            continue
        prefix, valid = _literal_prefix(call)
        progress.relationship_attempts += 1
        if not valid:
            _diagnose(diagnostics, limits, "dynamic_router_prefix", path, f"line {node.lineno}")
            continue
        bindings[router] = _join_route(prefix, bindings[router])
        progress.relationship_resolved += 1
    return bindings


def _qualified_call(
    call: ast.Call,
    unit: _PythonUnit,
    class_key: str | None = None,
    scope_key: str | None = None,
    shadowed: set[str] | None = None,
) -> str | None:
    def resolve(expression: ast.expr) -> str | None:
        if isinstance(expression, ast.Name):
            if expression.id == "self" and class_key:
                return class_key
            if shadowed and expression.id in shadowed:
                return None
            local = f"{unit.module}.{expression.id}" if unit.module else expression.id
            if local in unit.runtime_definitions:
                return unit.runtime_definitions[local]
            if expression.id in unit.instances:
                return unit.instances[expression.id]
            if expression.id in unit.imports:
                return unit.imports[expression.id]
            scope = scope_key
            while scope and scope.startswith(unit.module):
                nested = f"{scope}.{expression.id}"
                if nested in unit.runtime_definitions:
                    return unit.runtime_definitions[nested]
                scope = scope.rsplit(".", 1)[0] if "." in scope else None
            return None
        if isinstance(expression, ast.Attribute):
            base = resolve(expression.value)
            return f"{base}.{expression.attr}" if base else None
        if isinstance(expression, ast.Call):
            return resolve(expression.func)
        return None

    return resolve(call.func)


def _join_route(prefix: str, path: str) -> str:
    return f"{prefix.rstrip('/')}/{path.lstrip('/')}" if prefix else path


def _decorator_endpoint(
    decorator: ast.expr, endpoint_apps: dict[str, str]
) -> list[tuple[str, str]] | None:
    if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
        return None
    if (
        not isinstance(decorator.func.value, ast.Name)
        or decorator.func.value.id not in endpoint_apps
    ):
        return None
    name = decorator.func.attr.lower()
    if name not in _HTTP_METHODS and name != "api_route":
        return None
    if (
        not decorator.args
        or not isinstance(decorator.args[0], ast.Constant)
        or not isinstance(decorator.args[0].value, str)
    ):
        return []
    path = _join_route(endpoint_apps[decorator.func.value.id], decorator.args[0].value)
    if not path.startswith("/"):
        return []
    if name in _HTTP_METHODS:
        return [(name.upper(), path)]
    methods: list[str] = []
    methods_declared = False
    for keyword in decorator.keywords:
        if keyword.arg != "methods":
            continue
        methods_declared = True
        if isinstance(keyword.value, (ast.List, ast.Tuple, ast.Set)):
            for item in keyword.value.elts:
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    method = item.value.lower()
                    if method in _HTTP_METHODS:
                        methods.append(method.upper())
    if not methods and not methods_declared:
        methods.append("GET")
    return [(method, path) for method in sorted(set(methods))]


def _build_definitions(
    graph: nx.MultiDiGraph,
    unit: _PythonUnit,
    changed_symbols: set[str],
    progress: _Progress,
    diagnostics: list[GraphDiagnostic],
    limits: GraphLimits,
) -> None:
    file_id = _node_id("FILE", unit.path)
    counts: dict[str, int] = {}

    def walk(nodes: Iterable[ast.AST], scope: str, parent_id: str) -> None:
        for node in nodes:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(ast.iter_child_nodes(node), scope, parent_id)
                continue
            raw_key = f"{scope}.{node.name}" if scope else node.name
            counts[raw_key] = counts.get(raw_key, 0) + 1
            key = raw_key if counts[raw_key] == 1 else f"{raw_key}#{counts[raw_key]}"
            if isinstance(node, ast.ClassDef):
                node_type: NodeType = "CLASS"
            else:
                node_type = (
                    "TEST"
                    if _is_test_path(unit.path) and node.name.startswith("test_")
                    else "FUNCTION"
                )
            local_key = key.removeprefix(f"{unit.module}.")
            stable_key = f"{unit.path}::{local_key}" if node_type == "TEST" else key
            identifier = _add_node(
                graph,
                node_type,
                stable_key,
                path=unit.path,
                line=node.lineno,
                confidence=1.0,
                evidence=f"python-ast:{unit.path}:{node.lineno}",
                changed=key in changed_symbols,
            )
            _add_edge(
                graph,
                parent_id,
                identifier,
                "DEFINES",
                confidence=1.0,
                evidence=f"python-ast:{unit.path}:{node.lineno}",
            )
            unit.definitions[key] = identifier
            unit.runtime_definitions[raw_key] = key
            unit.node_by_ast_id[id(node)] = identifier
            unit.key_by_ast_id[id(node)] = key
            if isinstance(node, ast.ClassDef):
                unit.class_by_ast_id[id(node)] = key
                walk(node.body, key, identifier)
            else:
                for decorator in node.decorator_list:
                    endpoints = _decorator_endpoint(decorator, unit.endpoint_apps)
                    if endpoints is None:
                        continue
                    if not endpoints:
                        progress.relationship_attempts += 1
                        _diagnose(
                            diagnostics,
                            limits,
                            "dynamic_endpoint",
                            unit.path,
                            f"line {decorator.lineno}",
                        )
                    for method, path in endpoints:
                        progress.relationship_attempts += 1
                        endpoint_key = f"{method} {path}"
                        endpoint_id = _add_node(
                            graph,
                            "ENDPOINT",
                            endpoint_key,
                            path=unit.path,
                            line=decorator.lineno,
                            confidence=1.0,
                            evidence=f"fastapi-decorator:{unit.path}:{decorator.lineno}",
                        )
                        _add_edge(
                            graph,
                            identifier,
                            endpoint_id,
                            "SERVES_ENDPOINT",
                            confidence=1.0,
                            evidence=f"fastapi-decorator:{unit.path}:{decorator.lineno}",
                        )
                        progress.relationship_resolved += 1
                walk(node.body, key, identifier)

    walk(unit.tree.body, unit.module, file_id)


class _BodyVisitor(ast.NodeVisitor):
    def __init__(self, owner: ast.AST) -> None:
        self.owner = owner
        self.calls: list[ast.Call] = []

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node is self.owner:
            self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node is self.owner:
            self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node is self.owner:
            self.generic_visit(node)


class _BindingVisitor(ast.NodeVisitor):
    def __init__(self, owner: ast.AST) -> None:
        self.owner = owner
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.names.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node is self.owner:
            self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node is self.owner:
            self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node is self.owner:
            self.generic_visit(node)


def _shadowed_names(node: ast.AST) -> set[str]:
    visitor = _BindingVisitor(node)
    visitor.visit(node)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        arguments = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        visitor.names.update(argument.arg for argument in arguments)
        if node.args.vararg:
            visitor.names.add(node.args.vararg.arg)
        if node.args.kwarg:
            visitor.names.add(node.args.kwarg.arg)
    return visitor.names


def _owner_nodes(unit: _PythonUnit) -> list[tuple[ast.AST, str, str, str | None]]:
    result: list[tuple[ast.AST, str, str, str | None]] = []

    def walk(nodes: Iterable[ast.AST], class_key: str | None = None) -> None:
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                key = unit.class_by_ast_id.get(id(node))
                walk(node.body, key)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                identifier = unit.node_by_ast_id.get(id(node))
                key = unit.key_by_ast_id.get(id(node))
                if identifier and key:
                    result.append((node, identifier, key, class_key))
                    walk(node.body, class_key)
            else:
                walk(ast.iter_child_nodes(node), class_key)

    walk(unit.tree.body)
    return result


def _sql_relations(sql: str) -> list[tuple[EdgeType, str]]:
    relations: set[tuple[EdgeType, str]] = set()
    for edge_type, pattern in _TABLE_PATTERNS:
        for match in pattern.finditer(sql):
            relations.add((edge_type, match.group(1).lower()))
    return sorted(relations)


def _build_relationships(
    graph: nx.MultiDiGraph,
    unit: _PythonUnit,
    all_definitions: dict[str, str],
    progress: _Progress,
    diagnostics: list[GraphDiagnostic],
    limits: GraphLimits,
    local_modules: set[str],
    runtime_definitions: dict[str, str],
) -> None:
    for node, owner_id, scope_key, class_key in _owner_nodes(unit):
        visitor = _BodyVisitor(node)
        visitor.visit(node)
        shadowed = _shadowed_names(node)
        for call in visitor.calls:
            if isinstance(call.func, ast.Attribute) and call.func.attr in {
                "execute",
                "executemany",
            }:
                if (
                    call.args
                    and isinstance(call.args[0], ast.Constant)
                    and isinstance(call.args[0].value, str)
                ):
                    relations = _sql_relations(call.args[0].value)
                    if relations:
                        progress.relationship_attempts += 1
                        for edge_type, table in relations:
                            table_id = _add_node(
                                graph,
                                "DATABASE_TABLE",
                                table,
                                path=unit.path,
                                line=call.lineno,
                                confidence=0.75,
                                evidence=f"sql-literal:{unit.path}:{call.lineno}",
                            )
                            _add_edge(
                                graph,
                                owner_id,
                                table_id,
                                edge_type,
                                confidence=0.75,
                                evidence=f"sql-literal:{unit.path}:{call.lineno}",
                            )
                        progress.relationship_resolved += 1
                else:
                    progress.relationship_attempts += 1
                    _diagnose(diagnostics, limits, "dynamic_sql", unit.path, f"line {call.lineno}")
                continue
            target_key = _qualified_call(call, unit, class_key, scope_key, shadowed)
            if target_key is None:
                continue
            target_key = runtime_definitions.get(target_key, target_key)
            if target_key not in all_definitions:
                if target_key.startswith(unit.module) or any(
                    target_key == module or target_key.startswith(f"{module}.")
                    for module in local_modules
                ):
                    progress.relationship_attempts += 1
                    _diagnose(diagnostics, limits, "unresolved_call", unit.path, target_key)
                continue
            progress.relationship_attempts += 1
            target_id = all_definitions[target_key]
            _add_edge(
                graph,
                owner_id,
                target_id,
                "CALLS",
                confidence=0.9,
                evidence=f"python-ast:{unit.path}:{call.lineno}",
            )
            if graph.nodes[owner_id]["node_type"] == "TEST":
                _add_edge(
                    graph,
                    target_id,
                    owner_id,
                    "TESTED_BY",
                    confidence=0.9,
                    evidence=f"python-ast:{unit.path}:{call.lineno}",
                )
            progress.relationship_resolved += 1


def _parse_load_scenario(
    graph: nx.MultiDiGraph,
    path: str,
    source: str,
    progress: _Progress,
    diagnostics: list[GraphDiagnostic],
    limits: GraphLimits,
) -> None:
    scenario_id = _add_node(
        graph,
        "LOAD_SCENARIO",
        path,
        path=path,
        line=1,
        confidence=1.0,
        evidence=f"checked-in-loadtest:{path}",
    )
    newline_offsets = [index for index, character in enumerate(source) if character == "\n"]
    for invocation in _LOAD_INVOCATION.finditer(source):
        progress.relationship_attempts += 1
        method = invocation.group(1).upper()
        literal = _LOAD_LITERAL.match(source, invocation.end())
        line = bisect_right(newline_offsets, invocation.start()) + 1
        if literal is None:
            _diagnose(
                diagnostics,
                limits,
                "dynamic_load_endpoint",
                path,
                f"line {line}",
            )
            continue
        raw_url = literal.group(2).strip()
        if raw_url.startswith("${"):
            base = _BASE_URL_EXPRESSION.match(raw_url)
            if base is None:
                route = ""
            else:
                raw_url = raw_url[base.end() :]
                route = urlsplit(raw_url).path
        else:
            parsed = urlsplit(raw_url)
            route = parsed.path
        if not route.startswith("/") or "${" in raw_url or "}" in raw_url:
            _diagnose(
                diagnostics,
                limits,
                "dynamic_load_endpoint",
                path,
                f"line {line}",
            )
            continue
        endpoint_id = _node_id("ENDPOINT", f"{method} {route}")
        if endpoint_id not in graph:
            _diagnose(diagnostics, limits, "unresolved_load_endpoint", path, f"{method} {route}")
            continue
        _add_edge(
            graph,
            endpoint_id,
            scenario_id,
            "VALIDATED_BY",
            confidence=0.9,
            evidence=f"loadtest-http-call:{path}:{line}",
        )
        progress.relationship_resolved += 1


def _safe_artifact_target(artifact_root: Path, analysis_run_id: str) -> tuple[Path, str]:
    if _RUN_ID.fullmatch(analysis_run_id) is None or analysis_run_id in {".", ".."}:
        raise RepositoryGraphError("analysis_run_id is unsafe for artifact storage")
    if artifact_root.is_symlink():
        raise RepositoryGraphError("artifact root cannot be a symlink")
    root = artifact_root.resolve(strict=False)
    relative = PurePosixPath(analysis_run_id, "candidate", "repository-graph.json")
    target = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise RepositoryGraphError("artifact path passes through a symlink")
    if not target.resolve(strict=False).is_relative_to(root):
        raise RepositoryGraphError("artifact path resolves outside the artifact root")
    return target, relative.as_posix()


def _payload(
    graph: nx.MultiDiGraph,
    change: RepositoryChange,
    completeness: float,
    diagnostics: tuple[GraphDiagnostic, ...],
    limits: GraphLimits,
) -> bytes:
    nodes = [
        {"id": identifier, **dict(attributes)}
        for identifier, attributes in sorted(graph.nodes(data=True), key=lambda item: item[0])
    ]
    edges = [
        {"source": source, "target": target, "key": key, **dict(attributes)}
        for source, target, key, attributes in sorted(
            graph.edges(keys=True, data=True), key=lambda item: (item[0], item[2], item[1])
        )
    ]
    value = {
        "schema_version": "1",
        "source": "lou.repository.graph",
        "phase": "candidate",
        "repository_id": change.repository_id,
        "commit_sha": change.candidate_commit_sha,
        "completeness": completeness,
        "limits": asdict(limits),
        "nodes": nodes,
        "edges": edges,
        "diagnostics": [asdict(item) for item in diagnostics],
    }
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _existing_artifact_matches(target: Path, content: bytes) -> bool:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags)
    except (FileNotFoundError, OSError):
        return False
    try:
        metadata = os.fstat(descriptor)
        if not S_ISREG(metadata.st_mode) or metadata.st_size != len(content):
            return False
        remaining = memoryview(content)
        offset = 0
        while offset < len(remaining):
            chunk = os.read(descriptor, min(64 * 1024, len(remaining) - offset))
            if not chunk or chunk != remaining[offset : offset + len(chunk)]:
                return False
            offset += len(chunk)
        return os.read(descriptor, 1) == b""
    finally:
        os.close(descriptor)


def _write_once(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if _existing_artifact_matches(target, content):
            return
        raise RepositoryGraphError("a different completed graph artifact already exists")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".repository-graph-", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if not _existing_artifact_matches(target, content):
                raise RepositoryGraphError("a different completed graph artifact already exists")
        directory = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def build_repository_graph(
    *,
    repository_path: str | Path,
    change: RepositoryChange,
    analysis_run_id: str,
    artifact_root: Path,
    limits: GraphLimits | None = None,
) -> RepositoryGraphSnapshot:
    """Build RI-003 from RI-001/002 output without reading mutable worktree files.

    ``artifact_root`` is a trusted application boundary and must not be writable by
    untrusted principals.
    """
    active_limits = limits or GraphLimits()
    root = _discover_repository_root(_validate_path(repository_path))
    _validate_identity(root, change)
    _validate_symbol_provenance(change)
    target, artifact_uri = _safe_artifact_target(artifact_root, analysis_run_id)
    graph = nx.MultiDiGraph(
        repository_id=change.repository_id,
        commit_sha=change.candidate_commit_sha,
        phase="candidate",
    )
    diagnostics: list[GraphDiagnostic] = []
    progress = _Progress()
    units: list[_PythonUnit] = []
    load_sources: list[tuple[str, str]] = []
    entries = _tree_entries(root, change.candidate_commit_sha)
    eligible = [
        entry
        for entry in entries
        if entry.path.endswith(".py")
        or (entry.path.endswith((".js", ".ts")) and _is_load_path(entry.path))
    ]
    progress.eligible_files = len(eligible)
    if len(eligible) > active_limits.max_files:
        for entry in eligible[active_limits.max_files :]:
            _diagnose(diagnostics, active_limits, "file_limit", entry.path, "eligible file skipped")
        eligible = eligible[: active_limits.max_files]

    for entry in eligible:
        if entry.mode not in {"100644", "100755"} or entry.kind != "blob":
            _diagnose(diagnostics, active_limits, "unsupported_object_type", entry.path, entry.mode)
            continue
        try:
            source = _read_blob(root, entry, active_limits, progress)
        except ValueError as error:
            _diagnose(diagnostics, active_limits, str(error), entry.path, "source was not parsed")
            continue
        if entry.path.endswith(".py"):
            try:
                tree = ast.parse(source, filename=entry.path)
            except (SyntaxError, ValueError, RecursionError) as error:
                _diagnose(
                    diagnostics,
                    active_limits,
                    "source_parse_error",
                    entry.path,
                    type(error).__name__,
                )
                continue
            if sum(1 for _ in ast.walk(tree)) > active_limits.max_ast_nodes:
                _diagnose(
                    diagnostics, active_limits, "ast_node_limit", entry.path, "AST was too large"
                )
                continue
            import_module = _import_module_name(entry.path)
            is_package = PurePosixPath(entry.path).name == "__init__.py"
            imports, import_targets = _collect_imports(tree, import_module, is_package)
            endpoint_apps = _endpoint_bindings(
                tree, entry.path, progress, diagnostics, active_limits
            )
            unit = _PythonUnit(
                path=entry.path,
                module=canonical_python_module(entry.path),
                import_module=import_module,
                is_package=is_package,
                tree=tree,
                source=source,
                imports=imports,
                import_targets=import_targets,
                instances={},
                definitions={},
                runtime_definitions={},
                node_by_ast_id={},
                key_by_ast_id={},
                class_by_ast_id={},
                endpoint_apps=endpoint_apps,
            )
            units.append(unit)
            _add_node(
                graph,
                "FILE",
                entry.path,
                path=entry.path,
                line=1,
                confidence=1.0,
                evidence=f"git-blob:{entry.object_id}",
            )
        else:
            load_sources.append((entry.path, source))
        progress.parsed_files += 1

    changed_symbols = set(change.changed_symbols)
    for unit in units:
        _build_definitions(graph, unit, changed_symbols, progress, diagnostics, active_limits)
    all_definitions = {
        key: identifier for unit in units for key, identifier in unit.definitions.items()
    }
    runtime_definitions = {
        raw_key: key for unit in units for raw_key, key in unit.runtime_definitions.items()
    }
    module_units: dict[str, list[_PythonUnit]] = {}
    for unit in units:
        module_units.setdefault(unit.import_module, []).append(unit)
    module_keys = {
        module: candidates[0].module
        for module, candidates in module_units.items()
        if len(candidates) == 1
    }
    module_paths = {
        module: candidates[0].path
        for module, candidates in module_units.items()
        if len(candidates) == 1
    }
    local_roots = {module.partition(".")[0] for module in module_units}
    for unit in units:
        unit.imports = {
            binding: _canonical_reference(reference, module_keys)
            for binding, reference in unit.imports.items()
        }
        file_id = _node_id("FILE", unit.path)
        for imported in sorted(unit.import_targets):
            candidate = imported
            target_module = None
            ambiguous = False
            while candidate:
                if candidate in module_units and len(module_units[candidate]) > 1:
                    ambiguous = True
                    break
                if candidate in module_paths:
                    target_module = candidate
                    break
                candidate = candidate.rpartition(".")[0]
            if ambiguous:
                progress.relationship_attempts += 1
                _diagnose(diagnostics, active_limits, "ambiguous_import", unit.path, imported)
                continue
            if target_module:
                progress.relationship_attempts += 1
                _add_edge(
                    graph,
                    file_id,
                    _node_id("FILE", module_paths[target_module]),
                    "IMPORTS",
                    confidence=1.0,
                    evidence=f"python-import:{unit.path}",
                )
                progress.relationship_resolved += 1
            elif imported.partition(".")[0] in local_roots:
                progress.relationship_attempts += 1
                _diagnose(diagnostics, active_limits, "unresolved_import", unit.path, imported)
        for node in unit.tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                key = _qualified_call(node.value, unit)
                key = runtime_definitions.get(key, key) if key is not None else None
                if (
                    key in all_definitions
                    and graph.nodes[all_definitions[key]]["node_type"] == "CLASS"
                ):
                    for item in targets:
                        if isinstance(item, ast.Name):
                            unit.instances[item.id] = key
        _build_relationships(
            graph,
            unit,
            all_definitions,
            progress,
            diagnostics,
            active_limits,
            set(module_units),
            runtime_definitions,
        )
    for path, source in load_sources:
        _parse_load_scenario(graph, path, source, progress, diagnostics, active_limits)

    for key in sorted(changed_symbols - set(all_definitions)):
        progress.relationship_attempts += 1
        _diagnose(diagnostics, active_limits, "changed_symbol_missing_from_candidate", None, key)
    parsed_ratio = (
        progress.parsed_files / progress.eligible_files if progress.eligible_files else 1.0
    )
    relationship_ratio = (
        progress.relationship_resolved / progress.relationship_attempts
        if progress.relationship_attempts
        else 1.0
    )
    if progress.relationship_resolved > progress.relationship_attempts:
        raise RepositoryGraphError("relationship completeness invariant was violated")
    completeness = max(0.0, min(1.0, change.completeness * parsed_ratio * relationship_ratio))
    ordered_diagnostics = tuple(sorted(set(diagnostics)))
    graph.graph["completeness"] = completeness
    graph.graph["diagnostics"] = [asdict(item) for item in ordered_diagnostics]
    content = _payload(graph, change, completeness, ordered_diagnostics, active_limits)
    _write_once(target, content)
    digest = hashlib.sha256(content).hexdigest()
    return RepositoryGraphSnapshot(
        graph,
        change.repository_id,
        change.candidate_commit_sha,
        completeness,
        ordered_diagnostics,
        target,
        artifact_uri,
        digest,
    )
