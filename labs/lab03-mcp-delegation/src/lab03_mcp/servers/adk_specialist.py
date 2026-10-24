"""Travel Clock: an ADK agent with an ``McpToolset``, exposed over A2A with ``to_a2a``."""

from __future__ import annotations


def build_app():  # type: ignore[no-untyped-def]
    from a2a_labs_common.config import port, public_host
    from a2a_labs_common.telemetry import instrument_app
    from a2a_labs_common.telemetry.a2a_server import TracedExecutor
    from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    from lab03_mcp.agents.specialists import build_clock_agent
    from lab03_mcp.cards import clock_card

    service = "lab03-adk-specialist"
    app = to_a2a(
        build_clock_agent(),
        host=public_host(service),
        port=port(service),
        protocol="http",
        # A hand-built card: ADK's auto card would publish each MCP tool as a skill.
        agent_card=clock_card(),
        agent_executor_factory=lambda runner: TracedExecutor(  # type: ignore[arg-type,return-value]
            A2aAgentExecutor(runner=runner, force_new_version=True), "Travel Clock (ADK)"
        ),
    )
    instrument_app(app)
    return app


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab03-adk-specialist", framework="adk", lab_id="lab03")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab03-adk-specialist")


if __name__ == "__main__":
    main()
