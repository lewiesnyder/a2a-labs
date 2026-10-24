"""Notebook-like flow for the nested trace test: client -> Concierge -> Trip Planner."""

from __future__ import annotations

import asyncio

from a2a_labs_common import display
from a2a_labs_common.a2a.client import connect, send_text
from a2a_labs_common.telemetry import init_telemetry


async def main() -> None:
    tel = init_telemetry("notebook-e2e-nested", framework="notebook", lab_id="lab04")
    with display.cell_span("e2e nested trace") as span:
        async with await connect("lab04-langgraph-orchestrator", streaming=False) as c:
            first = await send_text(
                c, "Plan a trip from Chicago to Lisbon, 12-16 May, budget $2000, 2 travelers"
            )
            if first.state is not None and first.task is not None and first.task.status.state == 6:
                await send_text(c, "yes", task_id=first.task_id, context_id=first.context_id)
        trace_id = f"{span.get_span_context().trace_id:032x}"
    tel.flush()
    print(trace_id)


asyncio.run(main())
