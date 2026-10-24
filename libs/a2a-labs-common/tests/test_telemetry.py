"""Unit tests: TracedExecutor + tap record spans/events with an in-memory exporter."""

from __future__ import annotations

import pytest
from a2a.helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import a2a_pb2
from a2a_labs_common.a2a.strands import StrandsA2AExecutor
from a2a_labs_common.telemetry import a2a_server
from a2a_labs_common.telemetry.a2a_server import TracedExecutor
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


class Q:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def enqueue_event(self, e: object) -> None:
        self.events.append(e)


class Agent:
    async def stream_async(self, prompt: str, **_: object):
        yield {"data": "a"}
        yield {"data": "b"}


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(a2a_server, "_tracer", provider.get_tracer("test"))
    return exporter


def _ctx() -> RequestContext:
    msg = new_text_message("hi")
    msg.role = a2a_pb2.ROLE_USER
    return RequestContext(
        ServerCallContext(),
        request=a2a_pb2.SendMessageRequest(message=msg),
        task_id="t1",
        context_id="c1",
    )


async def test_traced_executor_records_states_and_chunks(spans: InMemorySpanExporter) -> None:
    inner: AgentExecutor = StrandsA2AExecutor(lambda _c: Agent())
    q = Q()
    await TracedExecutor(inner, "demo").execute(_ctx(), q)  # type: ignore[arg-type]
    assert len(q.events) > 3  # forwarded unchanged
    (span,) = spans.get_finished_spans()
    assert span.name == "a2a.server.execute"
    assert span.attributes["a2a.task.final_state"] == "COMPLETED"
    transitions = [e for e in span.events if e.name == "a2a.task.state_change"]
    assert [t.attributes["to"] for t in transitions] == ["SUBMITTED", "WORKING", "COMPLETED"]
    chunks = [e for e in span.events if e.name == "a2a.artifact.chunk"]
    assert len(chunks) == 3 and chunks[-1].attributes["last_chunk"]


async def test_failure_marks_span_error(spans: InMemorySpanExporter) -> None:
    class Boom(AgentExecutor):
        async def execute(self, context, event_queue):  # type: ignore[no-untyped-def]
            raise RuntimeError("x")

        async def cancel(self, context, event_queue):  # type: ignore[no-untyped-def]
            return None

    with pytest.raises(RuntimeError):
        await TracedExecutor(Boom(), "demo").execute(_ctx(), Q())  # type: ignore[arg-type]
    (span,) = spans.get_finished_spans()
    assert span.status.status_code == trace.StatusCode.ERROR
