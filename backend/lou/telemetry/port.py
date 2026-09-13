"""Best-effort OpenTelemetry adapters behind a deterministic local port."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Protocol
from uuid import uuid4

from contracts import RuntimeObservation
from lou.telemetry.models import (
    CorrelationResult,
    ExporterStatus,
    RuntimeCorrelationContext,
    SpanName,
    observation,
)
from lou.telemetry.sanitize import sanitize_attributes


class TelemetryPort(Protocol):
    """Records supporting evidence without participating in verification decisions."""

    @contextmanager
    def span(
        self,
        name: SpanName,
        context: RuntimeCorrelationContext,
        attributes: dict[str, object] | None = None,
    ) -> Iterator[None]: ...

    def record(
        self,
        name: SpanName,
        context: RuntimeCorrelationContext,
        attributes: dict[str, object] | None = None,
        *,
        status: str = "ok",
        duration_ms: float | None = None,
        correlation: CorrelationResult | None = None,
    ) -> RuntimeObservation | None: ...

    def observations(self) -> tuple[RuntimeObservation, ...]: ...

    @property
    def exporter_status(self) -> ExporterStatus: ...


@dataclass
class NoopTelemetry:
    """The default: no SDK, artifact, network, or effect on deterministic verification."""

    @contextmanager
    def span(
        self,
        name: SpanName,
        context: RuntimeCorrelationContext,
        attributes: dict[str, object] | None = None,
    ) -> Iterator[None]:
        yield

    def record(
        self,
        name: SpanName,
        context: RuntimeCorrelationContext,
        attributes: dict[str, object] | None = None,
        *,
        status: str = "ok",
        duration_ms: float | None = None,
        correlation: CorrelationResult | None = None,
    ) -> RuntimeObservation | None:
        return None

    def observations(self) -> tuple[RuntimeObservation, ...]:
        return ()

    @property
    def exporter_status(self) -> ExporterStatus:
        return "disabled"


@dataclass
class InMemoryTelemetry:
    """A bounded deterministic adapter used by local tests and the fixture demo."""

    sample_rate: float = 1.0
    max_spans: int = 500
    _items: list[RuntimeObservation] = field(default_factory=list, init=False)
    _trace_ids: dict[str, str] = field(default_factory=dict, init=False)
    _status: ExporterStatus = field(default="healthy", init=False)

    @contextmanager
    def span(
        self,
        name: SpanName,
        context: RuntimeCorrelationContext,
        attributes: dict[str, object] | None = None,
    ) -> Iterator[None]:
        started = perf_counter()
        try:
            yield
        except Exception:
            self.record(
                name,
                context,
                attributes,
                status="error",
                duration_ms=(perf_counter() - started) * 1000,
            )
            raise
        else:
            self.record(name, context, attributes, duration_ms=(perf_counter() - started) * 1000)

    def record(
        self,
        name: SpanName,
        context: RuntimeCorrelationContext,
        attributes: dict[str, object] | None = None,
        *,
        status: str = "ok",
        duration_ms: float | None = None,
        correlation: CorrelationResult | None = None,
    ) -> RuntimeObservation | None:
        sampled = context.sampled(self.sample_rate)
        if not sampled:
            self._status = "sampled_out"
            return observation(
                context=context,
                observation_id=f"telemetry_{uuid4().hex}",
                span_name=name,
                status="sampled_out",
                exporter_status="sampled_out",
                sampled=False,
            )
        safe = sanitize_attributes({**context.attributes(), **(attributes or {})})
        if len(self._items) >= self.max_spans:
            self._status = "failed"
            return observation(
                context=context,
                observation_id=f"telemetry_{uuid4().hex}",
                span_name=name,
                status="limited",
                exporter_status="failed",
                attributes=safe.values,
                sampled=True,
                redaction_count=safe.redaction_count,
            )
        value = observation(
            context=context,
            observation_id=f"telemetry_{uuid4().hex}",
            span_name=name,
            status=(
                "error" if status == "error" else "unavailable" if status == "unavailable" else "ok"
            ),
            exporter_status="unavailable" if status == "unavailable" else "healthy",
            trace_id=self._trace_ids.setdefault(context.analysis_run_id, uuid4().hex),
            span_id=uuid4().hex[:16],
            duration_ms=duration_ms,
            attributes=safe.values,
            correlation=correlation,
            sampled=True,
            redaction_count=safe.redaction_count,
        )
        self._items.append(value)
        return value

    def observations(self) -> tuple[RuntimeObservation, ...]:
        return tuple(self._items)

    @property
    def exporter_status(self) -> ExporterStatus:
        return self._status
