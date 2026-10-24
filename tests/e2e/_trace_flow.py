"""The notebook-like flow whose trace the e2e test inspects. Runs in its own process because
OpenTelemetry's global provider can be set only once per process (like a fresh notebook kernel).
Prints the trace id on the last line."""

from __future__ import annotations

import asyncio

from a2a.types import SubscribeToTaskRequest
from a2a_labs_common import display
from a2a_labs_common.a2a import events
from a2a_labs_common.a2a.client import connect, stream_text
from a2a_labs_common.telemetry import init_telemetry


async def main() -> None:
    tel = init_telemetry("notebook-e2e", framework="notebook", lab_id="lab00")
    with display.cell_span("e2e trace connectivity") as span:
        async with await connect("lab00-protocol-agent", streaming=True) as c:
            task_id = None
            async for ev in stream_text(c, "slow 3"):
                task_id = ev.task.id if events.kind(ev) == "task" else task_id
                if events.kind(ev) == "status_update":
                    break  # drop the stream, then re-attach
            _ = [e async for e in c.client.subscribe(SubscribeToTaskRequest(id=task_id))]
        trace_id = f"{span.get_span_context().trace_id:032x}"
    tel.flush()
    print(trace_id)


asyncio.run(main())
