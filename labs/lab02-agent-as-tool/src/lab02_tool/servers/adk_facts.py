"""City-facts specialist server: an ADK agent exposed with ``to_a2a`` (plain task).

Implements: spec lab02 §2; see lab01's ADK server for the ``force_new_version`` rationale (D10).
"""

from __future__ import annotations


def build_app():  # type: ignore[no-untyped-def]
    from a2a_labs_common.config import port, public_host
    from a2a_labs_common.telemetry import instrument_app
    from a2a_labs_common.telemetry.a2a_server import TracedExecutor
    from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    from lab02_tool.agents.facts import build_facts_agent

    service = "lab02-adk-facts"
    app = to_a2a(
        build_facts_agent(),
        host=public_host(service),  # ADK-4: advertise the docker DNS name, not localhost
        port=port(service),
        protocol="http",
        agent_executor_factory=lambda runner: TracedExecutor(  # type: ignore[arg-type,return-value]
            A2aAgentExecutor(runner=runner, force_new_version=True), "City Facts (ADK)"
        ),
    )
    instrument_app(app)
    return app


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab02-adk-facts", framework="adk", lab_id="lab02")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab02-adk-facts")


if __name__ == "__main__":
    main()
