"""Server wiring for Lab 11: delegate apps, the orchestrator with its reaper, the inbox."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from a2a.server.agent_execution import AgentExecutor
from a2a_labs_common.a2a.push import DEFAULT_ALLOWED_HOSTS, make_push_url_validator
from a2a_labs_common.a2a.server import build_a2a_app
from a2a_labs_common.config import public_url
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from lab11_hitl.cards import delegate_card, orchestrator_card


def delegate_app(service: str, executor: AgentExecutor) -> Starlette:
    return build_a2a_app(card=delegate_card(service), executor=executor)


def orchestrator_app(*, decision: Any = None, **kw: Any) -> Starlette:
    from lab11_hitl.orchestrator import TripDeskExecutor

    card = orchestrator_card()
    executor = TripDeskExecutor(
        card=card,
        self_url=public_url("lab11-orchestrator"),
        decision=decision,
        deadline_s=float(os.environ.get("HITL_DEADLINE_S", "300")),
        reaper_s=float(os.environ.get("HITL_REAPER_S", "2")),
        **kw,
    )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        reaper = asyncio.create_task(executor.run_reaper())
        try:
            yield
        finally:
            reaper.cancel()
            await asyncio.gather(reaper, return_exceptions=True)

    app = build_a2a_app(
        card=card,
        executor=executor,
        push=True,  # the async scenario: INPUT_REQUIRED is PUSHED to the inbox
        # the inbox is where this agent pushes, whatever PUSH_ALLOWED_HOSTS says (DEV-54 + inbox)
        push_url_validator=make_push_url_validator(
            os.environ.get("PUSH_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS) + ",lab11-inbox"
        ),
        lifespan=lifespan,
    )

    async def ledger(request: Request) -> JSONResponse:  # CUSTOM, lab only (like Lab 05's /debug)
        rows = executor.ledger(request.path_params["task_id"])
        return JSONResponse(
            rows if rows is not None else {"error": "unknown task"}, 200 if rows else 404
        )

    app.router.routes.append(Route("/hitl/ledger/{task_id}", ledger))
    app.state.executor = executor
    return app


def run_main(service: str, framework: str, build: Any) -> None:
    from a2a_labs_common.a2a.server import run
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry(service, framework=framework, lab_id="lab11")  # first call in main()
    run(build(), service=service)
