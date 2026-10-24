"""Trip Planner server: the planner graph behind ``LangGraphA2AExecutor`` with Pattern 3 mapping.

Node starts become WORKING events (metadata ``langgraph_node``), interrupts become INPUT_REQUIRED
(question text + a DataPart), END becomes the ``itinerary`` artifact. Implements: spec lab04 §2.
"""

from __future__ import annotations

import os
from typing import Any


def build_app(decision: Any = None):  # type: ignore[no-untyped-def]
    from a2a_labs_common.a2a.langgraph import LangGraphA2AExecutor, jsonable
    from a2a_labs_common.a2a.server import build_a2a_app
    from langgraph.checkpoint.memory import InMemorySaver
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from lab04_langgraph.agents.trip_planner import build_graph
    from lab04_langgraph.cards import planner_card

    # The checkpointer is REQUIRED for interrupts: it is what lets a task resume.
    graph = build_graph(InMemorySaver(), decision)
    executor = LangGraphA2AExecutor(
        graph,
        agent_name="trip-planner",
        artifact_name="itinerary",
        emit_node_updates=True,
        snapshot_artifact=os.environ.get("TRIP_SNAPSHOTS", "false").lower() == "true",
    )
    app = build_a2a_app(card=planner_card(), executor=executor)

    # CUSTOM (optional): show that LangGraph's thread_id IS the A2A context_id by reading the
    # graph's own checkpoint. Not part of A2A; the notebook uses it in step 6.
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

    init_telemetry("lab04-langgraph-travel", framework="langgraph", lab_id="lab04")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab04-langgraph-travel")


if __name__ == "__main__":
    main()
