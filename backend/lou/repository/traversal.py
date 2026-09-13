"""Deterministic impact traversal over an immutable repository graph."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, cast

from lou.repository.graph import EdgeType, NodeType, RepositoryGraphSnapshot


@dataclass(frozen=True)
class TraversalLimits:
    """Bounds for one impact query, independent of graph extraction limits."""

    max_depth: int = 3
    max_nodes: int = 64

    def __post_init__(self) -> None:
        if self.max_depth < 0:
            raise ValueError("traversal max_depth must not be negative")
        if self.max_nodes <= 0:
            raise ValueError("traversal max_nodes must be positive")


@dataclass(frozen=True)
class ImpactNode:
    """One graph node reached through the shortest allowed impact path."""

    node_id: str
    node_type: NodeType
    key: str
    path: str | None
    distance: int
    edge_path: tuple[EdgeType, ...]
    confidence: float


@dataclass(frozen=True)
class ImpactTraversal:
    """Stable, explainable output of an impact traversal."""

    nodes: tuple[ImpactNode, ...]
    unresolved_relationships: tuple[str, ...]
    completeness: float

    def by_type(self, node_type: NodeType) -> tuple[ImpactNode, ...]:
        """Return reached nodes of one type in stable traversal order."""

        return tuple(node for node in self.nodes if node.node_type == node_type)

    def payload(self) -> dict[str, object]:
        """Return a JSON-safe, deterministically ordered explanation of the traversal."""

        return {
            "completeness": self.completeness,
            "nodes": [
                {
                    "node_id": node.node_id,
                    "node_type": node.node_type,
                    "key": node.key,
                    "path": node.path,
                    "distance": node.distance,
                    "edge_path": list(node.edge_path),
                    "confidence": node.confidence,
                }
                for node in self.nodes
            ],
            "unresolved_relationships": list(self.unresolved_relationships),
        }


def traverse_repository_impact(
    snapshot: RepositoryGraphSnapshot, *, limits: TraversalLimits | None = None
) -> ImpactTraversal:
    """Find tests, endpoints, tables, and load scenarios affected by changed nodes.

    This is deliberately an impact query rather than a generic graph walk. It follows
    only relationships that can establish a verification target, preventing imports and
    definitions from broadening a run without evidence.
    """

    active_limits = limits or TraversalLimits()
    graph = snapshot.graph
    starts = sorted(node_id for node_id, data in graph.nodes(data=True) if data.get("changed"))
    unresolved = [
        _diagnostic_text(item.code, item.path, item.detail) for item in snapshot.diagnostics
    ]
    if not starts:
        unresolved.append("no_changed_graph_nodes")
        return ImpactTraversal((), tuple(sorted(set(unresolved))), snapshot.completeness)

    reached: dict[str, ImpactNode] = {}
    pending: deque[ImpactNode] = deque()
    truncated = False
    for node_id in starts:
        if len(reached) >= active_limits.max_nodes:
            truncated = True
            break
        node = _impact_node(graph, node_id, distance=0, edge_path=(), confidence=None)
        reached[node_id] = node
        pending.append(node)

    while pending:
        current = pending.popleft()
        if current.distance >= active_limits.max_depth:
            continue
        for next_id, edge_type, edge_confidence in _next_steps(graph, current.node_id):
            if next_id in reached:
                continue
            if len(reached) >= active_limits.max_nodes:
                truncated = True
                continue
            next_node = _impact_node(
                graph,
                next_id,
                distance=current.distance + 1,
                edge_path=(*current.edge_path, edge_type),
                confidence=min(current.confidence, edge_confidence),
            )
            reached[next_id] = next_node
            pending.append(next_node)

    if truncated:
        unresolved.append("traversal_node_limit")
    ordered = tuple(sorted(reached.values(), key=lambda item: (item.distance, item.node_id)))
    return ImpactTraversal(ordered, tuple(sorted(set(unresolved))), snapshot.completeness)


def _next_steps(graph: object, node_id: str) -> tuple[tuple[str, EdgeType, float], ...]:
    """Return the small, semantically allowed impact neighborhood of ``node_id``."""

    # NetworkX's dynamic graph types are intentionally contained here; all public traversal
    # output is typed immutable data.
    graph_data = cast(Any, graph)
    node_type = graph_data.nodes[node_id]["node_type"]
    steps: list[tuple[str, EdgeType, float]] = []
    if node_type in {"FUNCTION", "CLASS"}:
        for _, target, edge_type, data in graph_data.out_edges(node_id, keys=True, data=True):
            if edge_type in {
                "CALLS",
                "TESTED_BY",
                "READS_FROM",
                "WRITES_TO",
                "SERVES_ENDPOINT",
            }:
                steps.append((target, edge_type, float(data["confidence"])))
        for source, _, edge_type, data in graph_data.in_edges(node_id, keys=True, data=True):
            if edge_type == "CALLS":
                steps.append((source, edge_type, float(data["confidence"])))
    elif node_type == "ENDPOINT":
        for _, target, edge_type, data in graph_data.out_edges(node_id, keys=True, data=True):
            if edge_type == "VALIDATED_BY":
                steps.append((target, edge_type, float(data["confidence"])))
    return tuple(sorted(steps, key=lambda item: (item[0], item[1])))


def _impact_node(
    graph: object,
    node_id: str,
    *,
    distance: int,
    edge_path: tuple[EdgeType, ...],
    confidence: float | None,
) -> ImpactNode:
    graph_data = cast(Any, graph)
    data = graph_data.nodes[node_id]
    return ImpactNode(
        node_id=node_id,
        node_type=data["node_type"],
        key=data["key"],
        path=data.get("path"),
        distance=distance,
        edge_path=edge_path,
        confidence=float(data["confidence"]) if confidence is None else confidence,
    )


def _diagnostic_text(code: str, path: str | None, detail: str | None) -> str:
    return ": ".join(item for item in (code, path, detail) if item)
