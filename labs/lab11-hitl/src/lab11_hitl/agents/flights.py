"""Flights (a2a-sdk): asks a ``choice`` question (seat), unless the request already says."""

from __future__ import annotations

from a2a.helpers import new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from a2a_labs_common.hitl import Option, Prompt, extract_answer, prompt_part

from lab11_hitl.data import FLIGHT_PRICE, SEATS

PROMPT = Prompt(
    kind="choice",
    prompt="Which seat do you want on the Lisbon flight?",
    options=[Option(id=i, label=label) for i, label in SEATS],
)


class FlightsExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.message is not None
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)  # A2A: the Task is always the first event
        u = TaskUpdater(event_queue, task.id, task.context_id)
        await u.start_work()
        text = context.get_user_input().lower()
        resumed = task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        answer = extract_answer(context.message.parts) if resumed else None

        if answer is not None and answer.decision == "reject":
            await u.reject(
                u.new_agent_message(
                    [new_text_part(f"Flight booking declined: {answer.text or 'no reason'}")]
                )
            )
            return
        seat = answer.choice if answer else next((s for s, _ in SEATS if s in text), None)
        if seat not in dict(SEATS):
            # A2A: INTERRUPTED. The task stays open; the client resumes it with the same task_id.
            await u.requires_input(
                u.new_agent_message([new_text_part(PROMPT.prompt), prompt_part(PROMPT)])
            )
            return
        await u.add_artifact(
            [
                new_text_part(
                    f"Flight to Lisbon, seat 14{'C' if seat == 'aisle' else 'A'} ({seat})"
                ),
                new_data_part({"price": FLIGHT_PRICE, "seat": seat}),
            ],
            artifact_id="flights",
            name="flights",
        )
        await u.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.task_id and context.context_id
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()
