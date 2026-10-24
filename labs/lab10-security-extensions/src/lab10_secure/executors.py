"""The three hand-written executors of Lab 10 (pure a2a-sdk ``AgentExecutor``s).

* ``CalendarExecutor``: pre-flight screen -> REJECTED / AUTH_REQUIRED / COMPLETED.
* ``TraceSummaryExecutor``: an OPTIONAL extension, active only when the client asks for it.
* ``CostBudgetExecutor``: a REQUIRED extension; a missing one is an error, not a downgrade.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from a2a.helpers import (
    get_data_parts,
    new_data_part,
    new_task_from_user_message,
    new_text_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import AgentCard, Task, TaskState
from a2a.utils.errors import InvalidParamsError
from a2a_labs_common.a2a.extensions import activated, metadata_for, require_extensions
from a2a_labs_common.auth import caller
from a2a_labs_common.models import get_decision_model
from a2a_labs_common.models.decision import DecisionModel, Question
from opentelemetry import trace

from lab10_secure.data import (
    ADMIN_STATS,
    BUDGET_EXT,
    PRIVATE_CALENDARS,
    PUBLIC_CALENDAR,
    TRACE_EXT,
    UNIT_COST,
    render,
)
from lab10_secure.idp_client import IdpClient, IdpError, make_verifier
from lab10_secure.preflight import screen

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("lab10")


async def _task_for(context: RequestContext, queue: EventQueue) -> TaskUpdater:
    """A2A: a new task is announced with a ``Task`` event BEFORE any status update."""
    assert context.message is not None
    task = context.current_task
    if task is None:
        task = new_task_from_user_message(context.message)
        await queue.enqueue_event(task)
    return TaskUpdater(queue, task.id, task.context_id)


def _say(u: TaskUpdater, text: str, *extra: Any) -> Any:
    return u.new_agent_message([new_text_part(text), *extra])


# ── the secure calendar agent ────────────────────────────────────────────────────────────────
class CalendarExecutor(AgentExecutor):
    """Transport auth has already happened (the middleware). This is the second line: what the
    authenticated principal is allowed to do, and what needs a person's consent.

    A2A security in four states: HTTP 401 (no task exists), REJECTED (policy said no),
    AUTH_REQUIRED (the task exists and waits for consent) and COMPLETED.
    """

    def __init__(self, decision: DecisionModel | None = None, idp: IdpClient | None = None) -> None:
        self.decision = decision
        self.idp = idp or IdpClient()
        self.verifier = make_verifier()
        # device_code_ref -> {task_id, user_code}: kept on the SERVER; the client only sees the ref
        self.pending: dict[str, dict[str, str]] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.message is not None
        who, scopes = caller(context)
        task = context.current_task
        updater = await _task_for(context, event_queue)
        if task is not None and task.status.state == TaskState.TASK_STATE_AUTH_REQUIRED:
            await self._resume(updater, context, task)
            return
        text = context.get_user_input().strip()
        await updater.start_work()

        verdict = await screen(text, self.decision)
        logger.info("preflight %s %s principal=%s", verdict.choice, verdict.probabilities, who)
        evidence = {"decision": verdict.choice, "probabilities": verdict.probabilities}
        if verdict.choice == "reject":
            await updater.reject(
                _say(
                    updater,
                    "I won't do that: this assistant only reads calendars.",
                    new_data_part(evidence),
                )
            )
            return
        if "stats" in text.lower():  # a skill of the extended card
            if "agent:admin" not in scopes:
                await updater.reject(_say(updater, "admin statistics need the agent:admin scope"))
                return
            await updater.add_artifact([new_data_part(ADMIN_STATS)], artifact_id="stats")
            await updater.complete()
            return
        if verdict.choice == "needs_auth":
            if "calendar:read" in scopes:  # the transport token already carries the user's consent
                await self._answer_private(updater, who)
            else:
                await self._ask_for_consent(updater, context)
            return
        await updater.add_artifact(
            [new_text_part(render(PUBLIC_CALENDAR))], artifact_id="calendar", name="public calendar"
        )
        await updater.complete(_say(updater, f"Public calendar for {who or 'you'}."))

    async def _ask_for_consent(self, u: TaskUpdater, context: RequestContext) -> None:
        # A2A: AUTH_REQUIRED is an INTERRUPTED state: the task lives on and waits for the client.
        device = await self.idp.start_device_flow("calendar:read")
        ref = "devref_" + uuid.uuid4().hex[:12]
        self.pending[ref] = {"device_code": device["device_code"], "user_code": device["user_code"]}
        challenge = {
            "auth": {
                "flow": "device_code",
                "verification_uri": device["verification_uri"],
                "user_code": device["user_code"],
                "device_code_ref": ref,  # NOT the device code: that stays on this server
            }
        }
        await u.requires_auth(
            _say(
                u,
                f"Private data needs your consent. Open {device['verification_uri']} and enter "
                f"{device['user_code']}, then resume this task with the access token reference.",
                new_data_part(challenge),
            )
        )

    async def _resume(self, u: TaskUpdater, context: RequestContext, task: Task) -> None:
        assert context.message is not None
        auth = next((d["auth"] for d in get_data_parts(context.message.parts) if "auth" in d), {})
        ref = auth.get("access_token_ref")
        if not ref:
            await u.requires_auth(
                _say(u, "Still waiting: send {'auth': {'access_token_ref': ...}}")
            )
            return
        await u.start_work()
        try:
            # A2A: the client sent a REFERENCE. We exchange it at the IdP with OUR credentials.
            token = await self.idp.redeem(str(ref))
            claims = await self.verifier.verify(token)
        except (IdpError, Exception) as exc:
            logger.warning("token reference refused: %s", exc)
            await u.requires_auth(_say(u, "That reference was not accepted. Try the flow again."))
            return
        if "calendar:read" not in str(claims.get("scope", "")).split():
            await u.reject(_say(u, "the approved token does not grant calendar:read"))
            return
        await self._answer_private(u, str(claims.get("preferred_username") or claims["sub"]))

    async def _answer_private(self, u: TaskUpdater, user: str) -> None:
        rows = PRIVATE_CALENDARS.get(user, [])
        await u.add_artifact(
            [new_text_part(render(rows) or "(nothing scheduled)")],
            artifact_id="calendar",
            name=f"private calendar of {user}",
        )
        await u.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.task_id and context.context_id
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


# ── optional extension: trace-summary ────────────────────────────────────────────────────────
FAQ = {
    "portugal": "Lisbon is the capital of Portugal.",
    "france": "Paris is the capital of France.",
    "water": "Water boils at 100 C at sea level.",
}


class TraceSummaryExecutor(AgentExecutor):
    """The answer artifact is always there; ``trace-summary`` only if the client activated it.

    A2A: an extension is OPT-IN per request. An unrequested extension must change nothing, so a
    client that never heard of it gets exactly the base behaviour.
    """

    def __init__(self, card: AgentCard, decision: DecisionModel | None = None) -> None:
        self.card, self.decision = card, decision

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        u = await _task_for(context, event_queue)
        text = context.get_user_input().strip()
        await u.start_work()
        steps: list[dict[str, Any]] = []
        models: list[str] = []

        def timed(name: str) -> Any:
            class _T:
                def __enter__(s) -> None:
                    s.t0 = time.perf_counter()

                def __exit__(s, *a: Any) -> None:
                    steps.append(
                        {"name": name, "ms": round((time.perf_counter() - s.t0) * 1000, 2)}
                    )

            return _T()

        with timed("classify"), _tracer.start_as_current_span("lab10.classify"):
            model = self.decision or get_decision_model()
            res = await model.decide(
                f"Question: {text}",
                {
                    "topic": Question(
                        type="choice",
                        instructions="Which topic is the question about?",
                        criteria={"geography": "places", "science": "nature", "other": "anything"},
                    )
                },
            )
            models.append(res.model)
        with timed("lookup"):
            answer = next(
                (a for k, a in FAQ.items() if k in text.lower()), "I don't know that one."
            )
        await u.add_artifact([new_text_part(answer)], artifact_id="answer", name="answer")

        if TRACE_EXT in activated(self.card, context.requested_extensions):
            summary = {
                "spans": len(steps),
                "steps": steps,
                "duration_ms": round(sum(s["ms"] for s in steps), 2),
                "models": models,
                "topic": res.answers["topic"].choice,
            }
            await u.add_artifact(
                [new_data_part(summary)],
                artifact_id="trace-summary",
                name="trace-summary",
                extensions=[TRACE_EXT],
            )
        await u.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.task_id and context.context_id
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


# ── required extension: cost-budget ──────────────────────────────────────────────────────────
class CostBudgetExecutor(AgentExecutor):
    """Paid work behind a REQUIRED extension.

    A2A: "required" means the agent cannot do the job correctly without the extension. A request
    without it is refused with ``ExtensionSupportRequiredError``, never served in a degraded
    mode (the spec forbids silent fallback: a caller must not think a cap was applied).
    """

    def __init__(self, card: AgentCard) -> None:
        self.card = card

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        require_extensions(self.card, context)  # raises ExtensionSupportRequiredError (-32008)
        params = metadata_for(context, BUDGET_EXT)
        try:
            cap = float(params["max_usd"])
        except (KeyError, TypeError, ValueError):
            raise InvalidParamsError(
                message=f"metadata[{BUDGET_EXT!r}] must contain a numeric max_usd"
            ) from None
        u = await _task_for(context, event_queue)
        text = context.get_user_input().strip()
        price = UNIT_COST["report" if "report" in text.lower() else "summary"]
        await u.start_work()
        if price > cap:
            await u.reject(
                _say(
                    u,
                    f"This costs {price:.2f} USD but your cap is {cap:.2f} USD.",
                    new_data_part({"price_usd": price, "max_usd": cap}),
                )
            )
            return
        body = text.split(":", 1)[-1].strip()
        await u.add_artifact(
            [new_text_part(f"Summary: {body[:80]}")], artifact_id="summary", name="summary"
        )
        await u.add_artifact(
            [new_data_part({"spent_usd": price, "max_usd": cap, "currency": "USD"})],
            artifact_id="cost",
            name="cost",
            extensions=[BUDGET_EXT],
        )
        await u.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.task_id and context.context_id
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()
