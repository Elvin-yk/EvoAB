"""Pluggable telemetry for evoab."""

from evoab.telemetry.model import MetricRecord, SpanRecord, TraceContext
from evoab.telemetry.session import (
    TelemetryMode,
    TelemetrySession,
    create_telemetry,
)
from evoab.telemetry.sinks import MemorySink
from evoab.telemetry.tracer import NullTracer, Tracer

__all__ = [
    "MemorySink",
    "MetricRecord",
    "NullTracer",
    "SpanRecord",
    "TelemetryMode",
    "TelemetrySession",
    "TraceContext",
    "Tracer",
    "create_telemetry",
]
