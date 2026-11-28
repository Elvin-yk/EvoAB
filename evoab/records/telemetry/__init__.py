"""Pluggable telemetry for evoab."""

from evoab.records.telemetry.model import MetricRecord, SpanRecord, TraceContext
from evoab.records.telemetry.session import (
    TelemetryMode,
    TelemetrySession,
    create_telemetry,
)
from evoab.records.telemetry.sinks import MemorySink
from evoab.records.telemetry.tracer import NullTracer, Tracer

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
