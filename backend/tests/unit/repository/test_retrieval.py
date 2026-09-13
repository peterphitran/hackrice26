"""Tests for bounded lexical retrieval from immutable repository revisions."""

from __future__ import annotations

import socket
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest

from contracts import Finding
from lou.repository import (
    LocalLexicalRetriever,
    RetrievalLimits,
    RetrievalQuery,
    RetrievalResponse,
    build_lexical_index,
    build_repository_context,
    build_repository_graph,
    extract_changed_symbols,
    parse_repository_changes,
    traverse_repository_impact,
)
from lou.repository import retrieval as retrieval_module

FIXTURE_ROOT = Path(__file__).parents[3] / "fixtures" / "broken-store"


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write(repository: Path, relative: str, content: str) -> None:
    path = repository / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _commit(repository: Path, message: str = "snapshot") -> str:
    _git(repository, "add", "-A")
    _git(repository, "commit", "-qm", message)
    return _git(repository, "rev-parse", "HEAD")


def test_index_and_retrieval_are_deterministic_and_normalize_identifiers(
    git_repository: Path,
) -> None:
    _write(
        git_repository,
        "orders/related.py",
        'def checkout_items_cache():\n    """Cache checkout item lookups."""\n    return []\n',
    )
    commit = _commit(git_repository)
    limits = RetrievalLimits(max_result_bytes=20_000, max_result_tokens=5_000)

    first_index = build_lexical_index(
        repository_path=git_repository,
        repository_id="orders",
        candidate_commit_sha=commit,
        limits=limits,
    )
    second_index = build_lexical_index(
        repository_path=git_repository,
        repository_id="orders",
        candidate_commit_sha=commit,
        limits=limits,
    )
    snake = first_index.retrieve(RetrievalQuery(caller_query="checkout_items"))
    camel = first_index.retrieve(RetrievalQuery(caller_query="CheckoutItems"))
    kebab = second_index.retrieve(RetrievalQuery(caller_query="checkout-items"))

    assert first_index == second_index
    assert snake.results == camel.results == kebab.results
    ordering = [(-item.score, item.key) for item in snake.results]
    assert ordering == sorted(ordering)
    assert snake.results[0].symbol_key == "orders.related.checkout_items_cache"
    assert snake.results[0].score > 0
    assert snake.results[0].matched_terms
    assert snake.results[0].selection_reason
    assert len({item.key for item in snake.results}) == len(snake.results)


def test_broken_store_finding_retrieves_related_non_graph_symbol(tmp_path: Path) -> None:
    repository = tmp_path / "broken-store"
    subprocess.run(
        [sys.executable, str(FIXTURE_ROOT / "scripts" / "seed_fixture_repo.py"), str(repository)],
        check=True,
    )
    change = parse_repository_changes(
        repository_id="broken-store",
        repository_path=repository,
        base_revision="good",
        candidate_revision="n-plus-one",
    )
    change = extract_changed_symbols(repository_path=repository, change=change)
    snapshot = build_repository_graph(
        repository_path=repository,
        change=change,
        analysis_run_id="ri-007-broken-store",
        artifact_root=tmp_path / "artifacts",
    )
    context = build_repository_context(
        traverse_repository_impact(snapshot),
        repository_id=change.repository_id,
        commit_sha=change.candidate_commit_sha,
    )
    finding = Finding(
        finding_id="checkout-query-regression",
        analysis_run_id="ri-007",
        fingerprint="checkout-query-regression",
        source="test",
        category="database-query-regression",
        severity="high",
        confidence=1,
        phase="candidate",
        title="Checkout product query regression",
        message="The candidate performs one product query per checkout item.",
        file_path="store/app.py",
        symbol_key="store.app.Store.checkout",
    )
    response = LocalLexicalRetriever().retrieve(
        repository_path=repository,
        repository_id=context.repository_id,
        candidate_commit_sha=context.commit_sha,
        query=RetrievalQuery.from_analysis(context, finding),
        limits=RetrievalLimits(max_result_bytes=30_000, max_result_tokens=8_000),
    )

    assert "store.app.seed_database" in {item.symbol_key for item in response.results}
    assert not set(context.changed_symbols).intersection(
        item.symbol_key for item in response.results if item.symbol_key
    )
    assert all(item.score > 0 and item.selection_reason for item in response.results)


def test_graph_required_symbols_and_files_are_excluded(git_repository: Path) -> None:
    _write(
        git_repository,
        "service.py",
        "def changed_checkout():\n    return 'checkout'\n\n"
        "def checkout_helper():\n    return 'checkout helper'\n",
    )
    commit = _commit(git_repository)
    index = build_lexical_index(
        repository_path=git_repository,
        repository_id="service",
        candidate_commit_sha=commit,
    )

    response = index.retrieve(
        RetrievalQuery(
            changed_symbols=("service.changed_checkout",),
            graph_required_keys=("service.changed_checkout",),
            caller_query="checkout",
        )
    )

    keys = {item.key for item in response.results}
    assert "service.changed_checkout" not in keys
    assert "file:service.py" not in keys
    assert "service.checkout_helper" in keys


def test_index_preserves_graph_symbol_keys_for_collisions_duplicates_and_tests(
    git_repository: Path,
) -> None:
    _write(git_repository, "a.b.py", "def same(): pass\ndef same(): pass\n")
    _write(git_repository, "a/b.py", "def same(): pass\n")
    _write(
        git_repository,
        "tests/test_nested.py",
        "class TestNested:\n    def test_checkout(self):\n        return 1\n",
    )
    commit = _commit(git_repository)

    index = build_lexical_index(
        repository_path=git_repository,
        repository_id="symbols",
        candidate_commit_sha=commit,
    )
    symbols = {item.symbol_key for item in index.documents if item.symbol_key}

    assert {
        "a%2Eb.same",
        "a%2Eb.same#2",
        "a.b.same",
        "tests.test_nested.TestNested",
        "tests/test_nested.py::TestNested.test_checkout",
    } <= symbols


def test_empty_complete_and_empty_incomplete_are_distinct(git_repository: Path) -> None:
    _write(git_repository, "good.py", "def unrelated():\n    return 1\n")
    complete_commit = _commit(git_repository, "valid")
    complete = build_lexical_index(
        repository_path=git_repository,
        repository_id="empty",
        candidate_commit_sha=complete_commit,
    ).retrieve(RetrievalQuery(caller_query="nonexistentconcept"))

    _write(git_repository, "broken.py", "def broken(:\n")
    incomplete_commit = _commit(git_repository, "broken")
    incomplete = build_lexical_index(
        repository_path=git_repository,
        repository_id="empty",
        candidate_commit_sha=incomplete_commit,
    ).retrieve(RetrievalQuery(caller_query="nonexistentconcept"))

    assert complete.results == () and complete.completeness == 1
    assert incomplete.results == () and incomplete.completeness < 1
    assert "source_parse_error" in {item.code for item in incomplete.diagnostics}


def test_dirty_worktree_is_never_read(git_repository: Path) -> None:
    _write(git_repository, "source.py", "def immutable_checkout():\n    return 1\n")
    commit = _commit(git_repository)
    _write(git_repository, "source.py", "def dirty_worktree_secret():\n    return 2\n")

    index = build_lexical_index(
        repository_path=git_repository,
        repository_id="immutable",
        candidate_commit_sha=commit,
    )

    assert "source.immutable_checkout" in {item.symbol_key for item in index.documents}
    assert "source.dirty_worktree_secret" not in {item.symbol_key for item in index.documents}
    assert not index.retrieve(RetrievalQuery(caller_query="dirty_worktree_secret")).results


def test_malformed_unsupported_and_file_node_limits_are_diagnostics(
    git_repository: Path, tmp_path: Path
) -> None:
    _write(git_repository, "a.py", "def first():\n    return 1\n")
    _write(git_repository, "broken.py", "def broken(:\n")
    outside = tmp_path / "outside.py"
    outside.write_text("def outside():\n    return 1\n")
    (git_repository / "linked.py").symlink_to(outside)
    commit = _commit(git_repository)

    unsupported = build_lexical_index(
        repository_path=git_repository,
        repository_id="bounded",
        candidate_commit_sha=commit,
    )
    file_limited = build_lexical_index(
        repository_path=git_repository,
        repository_id="bounded",
        candidate_commit_sha=commit,
        limits=RetrievalLimits(max_files=1),
    )
    node_limited = build_lexical_index(
        repository_path=git_repository,
        repository_id="bounded",
        candidate_commit_sha=commit,
        limits=RetrievalLimits(max_ast_nodes=1),
    )

    assert {item.code for item in unsupported.diagnostics} >= {
        "source_parse_error",
        "unsupported_object_type",
    }
    assert unsupported.completeness < 1
    assert "file_limit" in {item.code for item in file_limited.diagnostics}
    assert "ast_node_limit" in {item.code for item in node_limited.diagnostics}
    assert file_limited.completeness < 1 and node_limited.completeness < 1


def test_decoding_and_missing_blob_failures_lower_completeness(
    git_repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (git_repository / "encoded.py").write_bytes(b"# coding: missing-codec\ndef value(): pass\n")
    commit = _commit(git_repository)
    encoded = build_lexical_index(
        repository_path=git_repository,
        repository_id="failures",
        candidate_commit_sha=commit,
    )

    real_execute = getattr(retrieval_module, "_execute_git")

    def missing_blob(
        repository: Path,
        arguments: Sequence[str],
        *,
        operation: str,
    ) -> subprocess.CompletedProcess[bytes]:
        if arguments[:2] == ["cat-file", "-s"]:
            return subprocess.CompletedProcess(arguments, 128, b"", b"missing object")
        return cast(
            subprocess.CompletedProcess[bytes],
            real_execute(repository, arguments, operation=operation),
        )

    monkeypatch.setattr(retrieval_module, "_execute_git", missing_blob)
    missing = build_lexical_index(
        repository_path=git_repository,
        repository_id="failures",
        candidate_commit_sha=commit,
    )

    assert encoded.completeness == 0
    assert "source_encoding_error" in {item.code for item in encoded.diagnostics}
    assert missing.completeness == 0
    assert "missing_blob" in {item.code for item in missing.diagnostics}


@pytest.mark.parametrize(
    ("limits", "code"),
    [
        (RetrievalLimits(max_blob_bytes=2), "blob_size_limit"),
        (RetrievalLimits(max_total_bytes=2), "total_size_limit"),
        (RetrievalLimits(max_index_tokens=1), "index_token_limit"),
    ],
)
def test_index_byte_and_token_budgets(
    git_repository: Path, limits: RetrievalLimits, code: str
) -> None:
    _write(git_repository, "source.py", "def checkout_items():\n    return 1\n")
    commit = _commit(git_repository)

    index = build_lexical_index(
        repository_path=git_repository,
        repository_id="budgets",
        candidate_commit_sha=commit,
        limits=limits,
    )

    assert index.documents == ()
    assert index.completeness == 0
    assert code in {item.code for item in index.diagnostics}


def test_result_count_byte_and_token_budgets_are_explicit(git_repository: Path) -> None:
    _write(
        git_repository,
        "many.py",
        "def checkout_alpha():\n    return 1\n\n"
        "def checkout_beta():\n    return 2\n\n"
        "def checkout_gamma():\n    return 3\n",
    )
    commit = _commit(git_repository)

    count_response = build_lexical_index(
        repository_path=git_repository,
        repository_id="results",
        candidate_commit_sha=commit,
        limits=RetrievalLimits(max_results=1, max_result_bytes=20_000, max_result_tokens=5_000),
    ).retrieve(RetrievalQuery(caller_query="checkout"))
    byte_response = build_lexical_index(
        repository_path=git_repository,
        repository_id="results",
        candidate_commit_sha=commit,
        limits=RetrievalLimits(max_result_bytes=1),
    ).retrieve(RetrievalQuery(caller_query="checkout"))
    token_response = build_lexical_index(
        repository_path=git_repository,
        repository_id="results",
        candidate_commit_sha=commit,
        limits=RetrievalLimits(max_result_tokens=1),
    ).retrieve(RetrievalQuery(caller_query="checkout"))

    assert len(count_response.results) == 1
    assert "result_count_limit" in {item.code for item in count_response.diagnostics}
    assert count_response.completeness < 1
    assert byte_response.results == ()
    assert "result_byte_limit" in {item.code for item in byte_response.diagnostics}
    assert token_response.results == ()
    assert "result_token_limit" in {item.code for item in token_response.diagnostics}


def test_retrieval_uses_no_network_or_optional_indexer(
    git_repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(git_repository, "local.py", "def local_checkout():\n    return 1\n")
    commit = _commit(git_repository)

    def blocked_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("lexical retrieval attempted network access")

    monkeypatch.setattr(socket, "create_connection", blocked_network)
    response = LocalLexicalRetriever().retrieve(
        repository_path=git_repository,
        repository_id="local",
        candidate_commit_sha=commit,
        query=RetrievalQuery(caller_query="checkout"),
    )

    assert response.backend == "local-lexical-v1"
    assert response.results


def test_limits_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        RetrievalLimits(max_results=0)


def test_retrieval_backend_must_be_named() -> None:
    with pytest.raises(ValueError, match="backend"):
        RetrievalResponse(
            backend="",
            repository_id="repository",
            commit_sha="a" * 40,
            query_terms=(),
            results=(),
            diagnostics=(),
            completeness=1,
            limits=RetrievalLimits(),
            used_result_bytes=0,
            used_result_tokens=0,
        )
