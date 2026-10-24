"""ADK coordinator: LLM transfer routing, and ADK's implicit per-remote tracking."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from a2a.types import TaskState
from a2a_labs_common.a2a.client import send_text
from a2a_labs_common.testing import asgi_client
from lab05_supervisor.prompts import ROUTING_TABLE


@pytest.mark.parametrize("case", ROUTING_TABLE[:4], ids=lambda c: c.prompt[:28])
async def test_the_coordinator_transfers_to_the_right_remote_specialist(
    serve_supervisor: Any, case: Any
) -> None:
    async with serve_supervisor("adk") as c:
        ctx = str(uuid.uuid4())
        res = await send_text(c, case.prompt, context_id=ctx)
        assert res.state == TaskState.TASK_STATE_COMPLETED and res.text
        async with asgi_client(c.app) as http:
            implicit = (await http.get(f"/debug/ledger/{ctx}")).json()
        # exactly one remote agent got the conversation, and ADK stamped its ids on the events
        assert len(implicit) == 1
        row = next(iter(implicit.values()))
        assert row["context_id"] and row["task_ids"]
