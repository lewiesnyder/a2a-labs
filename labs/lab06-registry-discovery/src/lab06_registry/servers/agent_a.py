"""Translator A (Strands): an honest agent whose card is signed with the trusted key ``lab06-a``."""

from __future__ import annotations

from typing import Any


def build_app() -> Any:
    from a2a_labs_common.a2a.server import build_a2a_app
    from a2a_labs_common.a2a.strands import StrandsA2AExecutor

    from lab06_registry.agents.translators import build_strands_translator
    from lab06_registry.cards import translator_a_card
    from lab06_registry.servers.common import add_jwks, signed

    app = build_a2a_app(
        card=signed(translator_a_card(), "lab06-a"),
        executor=StrandsA2AExecutor(build_strands_translator),
    )
    add_jwks(app, "lab06-a")
    return app


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab06-agent-a", framework="strands", lab_id="lab06")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab06-agent-a")


if __name__ == "__main__":
    main()
