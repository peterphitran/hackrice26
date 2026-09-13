"""Typed, vendor-neutral runtime telemetry values."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from contracts import RuntimeObservation

Phase = Literal["baseline", "candidate", "fix", "comparison"]
ExporterStatus = Literal["disabled", "healthy", "sampled_out", "unavailable", "failed"]
SpanName = Literal[
    "lou.analysis",
    "lou.workload.execute",
    "lou.sandbox.execute",
    "lou.verification.compare",
    "lou.db.query",
    "lou.correlation.resolve",
]


@dataclass(frozen=True)
class RuntimeCorrelationContext:
    """Safe identifiers propagated through one bounded Lou operation."""

    analysis_run_id: str
    repository_id: str
    commit_sha: str
    phase: Phase
    workload_id: str | None = None
    verification_run_id: str | None = None
    artifact_id: str | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("analysis_run_id", self.analysis_run_id),
            ("repository_id", self.repository_id),
            ("commit_sha", self.commit_sha),
        ):
            if not value or len(value) > 255:
                raise ValueError(f"{name} must be a non-empty safe identifier")

    def attributes(self) -> dict[str, str]:
        values = {
            "lou.analysis_run_id": self.analysis_run_id,
            "lou.repository_id": self.repository_id,
            "lou.commit_sha": self.commit_sha,
            "lou.phase": self.phase,
        }
        if self.workload_id:
            values["lou.workload_id"] = self.workload_id
        if self.verification_run_id:
            values["lou.verification_run_id"] = self.verification_run_id
        if self.artifact_id:
            values["lou.artifact_id"] = self.artifact_id
        return values

    def sampled(self, rate: float) -> bool:
        """Choose deterministically so retries retain the same tracing behavior."""

        if rate <= 0:
            return False
        if rate >= 1:
            return True
        value = int(hashlib.sha256(self.analysis_run_id.encode("utf-8")).hexdigest()[:8], 16)
        return value / 0xFFFFFFFF < rate


@dataclass(frozen=True)
class CorrelationResult:
    status: Literal["resolved", "ambiguous", "unresolved"]
    method: Literal["exact", "normalized", "workload", "unresolved", "ambiguous"]
    symbol_key: str | None
    graph_node_id: str | None
    confidence: float
    candidates: tuple[str, ...] = ()


def observation(
    *,
    context: RuntimeCorrelationContext,
    observation_id: str,
    span_name: SpanName,
    status: Literal["ok", "error", "unavailable", "sampled_out", "limited"],
    exporter_status: ExporterStatus,
    trace_id: str | None = None,
    span_id: str | None = None,
    duration_ms: float | None = None,
    attributes: dict[str, str | int | float | bool] | None = None,
    correlation: CorrelationResult | None = None,
    sampled: bool = True,
    redaction_count: int = 0,
) -> RuntimeObservation:
    """Build a shared contract without leaking runtime SDK details."""

    return RuntimeObservation(
        observation_id=observation_id,
        analysis_run_id=context.analysis_run_id,
        phase=context.phase,
        span_name=span_name,
        observed_at=datetime.now(UTC),
        status=status,
        commit_sha=context.commit_sha,
        workload_id=context.workload_id,
        verification_run_id=context.verification_run_id,
        trace_id=trace_id,
        span_id=span_id,
        duration_ms=duration_ms,
        symbol_key=correlation.symbol_key if correlation else None,
        graph_node_id=correlation.graph_node_id if correlation else None,
        correlation_method=correlation.method if correlation else None,
        correlation_confidence=correlation.confidence if correlation else None,
        attributes=attributes or {},
        redaction_count=redaction_count,
        sampled=sampled,
        exporter_status=exporter_status,
    )
