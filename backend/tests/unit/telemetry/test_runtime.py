"""M7 contract, redaction, sampling, exporter, and retention proof."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import networkx as nx  # type: ignore[import-untyped]
import pytest

from contracts import RepositoryContext
from lou.application.analysis import AnalysisApplicationService
from lou.repository import RepositoryGraphSnapshot
from lou.telemetry import InMemoryTelemetry, NoopTelemetry, RuntimeCorrelationContext
from lou.telemetry.artifacts import cleanup_expired, write_artifact
from lou.telemetry.correlation import correlate_runtime_name, correlation_summary
from lou.telemetry.otel import OtlpTelemetry, build_telemetry
from lou.telemetry.sanitize import sanitize_attributes


def _context(run_id: str = "run-1") -> RuntimeCorrelationContext:
    return RuntimeCorrelationContext(run_id, "repo-1", "candidate-sha", "candidate", "checkout-k6")


def test_memory_spans_share_trace_and_keep_safe_correlation_ids() -> None:
    telemetry = InMemoryTelemetry()
    with telemetry.span("lou.analysis", _context(), {"lou.entrypoint": "cli"}):
        with telemetry.span("lou.workload.execute", _context(), {"lou.workload_type": "k6"}):
            pass

    observed = telemetry.observations()
    assert len(observed) == 2
    assert len({item.trace_id for item in observed}) == 1
    assert all(item.analysis_run_id == "run-1" for item in observed)


def test_redaction_limits_sampling_and_noop_do_not_affect_execution() -> None:
    safe = sanitize_attributes(
        {
            "lou.entrypoint": "api",
            "authorization": "Bearer super-secret",
            "db.query": "SELECT * FROM users WHERE password = 'nope'",
            "request.body": "private",
        }
    )
    assert safe.values == {"lou.entrypoint": "api"}
    assert safe.redaction_count == 3

    sampled = InMemoryTelemetry(sample_rate=0)
    item = sampled.record("lou.analysis", _context())
    assert item and item.status == "sampled_out"
    assert sampled.observations() == ()
    assert _context("same-run").sampled(0.25) == _context("same-run").sampled(0.25)
    assert NoopTelemetry().record("lou.analysis", _context()) is None


def test_span_limit_records_bounded_failure_without_unbounded_artifact() -> None:
    telemetry = InMemoryTelemetry(max_spans=1)
    assert telemetry.record("lou.analysis", _context())
    limited = telemetry.record("lou.workload.execute", _context())
    assert limited and limited.status == "limited"
    assert len(telemetry.observations()) == 1


def test_correlation_handles_exact_renamed_missing_and_ambiguous_nodes(tmp_path: Path) -> None:
    graph = nx.MultiDiGraph()
    graph.add_node("function:checkout", node_type="FUNCTION", key="store.app.Store.checkout")
    snapshot = RepositoryGraphSnapshot(
        graph=graph,
        repository_id="repo-1",
        commit_sha="candidate-sha",
        completeness=1.0,
        diagnostics=(),
        artifact_path=tmp_path / "graph.json",
        artifact_uri="file://graph.json",
        artifact_sha256="a" * 64,
    )
    exact = correlate_runtime_name("store.app.Store.checkout", snapshot)
    assert exact.status == "resolved" and exact.method == "exact" and exact.confidence == 1.0
    assert (
        correlate_runtime_name("store.app.Store.renamed_checkout", snapshot).status == "unresolved"
    )
    graph.add_node("function:duplicate", node_type="FUNCTION", key="store-app-store-checkout")
    assert correlate_runtime_name("store app store checkout", snapshot).status == "ambiguous"


def test_telemetry_artifacts_are_bounded_and_cleanup_cannot_touch_core_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifacts"
    assert write_artifact(root, "run-1", {"safe": "value"}, max_bytes=64)
    assert write_artifact(root, "too-big", {"large": "x" * 128}, max_bytes=32) is None
    artifact = root / "telemetry" / "run-1.json"
    old = (datetime.now(UTC) - timedelta(days=8)).timestamp()
    os.utime(artifact, (old, old))
    assert cleanup_expired(root, retention_days=7) == (artifact,)
    assert not artifact.exists()


def test_otlp_outage_is_bounded_and_factory_never_enables_network_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert isinstance(
        build_telemetry(
            exporter="none", endpoint="unused", timeout_seconds=1, sample_rate=1, max_spans=1
        ),
        NoopTelemetry,
    )
    telemetry = OtlpTelemetry(endpoint="http://127.0.0.1:1/v1/traces", timeout_seconds=0.01)
    telemetry.record("lou.analysis", _context())
    monkeypatch.setattr(telemetry._provider, "force_flush", lambda timeout_millis: False)
    assert telemetry.flush() == "unavailable"
    telemetry.shutdown()


def test_recorded_correlation_summary_round_trips_into_the_analysis_service(tmp_path: Path) -> None:
    """The graph writes summaries and the analysis service reads them; keep formats aligned."""

    graph = nx.MultiDiGraph()
    graph.add_node("table:broken_store.products", node_type="DATABASE_TABLE", key="products")
    snapshot = RepositoryGraphSnapshot(
        graph=graph,
        repository_id="repo-1",
        commit_sha="candidate-sha",
        completeness=1.0,
        diagnostics=(),
        artifact_path=tmp_path / "graph.json",
        artifact_uri="file://graph.json",
        artifact_sha256="a" * 64,
    )
    context = RepositoryContext(
        repository_id="repo-1",
        commit_sha="candidate-sha",
        affected_data_dependencies=["products"],
        metadata={"runtime_correlations": {"products": correlation_summary("products", snapshot)}},
    )

    correlation = AnalysisApplicationService._correlation(context, "products")

    assert correlation.status == "resolved"
    assert correlation.method == "exact"
    assert correlation.confidence == 1.0
    assert correlation.graph_node_id == "table:broken_store.products"
