"""The Strands Graph supervisor: deterministic routing with conditional edges.

    classify --(intent == currency)--> currency --+
             --(intent == weather)---> weather  --+--> summarize
             --(intent == facts)-----> facts    --+
             --(intent == none)------> answer   --+

The edges are plain Python predicates over graph state, so the routing is as inspectable as it
gets: no prompt, no sampling. The only fuzzy step is the ``classify`` node, which asks the
DECISION model the same ``choice`` question as the LangGraph supervisor.

Every node is a ``MultiAgentBase`` (the official extension point of ``GraphBuilder``): the
classifier calls the decision model, the specialist nodes call a remote A2A agent through
``RemoteAgent``. Strands' own ``A2AAgent`` cannot be used because it imports a2a-sdk 0.3 types
(DEV-1), so this A2A node is ``# CUSTOM``.

Limits, on purpose, to contrast with the LangGraph supervisor: ONE specialist per request (no
fan-out), and a remote INPUT_REQUIRED is relayed as text instead of pausing the supervisor.
Implements: spec lab05 §5.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from a2a_labs_common.a2a.remote import RemoteAgent
from a2a_labs_common.models import get_decision_model, get_strands_model
from a2a_labs_common.models.decision import DecisionModel, Question
from strands import Agent
from strands.agent import AgentResult
from strands.multiagent.base import MultiAgentBase, MultiAgentResult, NodeResult, Status
from strands.multiagent.graph import GraphBuilder, GraphState
from strands.telemetry.metrics import EventLoopMetrics

from lab05_supervisor.discovery import Directory

NONE = "none"
# graph node id -> specialist service. Node ids are fixed because edges name them; what the
# classifier chooses between (names, descriptions) still comes from the Agent Cards.
NODES = {"currency": "lab05-currency", "weather": "lab05-weather", "facts": "lab05-facts"}


def _result(node_id: str, text: str, started: float) -> MultiAgentResult:
    """Wrap text as the result of a custom node (Strands expects an AgentResult inside)."""
    agent_result = AgentResult(
        stop_reason="end_turn",
        message={"role": "assistant", "content": [{"text": text}]},
        metrics=EventLoopMetrics(),
        state={},
    )
    took = int((time.time() - started) * 1000)
    node = NodeResult(result=agent_result, execution_time=took, status=Status.COMPLETED)
    return MultiAgentResult(
        status=Status.COMPLETED, results={node_id: node}, execution_time=took, execution_count=1
    )


def node_text(state: GraphState, node_id: str) -> str:
    """The text a custom node produced (read back from the graph state by edges and summarize)."""
    res = state.results.get(node_id)
    if res is None:
        return ""
    return str(res).removeprefix(f"{node_id}: ").strip()


class ClassifyNode(MultiAgentBase):
    """Decision-model routing. A remote task waiting for the user's answer wins over Nimble."""

    def __init__(
        self, directory: Directory, decision: DecisionModel, remotes: dict[str, RemoteAgent]
    ) -> None:
        super().__init__()
        self.id = "classify"
        self.directory, self.decision, self.remotes = directory, decision, remotes
        self.last: dict[str, Any] = {}  # the decision, for display

    async def invoke_async(
        self, task: Any, invocation_state: dict[str, Any] | None = None, **kwargs: Any
    ) -> MultiAgentResult:
        started = time.time()
        state = invocation_state or {}
        scope, request = state["context_id"], state["request"]
        for node_id, remote in self.remotes.items():
            if remote.pending_task(scope):  # continue the paused remote task, do not re-route
                self.last = {"intent": node_id, "reason": "remote task waiting for input"}
                return _result(self.id, node_id, started)
        specialists = {s.service: s for s in await self.directory.discover()}
        options = {
            node_id: specialists[svc].criteria
            for node_id, svc in NODES.items()
            if svc in specialists
        }
        options[NONE] = "No specialist fits this request; answer it directly."
        res = await self.decision.decide(
            f"User request: {request}",
            {
                "route": Question(
                    type="choice",
                    instructions="Which specialist should handle this request?",
                    criteria=options,
                )
            },
        )
        answer = res.answers["route"]
        choice = answer.choice or NONE
        probs = answer.probabilities
        intent = choice if choice in options else NONE
        self.last = {"intent": intent, "probabilities": probs, "options": list(options)}
        return _result(self.id, intent, started)


class A2ANode(MultiAgentBase):
    """# CUSTOM: delegates the user's request to one remote A2A agent."""

    def __init__(self, node_id: str, remote: RemoteAgent) -> None:
        super().__init__()
        self.id, self.remote = node_id, remote

    async def invoke_async(
        self, task: Any, invocation_state: dict[str, Any] | None = None, **kwargs: Any
    ) -> MultiAgentResult:
        started = time.time()
        state = invocation_state or {}
        # The graph's `task` is a composite of the request and earlier nodes' output; the remote
        # specialist should see only what the user said.
        reply = await self.remote.ask(state["request"], scope=state["context_id"])
        if reply.status == "needs_input":
            text = f"The {self.id} specialist needs more information: {reply.text}"
        elif reply.status == "error":
            text = f"The {self.id} specialist is unavailable ({reply.text})"
        else:
            text = reply.text
        state.setdefault("outputs", {})[self.id] = text
        state.setdefault("delegations", []).append(
            {
                "node": self.id,
                "status": reply.status,
                "task_id": reply.task_id,
                "context_id": reply.context_id,
            }
        )
        return _result(self.id, text, started)


class AnswerNode(MultiAgentBase):
    """The `none` branch: a local quick-model Strands Agent answers when no specialist fits."""

    def __init__(self) -> None:
        super().__init__()
        self.id = "answer"
        self.agent = Agent(
            name="answer",
            model=get_strands_model("quick"),
            system_prompt="You are a travel-desk supervisor. No specialist fits; answer briefly.",
            callback_handler=None,
        )

    async def invoke_async(
        self, task: Any, invocation_state: dict[str, Any] | None = None, **kwargs: Any
    ) -> MultiAgentResult:
        started = time.time()
        state = invocation_state or {}
        text = str(await self.agent.invoke_async(state["request"])).strip()
        state.setdefault("outputs", {})[self.id] = text
        return _result(self.id, text, started)


class SummarizeNode(MultiAgentBase):
    """Deterministic: relays whichever branch ran (the routing, not the prose, is the lesson)."""

    def __init__(self) -> None:
        super().__init__()
        self.id = "summarize"

    async def invoke_async(
        self, task: Any, invocation_state: dict[str, Any] | None = None, **kwargs: Any
    ) -> MultiAgentResult:
        started = time.time()
        outputs = (invocation_state or {}).get("outputs", {})
        return _result(self.id, next(iter(outputs.values()), ""), started)


def build_graph(
    scope: str,
    *,
    directory: Directory,
    decision: DecisionModel,
    remotes: dict[str, RemoteAgent],
    classify: ClassifyNode | None = None,
) -> Any:
    """Build the graph for one request. Remote agents are shared, so remote contexts persist."""
    classify = classify or ClassifyNode(directory, decision, remotes)
    b = GraphBuilder()
    b.add_node(classify, "classify")
    for node_id, remote in remotes.items():
        b.add_node(A2ANode(node_id, remote), node_id)
    b.add_node(AnswerNode(), "answer")
    b.add_node(SummarizeNode(), "summarize")

    def intent_is(name: str) -> Any:
        return lambda state: node_text(state, "classify") == name

    for node_id in remotes:
        b.add_edge("classify", node_id, condition=intent_is(node_id))
        b.add_edge(node_id, "summarize")
    b.add_edge("classify", "answer", condition=intent_is(NONE))
    b.add_edge("answer", "summarize")
    b.set_entry_point("classify")
    return b.build()


class GraphAgent:
    """Gives the graph the ``stream_async`` interface that ``StrandsA2AExecutor`` drives.

    # CUSTOM: ``strands.multiagent.a2a.A2AServer`` is unusable here (DEV-1), and the executor
    # only needs ``stream_async(prompt) -> {"data": text}`` events.
    """

    def __init__(
        self,
        context_id: str,
        *,
        directory: Directory,
        decision: DecisionModel | None,
        remotes: dict[str, RemoteAgent],
    ) -> None:
        self.context_id = context_id
        self.directory, self.remotes = directory, remotes
        self.decision = decision or get_decision_model()
        self.history: list[dict[str, Any]] = []  # classify decisions + delegations, for display

    async def stream_async(self, prompt: str, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        classify = ClassifyNode(self.directory, self.decision, self.remotes)
        graph = build_graph(
            self.context_id,
            directory=self.directory,
            decision=self.decision,
            remotes=self.remotes,
            classify=classify,
        )
        # `invocation_state` is the one mutable dict every node sees: it carries the user's
        # request in and each branch's output back out.
        state: dict[str, Any] = {"request": prompt, "context_id": self.context_id, "outputs": {}}
        result = await graph.invoke_async(prompt, invocation_state=state)
        text = str(result.results["summarize"]).removeprefix("summarize: ").strip()
        self.history.append({**classify.last, "delegations": state.get("delegations", [])})
        yield {"data": text}
