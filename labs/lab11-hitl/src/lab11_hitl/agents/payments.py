"""Payments (a2a-sdk): the decision model scores the risk; risky totals need a human APPROVAL.

The human can approve, reject, or EDIT the proposal (a lower amount): the agent re-scores the new
amount and asks again if it is still risky. Small amounts never reach a human.
"""

from __future__ import annotations

import uuid
from typing import Any

from a2a.helpers import get_data_parts, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from a2a_labs_common.hitl import Option, Prompt, extract_answer, prompt_part
from a2a_labs_common.models import get_decision_model
from a2a_labs_common.models.decision import DecisionModel, Question

from lab11_hitl.data import APPROVAL_LIMIT

LEVELS = ["low", "medium", "high"]
RISKY_FROM = 1.5  # score >= 1.5 means "ask a person"


async def risk_score(amount: float, decision: DecisionModel | None = None) -> tuple[float, str]:
    band = "above" if amount > APPROVAL_LIMIT else "below"
    facts = (
        f"A payment of ${amount:g} for a trip. It is {band} the usual limit of ${APPROVAL_LIMIT}."
    )
    res = await (decision or get_decision_model()).decide(
        facts,
        {
            "risk": Question(
                type="score",
                instructions="How risky is this payment without a person's approval?",
                criteria=LEVELS,
            )
        },
    )
    return float(res.answers["risk"].score or 0.0), res.backend


class PaymentsExecutor(AgentExecutor):
    def __init__(self, decision: DecisionModel | None = None) -> None:
        self.decision = decision
        self.amounts: dict[str, float] = {}  # task id -> the amount currently proposed

    @staticmethod
    def _amount(context: RequestContext) -> float:
        assert context.message is not None
        for d in get_data_parts(context.message.parts):
            if isinstance(d, dict) and isinstance(d.get("payment"), dict):
                return float(d["payment"]["amount"])
        return 0.0

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.message is not None
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        u = TaskUpdater(event_queue, task.id, task.context_id)
        await u.start_work()
        waiting = task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        answer = extract_answer(context.message.parts) if waiting else None
        amount = self.amounts.get(task.id) if waiting else self._amount(context)
        if amount is None:  # the executor restarted while a person was thinking
            await u.failed(u.new_agent_message([new_text_part("Lost the proposal; resubmit.")]))
            return

        if answer is not None:
            if answer.decision == "reject":
                why = answer.text or "declined"
                await u.reject(u.new_agent_message([new_text_part(f"Payment rejected: {why}")]))
                self.amounts.pop(task.id, None)
                return
            if answer.decision == "edit" and answer.edit and "amount" in answer.edit:
                amount = float(answer.edit["amount"])  # the human changed the proposal
                self.amounts[task.id] = amount
            elif answer.decision == "approve":
                await self._pay(u, task.id, amount, "approved by a person")
                return
            # an edit (or an unreadable answer) falls through: re-score and maybe ask again

        score, backend = await risk_score(amount, self.decision)
        if score < RISKY_FROM:
            await self._pay(u, task.id, amount, f"auto-approved (risk {score:.1f}, {backend})")
            return
        self.amounts[task.id] = amount
        prompt = Prompt(
            kind="approval",
            prompt=f"Approve a payment of ${amount:g}? (risk score {score:.1f} of 2)",
            options=[
                Option(id="approve", label="Approve"),
                Option(id="reject", label="Reject"),
                Option(id="edit", label="Change the amount"),
            ],
            proposal={"amount": amount, "currency": "USD", "risk_score": round(score, 2)},
        )
        await u.requires_input(
            u.new_agent_message([new_text_part(prompt.prompt), prompt_part(prompt)])
        )

    async def _pay(self, u: TaskUpdater, task_id: str, amount: float, how: str) -> None:
        self.amounts.pop(task_id, None)
        ref = "PAY-" + uuid.uuid4().hex[:6].upper()
        data: dict[str, Any] = {"paid": amount, "ref": ref, "how": how}
        await u.add_artifact(
            [new_text_part(f"Paid ${amount:g} ({how}), ref {ref}"), new_data_part(data)],
            artifact_id="payment",
            name="payment",
        )
        await u.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.task_id and context.context_id
        self.amounts.pop(context.task_id, None)
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()
