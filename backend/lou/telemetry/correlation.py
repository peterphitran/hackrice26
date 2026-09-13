"""Deterministic runtime-to-repository correlation; no fuzzy or AI matching."""

from __future__ import annotations

import re
from typing import Any

from lou.repository.graph import RepositoryGraphSnapshot
from lou.telemetry.models import CorrelationResult


def correlate_runtime_name(
    runtime_name: str, snapshot: RepositoryGraphSnapshot
) -> CorrelationResult:
    """Resolve exact then normalized graph keys, preserving ambiguity and misses."""

    candidates = [
        (node_id, str(data.get("key", "")))
        for node_id, data in snapshot.graph.nodes(data=True)
        if data.get("node_type") in {"FUNCTION", "CLASS", "ENDPOINT", "DATABASE_TABLE"}
    ]
    exact = sorted((node_id, key) for node_id, key in candidates if key == runtime_name)
    if len(exact) == 1:
        return CorrelationResult("resolved", "exact", exact[0][1], exact[0][0], 1.0)
    if len(exact) > 1:
        return CorrelationResult(
            "ambiguous", "ambiguous", None, None, 0.0, tuple(key for _, key in exact)
        )
    normalized = _normalize(runtime_name)
    matches = sorted((node_id, key) for node_id, key in candidates if _normalize(key) == normalized)
    if len(matches) == 1:
        return CorrelationResult("resolved", "normalized", matches[0][1], matches[0][0], 0.8)
    if len(matches) > 1:
        return CorrelationResult(
            "ambiguous", "ambiguous", None, None, 0.0, tuple(key for _, key in matches)
        )
    return CorrelationResult("unresolved", "unresolved", None, None, 0.0)


def correlation_summary(runtime_name: str, snapshot: RepositoryGraphSnapshot) -> dict[str, Any]:
    """Return a JSON-safe summary suitable for evidence, never graph internals."""

    result = correlate_runtime_name(runtime_name, snapshot)
    return {
        "runtime_name": runtime_name,
        "status": result.status,
        "method": result.method,
        "symbol_key": result.symbol_key,
        "graph_node_id": result.graph_node_id,
        "confidence": result.confidence,
        "candidates": list(result.candidates),
    }


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())
