"""ADK server: an ADK ``Agent`` exposed with the official ``to_a2a`` adapter.

Pattern 1, the Task side. D10: pass ``agent_executor_factory`` so we can set
``A2aAgentExecutor(force_new_version=True)``; the default executor picks the legacy path because
``use_legacy=False`` is ineffective on a2a-sdk 1.x (adk-python #7334).
Implements: spec lab01 §2.1.
"""

from __future__ import annotations


def build_app():  # type: ignore[no-untyped-def]
    from a2a_labs_common.config import port, public_host
    from a2a_labs_common.telemetry import instrument_app
    from a2a_labs_common.telemetry.a2a_server import TracedExecutor
    from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    from lab01_direct.agents.adk_agent import build_adk_agent

    service = "lab01-adk-agent"
    app = to_a2a(
        build_adk_agent(),
        # ADK-4: to_a2a builds the card URL from host:port; "localhost" would be wrong in compose.
        host=public_host(service),
        port=port(service),
        protocol="http",
        agent_executor_factory=lambda runner: TracedExecutor(  # type: ignore[arg-type,return-value]
            A2aAgentExecutor(runner=runner, force_new_version=True), "Trip Tip (ADK)"
        ),
    )
    instrument_app(app)
    return app


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab01-adk-agent", framework="adk", lab_id="lab01")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab01-adk-agent")


if __name__ == "__main__":
    main()
