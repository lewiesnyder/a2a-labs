"""SDK echo server: a pure a2a-sdk agent that ALWAYS answers with a single Message.

Pattern 1, the Message side: stateless, immediate, nothing to track. No LLM.
Implements: spec lab01 §2.4.
"""

from __future__ import annotations

from a2a.helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import Role


class EchoExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        text = context.get_user_input()
        # A2A: a Message-only reply is the ONLY event; emitting a Task as well would be an error.
        await event_queue.enqueue_event(
            new_text_message(f"[echo] {text}", role=Role.ROLE_AGENT, context_id=context.context_id)
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Nothing runs long enough to cancel; there is never a task.
        raise NotImplementedError("sdk-echo has no tasks to cancel")


def build_app():  # type: ignore[no-untyped-def]
    from a2a_labs_common.a2a.server import build_a2a_app

    from lab01_direct.cards import sdk_echo_card

    return build_a2a_app(card=sdk_echo_card(), executor=EchoExecutor())


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab01-sdk-echo", framework="a2a-sdk", lab_id="lab01")  # first call in main()
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab01-sdk-echo")


if __name__ == "__main__":
    main()
