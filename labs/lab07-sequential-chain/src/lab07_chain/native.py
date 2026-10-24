"""The same three-hop chain built with each framework's OWN constructs (spec lab07 §5).

* ADK: ``SequentialAgent`` of ``RemoteA2aAgent``s. Hand-off is IMPLICIT: ADK passes session
  events (conversation history), not artifacts, so a DataPart never travels by itself.
* LangGraph: three ``RemoteA2ANode``s in a line. Hand-off is explicit state, but those nodes pass
  TEXT only (the artifact text), so the outline arrives as its text rendering.
* Strands: a ``GraphBuilder`` of custom ``MultiAgentBase`` nodes (``A2AAgent`` cannot import on
  a2a-sdk 1.x, DEV-19). The previous node's output becomes the next node's input, as text.

None of these links hops with ``reference_task_ids``: that provenance is what the orchestrator adds.
"""

from __future__ import annotations

import operator
import time
from typing import Annotated, Any

from a2a_labs_common.a2a.remote import RemoteAgent, card_description, fetch_card_sync
from typing_extensions import TypedDict

HOPS = ["lab07-adk-planner", "lab07-strands-writer", "lab07-langgraph-editor"]
NAMES = ["planner", "writer", "editor"]


def writer_prompt(outline: str) -> str:
    return f"Write the article from this outline.\nOutline:\n{outline}"


# ── ADK ─────────────────────────────────────────────────────────────────────────────────────
def build_adk_chain() -> Any:
    from google.adk.agents import SequentialAgent
    from google.adk.agents.remote_a2a_agent import RemoteA2aAgent

    subs = []
    for name, service in zip(NAMES, HOPS, strict=True):
        card = fetch_card_sync(service)
        subs.append(
            RemoteA2aAgent(
                name=name, description=card_description(card), agent_card=card, use_legacy=False
            )
        )
    return SequentialAgent(name="chain", sub_agents=subs)


async def run_adk_chain(topic: str) -> list[tuple[str, str]]:
    """Run the SequentialAgent with an ADK Runner; return (author, text) per event."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    agent = build_adk_chain()
    sessions = InMemorySessionService()
    runner = Runner(app_name="lab07-native", agent=agent, session_service=sessions)
    session = await sessions.create_session(app_name="lab07-native", user_id="nb")
    out: list[tuple[str, str]] = []
    msg = types.Content(role="user", parts=[types.Part(text=topic)])
    async for ev in runner.run_async(user_id="nb", session_id=session.id, new_message=msg):
        text = "".join(
            p.text or "" for p in (ev.content.parts if ev.content else []) if not p.thought
        )
        if not text:
            continue
        # Streamed chunks arrive as partial events; a complete event carries the whole text.
        if out and out[-1][0] == ev.author:
            out[-1] = (ev.author, text if not ev.partial else out[-1][1] + text)
        else:
            out.append((ev.author, text))
    return out


class ChainState(TypedDict, total=False):
    topic: str
    outline: str
    draft: str
    final: str
    error: str | None
    progress: Annotated[list[str], operator.add]
    remote_tasks: dict[str, Any]


# ── LangGraph ───────────────────────────────────────────────────────────────────────────────
def build_langgraph_chain() -> Any:
    from a2a_labs_common.a2a.langgraph import RemoteA2ANode
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    g = StateGraph(ChainState)
    g.add_node("plan", RemoteA2ANode(HOPS[0], input_fn=lambda s: s["topic"], output_key="outline"))
    g.add_node(
        "write",
        RemoteA2ANode(HOPS[1], input_fn=lambda s: writer_prompt(s["outline"]), output_key="draft"),
    )
    g.add_node("edit", RemoteA2ANode(HOPS[2], input_fn=lambda s: s["draft"], output_key="final"))
    g.add_edge(START, "plan")
    g.add_edge("plan", "write")
    g.add_edge("write", "edit")
    g.add_edge("edit", END)
    return g.compile(checkpointer=InMemorySaver())


# ── Strands ─────────────────────────────────────────────────────────────────────────────────
def build_strands_chain(remotes: dict[str, RemoteAgent] | None = None) -> Any:
    from strands.agent import AgentResult
    from strands.multiagent.base import MultiAgentBase, MultiAgentResult, NodeResult, Status
    from strands.multiagent.graph import GraphBuilder
    from strands.telemetry.metrics import EventLoopMetrics

    remotes = remotes or {name: RemoteAgent(svc) for name, svc in zip(NAMES, HOPS, strict=True)}

    class Hop(MultiAgentBase):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.id = name

        async def invoke_async(
            self, task: Any, invocation_state: dict[str, Any] | None = None, **kw: Any
        ) -> MultiAgentResult:
            st = invocation_state if invocation_state is not None else {}
            started = time.time()
            prev = st.get("last", st["topic"])
            sent = writer_prompt(prev) if self.id == "writer" else prev
            reply = await remotes[self.id].ask(sent, scope=st["scope"])
            st["last"] = reply.text
            st.setdefault("sent", {})[self.id] = sent
            result = AgentResult(
                stop_reason="end_turn",
                message={"role": "assistant", "content": [{"text": reply.text}]},
                metrics=EventLoopMetrics(),
                state={},
            )
            took = int((time.time() - started) * 1000)
            node = NodeResult(result=result, execution_time=took, status=Status.COMPLETED)
            return MultiAgentResult(status=Status.COMPLETED, results={self.id: node})

    b = GraphBuilder()
    for name in NAMES:
        b.add_node(Hop(name), name)
    b.add_edge("planner", "writer")
    b.add_edge("writer", "editor")
    b.set_entry_point("planner")
    return b.build()


async def run_strands_chain(topic: str, scope: str = "native") -> tuple[str, dict[str, str]]:
    """Run the Graph; return (final text, what each hop was actually sent)."""
    state: dict[str, Any] = {"topic": topic, "scope": scope}
    await build_strands_chain().invoke_async(topic, invocation_state=state)
    return str(state["last"]), dict(state["sent"])
