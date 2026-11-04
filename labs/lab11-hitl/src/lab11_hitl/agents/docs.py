"""Documents (a2a-sdk): needs the traveller's CONSENT to read the passport: ``AUTH_REQUIRED``."""

from __future__ import annotations

from a2a.helpers import new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from a2a_labs_common.hitl import Option, Prompt, extract_answer, prompt_part

PROMPT = Prompt(
    kind="consent",
    prompt="May I read your passport from your wallet to check the visa rules?",
    options=[Option(id="approve", label="Allow"), Option(id="reject", label="Deny")],
    proposal={"scope": "passport:read"},
)


class DocsExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.message is not None
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        u = TaskUpdater(event_queue, task.id, task.context_id)
        await u.start_work()
        waiting = task.status.state == TaskState.TASK_STATE_AUTH_REQUIRED
        answer = extract_answer(context.message.parts) if waiting else None
        if answer is None:
            # A2A: AUTH_REQUIRED is INPUT_REQUIRED's sibling for CONSENT. Same resume mechanics.
            await u.requires_auth(
                u.new_agent_message([new_text_part(PROMPT.prompt), prompt_part(PROMPT)])
            )
            return
        if answer.decision != "approve":
            why = answer.text or "declined"
            await u.reject(u.new_agent_message([new_text_part(f"No consent: not checked ({why})")]))
            return
        await u.add_artifact(
            [
                new_text_part("Passport read: valid until 2031, no visa needed for Portugal."),
                new_data_part({"visa_needed": False}),
            ],
            artifact_id="docs",
            name="docs",
        )
        await u.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.task_id and context.context_id
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()
