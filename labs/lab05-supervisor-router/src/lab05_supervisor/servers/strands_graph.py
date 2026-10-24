"""Strands Graph supervisor server (``lab05-strands-graph-supervisor``).

The graph is wrapped by ``GraphAgent`` and served through the shared ``StrandsA2AExecutor``
(CUSTOM, DEV-1): Strands' own ``A2AServer`` cannot run next to a2a-sdk 1.x.
Implements: spec lab05 §2, §5.
"""

from __future__ import annotations

from typing import Any


def build_app(decision: Any = None, directory: Any = None) -> Any:
    from a2a_labs_common.a2a.remote import RemoteAgent
    from a2a_labs_common.a2a.server import build_a2a_app
    from a2a_labs_common.a2a.strands import StrandsA2AExecutor
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from lab05_supervisor.agents.strands_graph import NODES, GraphAgent
    from lab05_supervisor.cards import strands_graph_card
    from lab05_supervisor.discovery import Directory

    directory = directory or Directory()
    remotes = {node_id: RemoteAgent(service) for node_id, service in NODES.items()}
    agents: dict[str, GraphAgent] = {}

    def factory(context_id: str) -> GraphAgent:
        agents[context_id] = GraphAgent(
            context_id, directory=directory, decision=decision, remotes=remotes
        )
        return agents[context_id]

    app = build_a2a_app(card=strands_graph_card(), executor=StrandsA2AExecutor(factory))

    # CUSTOM: what the classifier decided and which remote tasks the graph created.
    async def debug_history(request: Request) -> JSONResponse:
        agent = agents.get(request.path_params["context_id"])
        return JSONResponse(agent.history if agent else [])

    app.router.routes.append(Route("/debug/history/{context_id}", debug_history))
    return app


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab05-strands-graph-supervisor", framework="strands", lab_id="lab05")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab05-strands-graph-supervisor")


if __name__ == "__main__":
    main()
