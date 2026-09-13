"""Deterministic, budgeted impact traversal over an RI-003 repository graph."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Literal, cast

from contracts import RepositoryContext
from lou.repository.graph import EdgeType, NodeType, RepositoryGraphSnapshot

_OUTGOING_EDGES: frozenset[EdgeType] = frozenset(
    {
        "CALLS",
        "TESTED_BY",
        "SERVES_ENDPOINT",
        "READS_FROM",
        "WRITES_TO",
        "VALIDATED_BY",
    }
)
_TERMINAL_NODE_TYPES: frozenset[NodeType] = frozenset({"TEST", "DATABASE_TABLE", "LOAD_SCENARIO"})
_EDGE_ORDER: dict[EdgeType, int] = {
    "TESTED_BY": 0,
    "CALLS": 1,
    "SERVES_ENDPOINT": 2,
    "READS_FROM": 3,
    "WRITES_TO": 4,
    "VALIDATED_BY": 5,
    "DEFINES": 6,
    "IMPORTS": 7,
}


@dataclass(frozen=True)
class TraversalLimits:
    """Budgets for impact traversal, excluding the changed-symbol roots."""

    max_depth: int = 3
    max_nodes: int = 100

    def __post_init__(self) -> None:
        if self.max_depth < 0:
            raise ValueError("max_depth must be non-negative")
        if self.max_nodes < 0:
            raise ValueError("max_nodes must be non-negative")


@dataclass(frozen=True)
class _Step:
    source: str
    target: str
    edge_type: EdgeType
    direction: Literal["forward", "reverse"]

    def render(self) -> str:
        if self.direction == "forward":
            return f"{self.source} --{self.edge_type}--> {self.target}"
        return f"{self.source} <--{self.edge_type}-- {self.target}"


@dataclass(frozen=True)
class _Transition:
    target: str
    step: _Step


def _node_type(snapshot: RepositoryGraphSnapshot, node_id: str) -> NodeType:
    return cast(NodeType, snapshot.graph.nodes[node_id]["node_type"])


def _node_key(snapshot: RepositoryGraphSnapshot, node_id: str) -> str:
    return cast(str, snapshot.graph.nodes[node_id]["key"])


def _is_test_internal(snapshot: RepositoryGraphSnapshot, node_id: str) -> bool:
    """Identify helper symbols defined in test files but not modeled as TEST nodes."""

    if _node_type(snapshot, node_id) == "TEST":
        return False
    raw_path = snapshot.graph.nodes[node_id].get("path")
    if not isinstance(raw_path, str):
        return False
    path = PurePosixPath(raw_path)
    return "tests" in path.parts or path.name.startswith("test_")


def _transitions(snapshot: RepositoryGraphSnapshot, node_id: str) -> list[_Transition]:
    """Return supported edges in stable preference order.

    Tests are impact results, not traversal intermediates. Reverse CALLS edges from
    tests are also excluded because TESTED_BY carries that relationship explicitly.
    """

    if _node_type(snapshot, node_id) in _TERMINAL_NODE_TYPES:
        return []

    transitions: list[_Transition] = []
    for _, target, raw_edge_type in snapshot.graph.out_edges(node_id, keys=True):
        edge_type = cast(EdgeType, raw_edge_type)
        if edge_type not in _OUTGOING_EDGES or _is_test_internal(snapshot, target):
            continue
        transitions.append(_Transition(target, _Step(node_id, target, edge_type, "forward")))

    for source, _, raw_edge_type in snapshot.graph.in_edges(node_id, keys=True):
        edge_type = cast(EdgeType, raw_edge_type)
        if (
            edge_type != "CALLS"
            or _node_type(snapshot, source) == "TEST"
            or _is_test_internal(snapshot, source)
        ):
            continue
        transitions.append(_Transition(source, _Step(node_id, source, edge_type, "reverse")))

    return sorted(
        transitions,
        key=lambda item: (
            _EDGE_ORDER[item.step.edge_type],
            item.target,
            item.step.direction,
        ),
    )


def _root_nodes(
    snapshot: RepositoryGraphSnapshot, changed_symbols: list[str]
) -> tuple[dict[str, str], list[str]]:
    candidates: dict[str, list[str]] = {}
    changed_set = set(changed_symbols)
    for node_id, attributes in snapshot.graph.nodes(data=True):
        node_type = cast(NodeType, attributes["node_type"])
        key = cast(str, attributes["key"])
        if node_type in {"FUNCTION", "CLASS"} and key in changed_set:
            candidates.setdefault(key, []).append(node_id)

    roots: dict[str, str] = {}
    missing: list[str] = []
    for key in changed_symbols:
        matches = sorted(candidates.get(key, ()))
        if matches:
            roots[key] = matches[0]
        else:
            missing.append(key)
    return roots, missing


def _diagnostic_message(code: str, path: str | None, detail: str | None) -> str:
    location = f" ({path})" if path else ""
    explanation = f": {detail}" if detail else ""
    return f"graph extraction incomplete: {code}{location}{explanation}"


def _path_reason(root: str, steps: tuple[_Step, ...]) -> str:
    rendered = " ; ".join(step.render() for step in steps)
    return f"Reached from changed symbol {root} in {len(steps)} hop(s): {rendered}"


def _classify(
    snapshot: RepositoryGraphSnapshot,
    node_ids: list[str],
) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    symbols: list[str] = []
    tests: list[str] = []
    endpoints: list[str] = []
    data_dependencies: list[str] = []
    workloads: list[str] = []
    for node_id in node_ids:
        key = _node_key(snapshot, node_id)
        node_type = _node_type(snapshot, node_id)
        if node_type in {"FUNCTION", "CLASS"}:
            symbols.append(key)
        elif node_type == "TEST":
            tests.append(key)
        elif node_type == "ENDPOINT":
            endpoints.append(key)
        elif node_type == "DATABASE_TABLE":
            data_dependencies.append(key)
        elif node_type == "LOAD_SCENARIO":
            workloads.append(key)
    return symbols, tests, endpoints, data_dependencies, workloads


def traverse_repository_graph(
    snapshot: RepositoryGraphSnapshot,
    *,
    changed_symbols: Iterable[str] | None = None,
    limits: TraversalLimits | None = None,
) -> RepositoryContext:
    """Build a RepositoryContext by walking callers, callees, and impact nodes.

    Changed-symbol roots do not consume ``max_nodes``. Paths are breadth-first, so
    the recorded reason is a shortest path; stable edge ordering breaks ties.
    """

    active_limits = limits or TraversalLimits()
    if changed_symbols is None:
        requested = sorted(
            {
                _node_key(snapshot, node_id)
                for node_id, attributes in snapshot.graph.nodes(data=True)
                if bool(attributes.get("changed"))
                and cast(NodeType, attributes["node_type"]) in {"FUNCTION", "CLASS"}
            }
        )
    else:
        requested = sorted(set(changed_symbols))

    roots, missing_roots = _root_nodes(snapshot, requested)
    reasons = {key: "Changed symbol; traversal root." for key in roots}
    for key in missing_roots:
        reasons[key] = "Changed symbol requested but absent from the candidate graph."

    unresolved = [
        _diagnostic_message(item.code, item.path, item.detail) for item in snapshot.diagnostics
    ]
    unresolved.extend(
        f"changed symbol missing from candidate graph: {key}" for key in missing_roots
    )

    queue: deque[tuple[str, int, str, tuple[_Step, ...]]] = deque(
        (node_id, 0, key, ()) for key, node_id in roots.items()
    )
    visited = set(roots.values())
    selected: list[str] = []
    selected_paths: dict[str, tuple[str, tuple[_Step, ...]]] = {}
    budget_stops: list[str] = []
    omitted_nodes: set[str] = set()
    node_budget_exhausted = False

    while queue and not node_budget_exhausted:
        node_id, depth, root, path = queue.popleft()
        transitions = [
            item for item in _transitions(snapshot, node_id) if item.target not in visited
        ]
        if depth >= active_limits.max_depth:
            for transition in transitions:
                omitted_nodes.add(transition.target)
                message = (
                    f"depth budget exhausted (max_depth={active_limits.max_depth}): "
                    f"not traversed: {transition.step.render()}"
                )
                unresolved.append(message)
                budget_stops.append(message)
            continue

        for transition in transitions:
            if transition.target in visited:
                continue
            next_path = (*path, transition.step)
            if len(selected) >= active_limits.max_nodes:
                omitted_nodes.add(transition.target)
                message = (
                    f"node budget exhausted (max_nodes={active_limits.max_nodes}): "
                    f"not selected via {_path_reason(root, next_path)}"
                )
                unresolved.append(message)
                budget_stops.append(message)
                node_budget_exhausted = True
                break
            visited.add(transition.target)
            selected.append(transition.target)
            selected_paths[transition.target] = (root, next_path)
            queue.append((transition.target, depth + 1, root, next_path))

    for node_id in selected:
        root, path = selected_paths[node_id]
        reasons[_node_key(snapshot, node_id)] = _path_reason(root, path)

    affected_symbols, affected_tests, affected_endpoints, dependencies, workloads = _classify(
        snapshot, selected
    )

    root_factor = len(roots) / len(requested) if requested else 1.0
    traversal_factor = (
        len(selected) / (len(selected) + len(omitted_nodes)) if omitted_nodes else 1.0
    )
    completeness = max(
        0.0,
        min(1.0, snapshot.completeness * root_factor * traversal_factor),
    )

    return RepositoryContext(
        repository_id=snapshot.repository_id,
        commit_sha=snapshot.commit_sha,
        changed_symbols=requested,
        affected_symbols=affected_symbols,
        affected_tests=affected_tests,
        affected_endpoints=affected_endpoints,
        affected_data_dependencies=dependencies,
        selected_workload_ids=workloads,
        selection_reasons=reasons,
        unresolved_relationships=sorted(set(unresolved)),
        completeness=completeness,
        metadata={
            "source": "lou.repository.traversal",
            "limits": asdict(active_limits),
            "selected_node_count": len(selected),
            "budget_stops": sorted(set(budget_stops)),
            "test_nodes_are_terminal": True,
        },
    )
