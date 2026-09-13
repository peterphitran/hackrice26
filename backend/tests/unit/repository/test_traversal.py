from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import networkx as nx  # type: ignore[import-untyped]
import pytest

from lou.repository import (
    GraphDiagnostic,
    RepositoryGraphSnapshot,
    TraversalLimits,
    build_repository_graph,
    extract_changed_symbols,
    parse_repository_changes,
    traverse_repository_graph,
)

FIXTURE_ROOT = Path(__file__).parents[3] / "fixtures" / "broken-store"


@pytest.fixture
def broken_store_snapshot(tmp_path: Path) -> RepositoryGraphSnapshot:
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
    return build_repository_graph(
        repository_path=repository,
        change=change,
        analysis_run_id="run-ri-004",
        artifact_root=tmp_path / "artifacts",
    )


def _isolated_snapshot(
    tmp_path: Path,
    *,
    completeness: float = 1.0,
    diagnostics: tuple[GraphDiagnostic, ...] = (),
) -> RepositoryGraphSnapshot:
    graph = nx.MultiDiGraph()
    graph.add_node(
        "function:store.isolated",
        node_type="FUNCTION",
        key="store.isolated",
        changed=True,
    )
    artifact = tmp_path / "unused.json"
    return RepositoryGraphSnapshot(
        graph=graph,
        repository_id="isolated-repository",
        commit_sha="a" * 40,
        completeness=completeness,
        diagnostics=diagnostics,
        artifact_path=artifact,
        artifact_uri="unused.json",
        artifact_sha256="0" * 64,
    )


def test_broken_store_traversal_reaches_demo_critical_impact(
    broken_store_snapshot: RepositoryGraphSnapshot,
) -> None:
    context = traverse_repository_graph(
        broken_store_snapshot,
        changed_symbols=["store.app.Store.checkout"],
    )

    assert "tests/test_checkout.py::test_checkout_receipt_stays_correct" in (context.affected_tests)
    assert "POST /checkout" in context.affected_endpoints
    assert set(context.affected_data_dependencies) >= {
        "broken_store.cart_items",
        "broken_store.products",
    }
    assert "loadtests/checkout.js" in context.selected_workload_ids
    assert context.completeness == 1.0
    assert context.unresolved_relationships == []

    returned_nodes = {
        *context.changed_symbols,
        *context.affected_symbols,
        *context.affected_tests,
        *context.affected_endpoints,
        *context.affected_data_dependencies,
        *context.selected_workload_ids,
    }
    assert returned_nodes <= context.selection_reasons.keys()
    assert all(context.selection_reasons[node] for node in returned_nodes)
    assert "3 hop(s)" in context.selection_reasons["loadtests/checkout.js"]
    assert "VALIDATED_BY" in context.selection_reasons["loadtests/checkout.js"]


def test_test_nodes_are_results_not_traversal_intermediates(
    broken_store_snapshot: RepositoryGraphSnapshot,
) -> None:
    context = traverse_repository_graph(broken_store_snapshot)

    assert all(not symbol.startswith("tests.") for symbol in context.affected_symbols)
    assert context.metadata["test_nodes_are_terminal"] is True


def test_depth_budget_records_incomplete_traversal(
    broken_store_snapshot: RepositoryGraphSnapshot,
) -> None:
    context = traverse_repository_graph(
        broken_store_snapshot,
        limits=TraversalLimits(max_depth=2, max_nodes=100),
    )

    assert "POST /checkout" in context.affected_endpoints
    assert "loadtests/checkout.js" not in context.selected_workload_ids
    assert any("depth budget exhausted" in item for item in context.unresolved_relationships)
    assert context.metadata["budget_stops"]
    assert context.completeness < broken_store_snapshot.completeness


def test_node_budget_records_incomplete_traversal(
    broken_store_snapshot: RepositoryGraphSnapshot,
) -> None:
    context = traverse_repository_graph(
        broken_store_snapshot,
        limits=TraversalLimits(max_depth=3, max_nodes=1),
    )

    returned_impact_count = sum(
        len(items)
        for items in (
            context.affected_symbols,
            context.affected_tests,
            context.affected_endpoints,
            context.affected_data_dependencies,
            context.selected_workload_ids,
        )
    )
    assert returned_impact_count == 1
    assert any("node budget exhausted" in item for item in context.unresolved_relationships)
    assert context.metadata["budget_stops"]
    assert context.completeness < broken_store_snapshot.completeness


def test_empty_complete_result_differs_from_failed_extraction(tmp_path: Path) -> None:
    complete = traverse_repository_graph(_isolated_snapshot(tmp_path))
    incomplete = traverse_repository_graph(
        _isolated_snapshot(
            tmp_path,
            completeness=0.5,
            diagnostics=(GraphDiagnostic("unresolved_call", "store.py", "dynamic_target"),),
        )
    )

    assert complete.affected_symbols == []
    assert complete.affected_tests == []
    assert complete.affected_endpoints == []
    assert complete.affected_data_dependencies == []
    assert complete.selected_workload_ids == []
    assert complete.unresolved_relationships == []
    assert complete.completeness == 1.0
    assert incomplete.affected_symbols == []
    assert incomplete.unresolved_relationships
    assert incomplete.completeness == 0.5


def test_missing_changed_symbol_is_explicitly_incomplete(tmp_path: Path) -> None:
    context = traverse_repository_graph(
        _isolated_snapshot(tmp_path), changed_symbols=["store.deleted"]
    )

    assert context.changed_symbols == ["store.deleted"]
    assert context.affected_symbols == []
    assert context.selection_reasons["store.deleted"]
    assert context.unresolved_relationships == [
        "changed symbol missing from candidate graph: store.deleted"
    ]
    assert context.completeness == 0.0


def test_traversal_is_deterministic(broken_store_snapshot: RepositoryGraphSnapshot) -> None:
    first = traverse_repository_graph(broken_store_snapshot)
    second = traverse_repository_graph(broken_store_snapshot)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


@pytest.mark.parametrize(
    "limits",
    [
        TraversalLimits(max_depth=0),
        TraversalLimits(max_nodes=0),
    ],
)
def test_zero_budgets_are_valid_stops(
    broken_store_snapshot: RepositoryGraphSnapshot,
    limits: TraversalLimits,
) -> None:
    context = traverse_repository_graph(broken_store_snapshot, limits=limits)

    assert context.metadata["budget_stops"]
    assert context.completeness == 0.0


@pytest.mark.parametrize(
    "limits",
    [TraversalLimits(max_depth=0), TraversalLimits(max_nodes=0)],
)
def test_limits_are_reported_verbatim(
    broken_store_snapshot: RepositoryGraphSnapshot, limits: TraversalLimits
) -> None:
    context = traverse_repository_graph(broken_store_snapshot, limits=limits)

    assert context.metadata["limits"] == {
        "max_depth": limits.max_depth,
        "max_nodes": limits.max_nodes,
    }


@pytest.mark.parametrize(
    "kwargs",
    [{"max_depth": -1}, {"max_nodes": -1}],
)
def test_rejects_negative_budgets(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        TraversalLimits(**kwargs)
