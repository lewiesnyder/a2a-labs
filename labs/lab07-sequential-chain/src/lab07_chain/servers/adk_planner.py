"""Planner server (ADK): ``to_a2a`` + our executor wrappers (outline artifact, relay)."""

from __future__ import annotations

from typing import Any


def build_app() -> Any:
    from a2a_labs_common.a2a.relay import RelayExecutor
    from a2a_labs_common.config import port, public_host
    from a2a_labs_common.telemetry import instrument_app
    from a2a_labs_common.telemetry.a2a_server import TracedExecutor
    from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    from lab07_chain.agents.planner import build_planner
    from lab07_chain.cards import planner_card
    from lab07_chain.executors.wrappers import OutlineExecutor

    service = "lab07-adk-planner"
    app = to_a2a(
        build_planner(),
        host=public_host(service),
        port=port(service),
        protocol="http",
        agent_card=planner_card(),
        agent_executor_factory=lambda runner: TracedExecutor(  # type: ignore[arg-type,return-value]
            RelayExecutor(
                OutlineExecutor(A2aAgentExecutor(runner=runner, force_new_version=True)),
                service=service,
            ),
            "Outline Planner",
        ),
    )
    instrument_app(app)
    return app


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab07-adk-planner", framework="adk", lab_id="lab07")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab07-adk-planner")


if __name__ == "__main__":
    main()
