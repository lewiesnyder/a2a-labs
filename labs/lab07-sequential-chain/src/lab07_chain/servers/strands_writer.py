"""Writer server (Strands): streams the ``draft`` artifact; understands the outline DataPart."""

from __future__ import annotations

from typing import Any


def build_app() -> Any:
    from a2a_labs_common.a2a.relay import RelayExecutor
    from a2a_labs_common.a2a.server import build_a2a_app
    from a2a_labs_common.a2a.strands import StrandsA2AExecutor

    from lab07_chain.agents.writer import build_writer
    from lab07_chain.cards import writer_card
    from lab07_chain.executors.wrappers import DataAsTextExecutor

    inner = StrandsA2AExecutor(build_writer, artifact_name="draft")
    return build_a2a_app(
        card=writer_card(),
        executor=RelayExecutor(DataAsTextExecutor(inner), service="lab07-strands-writer"),
    )


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab07-strands-writer", framework="strands", lab_id="lab07")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab07-strands-writer")


if __name__ == "__main__":
    main()
