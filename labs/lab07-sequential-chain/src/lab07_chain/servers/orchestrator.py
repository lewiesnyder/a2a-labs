"""Chain orchestrator server (``lab07-chain-orchestrator``): orchestrated mode."""

from __future__ import annotations

from typing import Any


def build_app(decision: Any = None, max_loops: int | None = None, services: Any = None) -> Any:
    from a2a_labs_common.a2a.server import build_a2a_app

    from lab07_chain.cards import orchestrator_card
    from lab07_chain.executors.chain_orchestrator import ChainOrchestrator

    return build_a2a_app(
        card=orchestrator_card(),
        executor=ChainOrchestrator(decision=decision, max_loops=max_loops, services=services),
    )


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab07-chain-orchestrator", framework="a2a-sdk", lab_id="lab07")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab07-chain-orchestrator")


if __name__ == "__main__":
    main()
