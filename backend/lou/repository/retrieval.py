"""Bounded lexical retrieval from immutable Python blobs at one Git commit."""

from __future__ import annotations

import ast
import io
import math
import re
import tokenize
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol, TypeAlias

from contracts import Finding, RepositoryContext
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

DocumentKind: TypeAlias = Literal["file", "class", "function", "method", "test"]

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_IDENTIFIER = re.compile(r"[^A-Za-z0-9]+")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "one",
        "or",
        "per",
        "that",
        "the",
        "this",
        "to",
        "with",
    }
)


@dataclass(frozen=True)
class RetrievalLimits:
    """Resource ceilings shared by indexing and one ranked retrieval."""

    max_files: int = 500
    max_blob_bytes: int = 1024 * 1024
    max_total_bytes: int = 10 * 1024 * 1024
    max_ast_nodes: int = 100_000
    max_index_tokens: int = 200_000
    max_results: int = 8
    max_result_bytes: int = 8 * 1024
    max_result_tokens: int = 2_048
    max_diagnostics: int = 500

    def __post_init__(self) -> None:
        if min(asdict(self).values()) <= 0:
            raise ValueError("retrieval limits must be positive")


@dataclass(frozen=True, order=True)
class RetrievalDiagnostic:
    """One bounded explanation for incomplete indexing or retrieval."""

    stage: Literal["index", "retrieve"]
    code: str
    path: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class IndexedDocument:
    """One immutable file or source symbol indexed from a candidate blob."""

    key: str
    kind: DocumentKind
    path: str
    symbol_key: str | None
    start_line: int
    end_line: int
    blob_object_id: str
    content: str
    terms: tuple[str, ...]
    content_bytes: int
    estimated_tokens: int


@dataclass(frozen=True)
class RetrievalQuery:
    """Analysis inputs from which deterministic lexical query terms are derived."""

    changed_symbols: tuple[str, ...] = ()
    finding_title: str = ""
    finding_message: str = ""
    finding_category: str = ""
    affected_graph_nodes: tuple[str, ...] = ()
    graph_required_keys: tuple[str, ...] = ()
    caller_query: str | None = None
    excluded_keys: tuple[str, ...] = ()

    @classmethod
    def from_analysis(
        cls,
        context: RepositoryContext,
        finding: Finding,
        *,
        caller_query: str | None = None,
        excluded_keys: Sequence[str] = (),
    ) -> RetrievalQuery:
        """Create a query without changing graph-selected context or workload identity."""

        affected = tuple(
            dict.fromkeys(
                (
                    *context.affected_symbols,
                    *context.affected_tests,
                    *context.affected_endpoints,
                    *context.affected_data_dependencies,
                    *context.selected_workload_ids,
                )
            )
        )
        required = tuple(dict.fromkeys((*context.changed_symbols, *affected)))
        return cls(
            changed_symbols=tuple(context.changed_symbols),
            finding_title=finding.title,
            finding_message=finding.message,
            finding_category=finding.category,
            affected_graph_nodes=affected,
            graph_required_keys=required,
            caller_query=caller_query,
            excluded_keys=tuple(excluded_keys),
        )

    def terms(self) -> tuple[str, ...]:
        values = (
            *self.changed_symbols,
            self.finding_title,
            self.finding_message,
            self.finding_category,
            *self.affected_graph_nodes,
            self.caller_query or "",
        )
        return tuple(sorted(set(_terms(" ".join(values)))))


@dataclass(frozen=True)
class RankedRetrievalResult:
    """One scored supplemental result with an explainable selection reason."""

    key: str
    kind: DocumentKind
    path: str
    symbol_key: str | None
    start_line: int
    end_line: int
    score: float
    matched_terms: tuple[str, ...]
    selection_reason: str
    content: str
    content_bytes: int
    estimated_tokens: int


@dataclass(frozen=True)
class RetrievalResponse:
    """Stable retrieval output that distinguishes no match from incomplete work."""

    backend: str
    repository_id: str
    commit_sha: str
    query_terms: tuple[str, ...]
    results: tuple[RankedRetrievalResult, ...]
    diagnostics: tuple[RetrievalDiagnostic, ...]
    completeness: float
    limits: RetrievalLimits
    used_result_bytes: int
    used_result_tokens: int

    def __post_init__(self) -> None:
        if not self.backend:
            raise ValueError("retrieval backend must be non-empty")

    def payload(self) -> dict[str, object]:
        """Return a JSON-safe value with deterministic tuple ordering preserved."""

        return {
            "backend": self.backend,
            "repository_id": self.repository_id,
            "commit_sha": self.commit_sha,
            "query_terms": list(self.query_terms),
            "results": [asdict(item) for item in self.results],
            "diagnostics": [asdict(item) for item in self.diagnostics],
            "completeness": self.completeness,
            "limits": asdict(self.limits),
            "used_result_bytes": self.used_result_bytes,
            "used_result_tokens": self.used_result_tokens,
        }


@dataclass(frozen=True)
class LexicalRepositoryIndex:
    """An immutable candidate-commit index reusable for multiple local queries."""

    repository_id: str
    commit_sha: str
    documents: tuple[IndexedDocument, ...]
    diagnostics: tuple[RetrievalDiagnostic, ...]
    completeness: float
    limits: RetrievalLimits
    eligible_file_count: int
    indexed_file_count: int
    indexed_bytes: int
    ast_nodes_processed: int
    lexical_tokens_processed: int

    def retrieve(self, query: RetrievalQuery) -> RetrievalResponse:
        return _retrieve(self, query)


class RepositoryRetriever(Protocol):
    """Extension point for a future local SCIP or embedding implementation."""

    def retrieve(
        self,
        *,
        repository_path: str | Path,
        repository_id: str,
        candidate_commit_sha: str,
        query: RetrievalQuery,
        limits: RetrievalLimits | None = None,
    ) -> RetrievalResponse: ...


class LocalLexicalRetriever:
    """Default dependency-free implementation of the retrieval interface."""

    def retrieve(
        self,
        *,
        repository_path: str | Path,
        repository_id: str,
        candidate_commit_sha: str,
        query: RetrievalQuery,
        limits: RetrievalLimits | None = None,
    ) -> RetrievalResponse:
        index = build_lexical_index(
            repository_path=repository_path,
            repository_id=repository_id,
            candidate_commit_sha=candidate_commit_sha,
            limits=limits,
        )
        return index.retrieve(query)


@dataclass(frozen=True)
class _TreeEntry:
    mode: str
    kind: str
    object_id: str
    path: str


class _Incomplete(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def _terms(value: str) -> tuple[str, ...]:
    """Normalize identifiers while retaining a joined form for compound matching."""

    result: list[str] = []
    for raw in _NON_IDENTIFIER.split(value):
        if not raw:
            continue
        pieces = [item.casefold() for item in _CAMEL_BOUNDARY.split(raw) if item]
        result.extend(item for item in pieces if len(item) > 1 and item not in _STOP_WORDS)
        joined = "".join(pieces)
        if len(pieces) > 1 and len(joined) > 1 and joined not in _STOP_WORDS:
            result.append(joined)
    return tuple(result)


def _source_terms(source: str, *, max_terms: int) -> tuple[tuple[int, str], ...]:
    result: list[tuple[int, str]] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in {tokenize.NAME, tokenize.COMMENT}:
                for term in _terms(token.string):
                    if len(result) >= max_terms:
                        raise _Incomplete("index_token_limit", str(max_terms))
                    result.append((token.start[0], term))
    except (IndentationError, SyntaxError, tokenize.TokenError) as error:
        raise _Incomplete("source_tokenize_error", type(error).__name__) from error
    return tuple(result)


def _docstring_terms(tree: ast.AST, *, max_terms: int) -> tuple[tuple[int, str], ...]:
    result: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            value = ast.get_docstring(node, clean=False)
            if value:
                for term in _terms(value):
                    if len(result) >= max_terms:
                        raise _Incomplete("index_token_limit", str(max_terms))
                    result.append((getattr(node, "lineno", 1), term))
    return tuple(result)


def _tree_entries(root: Path, sha: str) -> tuple[_TreeEntry, ...]:
    result = _execute_git(
        root,
        ["ls-tree", "-r", "-z", "--full-tree", sha, "--"],
        operation="enumerate lexical index inputs",
    )
    if result.returncode:
        raise GitExecutionError(
            "enumerate lexical index inputs", result.returncode, _decode_stderr(result.stderr)
        )
    if result.stdout and not result.stdout.endswith(b"\0"):
        raise GitExecutionError("parse lexical index tree", None, "Truncated ls-tree output")
    entries: list[_TreeEntry] = []
    records = result.stdout.removesuffix(b"\0").split(b"\0") if result.stdout else ()
    for record in records:
        header, separator, raw_path = record.partition(b"\t")
        fields = header.split()
        if not separator or len(fields) != 3:
            raise GitExecutionError("parse lexical index tree", None, "Invalid ls-tree entry")
        try:
            mode, kind, object_id = (field.decode("ascii") for field in fields)
        except UnicodeDecodeError as error:
            raise GitExecutionError(
                "parse lexical index tree", None, "Invalid ls-tree header"
            ) from error
        path = _decode_path(raw_path)
        if _OBJECT_ID_PATTERN.fullmatch(object_id) is None:
            raise GitExecutionError("parse lexical index tree", None, "Invalid Git object ID")
        entries.append(_TreeEntry(mode, kind, object_id, path))
    return tuple(sorted(entries, key=lambda item: item.path))


def _blob_size(root: Path, entry: _TreeEntry) -> int:
    result = _execute_git(root, ["cat-file", "-s", entry.object_id], operation="size lexical blob")
    if result.returncode:
        raise _Incomplete("missing_blob", _decode_stderr(result.stderr) or entry.object_id)
    try:
        return int(result.stdout)
    except ValueError as error:
        raise _Incomplete("invalid_blob_size", entry.object_id) from error


def _blob_bytes(root: Path, entry: _TreeEntry, expected_size: int) -> bytes:
    result = _execute_git(
        root, ["cat-file", "blob", entry.object_id], operation="read lexical blob"
    )
    if result.returncode:
        raise _Incomplete("missing_blob", _decode_stderr(result.stderr) or entry.object_id)
    if len(result.stdout) != expected_size:
        raise _Incomplete("unexpected_blob_size", entry.object_id)
    return result.stdout


def _decode_source(content: bytes) -> str:
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(content).readline)
        return content.decode(encoding).replace("\r\n", "\n")
    except (SyntaxError, UnicodeError, LookupError) as error:
        raise _Incomplete("source_encoding_error", type(error).__name__) from error


def _is_test_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return "tests" in pure.parts or pure.name.startswith("test_")


def _definition_documents(
    tree: ast.Module,
    source: str,
    entry: _TreeEntry,
    source_terms: Sequence[tuple[int, str]],
) -> tuple[IndexedDocument, ...]:
    module = canonical_python_module(entry.path)
    lines = source.splitlines(keepends=True)
    counts: Counter[str] = Counter()
    documents: list[IndexedDocument] = []

    def walk(nodes: Iterable[ast.AST], scope: str, in_class: bool) -> None:
        for node in nodes:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(ast.iter_child_nodes(node), scope, in_class)
                continue
            raw_key = f"{scope}.{node.name}" if scope else node.name
            counts[raw_key] += 1
            key = raw_key if counts[raw_key] == 1 else f"{raw_key}#{counts[raw_key]}"
            if isinstance(node, ast.ClassDef):
                kind: DocumentKind = "class"
            elif _is_test_path(entry.path) and node.name.startswith("test_"):
                kind = "test"
            else:
                kind = "method" if in_class else "function"
            local_key = key.removeprefix(f"{module}.")
            symbol_key = f"{entry.path}::{local_key}" if kind == "test" else key
            start = node.lineno
            end = node.end_lineno or node.lineno
            content = "".join(lines[start - 1 : end])
            name_terms = _terms(f"{entry.path} {module} {symbol_key} {node.name}")
            lexical = tuple(term for line, term in source_terms if start <= line <= end)
            terms = (*name_terms, *name_terms, *name_terms, *lexical)
            content_bytes = len(content.encode("utf-8"))
            documents.append(
                IndexedDocument(
                    key=symbol_key,
                    kind=kind,
                    path=entry.path,
                    symbol_key=symbol_key,
                    start_line=start,
                    end_line=end,
                    blob_object_id=entry.object_id,
                    content=content,
                    terms=terms,
                    content_bytes=content_bytes,
                    estimated_tokens=(content_bytes + 3) // 4,
                )
            )
            walk(node.body, key, isinstance(node, ast.ClassDef))

    walk(tree.body, module, False)
    return tuple(documents)


def _diagnose(
    diagnostics: list[RetrievalDiagnostic],
    limits: RetrievalLimits,
    code: str,
    path: str | None,
    detail: str,
) -> None:
    if len(diagnostics) < limits.max_diagnostics:
        diagnostics.append(RetrievalDiagnostic("index", code, path, detail[:500]))


def build_lexical_index(
    *,
    repository_path: str | Path,
    repository_id: str,
    candidate_commit_sha: str,
    limits: RetrievalLimits | None = None,
) -> LexicalRepositoryIndex:
    """Index regular Python blobs at an exact commit without reading the worktree."""

    active = limits or RetrievalLimits()
    root = _discover_repository_root(_validate_path(repository_path))
    if (
        _OBJECT_ID_PATTERN.fullmatch(candidate_commit_sha) is None
        or _resolve_commit(root, candidate_commit_sha, "candidate") != candidate_commit_sha
    ):
        raise InvalidCommitError(root, candidate_commit_sha, "candidate")
    if (
        not repository_id
        or len(repository_id) > 256
        or any(ord(char) < 32 for char in repository_id)
    ):
        raise ValueError("repository_id is invalid")

    entries = tuple(
        item for item in _tree_entries(root, candidate_commit_sha) if item.path.endswith(".py")
    )
    eligible_count = len(entries)
    diagnostics: list[RetrievalDiagnostic] = []
    if len(entries) > active.max_files:
        skipped = len(entries) - active.max_files
        _diagnose(diagnostics, active, "file_limit", None, f"{skipped} Python files skipped")
        entries = entries[: active.max_files]

    documents: list[IndexedDocument] = []
    indexed_files = 0
    indexed_bytes = 0
    ast_nodes_processed = 0
    lexical_tokens_processed = 0
    for entry in entries:
        if entry.mode not in {"100644", "100755"} or entry.kind != "blob":
            _diagnose(diagnostics, active, "unsupported_object_type", entry.path, entry.mode)
            continue
        try:
            size = _blob_size(root, entry)
            if size > active.max_blob_bytes:
                raise _Incomplete("blob_size_limit", str(size))
            if indexed_bytes + size > active.max_total_bytes:
                raise _Incomplete("total_size_limit", str(size))
            content = _blob_bytes(root, entry, size)
            indexed_bytes += size
            source = _decode_source(content)
            try:
                tree = ast.parse(source, filename=entry.path)
            except (SyntaxError, ValueError, RecursionError) as error:
                raise _Incomplete("source_parse_error", type(error).__name__) from error
            ast_count = sum(1 for _ in ast.walk(tree))
            if ast_nodes_processed + ast_count > active.max_ast_nodes:
                raise _Incomplete("ast_node_limit", str(ast_count))
            remaining_tokens = active.max_index_tokens - lexical_tokens_processed
            tagged_source_terms = _source_terms(source, max_terms=remaining_tokens)
            tagged_docstring_terms = _docstring_terms(
                tree,
                max_terms=remaining_tokens - len(tagged_source_terms),
            )
            tagged_terms = (*tagged_source_terms, *tagged_docstring_terms)
            source_terms = tuple(term for _, term in tagged_terms)

            path_terms = _terms(f"{entry.path} {canonical_python_module(entry.path)}")
            content_bytes = len(source.encode("utf-8"))
            file_document = IndexedDocument(
                key=f"file:{entry.path}",
                kind="file",
                path=entry.path,
                symbol_key=None,
                start_line=1,
                end_line=max(1, len(source.splitlines())),
                blob_object_id=entry.object_id,
                content=source,
                terms=(*path_terms, *path_terms, *source_terms),
                content_bytes=content_bytes,
                estimated_tokens=(content_bytes + 3) // 4,
            )
            symbol_documents = _definition_documents(tree, source, entry, tagged_terms)
        except _Incomplete as error:
            _diagnose(diagnostics, active, error.code, entry.path, error.detail)
            continue
        except GitExecutionError as error:
            _diagnose(diagnostics, active, "git_blob_error", entry.path, error.operation)
            continue

        documents.append(file_document)
        documents.extend(symbol_documents)
        indexed_files += 1
        ast_nodes_processed += ast_count
        lexical_tokens_processed += len(source_terms)

    completeness = indexed_files / eligible_count if eligible_count else 1.0
    return LexicalRepositoryIndex(
        repository_id=repository_id,
        commit_sha=candidate_commit_sha,
        documents=tuple(sorted(documents, key=lambda item: item.key)),
        diagnostics=tuple(sorted(set(diagnostics))),
        completeness=completeness,
        limits=active,
        eligible_file_count=eligible_count,
        indexed_file_count=indexed_files,
        indexed_bytes=indexed_bytes,
        ast_nodes_processed=ast_nodes_processed,
        lexical_tokens_processed=lexical_tokens_processed,
    )


def _excluded(index: LexicalRepositoryIndex, query: RetrievalQuery) -> tuple[set[str], set[str]]:
    keys = set(query.graph_required_keys) | set(query.changed_symbols) | set(query.excluded_keys)
    paths = {key.partition("::")[0] for key in keys if "::" in key}
    for document in index.documents:
        if document.symbol_key in keys:
            paths.add(document.path)
    return keys, paths


def _bm25_scores(
    documents: Sequence[IndexedDocument], query_terms: Sequence[str]
) -> list[tuple[float, IndexedDocument, tuple[str, ...]]]:
    if not documents or not query_terms:
        return []
    frequencies = [Counter(document.terms) for document in documents]
    lengths = [max(1, sum(values.values())) for values in frequencies]
    average_length = sum(lengths) / len(lengths)
    document_frequencies = {
        term: sum(term in values for values in frequencies) for term in query_terms
    }
    ranked: list[tuple[float, IndexedDocument, tuple[str, ...]]] = []
    for document, term_frequency, length in zip(documents, frequencies, lengths, strict=True):
        matched = tuple(sorted(term for term in query_terms if term_frequency.get(term, 0)))
        if not matched:
            continue
        score = 0.0
        for term in matched:
            frequency = term_frequency[term]
            document_frequency = document_frequencies[term]
            inverse = math.log(
                1 + (len(documents) - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * length / average_length)
            score += inverse * (frequency * 2.5) / denominator
        ranked.append((round(score, 8), document, matched))
    return sorted(ranked, key=lambda item: (-item[0], item[1].key))


def _retrieve(index: LexicalRepositoryIndex, query: RetrievalQuery) -> RetrievalResponse:
    query_terms = query.terms()
    excluded_keys, excluded_paths = _excluded(index, query)
    candidates = [
        document
        for document in index.documents
        if document.key not in excluded_keys
        and document.symbol_key not in excluded_keys
        and not (document.kind == "file" and document.path in excluded_paths)
    ]
    ranked = _bm25_scores(candidates, query_terms)
    results: list[RankedRetrievalResult] = []
    diagnostics: list[RetrievalDiagnostic] = []
    used_bytes = 0
    used_tokens = 0
    for score, document, matched in ranked:
        if len(results) >= index.limits.max_results:
            if len(diagnostics) < index.limits.max_diagnostics:
                diagnostics.append(
                    RetrievalDiagnostic(
                        "retrieve",
                        "result_count_limit",
                        detail=f"{len(ranked) - len(results)} ranked results omitted",
                    )
                )
            break
        exceeded: list[str] = []
        if used_bytes + document.content_bytes > index.limits.max_result_bytes:
            exceeded.append("result_byte_limit")
        if used_tokens + document.estimated_tokens > index.limits.max_result_tokens:
            exceeded.append("result_token_limit")
        if exceeded:
            for code in exceeded:
                if len(diagnostics) < index.limits.max_diagnostics:
                    diagnostics.append(
                        RetrievalDiagnostic("retrieve", code, document.path, document.key)
                    )
            continue
        reason = (
            f"Local lexical BM25 matched query terms {', '.join(matched)} in "
            f"{document.kind} {document.key} from immutable candidate blob "
            f"{document.blob_object_id}."
        )
        results.append(
            RankedRetrievalResult(
                key=document.key,
                kind=document.kind,
                path=document.path,
                symbol_key=document.symbol_key,
                start_line=document.start_line,
                end_line=document.end_line,
                score=score,
                matched_terms=matched,
                selection_reason=reason,
                content=document.content,
                content_bytes=document.content_bytes,
                estimated_tokens=document.estimated_tokens,
            )
        )
        used_bytes += document.content_bytes
        used_tokens += document.estimated_tokens

    retrieval_ratio = len(results) / len(ranked) if ranked and diagnostics else 1.0
    completeness = index.completeness * retrieval_ratio
    combined = tuple(sorted((*index.diagnostics, *diagnostics)))
    return RetrievalResponse(
        backend="local-lexical-v1",
        repository_id=index.repository_id,
        commit_sha=index.commit_sha,
        query_terms=query_terms,
        results=tuple(results),
        diagnostics=combined,
        completeness=completeness,
        limits=index.limits,
        used_result_bytes=used_bytes,
        used_result_tokens=used_tokens,
    )


__all__ = [
    "IndexedDocument",
    "LexicalRepositoryIndex",
    "LocalLexicalRetriever",
    "RankedRetrievalResult",
    "RepositoryRetriever",
    "RetrievalDiagnostic",
    "RetrievalLimits",
    "RetrievalQuery",
    "RetrievalResponse",
    "build_lexical_index",
]
