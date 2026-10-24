"""ADK controller server: ``AgentTool(RemoteA2aAgent)`` for currency/weather, sub-agent for facts.

Implements: spec lab02 §2 (``lab02-adk-controller``).
"""

from __future__ import annotations


def build_app():  # type: ignore[no-untyped-def]
    from a2a_labs_common.config import port, public_host
    from a2a_labs_common.telemetry import instrument_app
    from a2a_labs_common.telemetry.a2a_server import TracedExecutor
    from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    from lab02_tool.agents.controllers import build_adk_controller

    service = "lab02-adk-controller"
    app = to_a2a(
        build_adk_controller(),
        host=public_host(service),
        port=port(service),
        protocol="http",
        agent_executor_factory=lambda runner: TracedExecutor(  # type: ignore[arg-type,return-value]
            A2aAgentExecutor(runner=runner, force_new_version=True), "Travel Desk (ADK)"
        ),
    )
    instrument_app(app)
    return app


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab02-adk-controller", framework="adk", lab_id="lab02")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab02-adk-controller")


if __name__ == "__main__":
    main()
