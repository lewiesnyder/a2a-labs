"""LangGraph supervisor server (``lab05-supervisor``), served over A2A with the shared bridge.

Beyond the A2A endpoints it exposes three CUSTOM, non-A2A helper routes for the notebook:

* ``GET /debug/state/{context_id}``: the graph state (plan, results, ledger, decision trace);
* ``GET /debug/ledger/{context_id}``: the LIVE ledger, which already shows a task that is paused;
* ``GET|POST /specialists`` and ``DELETE /specialists``: register another specialist at runtime.

Implements: spec lab05 §2, §3.
"""

from __future__ import annotations

from typing import Any


def build_app(supervisor: Any = None) -> Any:
    from a2a_labs_common.a2a.langgraph import LangGraphA2AExecutor, jsonable
    from a2a_labs_common.a2a.server import build_a2a_app
    from langgraph.checkpoint.memory import InMemorySaver
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from lab05_supervisor.agents.supervisor import Supervisor
    from lab05_supervisor.cards import supervisor_card

    sup = supervisor or Supervisor()
    graph = sup.build(InMemorySaver())
    app = build_a2a_app(
        card=supervisor_card(),
        executor=LangGraphA2AExecutor(
            graph, agent_name="supervisor", emit_node_updates=True, artifact_name="answer"
        ),
    )

    # CUSTOM: debugging and runtime-registration helpers, not part of A2A.
    async def debug_state(request: Request) -> JSONResponse:
        ctx = request.path_params["context_id"]
        snap = await graph.aget_state({"configurable": {"thread_id": ctx}})
        values = {k: v for k, v in snap.values.items() if k != "messages"}
        return JSONResponse({"thread_id": ctx, "next": list(snap.next), "values": jsonable(values)})

    async def debug_ledger(request: Request) -> JSONResponse:
        return JSONResponse(jsonable(sup.ledger.entries(request.path_params["context_id"])))

    async def specialists(request: Request) -> JSONResponse:
        if request.method == "POST":
            sup.directory.register((await request.json())["service"])
        elif request.method == "DELETE":
            sup.directory.unregister((await request.json())["service"])
        return JSONResponse([s.row() for s in await sup.directory.discover()])

    app.router.routes += [
        Route("/debug/state/{context_id}", debug_state),
        Route("/debug/ledger/{context_id}", debug_ledger),
        Route("/specialists", specialists, methods=["GET", "POST", "DELETE"]),
    ]
    app.state.supervisor = sup
    app.state.graph = graph
    return app


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab05-supervisor", framework="langgraph", lab_id="lab05")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab05-supervisor")


if __name__ == "__main__":
    main()
