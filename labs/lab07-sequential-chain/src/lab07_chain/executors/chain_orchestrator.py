"""Orchestrated mode (spec lab07 §3): one orchestrator owns the overall task and calls each hop.

Per hop: its own remote ``context_id`` (uuid5 of the chain context + hop name; the writer's is
reused in revision loops so it remembers its drafts), its own task, and
``reference_task_ids`` pointing at the task whose output it consumes. A hop's ``task_id`` is never
sent to another hop. A hop in INPUT_REQUIRED pauses the orchestrator; the next client message
goes to THAT hop's task. Cancelling the orchestrator cancels the hop that is running.

Implements: spec lab07 §3. # CUSTOM: a2a-sdk has no workflow engine; this uses only client calls.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from a2a.helpers import new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import CancelTaskRequest, TaskState
from a2a_labs_common.a2a import events
from a2a_labs_common.a2a.client import Connected, SendResult, collect, connect, stream_text
from a2a_labs_common.a2a.ids import derived_context_id
from a2a_labs_common.a2a.states import state_name
from a2a_labs_common.models import get_decision_model
from a2a_labs_common.models.decision import DecisionModel, Question
from google.protobuf.json_format import MessageToDict

logger = logging.getLogger(__name__)
HOPS = {
    "planner": "lab07-adk-planner",
    "writer": "lab07-strands-writer",
    "editor": "lab07-langgraph-editor",
}
LEVELS = ["unusable", "major", "minor", "publishable"]
PUBLISHABLE_FROM = 2.0  # a score below this sends the draft back to the writer


class HopError(Exception):
    """A hop could not be reached or broke the stream; the message names it."""

    def __init__(self, hop: str, cause: Exception) -> None:
        self.hop = hop
        super().__init__(f"hop {hop} failed: {type(cause).__name__}: {cause}")


@dataclass
class Run:
    chain_context: str
    topic: str
    hops: list[dict[str, Any]] = field(default_factory=list)
    outline: dict[str, Any] | None = None
    draft: str = ""
    final: str = ""
    review: dict[str, Any] = field(default_factory=dict)
    scores: list[float] = field(default_factory=list)
    loops: int = 0
    step: str = "plan"  # plan -> write -> edit -> (score -> write ...)
    waiting: tuple[str, str] | None = None  # (hop, task id) paused in INPUT_REQUIRED
    active: tuple[Connected, str] | None = None  # the hop task running right now
    last: dict[str, str] = field(default_factory=dict)  # hop -> latest task id


def artifact(result: SendResult, name: str) -> Any:
    task = result.task
    return next((a for a in task.artifacts if a.name == name), None) if task else None


def artifact_text(result: SendResult, name: str) -> str:
    art = artifact(result, name)
    return "".join(p.text for p in art.parts if p.text) if art else ""


def artifact_data(result: SendResult, name: str) -> dict[str, Any] | None:
    art = artifact(result, name)
    if art is None:
        return None
    for p in art.parts:
        if p.HasField("data"):
            data = MessageToDict(p.data)
            return data if isinstance(data, dict) else None
    return None


class ChainOrchestrator(AgentExecutor):
    def __init__(
        self,
        decision: DecisionModel | None = None,
        services: dict[str, str] | None = None,
        max_loops: int | None = None,
    ) -> None:
        self.decision = decision
        self.services = services or HOPS
        self.max_loops = int(os.environ.get("MAX_LOOPS", "2")) if max_loops is None else max_loops
        self.runs: dict[str, Run] = {}

    # ── AgentExecutor ──────────────────────────────────────────────────────────────────────
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.message and context.task_id and context.context_id
        if context.current_task is None:
            await event_queue.enqueue_event(new_task_from_user_message(context.message))
            run = self.runs[context.task_id] = Run(context.context_id, context.get_user_input())
            answer = None
        else:
            found = self.runs.get(context.task_id)
            if found is None:
                u0 = TaskUpdater(event_queue, context.task_id, context.context_id)
                await u0.failed(u0.new_agent_message([new_text_part("unknown chain run")]))
                return
            run, answer = found, context.get_user_input()
        u = TaskUpdater(event_queue, context.task_id, context.context_id)
        await u.start_work()
        try:
            await self._drive(run, u, answer)
        except HopError as exc:
            await u.failed(u.new_agent_message([new_text_part(str(exc))]))
        except Exception as exc:
            logger.exception("chain failed")
            await u.failed(u.new_agent_message([new_text_part(f"chain error: {exc}")]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.task_id and context.context_id
        run = self.runs.get(context.task_id)
        if run is not None and run.active is not None:
            conn, hop_task = run.active
            try:  # A2A: cancelling the chain cancels the hop task that is running
                await conn.client.cancel_task(CancelTaskRequest(id=hop_task))
            except Exception as exc:
                logger.warning("could not cancel hop task %s: %s", hop_task, exc)
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()

    # ── the chain ──────────────────────────────────────────────────────────────────────────
    async def _drive(self, run: Run, u: TaskUpdater, answer: str | None) -> None:
        if run.waiting is not None:  # the client answered a hop's question: forward it
            hop, task_id = run.waiting
            run.waiting = None
            res = await self._call(run, u, hop, answer or "", task_id=task_id)
            if not await self._after(run, u, hop, res):
                return
        while True:
            if run.step == "plan":
                await self._say(u, "1/3 planning")
                res = await self._call(run, u, "planner", run.topic)
                if not await self._after(run, u, "planner", res):
                    return
            elif run.step == "write":
                label = f"revising (loop {run.loops})" if run.loops else "2/3 writing"
                await self._say(u, label)
                assert run.outline is not None
                feedback = "; ".join(run.review.get("issues", [])) or run.review.get("summary", "")
                text = (
                    f"Revise the draft using this feedback:\n{feedback}"
                    if run.loops
                    else "Write the article from the attached outline."
                )
                refs = [run.last[h] for h in ("writer", "editor") if h in run.last]
                refs = refs if run.loops else [run.last["planner"]]
                res = await self._call(run, u, "writer", text, data=run.outline, refs=refs)
                if not await self._after(run, u, "writer", res):
                    return
            elif run.step == "edit":
                await self._say(u, "3/3 editing")
                res = await self._call(run, u, "editor", run.draft, refs=[run.last["writer"]])
                if not await self._after(run, u, "editor", res):
                    return
            else:  # score
                score = await self._score(run)
                run.scores.append(score)
                if score < PUBLISHABLE_FROM and run.loops < self.max_loops:
                    run.loops += 1
                    run.step = "write"
                    continue
                break
        await u.add_artifact([new_text_part(run.final)], artifact_id="final", name="final")
        await u.add_artifact(
            [new_data_part(self._provenance(run))], artifact_id="provenance", name="provenance"
        )
        await u.complete()

    async def _after(self, run: Run, u: TaskUpdater, hop: str, res: SendResult) -> bool:
        """Handle a hop's outcome. Return True to keep driving the chain."""
        state = res.state
        if state == TaskState.TASK_STATE_INPUT_REQUIRED:
            run.waiting = (hop, res.task_id or "")
            question = res.task.status.message.parts[0].text if res.task else "More input needed"
            await u.requires_input(u.new_agent_message([new_text_part(question)]))
            return False
        if state != TaskState.TASK_STATE_COMPLETED:
            name = state_name(state) if state else "no task"
            reason = (
                res.task.status.message.parts[0].text
                if res.task
                and res.task.status.HasField("message")
                and res.task.status.message.parts
                else ""
            )
            await u.failed(
                u.new_agent_message([new_text_part(f"hop {hop} ended {name}: {reason}".strip())])
            )
            return False
        if hop == "planner":
            run.outline = artifact_data(res, "outline")
            if run.outline is None:
                await u.failed(u.new_agent_message([new_text_part("planner returned no outline")]))
                return False
            run.step = "write"
        elif hop == "writer":
            run.draft = artifact_text(res, "draft")
            run.step = "edit"
        else:
            run.final = artifact_text(res, "final") or run.draft
            run.review = artifact_data(res, "review") or {}
            run.step = "score"
        return True

    async def _call(
        self,
        run: Run,
        u: TaskUpdater,
        hop: str,
        text: str,
        *,
        data: dict[str, Any] | None = None,
        refs: list[str] | None = None,
        task_id: str | None = None,
    ) -> SendResult:
        service = self.services[hop]
        context_id = derived_context_id(run.chain_context, hop)
        started, evs, chars, shown = time.perf_counter(), [], 0, 0
        try:
            conn_cm = await connect(service, streaming=True)
        except Exception as exc:
            raise HopError(hop, exc) from exc
        async with conn_cm as conn:
            try:
                async for ev in stream_text(
                    conn,
                    text,
                    data=data,
                    context_id=context_id,
                    task_id=task_id,
                    reference_task_ids=refs or [],
                ):
                    evs.append(ev)
                    if run.active is None or run.active[0] is not conn:
                        tid = _task_id_of(ev)
                        if tid:
                            run.active = (conn, tid)
                    if events.kind(ev) == "artifact_update":
                        chars += sum(len(p.text) for p in ev.artifact_update.artifact.parts)
                        if hop == "writer" and chars - shown >= 200:  # relay chunks as progress
                            shown = chars
                            await self._say(u, f"{hop}: writing… {chars} chars")
            except Exception as exc:
                raise HopError(hop, exc) from exc
            finally:
                run.active = None
        res = collect(evs)
        run.hops.append(
            {
                "agent": hop,
                "service": service,
                "task_id": res.task_id,
                "context_id": res.context_id,
                "state": state_name(res.state) if res.state else "MESSAGE",
                "references": list(refs or []),
                "ms": round((time.perf_counter() - started) * 1000),
            }
        )
        if res.task_id:
            run.last[hop] = res.task_id
        return res

    async def _score(self, run: Run) -> float:
        review = run.review
        evidence = f"Summary: {review.get('summary', '')}\nIssues: {review.get('issues', [])}"
        model = self.decision or get_decision_model()
        res = await model.decide(
            evidence,
            {
                "quality": Question(
                    type="score",
                    instructions="How publishable is the article, given this editor's review?",
                    criteria=LEVELS,
                )
            },
        )
        return float(res.answers["quality"].score or 0.0)

    @staticmethod
    async def _say(u: TaskUpdater, text: str) -> None:
        await u.update_status(
            TaskState.TASK_STATE_WORKING, u.new_agent_message([new_text_part(text)])
        )

    @staticmethod
    def _provenance(run: Run) -> dict[str, Any]:
        return {
            "hops": run.hops,
            "loops": run.loops,
            "scores": run.scores,
            "contexts": {h: derived_context_id(run.chain_context, h) for h in HOPS},
        }


def _task_id_of(ev: Any) -> str | None:
    kind = events.kind(ev)
    if kind == "task":
        return str(ev.task.id)
    if kind == "status_update":
        return str(ev.status_update.task_id)
    if kind == "artifact_update":
        return str(ev.artifact_update.task_id)
    return None


__all__ = ["ChainOrchestrator", "collect"]
