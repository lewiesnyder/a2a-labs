"""Wiring for the squads and the gateways: lifespan tasks and the lab-only /agp/* endpoints."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from a2a.server.agent_execution import AgentExecutor
from a2a.types import AgentCard
from a2a_labs_common.a2a.extensions import ExtensionEcho
from a2a_labs_common.a2a.server import build_a2a_app
from a2a_labs_common.agp import AgpTable
from a2a_labs_common.agp_gateway import AgpGatewayExecutor, Announcer
from a2a_labs_common.config import endpoint, public_url
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from lab09_agp.cards import gateway_card, squad_card
from lab09_agp.squads import SQUADS


def ttl_s() -> float:
    return float(os.environ.get("AGP_TTL_S", "120"))


def reannounce_s() -> float:
    return float(os.environ.get("AGP_REANNOUNCE_S", "60"))


def _lifespan(*loops: Any) -> Any:
    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        tasks = [asyncio.create_task(loop()) for loop in loops]
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    return lifespan


def _control_routes(target: Any, table: AgpTable | None = None) -> list[Route]:
    """# CUSTOM, lab only: pause / resume announcing, and dump the AGP table."""

    async def pause(_: Request) -> JSONResponse:
        target.paused = True
        return JSONResponse({"paused": True})

    async def resume(_: Request) -> JSONResponse:
        target.paused = False
        return JSONResponse({"paused": False})

    routes = [
        Route("/agp/pause", pause, methods=["POST"]),
        Route("/agp/resume", resume, methods=["POST"]),
    ]
    if table is not None:

        async def dump(_: Request) -> JSONResponse:
            return JSONResponse(table.snapshot())

        routes.append(Route("/agp/table", dump))
    return routes


def squad_app(service: str, executor: AgentExecutor, *, parent: str | None = None) -> Starlette:
    squad = SQUADS[service]
    announcer = Announcer(
        name=service,
        parent_url=endpoint(parent or squad.parent),
        self_url=public_url(service),
        announcements=squad.announcements,
        interval_s=reannounce_s(),
    )
    app = build_a2a_app(
        card=squad_card(service), executor=executor, lifespan=_lifespan(announcer.run)
    )
    app.router.routes.extend(_control_routes(announcer))
    app.state.announcer = announcer
    return app


GATEWAYS = {
    # service: (parent service or None, decision question used by the non-AGP fallback)
    "lab09-root-gateway": (None, "domain"),
    "lab09-finance-gateway": ("lab09-root-gateway", "squad"),
    "lab09-travel-gateway": ("lab09-root-gateway", "squad"),
}


def gateway_app(service: str, *, decision: Any = None) -> Starlette:
    parent, question = GATEWAYS[service]
    card: AgentCard = gateway_card(service)
    table = AgpTable(ttl_s=ttl_s())
    executor = AgpGatewayExecutor(
        name=service,
        card=card,
        table=table,
        self_url=public_url(service),
        parent_url=endpoint(parent) if parent else None,
        question=question,
        decision=decision,
        reannounce_s=reannounce_s(),
    )
    app = build_a2a_app(
        card=card,
        executor=executor,
        middleware=[Middleware(ExtensionEcho, card=card)],
        lifespan=_lifespan(executor.run),
    )
    app.router.routes.extend(_control_routes(executor, table))
    app.state.executor, app.state.table = executor, table
    return app


def run_main(service: str, framework: str, build: Any) -> None:
    from a2a_labs_common.a2a.server import run
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry(service, framework=framework, lab_id="lab09")  # first call in main()
    run(build(), service=service)
