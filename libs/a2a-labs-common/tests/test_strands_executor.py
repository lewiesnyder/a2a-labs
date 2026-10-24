"""In-process tests for StrandsA2AExecutor (no LLM, no network)."""

from __future__ import annotations

from a2a.helpers import new_text_message
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import a2a_pb2

from a2a_labs_common.a2a.strands import StrandsA2AExecutor


class Queue:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def enqueue_event(self, event: object) -> None:
        self.events.append(event)


class FakeAgent:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def stream_async(self, prompt: str, **_: object):
        yield {"data": "Hello, "}
        yield {"event": "ignored"}
        if self.fail:
            raise RuntimeError("boom")
        yield {"data": prompt}


def _ctx(text: str, context_id: str = "c1") -> RequestContext:
    msg = new_text_message(text)
    msg.role = a2a_pb2.ROLE_USER  # A2A: requests come from the user
    msg.context_id = context_id
    req = a2a_pb2.SendMessageRequest(message=msg)
    return RequestContext(ServerCallContext(), request=req, task_id="t1", context_id=context_id)


def _kinds(q: Queue) -> list[str]:
    return [type(e).__name__ for e in q.events]


async def test_task_first_then_chunked_artifact_then_completed() -> None:
    q = Queue()
    await StrandsA2AExecutor(lambda _cid: FakeAgent()).execute(_ctx("world"), q)  # type: ignore[arg-type]
    assert _kinds(q)[0] == "Task"
    updates = [e for e in q.events if isinstance(e, a2a_pb2.TaskArtifactUpdateEvent)]
    assert [u.append for u in updates] == [False, True, True]
    assert updates[-1].last_chunk
    assert len({u.artifact.artifact_id for u in updates}) == 1
    last = q.events[-1]
    assert isinstance(last, a2a_pb2.TaskStatusUpdateEvent)
    assert last.status.state == a2a_pb2.TASK_STATE_COMPLETED


async def test_agent_failure_marks_task_failed() -> None:
    q = Queue()
    await StrandsA2AExecutor(lambda _cid: FakeAgent(fail=True)).execute(_ctx("x"), q)  # type: ignore[arg-type]
    last = q.events[-1]
    assert isinstance(last, a2a_pb2.TaskStatusUpdateEvent)
    assert last.status.state == a2a_pb2.TASK_STATE_FAILED


async def test_agents_are_per_context_with_lru_eviction() -> None:
    made: list[str] = []

    def factory(cid: str) -> FakeAgent:
        made.append(cid)
        return FakeAgent()

    ex = StrandsA2AExecutor(factory, max_contexts=1)
    for cid in ("a", "a", "b", "a"):
        await ex.execute(_ctx("hi", cid), Queue())  # type: ignore[arg-type]
    assert made == ["a", "b", "a"]
