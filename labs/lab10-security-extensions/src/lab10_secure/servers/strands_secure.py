"""Strands calendar helper (``lab10-strands-secure``) behind the same bearer middleware."""

from __future__ import annotations

from typing import Any


def build_app() -> Any:
    from a2a_labs_common.a2a.server import build_a2a_app
    from a2a_labs_common.a2a.strands import StrandsA2AExecutor

    from lab10_secure.agents.helper import build_strands_helper
    from lab10_secure.cards import framework_card
    from lab10_secure.servers.common import bearer_middleware

    return build_a2a_app(
        card=framework_card("lab10-strands-secure", "Strands Calendar Helper", "Strands"),
        executor=StrandsA2AExecutor(build_strands_helper),
        middleware=bearer_middleware(),
    )


def main() -> None:
    from lab10_secure.servers.common import run_main

    run_main("lab10-strands-secure", "strands", build_app)


if __name__ == "__main__":
    main()
