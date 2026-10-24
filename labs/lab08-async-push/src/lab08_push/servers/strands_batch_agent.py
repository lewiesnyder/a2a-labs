"""Batch agent (Strands behind our executor): push through ``build_a2a_app(push=True)``."""

from __future__ import annotations

from typing import Any


def build_app(**kw: Any) -> Any:
    from a2a_labs_common.a2a.strands import StrandsA2AExecutor

    from lab08_push.agents.batch import BatchAgent
    from lab08_push.cards import batch_card
    from lab08_push.servers.common import push_app

    return push_app(
        batch_card(),
        StrandsA2AExecutor(BatchAgent, artifact_name="summaries"),
        service="lab08-strands-batch-agent",
        **kw,
    )


def main() -> None:
    from lab08_push.servers.common import run_main

    run_main("lab08-strands-batch-agent", "strands", build_app)


if __name__ == "__main__":
    main()
