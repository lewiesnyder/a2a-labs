"""Strands controller server: allowlisted ``call_specialist`` plus stateful specialist tools.

Implements: spec lab02 §2 (``lab02-strands-controller``).
"""

from __future__ import annotations


def build_app():  # type: ignore[no-untyped-def]
    from a2a_labs_common.a2a.server import build_a2a_app
    from a2a_labs_common.a2a.strands import StrandsA2AExecutor

    from lab02_tool.agents.controllers import build_strands_controller
    from lab02_tool.cards import controller_card

    return build_a2a_app(
        card=controller_card("lab02-strands-controller", "Travel Desk (Strands)"),
        executor=StrandsA2AExecutor(build_strands_controller, max_contexts=32),
    )


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab02-strands-controller", framework="strands", lab_id="lab02")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab02-strands-controller")


if __name__ == "__main__":
    main()
