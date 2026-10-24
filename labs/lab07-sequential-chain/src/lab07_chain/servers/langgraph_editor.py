"""Editor server (LangGraph): ``final`` (text) and ``review`` (DataPart) artifacts."""

from __future__ import annotations

from typing import Any


def build_app() -> Any:
    from a2a.helpers import new_data_part
    from a2a_labs_common.a2a.langgraph import LangGraphA2AExecutor
    from a2a_labs_common.a2a.relay import RelayExecutor
    from a2a_labs_common.a2a.server import build_a2a_app

    from lab07_chain.agents.editor import build_graph
    from lab07_chain.cards import editor_card

    def extra(values: dict[str, Any]) -> list[tuple[str, list[Any]]]:
        return [("review", [new_data_part(values["review"])])]

    executor = LangGraphA2AExecutor(
        build_graph(),
        agent_name="editor",
        output_key="final",
        artifact_name="final",
        extra_artifacts=extra,
    )
    return build_a2a_app(
        card=editor_card(),
        executor=RelayExecutor(executor, service="lab07-langgraph-editor"),
    )


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab07-langgraph-editor", framework="langgraph", lab_id="lab07")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab07-langgraph-editor")


if __name__ == "__main__":
    main()
