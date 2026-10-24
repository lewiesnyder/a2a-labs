"""Pure a2a-sdk server builder (spec 03 §3).

Implements: spec 03 §3. Uses only official constructs: ``DefaultRequestHandler`` and the
route factories. Wraps the executor with ``TracedExecutor`` and instruments the app.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

import grpc
import httpx
import uvicorn
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler, GrpcHandler, RequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes, create_rest_routes
from a2a.server.tasks import (
    BasePushNotificationSender,
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
    PushNotificationConfigStore,
    PushNotificationSender,
    TaskStore,
)
from a2a.types import AgentCard, a2a_pb2_grpc
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import BaseRoute

from a2a_labs_common.a2a.cards import REST_PREFIX
from a2a_labs_common.config import port, settings
from a2a_labs_common.telemetry import instrument_app
from a2a_labs_common.telemetry.a2a_server import TracedExecutor

PushSenderFactory = Callable[
    [httpx.AsyncClient, PushNotificationConfigStore], PushNotificationSender
]


def build_handler(
    *,
    card: AgentCard,
    executor: AgentExecutor,
    task_store: TaskStore | None = None,
    push: bool = False,
    push_config_store: PushNotificationConfigStore | None = None,
    push_sender_factory: PushSenderFactory | None = None,
    extended_card: AgentCard | None = None,
    push_url_validator: Callable[[str], Awaitable[bool]] | None = None,
) -> RequestHandler:
    store = push_config_store or (InMemoryPushNotificationConfigStore() if push else None)
    sender = None
    if push and store is not None:
        client = httpx.AsyncClient(timeout=10)
        sender = (push_sender_factory or BasePushNotificationSender)(client, store)
    return DefaultRequestHandler(
        agent_executor=TracedExecutor(executor, card.name),
        task_store=task_store or InMemoryTaskStore(),
        agent_card=card,
        push_config_store=store,
        push_sender=sender,
        extended_agent_card=extended_card,
        push_url_validator=push_url_validator,
    )


def build_a2a_app(
    *,
    card: AgentCard,
    executor: AgentExecutor,
    rest: bool = False,
    rest_prefix: str = REST_PREFIX,
    enable_v0_3_compat: bool | None = None,
    middleware: Sequence[Middleware] = (),
    handler: RequestHandler | None = None,
    **handler_kwargs: object,
) -> Starlette:
    """Agent card + JSON-RPC at ``/`` (+ REST at ``rest_prefix``), all on one handler."""
    compat = settings().enable_v03_compat if enable_v0_3_compat is None else enable_v0_3_compat
    h = handler or build_handler(card=card, executor=executor, **handler_kwargs)  # type: ignore[arg-type]
    routes: list[BaseRoute] = [
        # A2A: discovery is plain HTTP GET /.well-known/agent-card.json, not an RPC.
        *create_agent_card_routes(card),
        *create_jsonrpc_routes(h, rpc_url="/", enable_v0_3_compat=compat),
    ]
    if rest:
        routes += create_rest_routes(h, enable_v0_3_compat=compat, path_prefix=rest_prefix)
    app = Starlette(routes=routes, middleware=list(middleware))
    instrument_app(app)
    return app


async def serve_grpc(*, handler: RequestHandler, port_number: int) -> None:
    """The SDK's gRPC servicer for the same handler (Lab 00 only)."""
    server = grpc.aio.server()
    a2a_pb2_grpc.add_A2AServiceServicer_to_server(  # type: ignore[no-untyped-call]
        GrpcHandler(handler), server
    )
    server.add_insecure_port(f"0.0.0.0:{port_number}")
    await server.start()
    await server.wait_for_termination()


def run(app: Starlette, *, service: str) -> None:
    # log_config=None: logging goes through the OTel bridge set up by init_telemetry().
    uvicorn.run(app, host="0.0.0.0", port=port(service), log_config=None)
