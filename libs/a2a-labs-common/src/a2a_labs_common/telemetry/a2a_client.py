"""Client interceptor: one span + metrics per A2A call (spec 05 §3.1).

# CUSTOM: the SDK ships no tracing interceptor; this implements its ClientCallInterceptor.

Approach (documented as the spec requires): the SDK interceptor has ``before`` and a per-event
``after`` but NO end-of-stream hook. So the interceptor opens the span, records each streamed
event and ends the span on a terminal/interrupted state or a Message. The client helpers
(``a2a.client``) call ``finish(context)`` in a ``finally`` for streams that end otherwise.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

from a2a.client import ClientCallContext, ClientCallInterceptor
from a2a.client.interceptors import AfterArgs, BeforeArgs
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from a2a_labs_common.a2a.states import INTERRUPTED, TERMINAL, state_name
from a2a_labs_common.telemetry.metrics import instruments

_tracer = trace.get_tracer("a2a_labs_common.client")
_STATE_KEY = "a2a_labs.client_call"

# SDK client method -> v1.0 operation name
OPERATION = {
    "send_message": "SendMessage",
    "send_message_streaming": "SendStreamingMessage",
    "get_task": "GetTask",
    "list_tasks": "ListTasks",
    "cancel_task": "CancelTask",
    "subscribe": "SubscribeToTask",
    "create_task_push_notification_config": "CreateTaskPushNotificationConfig",
    "get_task_push_notification_config": "GetTaskPushNotificationConfig",
    "list_task_push_notification_configs": "ListTaskPushNotificationConfigs",
    "delete_task_push_notification_config": "DeleteTaskPushNotificationConfig",
    "get_extended_agent_card": "GetExtendedAgentCard",
}
_STREAMING = {"send_message_streaming", "subscribe"}


class _Call:
    def __init__(self, span: trace.Span, token: object, method: str, agent: str) -> None:
        self.span, self.token, self.method, self.agent = span, token, method, agent
        self.start = time.perf_counter()
        self.first_event = True
        self.done = False


class TelemetryInterceptor(ClientCallInterceptor):
    def __init__(self, agent_name: str = "") -> None:
        self.agent_name = agent_name

    async def before(self, args: BeforeArgs) -> None:
        op = OPERATION.get(args.method, args.method)
        agent = self.agent_name or args.agent_card.name
        iface = (
            args.agent_card.supported_interfaces[0]
            if args.agent_card.supported_interfaces
            else None
        )
        span = _tracer.start_span(
            f"a2a.client {op}",
            kind=SpanKind.CLIENT,
            attributes={
                "rpc.system": "a2a",
                "rpc.method": op,
                "a2a.agent.name": agent,
                "a2a.protocol.version": iface.protocol_version if iface else "",
                "a2a.binding": iface.protocol_binding if iface else "",
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": agent,
            },
        )
        # Make this span current so the httpx child span (and traceparent header) hangs off it.
        token = otel_context.attach(trace.set_span_in_context(span))
        if args.context is None:
            args.context = ClientCallContext()
        args.context.state[_STATE_KEY] = _Call(span, token, args.method, agent)

    async def after(self, args: AfterArgs) -> None:
        call: _Call | None = args.context.state.get(_STATE_KEY) if args.context else None
        if call is None or call.done:
            return
        if call.method not in _STREAMING:  # unary: one result, then done
            _annotate_result(call.span, args.result)
            finish(args.context)
            return
        if call.first_event:
            call.first_event = False
            instruments().client_ttfe.record(
                time.perf_counter() - call.start, {"a2a.agent.name": call.agent}
            )
        _annotate_result(call.span, args.result)
        if _ends_stream(args.result):
            finish(args.context)


def _which(result: Any) -> str | None:
    if hasattr(result, "WhichOneof"):
        try:
            return str(result.WhichOneof("payload"))
        except ValueError:
            return None
    return None


def _annotate_result(span: trace.Span, result: Any) -> None:
    which = _which(result)
    task = None
    if which == "task":
        task = result.task
    elif which == "status_update":
        span.add_event(
            "a2a.stream.event",
            {"kind": which, "state": state_name(result.status_update.status.state)},
        )
        span.set_attributes(
            {
                "a2a.task.id": result.status_update.task_id,
                "a2a.context.id": result.status_update.context_id,
                "a2a.task.state": state_name(result.status_update.status.state),
            }
        )
        return
    elif which in ("artifact_update", "message"):
        span.add_event("a2a.stream.event", {"kind": which})
        if which == "message":
            span.set_attribute("a2a.response.kind", "message")
        return
    elif hasattr(result, "status") and hasattr(result, "context_id"):
        task = result  # a bare Task (GetTask / CancelTask)
    elif hasattr(result, "HasField") and hasattr(result, "task") and result.HasField("task"):
        task = result.task  # SendMessageResponse
    elif hasattr(result, "HasField") and hasattr(result, "message") and result.HasField("message"):
        span.set_attribute("a2a.response.kind", "message")
        return
    if task is not None:
        span.set_attributes(
            {
                "a2a.task.id": task.id,
                "a2a.context.id": task.context_id,
                "a2a.task.state": state_name(task.status.state),
                "a2a.response.kind": "task",
            }
        )
        span.add_event("a2a.stream.event", {"kind": "task", "state": state_name(task.status.state)})


def _ends_stream(result: Any) -> bool:
    which = _which(result)
    if which == "message":
        return True
    if which == "task":
        return result.task.status.state in TERMINAL | INTERRUPTED
    if which == "status_update":
        return result.status_update.status.state in TERMINAL | INTERRUPTED
    return False


def finish(context: ClientCallContext | None, error: BaseException | None = None) -> None:
    """End the call span (idempotent) and record request count/duration."""
    call: _Call | None = context.state.get(_STATE_KEY) if context else None
    if call is None or call.done:
        return
    call.done = True
    attrs = {"rpc.method": OPERATION.get(call.method, call.method), "a2a.agent.name": call.agent}
    instruments().client_duration.record(time.perf_counter() - call.start, attrs)
    if error is not None:
        call.span.record_exception(error)
        call.span.set_status(Status(StatusCode.ERROR, str(error)))
        call.span.set_attribute("error.type", type(error).__name__)
        code = getattr(error, "code", None)
        if code is not None:
            call.span.set_attribute("a2a.error.code", int(code))
    instruments().client_requests.add(1, {**attrs, "outcome": "error" if error else "ok"})
    call.span.end()
    # Detaching from a different task only logs inside OTel, but never let it raise.
    with contextlib.suppress(Exception):
        otel_context.detach(call.token)  # type: ignore[arg-type]
