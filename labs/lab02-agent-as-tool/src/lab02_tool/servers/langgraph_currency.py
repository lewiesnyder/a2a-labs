"""Currency specialist server: LangGraph graph behind ``LangGraphA2AExecutor``.

Replies with INPUT_REQUIRED when the target currency is missing (a graph ``interrupt()``).
Implements: spec lab02 §2.
"""

from __future__ import annotations

import os


def service_name() -> str:
    """The compose service name (lab02-* or lab05-*); OTEL_SERVICE_NAME carries it."""
    return os.environ.get("OTEL_SERVICE_NAME") or "lab02-langgraph-currency"


def build_app():  # type: ignore[no-untyped-def]
    from a2a_labs_common.a2a.langgraph import LangGraphA2AExecutor
    from a2a_labs_common.a2a.server import build_a2a_app

    from lab02_tool.agents.currency import build_graph
    from lab02_tool.cards import currency_card

    return build_a2a_app(
        card=currency_card(service_name()),
        executor=LangGraphA2AExecutor(build_graph(), agent_name="currency"),
    )


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry(service_name(), framework="langgraph", lab_id="lab02")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service=service_name())


if __name__ == "__main__":
    main()
