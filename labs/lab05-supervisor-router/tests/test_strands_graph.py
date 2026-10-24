"""Strands Graph supervisor: deterministic edges after a decision-model classification."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from a2a.types import TaskState
from a2a_labs_common.a2a.client import send_text
from a2a_labs_common.testing import asgi_client
from lab05_supervisor.prompts import CURRENCY, ROUTING_TABLE

INTENT = {CURRENCY: "currency", "Weather Reporter": "weather", "City Facts": "facts"}


@pytest.mark.parametrize("case", ROUTING_TABLE, ids=lambda c: c.prompt[:28])
async def test_the_graph_follows_the_edge_for_the_classified_intent(
    serve_supervisor: Any, case: Any
) -> None:
    async with serve_supervisor("strands") as c:
        ctx = str(uuid.uuid4())
        res = await send_text(c, case.prompt, context_id=ctx)
        assert res.state == TaskState.TASK_STATE_COMPLETED and res.text
        async with asgi_client(c.app) as http:
            history = (await http.get(f"/debug/history/{ctx}")).json()
        taken = {d["node"] for d in history[-1]["delegations"]}
        assert taken == {INTENT[a] for a in case.single_route}


async def test_a_remote_question_is_relayed_and_the_next_message_continues_that_task(
    serve_supervisor: Any,
) -> None:
    """The graph has no interrupt: the question is plain text, and the classifier prefers the
    paused remote task over asking the decision model again."""
    async with serve_supervisor("strands") as c:
        ctx = str(uuid.uuid4())
        first = await send_text(c, "Convert 100 USD", context_id=ctx)
        assert "needs more information" in first.text
        second = await send_text(c, "JPY", context_id=ctx)
        assert second.state == TaskState.TASK_STATE_COMPLETED and "JPY" in second.text
        async with asgi_client(c.app) as http:
            history = (await http.get(f"/debug/history/{ctx}")).json()
        assert history[-1]["reason"] == "remote task waiting for input"
        first_task, second_task = (h["delegations"][0]["task_id"] for h in history)
        assert first_task == second_task  # the SAME remote task was continued
