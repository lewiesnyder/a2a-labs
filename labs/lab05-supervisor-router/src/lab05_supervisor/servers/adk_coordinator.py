"""ADK coordinator server (``lab05-adk-coordinator``): LLM ``transfer_to_agent`` routing.

CUSTOM debug route ``GET /debug/ledger/{context_id}`` shows ADK's implicit per-remote tracking.
Implements: spec lab05 §2, §4.
"""

from __future__ import annotations

from typing import Any


def build_app() -> Any:
    from a2a_labs_common.config import port, public_host
    from a2a_labs_common.telemetry import instrument_app
    from a2a_labs_common.telemetry.a2a_server import TracedExecutor
    from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
    from google.adk.a2a.utils.agent_to_a2a import to_a2a
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from lab05_supervisor.agents.adk_coordinator import build_coordinator, implicit_ledger
    from lab05_supervisor.cards import coordinator_card

    service = "lab05-adk-coordinator"
    runners: list[Any] = []

    def executor(runner: Any) -> Any:
        runners.append(runner)  # kept to read the session events back in the debug route
        return TracedExecutor(
            A2aAgentExecutor(runner=runner, force_new_version=True), "Travel Coordinator (ADK)"
        )

    app = to_a2a(
        build_coordinator(),
        host=public_host(service),
        port=port(service),
        protocol="http",
        agent_card=coordinator_card(),
        agent_executor_factory=executor,
    )

    async def debug_ledger(request: Request) -> JSONResponse:
        ctx = request.path_params["context_id"]
        runner = runners[0]
        session = await runner.session_service.get_session(
            app_name=runner.app_name, user_id=f"A2A_USER_{ctx}", session_id=ctx
        )
        return JSONResponse(implicit_ledger(session.events) if session else {})

    app.router.routes.append(Route("/debug/ledger/{context_id}", debug_ledger))
    instrument_app(app)
    return app


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab05-adk-coordinator", framework="adk", lab_id="lab05")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab05-adk-coordinator")


if __name__ == "__main__":
    main()
