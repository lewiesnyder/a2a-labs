"""Delegator server (``lab06-delegator``): the verifying delegator, served over A2A.

CUSTOM ``GET /debug/state/{context_id}`` shows candidates, verdicts and the decision.
Implements: spec lab06 §2, §5.
"""

from __future__ import annotations

from typing import Any


def build_app(delegator: Any = None) -> Any:
    from a2a_labs_common.a2a.langgraph import LangGraphA2AExecutor, jsonable
    from a2a_labs_common.a2a.server import build_a2a_app
    from langgraph.checkpoint.memory import InMemorySaver
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from lab06_registry.agents.delegator import Delegator
    from lab06_registry.cards import delegator_card
    from lab06_registry.keys import load_trust_store

    graph = (delegator or Delegator(load_trust_store())).build(InMemorySaver())
    app = build_a2a_app(
        card=delegator_card(),
        executor=LangGraphA2AExecutor(graph, agent_name="delegator", emit_node_updates=True),
    )

    async def debug_state(request: Request) -> JSONResponse:
        ctx = request.path_params["context_id"]
        snap = await graph.aget_state({"configurable": {"thread_id": ctx}})
        values = {k: v for k, v in snap.values.items() if k != "messages"}
        return JSONResponse({"thread_id": ctx, "values": jsonable(values)})

    app.router.routes.append(Route("/debug/state/{context_id}", debug_state))
    return app


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab06-delegator", framework="langgraph", lab_id="lab06")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab06-delegator")


if __name__ == "__main__":
    main()
