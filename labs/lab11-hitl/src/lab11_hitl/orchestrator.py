"""The Trip Desk orchestrator: delegates in two stages and RELAYS every question to the human.

Stage 1 runs flights, hotels and (optionally) documents in parallel. Stage 2 pays, because the
amount depends on stage 1. Any delegate may pause its OWN task with INPUT_REQUIRED or
AUTH_REQUIRED. The orchestrator then:

* keeps the delegates that finished (their artifacts are already on the orchestrator's task);
* pauses its own task and lists EVERY open question at once (an ``interrupts`` data part);
* routes each answer to the right delegate task (the ``Run`` ledger), one at a time or all at once;
* continues to the next stage when a stage has no question left, which may raise new ones;
* on a reject, cancels the delegates that are still open and ends REJECTED;
* lets a reaper answer "reject: timed out" for a question nobody answered, through the SAME
  resume path a person uses (the clock is just another responder).

A2A mechanics used: ``INPUT_REQUIRED``/``AUTH_REQUIRED`` and resume by ``task_id``, one task and
context per delegate (Lab 05), parallel ``SendMessage`` calls, ``CancelTask``.

# CUSTOM: the whole executor; a2a-sdk has no orchestration or HITL relay (DEV-70).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from a2a.helpers import new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import AgentCard, CancelTaskRequest
from a2a_labs_common.a2a.client import Connected, SendResult, connect, send_text
from a2a_labs_common.a2a.states import state_name
from a2a_labs_common.hitl import (
    Answer,
    Interrupt,
    Prompt,
    answers_part,
    extract_answer,
    extract_answers,
    extract_prompt,
    interpret_reply,
    interrupts_part,
)
from a2a_labs_common.models import get_decision_model
from a2a_labs_common.models.decision import DecisionModel
from google.protobuf.json_format import MessageToDict
from opentelemetry import trace

from lab11_hitl.data import DEFAULT_NIGHTS, HOTELS, SEATS

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("lab11_hitl")
INTERRUPTED = ("INPUT_REQUIRED", "AUTH_REQUIRED")
TERMINAL_BAD = ("REJECTED", "FAILED", "CANCELED")
FLIGHTS, HOTELS_S, DOCS, PAYMENTS = (
    "lab11-flights",
    "lab11-hotels",
    "lab11-docs",
    "lab11-payments",
)


@dataclass
class Run:
    """The ledger row for ONE delegate task (what Lab 05 calls per-remote tracking)."""

    agent: str
    stage: int
    task_id: str | None = None
    context_id: str | None = None
    state: str = "PENDING"
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    prompt: Prompt | None = None
    interrupt_id: str | None = None
    deadline_ts: float | None = None
    emitted: bool = False  # its artifact is already on the orchestrator's task
    deadline_s: float = 300.0  # how long a question of this run may wait for a person

    def row(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "stage": self.stage,
            "task_id": self.task_id,
            "state": self.state,
            "interrupt_id": self.interrupt_id,
            "note": self.note,
        }


@dataclass
class Trip:
    task_id: str
    context_id: str
    request: str
    nights: int
    deadline_s: float = 300.0
    runs: dict[str, Run] = field(default_factory=dict)
    stage: int = 1
    done: bool = False
    notes: list[str] = field(default_factory=list)  # shown with the next question
    reaping: float = 0.0  # monotonic time until which the reaper leaves this trip alone

    def stage_runs(self) -> list[Run]:
        return [r for r in self.runs.values() if r.stage == self.stage]

    def open(self) -> list[Run]:
        return [r for r in self.runs.values() if r.state in INTERRUPTED]


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class TripDeskExecutor(AgentExecutor):
    def __init__(
        self,
        *,
        card: AgentCard,
        self_url: str,
        decision: DecisionModel | None = None,
        deadline_s: float = 300.0,
        reaper_s: float = 5.0,
    ) -> None:
        self.card, self.self_url, self.decision = card, self_url, decision
        self.deadline_s, self.reaper_s = deadline_s, reaper_s
        self.trips: dict[str, Trip] = {}
        self._conns: dict[str, Connected] = {}
        self._self: Connected | None = None

    # ── plumbing ──────────────────────────────────────────────────────────────────────────
    async def _conn(self, service: str) -> Connected:
        if service not in self._conns:
            self._conns[service] = await connect(service, streaming=False, timeout=120)
        return self._conns[service]

    async def aclose(self) -> None:
        for c in [*self._conns.values(), *([self._self] if self._self else [])]:
            await c.aclose()
        self._conns.clear()

    def _absorb(self, run: Run, res: SendResult) -> None:
        """Copy a delegate's reply into its ledger row."""
        if res.task is None:  # a plain Message answer
            run.state, run.text = "COMPLETED", res.text
            return
        t = res.task
        run.task_id, run.context_id = t.id, t.context_id
        run.state = state_name(t.status.state)
        status_text = (
            " ".join(p.text for p in t.status.message.parts if p.text)
            if t.status.HasField("message")
            else ""
        )
        run.text = res.text or status_text
        run.data = {}
        for art in t.artifacts:
            for p in art.parts:
                if p.HasField("data"):
                    run.data.update(MessageToDict(p.data))
        if run.state in INTERRUPTED:
            msg_parts = list(t.status.message.parts)
            kind = "consent" if run.state == "AUTH_REQUIRED" else "input"
            run.prompt = extract_prompt(msg_parts) or Prompt(kind=kind, prompt=status_text)
            run.interrupt_id = run.interrupt_id or f"{run.agent.removeprefix('lab11-')}:{t.id[:6]}"
            run.deadline_ts = time.time() + run.deadline_s  # a (re-)asked question restarts it
            run.prompt.deadline = _iso(run.deadline_ts)
        else:
            run.prompt = run.deadline_ts = None  # type: ignore[assignment]
            run.note = status_text if run.state in TERMINAL_BAD else run.note
            if run.state == "COMPLETED" and run.data.get("declined"):
                # The LangGraph bridge ends COMPLETED; a graph reports a refusal in its artifact.
                run.state, run.note = "REJECTED", run.text or "declined"

    async def _call(self, run: Run, text: str, *, data: dict[str, Any] | None = None) -> None:
        """One A2A exchange with a delegate: new task, or resume ``run.task_id``."""
        conn = await self._conn(run.agent)
        try:
            with _tracer.start_as_current_span("hitl.delegate") as span:
                span.set_attributes({"hitl.agent": run.agent, "hitl.resume": bool(run.task_id)})
                res = await send_text(
                    conn, text, task_id=run.task_id, context_id=run.context_id, data=data
                )
            self._absorb(run, res)
        except Exception as exc:
            logger.warning("delegate %s failed: %s", run.agent, exc)
            run.state, run.note = "FAILED", f"{type(exc).__name__}: {exc}"

    # ── AgentExecutor ─────────────────────────────────────────────────────────────────────
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.message is not None
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)  # A2A: the Task is always the first event
            u = TaskUpdater(event_queue, task.id, task.context_id)
            await u.start_work()
            await self._start(task.id, task.context_id, context.get_user_input(), u)
            return
        u = TaskUpdater(event_queue, task.id, task.context_id)
        await u.start_work()
        trip = self.trips.get(task.id)
        if trip is None or trip.done:
            await u.failed(
                u.new_agent_message(
                    [new_text_part("I lost track of this trip; please start again.")]
                )
            )
            return
        await self._resume(context, u, trip)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.task_id and context.context_id
        trip = self.trips.get(context.task_id)
        if trip is not None:
            await self._cancel_open(trip)
            trip.done = True
        u = TaskUpdater(event_queue, context.task_id, context.context_id)
        await u.cancel(u.new_agent_message([new_text_part("Cancelled; open delegate tasks too.")]))

    # ── stage machine ─────────────────────────────────────────────────────────────────────
    async def _start(self, task_id: str, context_id: str, text: str, u: TaskUpdater) -> None:
        low = text.lower()
        m = re.search(r"(\d+)\s*night", low)
        nights = int(m.group(1)) if m else DEFAULT_NIGHTS
        # a request may shorten the wait for a person: "deadline 20s" (the notebook uses it)
        dl = re.search(r"deadline\s+(\d+(?:\.\d+)?)\s*s", low)
        deadline_s = float(dl.group(1)) if dl else self.deadline_s
        trip = Trip(task_id, context_id, text, nights, deadline_s=deadline_s)
        self.trips[task_id] = trip
        seat = next((s for s, _ in SEATS if s in low), "")
        hotel = next((h["id"] for h in HOTELS if h["id"] in low), "")
        trip.runs = {
            FLIGHTS: Run(FLIGHTS, 1, deadline_s=deadline_s),
            HOTELS_S: Run(HOTELS_S, 1, deadline_s=deadline_s),
        }
        if "document" in low or "passport" in low:
            trip.runs[DOCS] = Run(DOCS, 1, deadline_s=deadline_s)
        asks = {
            FLIGHTS: f"Book a flight to Lisbon. {seat}".strip(),
            HOTELS_S: f"Book a hotel for {nights} nights. {hotel}".strip(),
            DOCS: "Check my travel documents.",
        }
        await asyncio.gather(*(self._call(r, asks[r.agent]) for r in trip.runs.values()))
        await self._advance(trip, u)

    async def _advance(self, trip: Trip, u: TaskUpdater) -> None:
        while True:
            runs = trip.stage_runs()
            for r in runs:
                if r.state == "COMPLETED" and not r.emitted:
                    await self._emit_result(u, r)
            if any(r.state in TERMINAL_BAD for r in runs):
                await self._abort(trip, u)
                return
            if any(r.state in INTERRUPTED for r in runs):
                await self._pause(trip, u)
                return
            if trip.stage == 1:
                trip.stage = 2
                amount = self._total(trip)
                pay = trip.runs[PAYMENTS] = Run(PAYMENTS, 2, deadline_s=trip.deadline_s)
                await self._call(pay, "Pay for the trip.", data={"payment": {"amount": amount}})
                continue
            await self._finish(trip, u)
            return

    @staticmethod
    def _total(trip: Trip) -> float:
        flights = trip.runs[FLIGHTS].data.get("price", 0)
        hotel = trip.runs[HOTELS_S].data.get("total", 0)
        return float(flights) + float(hotel)

    async def _emit_result(self, u: TaskUpdater, r: Run) -> None:
        r.emitted = True
        name = r.agent.removeprefix("lab11-")
        await u.add_artifact([new_text_part(r.text)], artifact_id=name, name=name)

    async def _pause(self, trip: Trip, u: TaskUpdater) -> None:
        """Pause THIS task and list every open question (the human may answer in any order)."""
        items = [
            Interrupt(
                id=r.interrupt_id or "",
                agent=r.agent,
                task_id=r.task_id or "",
                context_id=r.context_id or "",
                state=r.state,  # type: ignore[arg-type]
                prompt=r.prompt or Prompt(kind="input", prompt=r.text),
                deadline_ts=r.deadline_ts,
            )
            for r in trip.open()
        ]
        lines = [f"[{i.agent.removeprefix('lab11-')}] {i.prompt.prompt}" for i in items]
        done = [
            r.agent.removeprefix("lab11-") for r in trip.runs.values() if r.state == "COMPLETED"
        ]
        head = f"{len(items)} question(s) need a human."
        if done:
            head += f" Already done: {', '.join(done)}."
        text = "\n".join([*trip.notes, head, *lines])
        trip.notes.clear()
        msg = u.new_agent_message([new_text_part(text), interrupts_part(items)])
        # A2A: INPUT_REQUIRED unless EVERY open question is a consent request.
        if all(i.state == "AUTH_REQUIRED" for i in items):
            await u.requires_auth(msg)
        else:
            await u.requires_input(msg)

    async def _cancel_open(self, trip: Trip) -> None:
        for r in trip.runs.values():
            if r.state in INTERRUPTED and r.task_id:
                try:
                    conn = await self._conn(r.agent)
                    await conn.client.cancel_task(CancelTaskRequest(id=r.task_id))
                    r.state = "CANCELED"
                except Exception as exc:
                    logger.warning("cancel of %s %s failed: %s", r.agent, r.task_id, exc)

    async def _abort(self, trip: Trip, u: TaskUpdater) -> None:
        bad = [r for r in trip.runs.values() if r.state in TERMINAL_BAD]
        await self._cancel_open(trip)  # compensate: nothing is left waiting for a human
        trip.done = True
        why = "; ".join(f"{r.agent.removeprefix('lab11-')}: {r.note or r.state}" for r in bad)
        ledger = new_data_part({"ledger": [r.row() for r in trip.runs.values()]})
        msg = u.new_agent_message([new_text_part(f"Trip not booked. {why}"), ledger])
        if any(r.state == "REJECTED" for r in bad):
            await u.reject(msg)
        else:
            await u.failed(msg)

    async def _finish(self, trip: Trip, u: TaskUpdater) -> None:
        trip.done = True
        lines = [f"{r.agent.removeprefix('lab11-')}: {r.text}" for r in trip.runs.values()]
        await u.add_artifact(
            [new_text_part("\n".join(lines))], artifact_id="itinerary", name="itinerary"
        )
        await u.add_artifact(
            [new_data_part({"ledger": [r.row() for r in trip.runs.values()]})],
            artifact_id="ledger",
            name="ledger",
        )
        await u.complete()

    # ── resume: a person (or the reaper) answers ──────────────────────────────────────────
    async def _resume(self, context: RequestContext, u: TaskUpdater, trip: Trip) -> None:
        assert context.message is not None
        parts = list(context.message.parts)
        open_ = trip.open()
        answers = extract_answers(parts)
        if answers is None:
            single = extract_answer(parts)
            text = context.get_user_input().strip()
            if len(open_) != 1:
                trip.notes.append(
                    "Several questions are open: answer with "
                    "{'hitl': {'answers': {'<id>': {...}}}}."
                )
                await self._pause(trip, u)
                return
            if single is None:
                single, probs = await interpret_reply(
                    text, open_[0].prompt or Prompt(kind="input", prompt=""), self._decision()
                )
                if single is None:
                    trip.notes.append(f"I could not read {text!r} as an answer (reading: {probs}).")
                    await self._pause(trip, u)
                    return
            answers = {open_[0].interrupt_id or "": single}
        by_id = {r.interrupt_id: r for r in open_}
        todo: list[tuple[Run, Answer]] = []
        for iid, ans in answers.items():
            if iid in by_id:
                todo.append((by_id[iid], ans))
            else:
                trip.notes.append(f"Unknown question id {iid!r}; open: {sorted(map(str, by_id))}.")
        with _tracer.start_as_current_span("hitl.resume") as span:
            span.set_attributes({"hitl.answers": len(todo), "hitl.open": len(open_)})
            await asyncio.gather(
                *(
                    self._call(
                        run,
                        "human answer",
                        data={"hitl": {"answer": ans.model_dump(exclude_none=True)}},
                    )
                    for run, ans in todo
                )
            )
        await self._advance(trip, u)

    def _decision(self) -> DecisionModel:
        return self.decision or get_decision_model()

    # ── the clock as a responder ──────────────────────────────────────────────────────────
    async def reap_once(self) -> int:
        """Answer "reject: timed out" for every question past its deadline; returns how many."""
        now, expired = time.time(), 0
        for trip in list(self.trips.values()):
            if trip.done or trip.reaping > time.monotonic():
                continue
            late = [r for r in trip.open() if r.deadline_ts and r.deadline_ts < now]
            if not late:
                continue
            trip.reaping = time.monotonic() + 5  # do not fire twice while this one is running
            answers = {
                r.interrupt_id or "": Answer(
                    decision="reject", text="timed out waiting for a human", by="timeout"
                )
                for r in late
            }
            try:
                if self._self is None:
                    self._self = await connect(self.self_url, streaming=False, timeout=120)
                await send_text(
                    self._self,
                    "deadline reached",
                    task_id=trip.task_id,
                    context_id=trip.context_id,
                    data=answers_part_dict(answers),
                )
                expired += len(late)
            except Exception as exc:
                logger.warning("reaper could not answer for %s: %s", trip.task_id, exc)
        return expired

    async def run_reaper(self) -> None:
        while True:
            await asyncio.sleep(self.reaper_s)
            await self.reap_once()

    # ── debugging ─────────────────────────────────────────────────────────────────────────
    def ledger(self, task_id: str) -> list[dict[str, Any]] | None:
        trip = self.trips.get(task_id)
        return [r.row() for r in trip.runs.values()] if trip else None


def answers_part_dict(answers: dict[str, Answer]) -> dict[str, Any]:
    """The same payload ``answers_part`` builds, as a plain dict for ``send_text(data=...)``."""
    return {"hitl": {"answers": {k: v.model_dump(exclude_none=True) for k, v in answers.items()}}}


__all__ = ["Run", "Trip", "TripDeskExecutor", "answers_part", "answers_part_dict"]
