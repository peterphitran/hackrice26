"""Tests for bounded, deterministic graph impact traversal."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from lou.repository import (
    GraphDiagnostic,
    RepositoryGraphSnapshot,
    TraversalLimits,
    traverse_repository_impact,
)


def _snapshot(*, diagnostics: tuple[GraphDiagnostic, ...] = ()) -> RepositoryGraphSnapshot:
    graph = nx.MultiDiGraph()
    _node(graph, "function:store.app.Store.checkout", "FUNCTION", "store.app.Store.checkout", True)
    _node(graph, "function:store.app.checkout", "FUNCTION", "store.app.checkout")
    _node(
        graph,
        "test:tests/test_checkout.py::test_receipt",
        "TEST",
        "tests/test_checkout.py::test_receipt",
        path="tests/test_checkout.py",
    )
    _node(graph, "endpoint:POST /checkout", "ENDPOINT", "POST /checkout")
    _node(graph, "table:broken_store.products", "DATABASE_TABLE", "broken_store.products")
    _node(
        graph,
        "load_scenario:loadtests/checkout.js",
        "LOAD_SCENARIO",
        "loadtests/checkout.js",
        path="loadtests/checkout.js",
    )
    _edge(
        graph,
        "function:store.app.Store.checkout",
        "test:tests/test_checkout.py::test_receipt",
        "TESTED_BY",
    )
    _edge(graph, "function:store.app.checkout", "function:store.app.Store.checkout", "CALLS")
    _edge(graph, "function:store.app.checkout", "endpoint:POST /checkout", "SERVES_ENDPOINT")
    _edge(graph, "endpoint:POST /checkout", "load_scenario:loadtests/checkout.js", "VALIDATED_BY")
    _edge(graph, "function:store.app.Store.checkout", "table:broken_store.products", "READS_FROM")
    return RepositoryGraphSnapshot(
        graph=graph,
        repository_id="repo-store",
        commit_sha="a" * 40,
        completeness=1.0,
        diagnostics=diagnostics,
        artifact_path=Path("graph.json"),
        artifact_uri="run/candidate/repository-graph.json",
        artifact_sha256="b" * 64,
    )


def _node(
    graph: nx.MultiDiGraph,
    node_id: str,
    node_type: str,
    key: str,
    changed: bool = False,
    path: str | None = None,
) -> None:
    graph.add_node(
        node_id,
        node_type=node_type,
        key=key,
        path=path,
        confidence=0.9,
        changed=changed,
    )


def _edge(graph: nx.MultiDiGraph, source: str, target: str, edge_type: str) -> None:
    graph.add_edge(source, target, key=edge_type, edge_type=edge_type, confidence=0.8)


def test_traversal_reaches_fixture_verification_targets() -> None:
    traversal = traverse_repository_impact(_snapshot())

    assert [item.key for item in traversal.by_type("TEST")] == [
        "tests/test_checkout.py::test_receipt"
    ]
    assert [item.key for item in traversal.by_type("ENDPOINT")] == ["POST /checkout"]
    assert [item.key for item in traversal.by_type("DATABASE_TABLE")] == ["broken_store.products"]
    assert [item.key for item in traversal.by_type("LOAD_SCENARIO")] == [
        "loadtests/checkout.js"
    ]
    load = traversal.by_type("LOAD_SCENARIO")[0]
    assert load.distance == 3
    assert load.edge_path == ("CALLS", "SERVES_ENDPOINT", "VALIDATED_BY")
    assert load.confidence == 0.8


def test_traversal_returns_direct_callees_as_well_as_callers() -> None:
    snapshot = _snapshot()
    _node(snapshot.graph, "function:store.app.audit", "FUNCTION", "store.app.audit")
    _edge(
        snapshot.graph,
        "function:store.app.Store.checkout",
        "function:store.app.audit",
        "CALLS",
    )

    traversal = traverse_repository_impact(snapshot, limits=TraversalLimits(max_depth=1))

    functions = {item.key: item.distance for item in traversal.by_type("FUNCTION")}
    assert functions["store.app.checkout"] == 1
    assert functions["store.app.audit"] == 1


def test_traversal_respects_depth_and_node_limits() -> None:
    depth_limited = traverse_repository_impact(_snapshot(), limits=TraversalLimits(max_depth=1))
    node_limited = traverse_repository_impact(_snapshot(), limits=TraversalLimits(max_nodes=2))

    assert max(item.distance for item in depth_limited.nodes) == 1
    assert len(node_limited.nodes) == 2
    assert "traversal_node_limit" in node_limited.unresolved_relationships


def test_traversal_is_deterministic_and_preserves_extraction_diagnostics() -> None:
    snapshot = _snapshot(
        diagnostics=(GraphDiagnostic("unresolved_call", "store/app.py", "dynamic target"),)
    )

    first = traverse_repository_impact(snapshot)
    second = traverse_repository_impact(snapshot)

    assert first.payload() == second.payload()
    assert first.unresolved_relationships == ("unresolved_call: store/app.py: dynamic target",)


def test_traversal_reports_missing_changed_nodes() -> None:
    snapshot = _snapshot()
    snapshot.graph.nodes["function:store.app.Store.checkout"]["changed"] = False

    traversal = traverse_repository_impact(snapshot)

    assert traversal.nodes == ()
    assert traversal.unresolved_relationships == ("no_changed_graph_nodes",)
