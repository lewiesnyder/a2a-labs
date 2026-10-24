"""Framework-specific enablement (spec 05 §2 step 6). Imports stay local (O-1)."""

from __future__ import annotations

import os
from typing import Any


def enable_framework(framework: str, tracer_provider: Any) -> None:
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")
    if framework == "langgraph":
        from openinference.instrumentation.langchain import LangChainInstrumentor

        LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
    elif framework == "strands":
        from strands.telemetry import StrandsTelemetry

        # O-2: hand Strands OUR provider; never call setup_otlp_exporter() (duplicate spans).
        StrandsTelemetry(tracer_provider=tracer_provider)
    # adk and a2a-sdk emit spans through the global provider; nothing to enable.
