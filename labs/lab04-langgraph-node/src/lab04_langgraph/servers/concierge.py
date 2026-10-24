"""Concierge server: a graph with a REMOTE A2A node, itself served over A2A.

The nesting is A2A -> LangGraph -> A2A -> LangGraph; a remote interrupt becomes a local one.
Implements: spec lab04 §2 (``lab04-langgraph-orchestrator``).
"""

from __future__ import annotations

from typing import Any


def build_app() -> Any:
    from a2a_labs_common.a2a.langgraph import LangGraphA2AExecutor, jsonable
    from a2a_labs_common.a2a.server import build_a2a_app
    from langgraph.checkpoint.memory import InMemorySaver
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from lab04_langgraph.agents.concierge import build_graph
    from lab04_langgraph.cards import concierge_card

    graph = build_graph(InMemorySaver())
    app = build_a2a_app(
        card=concierge_card(),
        executor=LangGraphA2AExecutor(graph, agent_name="concierge", emit_node_updates=True),
    )

    # CUSTOM (optional, like the planner's): expose the concierge's own checkpoint so the
    # notebook can show `remote_tasks`, the per-remote task tracking.
    async def debug_state(request: Request) -> JSONResponse:
        ctx = request.path_params["context_id"]
        snap = await graph.aget_state({"configurable": {"thread_id": ctx}})
        values = {k: v for k, v in snap.values.items() if k != "messages"}
        return JSONResponse({"thread_id": ctx, "next": list(snap.next), "values": jsonable(values)})

    app.router.routes.append(Route("/debug/state/{context_id}", debug_state))
    app.state.graph = graph
    return app


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab04-langgraph-orchestrator", framework="langgraph", lab_id="lab04")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab04-langgraph-orchestrator")


if __name__ == "__main__":
    main()
