"""Optional local OTLP export layered on the deterministic telemetry port."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from contracts import RuntimeObservation
from lou.telemetry.models import (
    CorrelationResult,
    ExporterStatus,
    RuntimeCorrelationContext,
    SpanName,
)
from lou.telemetry.port import InMemoryTelemetry, NoopTelemetry, TelemetryPort


@dataclass
class OtlpTelemetry(InMemoryTelemetry):
    """Exports sanitized observations to an explicit local OTLP endpoint.

    The in-memory evidence remains authoritative for the current run. OTLP is a
    best-effort mirror: exporter exceptions are converted to a health state and
    never escape into verification code.
    """

    endpoint: str = "http://127.0.0.1:4318/v1/traces"
    timeout_seconds: float = 2.0
    _provider: TracerProvider = field(init=False, repr=False)
    _exporter: _TrackingExporter = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._provider = TracerProvider(resource=Resource.create({"service.name": "lou"}))
        exporter = OTLPSpanExporter(endpoint=self.endpoint, timeout=self.timeout_seconds)
        self._exporter = _TrackingExporter(exporter)
        # Synchronous export makes a local rehearsal's health result observable before
        # the process exits. It remains opt-in, so normal analysis has no network cost.
        self._provider.add_span_processor(SimpleSpanProcessor(self._exporter))

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
        item = super().record(
            name,
            context,
            attributes,
            status=status,
            duration_ms=duration_ms,
            correlation=correlation,
        )
        if item is None or item.status in {"sampled_out", "limited"}:
            return item
        try:
            tracer = self._provider.get_tracer("lou.runtime")
            parent = trace.SpanContext(
                trace_id=int(item.trace_id or "1", 16),
                span_id=1,
                is_remote=False,
                trace_flags=trace.TraceFlags(0x01),
                trace_state=trace.TraceState(),
            )
            parent_context = trace.set_span_in_context(trace.NonRecordingSpan(parent))
            with tracer.start_as_current_span(name, context=parent_context) as span:
                for key, value in item.attributes.items():
                    span.set_attribute(key, value)
                span.set_attribute("lou.telemetry.trace_id", item.trace_id or "")
                span.set_attribute("lou.telemetry.span_id", item.span_id or "")
                span.set_attribute("lou.status", item.status)
        except Exception:
            self._status = "unavailable"
        return item

    def flush(self) -> ExporterStatus:
        """Flush only when explicitly requested by the caller/rehearsal."""

        try:
            completed = self._provider.force_flush(timeout_millis=int(self.timeout_seconds * 1000))
        except Exception:
            self._status = "unavailable"
        else:
            if not completed or self._exporter.status != "healthy":
                self._status = "unavailable"
        return self._status

    def shutdown(self) -> None:
        try:
            self._provider.shutdown()
        except Exception:
            self._status = "unavailable"


def build_telemetry(
    *, exporter: str, endpoint: str, timeout_seconds: float, sample_rate: float, max_spans: int
) -> TelemetryPort:
    """Select no implicit network behavior; unknown values fail closed to no-op-like memory."""

    if exporter == "otlp":
        return OtlpTelemetry(
            endpoint=endpoint,
            timeout_seconds=timeout_seconds,
            sample_rate=sample_rate,
            max_spans=max_spans,
        )
    if exporter == "memory":
        return InMemoryTelemetry(sample_rate=sample_rate, max_spans=max_spans)
    return NoopTelemetry()


class _TrackingExporter(SpanExporter):
    """Convert SDK exporter results into a queryable health state."""

    def __init__(self, delegate: SpanExporter) -> None:
        self._delegate = delegate
        self.status: ExporterStatus = "healthy"

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self._delegate.export(spans)
        except Exception:
            self.status = "unavailable"
            return SpanExportResult.FAILURE
        if result is not SpanExportResult.SUCCESS:
            self.status = "unavailable"
        return result

    def shutdown(self) -> None:
        try:
            self._delegate.shutdown()
        except Exception:
            self.status = "unavailable"
