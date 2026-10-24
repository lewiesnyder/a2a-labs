"""Calendar Assistant (``lab10-secure-agent``): API key OR OAuth, extended card, consent."""

from __future__ import annotations

from typing import Any


def build_app(**kw: Any) -> Any:
    from a2a.server.context import ServerCallContext
    from a2a.server.tasks import InMemoryTaskStore
    from a2a.types import AgentCard
    from a2a_labs_common.a2a.push import make_push_url_validator
    from a2a_labs_common.a2a.server import build_a2a_app
    from a2a_labs_common.auth import ApiKeyBackend, BearerBackend, ChainBackend, auth_middleware

    from lab10_secure.cards import secure_card, secure_extended_card
    from lab10_secure.data import API_KEYS
    from lab10_secure.executors import CalendarExecutor
    from lab10_secure.idp_client import make_verifier
    from lab10_secure.tenancy import tenant_owner

    async def per_caller(_: AgentCard, ctx: ServerCallContext) -> AgentCard:
        # A2A: the extended card is computed PER CALLER. The admin skill only appears for tokens
        # that carry the agent:admin scope; everyone else sees the basic extended card.
        scopes = set(getattr(ctx.state.get("auth"), "scopes", []) or [])
        return secure_extended_card(admin="agent:admin" in scopes)

    return build_a2a_app(
        card=secure_card(),
        executor=CalendarExecutor(**kw),
        task_store=InMemoryTaskStore(owner_resolver=tenant_owner),
        push=True,  # the authenticated-push demo: config.authentication -> Authorization header
        push_url_validator=make_push_url_validator(),
        extended_card=secure_extended_card(),
        extended_card_modifier=per_caller,
        middleware=auth_middleware(
            ChainBackend(BearerBackend(make_verifier()), ApiKeyBackend(API_KEYS))
        ),
    )


def main() -> None:
    from lab10_secure.servers.common import run_main

    run_main("lab10-secure-agent", "a2a-sdk", build_app)


if __name__ == "__main__":
    main()
