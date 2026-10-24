"""Trace Reporter (``lab10-ext-agent``): optional extension + two protocol-version interfaces."""

from __future__ import annotations

from typing import Any


def build_app(**kw: Any) -> Any:
    from a2a_labs_common.a2a.extensions import ExtensionEcho
    from a2a_labs_common.a2a.server import build_a2a_app
    from starlette.middleware import Middleware

    from lab10_secure.cards import ext_card
    from lab10_secure.executors import TraceSummaryExecutor

    card = ext_card()
    return build_a2a_app(
        card=card,
        executor=TraceSummaryExecutor(card, **kw),
        # A2A: the card lists two interfaces; the SECOND mount ("/v03") serves the same handler
        # with the v0.3 compatibility layer on, so an old client that follows it still works.
        extra_rpc_urls=["/v03"],
        enable_v0_3_compat=True,
        middleware=[Middleware(ExtensionEcho, card=card)],
    )


def main() -> None:
    from lab10_secure.servers.common import run_main

    run_main("lab10-ext-agent", "a2a-sdk", build_app)


if __name__ == "__main__":
    main()
