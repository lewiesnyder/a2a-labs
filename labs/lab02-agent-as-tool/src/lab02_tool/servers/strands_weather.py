"""Weather specialist server: a Strands agent behind ``StrandsA2AExecutor`` (streams the artifact).

Implements: spec lab02 §2. See DEV-1 for why this is not ``A2AServer``.
"""

from __future__ import annotations

import os


def service_name() -> str:
    """The compose service name (lab02-* or lab05-*); OTEL_SERVICE_NAME carries it."""
    return os.environ.get("OTEL_SERVICE_NAME") or "lab02-strands-weather"


def build_app():  # type: ignore[no-untyped-def]
    from a2a_labs_common.a2a.server import build_a2a_app
    from a2a_labs_common.a2a.strands import StrandsA2AExecutor

    from lab02_tool.agents.weather import build_weather_agent
    from lab02_tool.cards import weather_card

    return build_a2a_app(
        card=weather_card(service_name()), executor=StrandsA2AExecutor(build_weather_agent)
    )


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry(service_name(), framework="strands", lab_id="lab02")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service=service_name())


if __name__ == "__main__":
    main()
