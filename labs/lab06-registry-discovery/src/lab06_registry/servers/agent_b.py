"""Translator B (ADK): signed with ``lab06-b``; its extended card (a private skill) needs a token.

Served with our server stack instead of ``to_a2a`` because ``to_a2a`` builds its own request
handler, which leaves no way to attach an extended card. The executor is still ADK's own
``A2aAgentExecutor`` (``force_new_version=True``, see Lab 01).
"""

from __future__ import annotations

import os
from typing import Any


def build_app() -> Any:
    from a2a_labs_common.a2a.server import build_a2a_app
    from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
    from google.adk.artifacts import InMemoryArtifactService
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from lab00_protocol.auth import AdminBearerBackend, ExtendedCardGuard, on_auth_error
    from starlette.middleware import Middleware
    from starlette.middleware.authentication import AuthenticationMiddleware

    from lab06_registry.agents.translators import build_adk_translator
    from lab06_registry.cards import translator_b_card, translator_b_extended_card
    from lab06_registry.servers.common import add_jwks, signed

    agent = build_adk_translator()
    runner = Runner(
        app_name=agent.name,
        agent=agent,
        session_service=InMemorySessionService(),
        artifact_service=InMemoryArtifactService(),
    )
    token = os.environ.get("LAB06_B_TOKEN", "lab06-b-token")  # dev token, Lab 10 does this properly
    app = build_a2a_app(
        card=signed(translator_b_card(), "lab06-b"),
        executor=A2aAgentExecutor(runner=runner, force_new_version=True),
        extended_card=translator_b_extended_card(),
        middleware=[
            Middleware(
                AuthenticationMiddleware, backend=AdminBearerBackend(token), on_error=on_auth_error
            ),
            Middleware(ExtendedCardGuard),
        ],
    )
    add_jwks(app, "lab06-b")
    return app


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab06-agent-b", framework="adk", lab_id="lab06")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab06-agent-b")


if __name__ == "__main__":
    main()
