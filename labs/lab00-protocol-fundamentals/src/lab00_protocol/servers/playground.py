"""Playground server: one module, three variants (spec lab00 §4).

Run: ``python -m lab00_protocol.servers.playground`` (``PLAYGROUND_VARIANT=full|nostream|nopush``)
or the console script ``lab00-playground``.
"""

from __future__ import annotations

import os
from typing import cast

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware


def build_app(variant: str = "full", *, step_delay: float | None = None):  # type: ignore[no-untyped-def]
    from a2a_labs_common.a2a.server import build_a2a_app, build_handler, grpc_lifespan
    from a2a_labs_common.config import port

    from lab00_protocol import cards
    from lab00_protocol.auth import AdminBearerBackend, ExtendedCardGuard, on_auth_error
    from lab00_protocol.executors.playground import PlaygroundExecutor
    from lab00_protocol.executors.tck import TckExecutor

    service = cards.SERVICES[variant]
    card = cards.playground_card(service, cast(cards.Variant, variant))
    extended = cards.playground_extended_card(service) if variant == "full" else None
    store = InMemoryTaskStore()  # shared by every transport so tasks are visible across them
    handler = build_handler(
        card=card,
        executor=TckExecutor(PlaygroundExecutor(store, service=service, step_delay=step_delay)),
        task_store=store,
        push=variant != "nopush",
        extended_card=extended,
        # Strict media-type checking (-> ContentTypeNotSupportedError) is on for the variants that
        # demonstrate errors. `full` keeps the SDK default because TCK CORE-SEND-003 requires an
        # unsupported media type to be *accepted* (DEV-10).
        validate_input_modes=variant != "full",
    )
    lifespan = (
        grpc_lifespan(
            lambda app: cast(DefaultRequestHandler, app.state.handler), port(f"{service}-grpc")
        )
        if variant == "full"
        else None
    )
    return build_a2a_app(
        card=card,
        handler=handler,
        rest=variant == "full",
        enable_v0_3_compat=True,
        lifespan=lifespan,
        middleware=[
            Middleware(
                AuthenticationMiddleware,
                backend=AdminBearerBackend(cards.admin_token()),
                on_error=on_auth_error,
            ),
            *([Middleware(ExtendedCardGuard)] if extended else []),
        ],
    )


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    variant = os.getenv("PLAYGROUND_VARIANT", "full")
    from lab00_protocol.cards import SERVICES

    service = SERVICES[variant]
    init_telemetry(service, framework="a2a-sdk", lab_id="lab00")  # first call in main()
    from a2a_labs_common.a2a.server import run

    run(build_app(variant), service=service)


if __name__ == "__main__":
    main()
