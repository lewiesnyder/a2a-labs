"""OpenTelemetry for the labs (spec 05)."""

from __future__ import annotations

from opentelemetry import metrics, trace

from a2a_labs_common.telemetry.setup import (
    TelemetryHandles,
    init_telemetry,
    instrument_app,
)

__all__ = ["TelemetryHandles", "get_meter", "get_tracer", "init_telemetry", "instrument_app"]


def get_tracer(name: str = "a2a_labs") -> trace.Tracer:
    return trace.get_tracer(name)


def get_meter(name: str = "a2a_labs") -> metrics.Meter:
    return metrics.get_meter(name)
