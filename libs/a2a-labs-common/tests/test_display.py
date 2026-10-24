"""Smoke tests for the notebook renderers (they must not raise)."""

from __future__ import annotations

from a2a.helpers import new_text_message
from a2a.types import StreamResponse, Task, TaskState, TaskStatus
from a2a_labs_common import display
from a2a_labs_common.a2a import cards
from opentelemetry.sdk.trace import TracerProvider


def _card():  # type: ignore[no-untyped-def]
    return cards.build_card(
        service="lab00-protocol-agent",
        name="Demo",
        description="A demo agent for display",
        skills=[cards.skill("a", "A", "d", ["t"], ["e"])],
    )


def test_renderers_do_not_raise() -> None:
    display.show_models()
    display.table([{"a": 1, "b": 2}])
    display.table([])
    display.show_card(_card())
    display.compare_cards([_card(), _card()])
    display.crosswalk_table()
    task = Task(
        id="abcdef012345", context_id="c", status=TaskStatus(state=TaskState.TASK_STATE_WORKING)
    )
    display.show_task(task)
    display.show_event(StreamResponse(task=task))
    display.show_event(StreamResponse(message=new_text_message("hello")))
    display.show_wire({"a": 1}, task)


def test_cell_span_and_trace_link(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from opentelemetry import trace

    monkeypatch.setattr(trace, "get_tracer", TracerProvider().get_tracer)
    with display.cell_span("demo") as span:
        assert display.trace_link(span) is None
    tid = f"{span.get_span_context().trace_id:032x}"
    assert "explore" in display.trace_url(tid) and tid in display.trace_url(tid).replace("%22", "")
