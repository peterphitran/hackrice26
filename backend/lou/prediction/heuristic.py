"""Small, reproducible impact heuristic built on the existing repository graph."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from contracts import (
    ImpactItem,
    ImpactPrediction,
    PredictionFeatures,
    RepositoryChange,
    WorkloadSelection,
)
from lou.repository.graph import RepositoryGraphSnapshot
from lou.repository.traversal import ImpactTraversal

REVISION = "impact-v1"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _history(root: Path, paths: set[str]) -> tuple[float | None, str | None]:
    if not paths:
        return None, "history:no_changed_paths"
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "log", "--format=%H", "-n", "20", "--", *sorted(paths)],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None, "history:unavailable"
    if result.returncode:
        return None, "history:unavailable"
    return _clamp(len(result.stdout.splitlines()) / 20), None


def _coverage(root: Path, keys: set[str]) -> tuple[float | None, str | None]:
    path = root / "coverage.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "coverage:unavailable"
    files = data.get("files", {}) if isinstance(data, dict) else {}
    values = []
    for key in keys:
        file_data = files.get(key, {}) if isinstance(files, dict) else {}
        summary = file_data.get("summary", {}) if isinstance(file_data, dict) else {}
        if isinstance(summary.get("percent_covered"), (int, float)):
            values.append(float(summary["percent_covered"]) / 100)
    return (sum(values) / len(values), None) if values else (None, "coverage:unavailable")


def _ownership(root: Path | None, paths: set[str]) -> tuple[float | None, str | None]:
    if root is None:
        return None, "ownership:omitted"
    for name in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"):
        try:
            lines = (root / name).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        patterns = [
            line.split()[0]
            for line in lines
            if line.strip() and not line.lstrip().startswith("#") and len(line.split()) > 1
        ]
        return (
            1.0
            if any(pattern.strip("*") in path for pattern in patterns for path in paths)
            else 0.0,
            None,
        )
    return None, "ownership:unavailable"


def predict_impact(
    *,
    analysis_run_id: str,
    change: RepositoryChange,
    snapshot: RepositoryGraphSnapshot,
    traversal: ImpactTraversal,
    workloads: tuple[WorkloadSelection, ...] = (),
    repository_root: Path | None = None,
) -> ImpactPrediction:
    graph = snapshot.graph
    changed_paths = set(change.added_files) | set(change.modified_files) | set(change.deleted_files)
    history, history_missing = (
        _history(repository_root, changed_paths) if repository_root else (None, "history:omitted")
    )
    coverage, coverage_missing = (
        _coverage(repository_root, changed_paths) if repository_root else (None, "coverage:omitted")
    )
    ownership, ownership_missing = _ownership(repository_root, changed_paths)
    omitted = [item for item in (history_missing, coverage_missing, ownership_missing) if item]
    if snapshot.completeness < 1:
        omitted.append("graph:incomplete")
    centrality = {}
    if graph.number_of_nodes():
        centrality = {
            key: float(value)
            for key, value in __import__("networkx").degree_centrality(graph).items()
        }
    items: list[ImpactItem] = []
    for node in traversal.nodes:
        node_data: dict[str, Any] = graph.nodes[node.node_id]
        score = _clamp(
            0.55 * node.confidence
            + 0.25 * centrality.get(node.node_id, 0)
            + 0.20 * (1 if node_data.get("changed") else 0)
        )
        kind: Literal["symbol", "service", "runtime_path"] = (
            "service"
            if node.node_type in {"ENDPOINT", "DATABASE_TABLE"}
            else "symbol"
            if node.node_type in {"FUNCTION", "CLASS", "TEST"}
            else "runtime_path"
        )
        items.append(
            ImpactItem(
                kind=kind,
                key=node.key,
                score=score,
                reason=f"reachable at distance {node.distance}",
                provenance=["graph-traversal", *node.edge_path],
            )
        )
    for workload in workloads:
        items.append(
            ImpactItem(
                kind="workload",
                key=workload.workload_id,
                score=1.0 if workload.confidence else 0.5,
                reason=workload.reason,
                provenance=["workload-registry"],
            )
        )
    features = PredictionFeatures(
        reachability=_clamp(len(traversal.nodes) / max(1, len(graph.nodes))),
        centrality=max(centrality.values(), default=None),
        changed_file_type=1.0
        if any(PurePosixPath(path).suffix == ".py" for path in changed_paths)
        else 0.0,
        history_churn=history,
        coverage_signal=coverage,
        ownership_signal=ownership,
    )
    known = sum(value is not None for value in features.model_dump().values())
    confidence = _clamp(snapshot.completeness * (0.5 + 0.5 * known / 6))
    return ImpactPrediction(
        analysis_run_id=analysis_run_id,
        repository_id=change.repository_id,
        base_commit_sha=change.base_commit_sha,
        candidate_commit_sha=change.candidate_commit_sha,
        items=tuple(
            sorted(
                {(item.kind, item.key): item for item in items}.values(),
                key=lambda item: (item.kind, item.key),
            )
        ),
        required_workload_ids=tuple(sorted({item.workload_id for item in workloads})),
        risk_signals={
            "graph_completeness": snapshot.completeness,
            "validation_required": float(snapshot.completeness < 1 or coverage is None),
        },
        features=features,
        confidence=confidence,
        omitted_context=tuple(sorted(set(omitted))),
        predictor_revision=REVISION,
    )
