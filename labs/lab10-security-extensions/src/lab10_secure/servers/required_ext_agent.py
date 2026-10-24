"""Budgeted Summarizer (``lab10-required-ext-agent``): a REQUIRED extension."""

from __future__ import annotations

from typing import Any


def build_app() -> Any:
    from a2a_labs_common.a2a.extensions import ExtensionEcho
    from a2a_labs_common.a2a.server import build_a2a_app
    from starlette.middleware import Middleware

    from lab10_secure.cards import required_ext_card
    from lab10_secure.executors import CostBudgetExecutor

    card = required_ext_card()
    return build_a2a_app(
        card=card,
        executor=CostBudgetExecutor(card),
        middleware=[Middleware(ExtensionEcho, card=card)],
    )


def main() -> None:
    from lab10_secure.servers.common import run_main

    run_main("lab10-required-ext-agent", "a2a-sdk", build_app)


if __name__ == "__main__":
    main()
