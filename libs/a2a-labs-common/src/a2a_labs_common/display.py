"""Rich renderers for notebooks (spec 03 §6, 07).

Implements: spec 03 §6. Every helper prints through ``rich`` so output reads the same in
Jupyter and in a terminal.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any
from urllib.parse import quote

from a2a.types import AgentCard, StreamResponse, Task
from google.protobuf.json_format import MessageToDict
from opentelemetry import trace
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from a2a_labs_common.a2a import events
from a2a_labs_common.a2a.client import result_text
from a2a_labs_common.a2a.methods import METHOD_CROSSWALK
from a2a_labs_common.a2a.states import state_name
from a2a_labs_common.config import settings
from a2a_labs_common.models.settings import describe_models

console = Console()
_STATE_STYLE = {
    "COMPLETED": "green",
    "FAILED": "red",
    "CANCELED": "yellow",
    "REJECTED": "red",
    "INPUT_REQUIRED": "magenta",
    "AUTH_REQUIRED": "magenta",
    "WORKING": "cyan",
}


def table(rows: Sequence[dict[str, Any]], title: str | None = None) -> None:
    if not rows:
        console.print("(no rows)")
        return
    t = Table(title=title, show_lines=False)
    for col in rows[0]:
        t.add_column(str(col))
    for row in rows:
        t.add_row(*(str(row.get(c, "")) for c in rows[0]))
    console.print(t)


def show_models() -> None:
    table(describe_models(), "Model roles")


def _badge(state: str) -> str:
    return f"[bold {_STATE_STYLE.get(state, 'white')}]{state}[/]"


def show_card(card: AgentCard) -> None:
    ifaces = Table("binding", "version", "url")
    for i in card.supported_interfaces:
        ifaces.add_row(i.protocol_binding, i.protocol_version, i.url)
    caps = card.capabilities
    flags = "  ".join(
        f"{'✔' if v else '✘'} {k}"
        for k, v in {
            "streaming": caps.streaming,
            "pushNotifications": caps.push_notifications,
            "extendedAgentCard": caps.extended_agent_card,
        }.items()
    )
    skills = Table("id", "name", "tags")
    for s in card.skills:
        skills.add_row(s.id, s.name, ", ".join(s.tags))
    exts = "\n".join(f"{e.uri} (required={e.required})" for e in caps.extensions) or "none"
    console.print(
        Panel(
            Columns([ifaces, skills]),
            title=f"{card.name} v{card.version}",
            subtitle=card.description,
        )
    )
    console.print(flags)
    console.print(f"extensions: {exts}")


def compare_cards(cards: Sequence[AgentCard]) -> None:
    t = Table("name", "bindings", "streaming", "push", "skills")
    for c in cards:
        t.add_row(
            c.name,
            ",".join(i.protocol_binding for i in c.supported_interfaces),
            str(c.capabilities.streaming),
            str(c.capabilities.push_notifications),
            ",".join(s.id for s in c.skills),
        )
    console.print(t)


def show_event(ev: StreamResponse) -> None:
    k = events.kind(ev)
    stamp = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
    state = events.state_of(ev)
    ident = {
        "task": ev.task.id,
        "status_update": ev.status_update.task_id,
        "artifact_update": ev.artifact_update.task_id,
    }.get(k, "")[:8]
    text = events.event_text(ev)[:60]
    console.print(
        f"[dim][{stamp}][/] {k:<15} task={ident}… {_badge(state) if state else ''} {text!r}"
        if text
        else f"[dim][{stamp}][/] {k:<15} task={ident}… {_badge(state) if state else ''}"
    )


def show_task(task: Task) -> None:
    state = state_name(task.status.state)
    body = [f"state: {_badge(state)}   history: {len(task.history)} message(s)"]
    for a in task.artifacts:
        body.append(f"artifact [bold]{a.name or a.artifact_id}[/]: {result_text(task)[:200]!r}")
    console.print(Panel("\n".join(body), title=f"Task {task.id[:8]}…"))


def show_wire(request: Any, response: Any) -> None:
    def js(x: Any) -> Syntax:
        d = x if isinstance(x, (dict, list)) else MessageToDict(x)
        return Syntax(json.dumps(d, indent=2), "json", background_color="default")

    console.print(
        Columns([Panel(js(request), title="request"), Panel(js(response), title="response")])
    )


def mermaid(src: str) -> None:
    from IPython.display import Markdown, display

    display(Markdown(f"```mermaid\n{src.strip()}\n```"))  # type: ignore[no-untyped-call]


def crosswalk_table() -> None:
    t = Table(
        "★",
        "Operation",
        "Legacy v0.1",
        "v0.3",
        "v1.0 JSON-RPC/gRPC",
        "v1.0 REST",
        title="A2A operation crosswalk",
    )
    for m in METHOD_CROSSWALK:
        t.add_row(
            "★" if m.requested else "",
            m.operation,
            m.legacy or "—",
            m.v03 or "—",
            m.v10_jsonrpc,
            m.v10_rest,
        )
    console.print(t)


@contextmanager
def cell_span(title: str) -> Iterator[trace.Span]:
    """Root span of a notebook cell; the whole multi-agent trace hangs under it."""
    tracer = trace.get_tracer("a2a_labs_common.notebook")
    with tracer.start_as_current_span(
        f"notebook.cell {title}",
        attributes={"a2a_labs.lab_id": settings().lab_id, "a2a_labs.cell": title},
    ) as span:
        yield span


def trace_url(trace_id: str) -> str:
    left = {
        "datasource": "tempo",
        "queries": [{"refId": "A", "queryType": "traceql", "query": trace_id}],
    }
    return "http://localhost:3000/explore?left=" + quote(json.dumps(left))


def trace_link(span_or_trace_id: trace.Span | str) -> None:
    tid = (
        span_or_trace_id
        if isinstance(span_or_trace_id, str)
        else f"{span_or_trace_id.get_span_context().trace_id:032x}"
    )
    url = trace_url(tid)
    try:
        from IPython.display import HTML, display

        display(  # type: ignore[no-untyped-call]
            HTML(  # type: ignore[no-untyped-call]
                f'🔭 <a href="{url}" target="_blank">Open trace {tid[:8]}… in Grafana</a>'
            )
        )
    except ImportError:  # pragma: no cover
        console.print(url)
