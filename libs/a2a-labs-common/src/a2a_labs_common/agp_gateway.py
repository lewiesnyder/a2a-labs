"""The AGP gateway: announcements in, Intents routed out (EXPERIMENTAL, see ``agp.py``).

``AgpGatewayExecutor`` is an ordinary a2a-sdk ``AgentExecutor``. A gateway:

1. learns routes from ``announce`` messages and re-announces them upward (cost + 1);
2. routes an ``intent``: PII screen (decision model) -> table lookup -> forward -> mirror;
3. serves clients that know nothing about AGP: a decision-model ``choice`` over its children.

A2A task ownership is kept per hop: the gateway owns ITS task, mirrors the downstream task's
artifacts and final state, and adds a ``route`` artifact naming the downstream task id.

# CUSTOM: all of it (DEV-63..66). Official calls only: ``connect``/``send_text``, ``TaskUpdater``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from a2a.client.errors import A2AClientError
from a2a.helpers import new_data_part, new_message, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import AgentCard, Role, TaskState
from a2a.utils.errors import InvalidParamsError
from opentelemetry import trace

from a2a_labs_common.a2a.client import Connected, SendResult, connect, send_text
from a2a_labs_common.a2a.extensions import activated
from a2a_labs_common.agp import (
    AGP_URI,
    AgpPolicyViolationError,
    AgpRouteNotFoundError,
    AgpTable,
    AgpTableStaleError,
    CapabilityAnnouncement,
    Intent,
    RouteEntry,
    agp_error,
    announce_data,
    extract_agp,
    intent_data,
)
from a2a_labs_common.models import get_decision_model
from a2a_labs_common.models.decision import DecisionModel, Question

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("a2a_labs_common.agp")
PII_THRESHOLD = 0.5
TRANSPORT_ERRORS = (httpx.HTTPError, OSError, A2AClientError)


# ── announcing (used by squads and by gateways going upward) ──────────────────────────────────
async def agp_announce(
    parent_url: str,
    announcements: list[CapabilityAnnouncement],
    path: list[str],
    next_hop_url: str,
    *,
    gateway: bool = False,
) -> int:
    """Tell a gateway what we can do. Returns how many announcements it accepted."""
    async with await connect(parent_url, extensions=[AGP_URI], streaming=False) as c:
        res = await send_text(
            c,
            "AGP announce",
            data=announce_data(announcements, path, next_hop_url, gateway=gateway),
        )
    ack = extract_agp(res.message) if res.message else None
    return int((ack or {}).get("accepted", 0))


class Announcer:
    """A squad's side: announce at startup and again every ``interval_s`` until paused."""

    def __init__(
        self,
        *,
        name: str,
        parent_url: str,
        self_url: str,
        announcements: list[CapabilityAnnouncement],
        interval_s: float = 60.0,
    ) -> None:
        self.name, self.parent_url, self.self_url = name, parent_url, self_url
        self.announcements, self.interval_s = announcements, interval_s
        self.paused = False
        self.sent = 0

    async def once(self) -> None:
        if self.paused:
            return
        try:
            await agp_announce(self.parent_url, self.announcements, [self.name], self.self_url)
            self.sent += 1
        except Exception as exc:  # the parent may still be starting: try again next tick
            logger.warning("announce to %s failed: %s", self.parent_url, exc)

    async def run(self) -> None:
        while True:
            await self.once()
            await asyncio.sleep(self.interval_s)


# ── the gateway ───────────────────────────────────────────────────────────────────────────────
def _text_of(intent: Intent) -> str:
    return f"{intent.target_capability} {json.dumps(intent.payload)}"


class AgpGatewayExecutor(AgentExecutor):
    def __init__(
        self,
        *,
        name: str,
        card: AgentCard,
        table: AgpTable,
        self_url: str,
        parent_url: str | None = None,
        question: str = "squad",
        decision: DecisionModel | None = None,
        reannounce_s: float = 60.0,
    ) -> None:
        self.name, self.card, self.table = name, card, table
        self.self_url, self.parent_url = self_url, parent_url
        self.question, self.decision = question, decision
        self.reannounce_s = reannounce_s
        self.paused = False
        self._conns: dict[str, Connected] = {}
        self._up: asyncio.Task[None] | None = None

    # ── wiring to the parent ──────────────────────────────────────────────────────────────
    async def announce_up(self) -> None:
        """Re-announce every FRESH route upward: ``cost + 1``, ``path = [self] + path``."""
        if self.parent_url is None or self.paused:
            return
        groups: dict[tuple[str, ...], list[CapabilityAnnouncement]] = {}
        for r in self.table.fresh_routes():
            groups.setdefault(tuple(r.path), []).append(
                CapabilityAnnouncement(
                    capability=r.capability, version=r.version, cost=r.cost + 1, policy=r.policy
                )
            )
        for path, anns in groups.items():
            try:
                await agp_announce(
                    self.parent_url, anns, [self.name, *path], self.self_url, gateway=True
                )
            except Exception as exc:
                logger.warning("announce up to %s failed: %s", self.parent_url, exc)

    async def run(self) -> None:
        while True:
            self.table.purge()
            await self.announce_up()
            await asyncio.sleep(self.reannounce_s)

    def _schedule_up(self) -> None:
        if self.parent_url and (self._up is None or self._up.done()):
            self._up = asyncio.create_task(self.announce_up())

    async def aclose(self) -> None:
        for c in self._conns.values():
            await c.aclose()
        self._conns.clear()

    async def _conn(self, url: str, *, agp: bool) -> Connected:
        key = f"{url}|{agp}"
        if key not in self._conns:
            self._conns[key] = await connect(
                url, streaming=False, extensions=[AGP_URI] if agp else ()
            )
        return self._conns[key]

    # ── AgentExecutor ─────────────────────────────────────────────────────────────────────
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.message is not None
        data = extract_agp(context.message)
        on = AGP_URI in activated(self.card, context.requested_extensions)
        if data is None:
            if on:
                raise InvalidParamsError(
                    message="AGP is activated but the message has no 'agp' part"
                )
            await self._fallback(context, event_queue)
            return
        kind = data.get("type")
        if kind == "announce":
            await self._on_announce(context, event_queue, data)
        elif kind == "intent":
            await self._on_intent(context, event_queue, data)
        else:
            raise InvalidParamsError(message=f"unknown AGP message type {kind!r}")

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.task_id and context.context_id
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()

    # ── announce ──────────────────────────────────────────────────────────────────────────
    async def _on_announce(
        self, ctx: RequestContext, queue: EventQueue, data: dict[str, Any]
    ) -> None:
        try:
            anns = [CapabilityAnnouncement(**a) for a in data["announcements"]]
            path, url = list(data["path"]), str(data["next_hop_url"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidParamsError(message=f"bad AGP announce: {exc}") from exc
        for a in anns:
            self.table.upsert(a, path, url, is_gateway=bool(data.get("gateway")))
        logger.info("announce from %s: %s", "/".join(path), [a.capability for a in anns])
        # A2A: an announce is a one-shot exchange, answered with a single Message (no task).
        await queue.enqueue_event(
            new_message(
                [
                    new_text_part(f"accepted {len(anns)}"),
                    new_data_part({"agp": {"type": "ack", "accepted": len(anns)}}),
                ],
                context_id=ctx.context_id,
                role=Role.ROLE_AGENT,
            )
        )
        self._schedule_up()

    # ── intent ────────────────────────────────────────────────────────────────────────────
    async def _detect_pii(self, intent: Intent, data: dict[str, Any]) -> tuple[bool, float | None]:
        if "pii_detected" in data:  # an upstream gateway already asked
            return bool(data["pii_detected"]), None
        model = self.decision or get_decision_model()
        res = await model.decide(
            f"Payload: {json.dumps(intent.payload)}",
            {
                "pii": Question(
                    type="noul",
                    instructions="Does the payload contain personal data (PII)?",
                )
            },
        )
        p = float(res.answers["pii"].noul or 0.0)
        return p >= PII_THRESHOLD, p

    async def _on_intent(
        self, ctx: RequestContext, queue: EventQueue, data: dict[str, Any]
    ) -> None:
        try:
            intent = Intent(**data["intent"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidParamsError(message=f"bad AGP intent: {exc}") from exc
        constraints = dict(intent.policy_constraints or {})
        with _tracer.start_as_current_span("agp.route") as span:
            span.set_attributes(
                {"agp.gateway": self.name, "agp.capability": intent.target_capability}
            )
            detected, p = await self._detect_pii(intent, data)
            span.set_attribute("agp.pii.detected", detected)
            if p is not None:
                span.set_attribute("agp.pii.p", p)
            if detected:
                if constraints.get("pii") is False:
                    raise agp_error(
                        AgpPolicyViolationError,
                        "the Intent asserts pii=false but the payload contains personal data (PII)",
                        pii_probability=f"{p:.3f}" if p is not None else "upstream",
                    )
                constraints["pii"] = True
            result, entry, alternatives = await self._forward_with_failover(
                intent, constraints, detected
            )
            span.set_attributes(
                {
                    "agp.route.path": "/".join(entry.path),
                    "agp.route.cost": entry.cost,
                    "agp.route.rejected": len(alternatives),
                }
            )
        await self._mirror(
            ctx,
            queue,
            result,
            {
                "capability": intent.target_capability,
                "via": self.name,
                "path": entry.path,
                "cost": entry.cost,
                "constraints": constraints,
                "pii_detected": detected,
                "pii_probability": p,
                "alternatives": alternatives,
            },
        )

    async def _forward_with_failover(
        self, intent: Intent, constraints: dict[str, Any], detected: bool
    ) -> tuple[SendResult, RouteEntry, list[dict[str, Any]]]:
        """Best route first; an unreachable next hop is skipped and the next best one is tried."""
        while True:
            lk = self.table.lookup(intent.target_capability, constraints)  # may raise AGP errors
            entry = lk.best
            try:
                if entry.is_gateway:
                    conn = await self._conn(entry.next_hop_url, agp=True)
                    fwd = Intent(
                        target_capability=intent.target_capability,
                        payload=intent.payload,
                        policy_constraints=intent.policy_constraints,
                    )
                    result = await send_text(
                        conn, "AGP intent", data=intent_data(fwd, pii_detected=detected)
                    )
                else:
                    conn = await self._conn(entry.next_hop_url, agp=False)
                    result = await send_text(conn, _text_of(intent))
            except (*TRANSPORT_ERRORS, AgpRouteNotFoundError, AgpTableStaleError) as exc:
                # A2A: a dead hop, or a child gateway that lost its own route: BGP-style, try the
                # next best route. A policy violation is NOT retried: another path would not help.
                logger.warning("next hop %s unusable (%s): trying the next route", entry.path, exc)
                self.table.mark_failed(entry)
                self._conns.pop(f"{entry.next_hop_url}|{entry.is_gateway}", None)
                continue
            return result, entry, lk.rejected

    # ── non-AGP clients ───────────────────────────────────────────────────────────────────
    async def _fallback(self, ctx: RequestContext, queue: EventQueue) -> None:
        text = ctx.get_user_input().strip()
        children: dict[str, list[RouteEntry]] = {}
        for r in self.table.fresh_routes():
            children.setdefault(r.next_hop, []).append(r)
        if not children:
            raise agp_error(AgpRouteNotFoundError, "this gateway has no fresh routes")
        options = {
            child: "handles " + ", ".join(sorted({r.capability for r in rs}))
            for child, rs in children.items()
        }
        model = self.decision or get_decision_model()
        with _tracer.start_as_current_span("agp.fallback") as span:
            res = await model.decide(
                f"Request: {text}",
                {
                    self.question: Question(
                        type="choice",
                        instructions="Which of these should handle the request?",
                        criteria=options,
                    )
                },
            )
            answer = res.answers[self.question]
            pick = answer.choice if answer.choice in children else next(iter(children))
            entry = min(children[pick], key=lambda r: r.cost)
            span.set_attributes({"agp.fallback.choice": pick, "agp.fallback.backend": res.backend})
        conn = await self._conn(entry.next_hop_url, agp=False)
        try:
            result = await send_text(conn, text)
        except TRANSPORT_ERRORS:
            self.table.mark_failed(entry)
            raise
        await self._mirror(
            ctx,
            queue,
            result,
            {
                "capability": None,
                "via": self.name,
                "path": [pick],
                "cost": entry.cost,
                "fallback": True,
                "probabilities": answer.probabilities
                if isinstance(answer.probabilities, dict)
                else {},
            },
        )

    # ── answer as OUR task, mirroring the downstream one ─────────────────────────────────
    async def _mirror(
        self, ctx: RequestContext, queue: EventQueue, result: SendResult, route: dict[str, Any]
    ) -> None:
        assert ctx.message is not None
        task = ctx.current_task or new_task_from_user_message(ctx.message)
        if ctx.current_task is None:
            await queue.enqueue_event(task)
        u = TaskUpdater(queue, task.id, task.context_id)
        await u.start_work()
        route = {**route, "downstream_task_id": result.task_id}
        if result.task is not None:
            for art in result.task.artifacts:
                await u.add_artifact(
                    list(art.parts), artifact_id=art.artifact_id, name=art.name or None
                )
        elif result.message is not None:
            await u.add_artifact([new_text_part(result.text)], artifact_id="answer", name="answer")
        await u.add_artifact(
            [new_data_part(json.loads(json.dumps(route, default=str)))],
            artifact_id="agp-route",
            name="agp-route",
            extensions=[AGP_URI],
        )
        state = result.state
        note = u.new_agent_message([new_text_part(f"via {self.name}: {'/'.join(route['path'])}")])
        if state in (None, TaskState.TASK_STATE_COMPLETED):
            await u.complete(note)
        elif state == TaskState.TASK_STATE_REJECTED:
            await u.reject(result.task.status.message if result.task else note)
        elif state == TaskState.TASK_STATE_CANCELED:
            await u.cancel()
        else:
            msg = result.task.status.message if result.task else note
            await u.failed(msg)


def agp_extension() -> Any:
    """The card declaration of spec §4: ``params {agent_role: gateway, supported_agp_versions}``."""
    from a2a_labs_common.a2a.extensions import extension

    return extension(
        AGP_URI,
        "Agent Gateway Protocol (experimental): capability routing between squads.",
        params={"agent_role": "gateway", "supported_agp_versions": ["1.0"]},
    )
