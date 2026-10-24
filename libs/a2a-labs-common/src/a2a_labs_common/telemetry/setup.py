"""``init_telemetry()``: one global provider per process for traces, metrics and logs.

Implements: spec 05 §2. Call it FIRST in every ``main()`` and notebook setup cell (O-1).
"""

from __future__ import annotations

import atexit
import logging
import os
import socket
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any, Literal

from opentelemetry import metrics, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from a2a_labs_common.config import settings

Framework = Literal["a2a-sdk", "adk", "strands", "langgraph", "mcp", "notebook", "service"]
CONSOLE_FORMAT = (
    "%(asctime)s %(levelname)s [%(name)s] [trace=%(otelTraceID)s span=%(otelSpanID)s] %(message)s"
)
logger = logging.getLogger(__name__)


@dataclass
class TelemetryHandles:
    service_name: str
    enabled: bool
    tracer_provider: Any = None
    meter_provider: Any = None
    logger_provider: Any = None
    _log_handler: logging.Handler | None = field(default=None, repr=False)

    def flush(self, timeout_ms: int = 5000) -> None:
        """Push pending telemetry now, so it is visible in Grafana right away."""
        for provider in (self.tracer_provider, self.meter_provider, self.logger_provider):
            if provider is not None and hasattr(provider, "force_flush"):
                provider.force_flush(timeout_ms)

    def shutdown(self) -> None:
        self.flush()
        for provider in (self.tracer_provider, self.meter_provider, self.logger_provider):
            if provider is not None and hasattr(provider, "shutdown"):
                provider.shutdown()


_handles: TelemetryHandles | None = None


def _version() -> str:
    try:
        return metadata.version("a2a-labs-common")
    except metadata.PackageNotFoundError:  # pragma: no cover
        return "0.0.0"


def _resource(service_name: str, framework: str, lab_id: str) -> Resource:
    return Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "a2a-labs",
            "service.version": _version(),
            "deployment.environment": os.environ.get("A2A_LABS_ENV", "lab"),
            "a2a_labs.lab_id": lab_id,
            "a2a_labs.framework": framework,
            "host.name": socket.gethostname(),
        }
    )


def init_telemetry(
    service_name: str,
    *,
    framework: Framework = "service",
    lab_id: str | None = None,
    enable_metrics: bool = True,
    enable_logs: bool = True,
    instrument_httpx: bool = True,
    instrument_asgi_app: Any = None,
) -> TelemetryHandles:
    """Idempotent: a second call returns the existing handles (and instruments a new app)."""
    global _handles
    if _handles is not None:
        if instrument_asgi_app is not None and _handles.enabled:
            _instrument_app(instrument_asgi_app)
        return _handles
    if not settings().otel_enabled:
        _handles = TelemetryHandles(service_name, enabled=False)
        return _handles

    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased

    resource = _resource(service_name, framework, lab_id or settings().lab_id)
    # A2A: W3C trace context is what links notebook -> agent -> agent into ONE trace.
    set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )

    tp = TracerProvider(resource=resource, sampler=ParentBased(ALWAYS_ON))
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tp)
    handles = TelemetryHandles(service_name, enabled=True, tracer_provider=tp)

    if enable_metrics and os.environ.get("OTEL_METRICS_EXPORTER", "otlp") != "none":
        reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=10_000)
        handles.meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(handles.meter_provider)

    if enable_logs and os.environ.get("OTEL_LOGS_EXPORTER", "otlp") != "none":
        lp = LoggerProvider(resource=resource)
        lp.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
        handles.logger_provider = lp
        _setup_logging(handles, lp, LoggingHandler)

    if instrument_httpx:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()  # covers A2A clients, Ollama and webhook POSTs
    if instrument_asgi_app is not None:
        _instrument_app(instrument_asgi_app)

    from a2a_labs_common.telemetry.frameworks import enable_framework

    enable_framework(framework, tp)
    atexit.register(handles.shutdown)
    _handles = handles
    return handles


def _setup_logging(handles: TelemetryHandles, lp: Any, handler_cls: Any) -> None:
    from opentelemetry.instrumentation.logging import LoggingInstrumentor

    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    # inject_trace_context is required by current instrumentation to add otelTraceID/otelSpanID
    # to records (set_logging_format=False alone no longer does) - see DEV-4.
    LoggingInstrumentor().instrument(set_logging_format=False, inject_trace_context=True)
    root = logging.getLogger()
    root.setLevel(level)
    for noisy in (
        "httpx",
        "httpcore",
        "LiteLLM",
        "litellm",
    ):  # one INFO line per request drowns notebook output
        logging.getLogger(noisy).setLevel(logging.WARNING)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    handles._log_handler = handler_cls(level=level, logger_provider=lp)
    root.addHandler(console)
    root.addHandler(handles._log_handler)


def _instrument_app(app: Any) -> None:
    from opentelemetry.instrumentation.starlette import StarletteInstrumentor

    StarletteInstrumentor.instrument_app(app)


def instrument_app(app: Any) -> None:
    """Server-side spans for a Starlette/FastAPI app; no-op when telemetry is disabled."""
    if _handles is not None and _handles.enabled:
        _instrument_app(app)


def reset_for_tests() -> None:
    """Forget the cached handles (tests only; providers are process-global in OTel)."""
    global _handles
    _handles = None
