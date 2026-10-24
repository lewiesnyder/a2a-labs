"""ADK calendar helper (``lab10-adk-secure``) behind the bearer middleware."""

from __future__ import annotations

from typing import Any


def build_app() -> Any:
    from a2a_labs_common.a2a.server import build_a2a_app
    from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
    from google.adk.artifacts import InMemoryArtifactService
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService

    from lab10_secure.agents.helper import build_adk_helper
    from lab10_secure.cards import framework_card
    from lab10_secure.servers.common import bearer_middleware

    agent = build_adk_helper()
    runner = Runner(
        app_name=agent.name,
        agent=agent,
        session_service=InMemorySessionService(),
        artifact_service=InMemoryArtifactService(),
    )
    # CUSTOM: `to_a2a` builds its own handler and app, leaving no place for our middleware, and
    # its card would not carry the oauth2 scheme through; the same ADK executor on our server
    # stack does (DEV-58, like Lab 06's agent B).
    return build_a2a_app(
        card=framework_card("lab10-adk-secure", "ADK Calendar Helper", "ADK"),
        executor=A2aAgentExecutor(runner=runner, force_new_version=True),
        middleware=bearer_middleware(),
    )


def main() -> None:
    from lab10_secure.servers.common import run_main

    run_main("lab10-adk-secure", "adk", build_app)


if __name__ == "__main__":
    main()
