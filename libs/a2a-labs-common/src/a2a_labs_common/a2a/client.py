"""Client helpers, thin over ``a2a.client`` (spec 03 §4).

Implements: spec 03 §4. Notebooks may use the raw SDK; Lab 00 does, on purpose.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import grpc
import httpx
from a2a.client import (
    A2ACardResolver,
    AuthInterceptor,
    Client,
    ClientCallContext,
    ClientConfig,
    CredentialService,
    create_client,
)
from a2a.helpers import get_message_text, new_text_message
from a2a.types import (
    AgentCard,
    Message,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskPushNotificationConfig,
    TaskState,
)
from google.protobuf.json_format import ParseDict

from a2a_labs_common import config
from a2a_labs_common.a2a import events
from a2a_labs_common.a2a.states import TERMINAL, state_name
from a2a_labs_common.telemetry.a2a_client import TelemetryInterceptor, finish

Binding = Literal["JSONRPC", "HTTP+JSON", "GRPC"]


@dataclass
class SendResult:
    kind: Literal["message", "task"]
    message: Message | None
    task: Task | None
    events: list[StreamResponse] = field(default_factory=list)

    @property
    def context_id(self) -> str:
        return (self.task or self.message).context_id  # type: ignore[union-attr]

    @property
    def task_id(self) -> str | None:
        return self.task.id if self.task else None

    @property
    def state(self) -> TaskState | None:
        return self.task.status.state if self.task else None

    @property
    def text(self) -> str:
        return result_text(self.task or self.message)  # type: ignore[arg-type]


@dataclass
class Connected:
    name: str
    card: AgentCard
    client: Client
    httpx_client: httpx.AsyncClient

    async def aclose(self) -> None:
        await self.client.close()
        await self.httpx_client.aclose()

    async def __aenter__(self) -> Connected:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


def _base_url(name_or_url: str) -> str:
    return name_or_url if "://" in name_or_url else config.endpoint(name_or_url)


def bridge_card_urls(card: AgentCard) -> None:
    # CUSTOM: host/docker URL bridging. Cards advertise docker DNS names; a notebook on the
    # host cannot resolve them, so rewrite each interface URL to its host_url (spec 02 §4).
    if config.settings().runtime == "docker":
        return
    services = config._endpoints()
    for iface in card.supported_interfaces:
        for entry in services.values():
            d, h = str(entry["docker_url"]), str(entry["host_url"])
            if iface.url == d or iface.url.startswith(d + "/"):
                iface.url = h + iface.url[len(d) :]
                break


async def connect(
    name_or_url: str,
    *,
    streaming: bool = True,
    push_config: TaskPushNotificationConfig | None = None,
    headers: Mapping[str, str] | None = None,
    extensions: Sequence[str] = (),
    auth: CredentialService | None = None,
    transport: Binding | None = None,
    a2a_version: str | None = None,
    httpx_client: httpx.AsyncClient | None = None,
    timeout: float = 60,  # noqa: ASYNC109 - the httpx read timeout, not an asyncio deadline
) -> Connected:
    """``timeout`` is the HTTP read timeout: raise it for a non-streaming call that waits for a
    whole multi-agent chain (a streaming call keeps the connection alive with events)."""
    hdrs = {"A2A-Version": a2a_version or config.settings().a2a_version, **(headers or {})}
    if extensions:
        hdrs["A2A-Extensions"] = ",".join(extensions)
    http = httpx_client or httpx.AsyncClient(timeout=timeout)
    http.headers.update(hdrs)
    card = await A2ACardResolver(http, _base_url(name_or_url)).get_agent_card()
    bridge_card_urls(card)
    return await connect_with_card(
        card,
        name=name_or_url,
        http=http,
        streaming=streaming,
        push_config=push_config,
        auth=auth,
        transport=transport,
    )


async def connect_with_card(
    card: AgentCard,
    *,
    name: str | None = None,
    http: httpx.AsyncClient | None = None,
    streaming: bool = True,
    push_config: TaskPushNotificationConfig | None = None,
    auth: CredentialService | None = None,
    transport: Binding | None = None,
) -> Connected:
    """Connect using a card you already hold (e.g. one you verified): nothing is re-fetched."""
    http = http or httpx.AsyncClient(
        timeout=60, headers={"A2A-Version": config.settings().a2a_version}
    )
    cfg = ClientConfig(
        streaming=streaming,
        httpx_client=http,
        push_notification_config=push_config,
        # A2A: the gRPC binding needs a channel factory (only used if gRPC is chosen).
        grpc_channel_factory=lambda url: grpc.aio.insecure_channel(url),
    )
    if transport:
        cfg.supported_protocol_bindings = [transport]
        cfg.use_client_preference = True
    interceptors: list[Any] = [TelemetryInterceptor(card.name)]
    if auth is not None:
        interceptors.append(AuthInterceptor(auth))
    client = await create_client(card, client_config=cfg, interceptors=interceptors)
    return Connected(name=name or card.name, card=card, client=client, httpx_client=http)


def _request(
    text: str,
    *,
    context_id: str | None,
    task_id: str | None,
    reference_task_ids: Sequence[str],
    metadata: Mapping[str, Any] | None,
    data: Mapping[str, Any] | None,
    return_immediately: bool,
    history_length: int | None,
) -> SendMessageRequest:
    msg = new_text_message(text, context_id=context_id, task_id=task_id, role=Role.ROLE_USER)
    msg.reference_task_ids.extend(reference_task_ids)
    if metadata:
        ParseDict(dict(metadata), msg.metadata)
    if data:
        part = msg.parts.add()
        ParseDict(dict(data), part.data.struct_value)
        part.media_type = "application/json"
    cfg = SendMessageConfiguration(return_immediately=return_immediately)
    if history_length is not None:
        cfg.history_length = history_length
    return SendMessageRequest(message=msg, configuration=cfg)


async def stream_text(
    c: Connected,
    text: str,
    *,
    context_id: str | None = None,
    task_id: str | None = None,
    reference_task_ids: Sequence[str] = (),
    metadata: Mapping[str, Any] | None = None,
    data: Mapping[str, Any] | None = None,
    return_immediately: bool = False,
    history_length: int | None = None,
) -> AsyncIterator[StreamResponse]:
    req = _request(
        text,
        context_id=context_id,
        task_id=task_id,
        reference_task_ids=reference_task_ids,
        metadata=metadata,
        data=data,
        return_immediately=return_immediately,
        history_length=history_length,
    )
    ctx = ClientCallContext()
    err: BaseException | None = None
    try:
        async for ev in c.client.send_message(req, context=ctx):
            yield ev
    except BaseException as exc:
        err = exc
        raise
    finally:
        finish(ctx, err)  # ends the span for streams that close without a terminal event


async def send_text(c: Connected, text: str, **kw: Any) -> SendResult:
    evs = [ev async for ev in stream_text(c, text, **kw)]
    return collect(evs)


def collect(evs: Sequence[StreamResponse]) -> SendResult:
    """Fold an event stream into the final Task (or the single Message).

    A stream that CONTINUES an existing task has no leading ``Task`` event (only status and
    artifact updates), so the Task is rebuilt from the first update's ids.
    """
    task: Task | None = None
    message: Message | None = None
    for ev in evs:
        k = events.kind(ev)
        if k == "message":
            message = ev.message
        elif k == "task":
            task = Task()
            task.CopyFrom(ev.task)
        elif k == "status_update":
            su = ev.status_update
            task = task or Task(id=su.task_id, context_id=su.context_id)
            task.status.CopyFrom(su.status)
        elif k == "artifact_update":
            au = ev.artifact_update
            task = task or Task(id=au.task_id, context_id=au.context_id)
            _merge_artifact(task, au)
    if task is None and message is None:
        raise ValueError("no task or message in response")
    return SendResult("task" if task else "message", message, task, list(evs))


def _merge_artifact(task: Task, upd: Any) -> None:
    for art in task.artifacts:
        if art.artifact_id == upd.artifact.artifact_id:
            if upd.append:
                art.parts.extend(upd.artifact.parts)
            else:
                art.CopyFrom(upd.artifact)
            return
    task.artifacts.add().CopyFrom(upd.artifact)


async def wait_for_terminal(
    c: Connected,
    task_id: str,
    *,
    poll_interval: float = 1.0,
    timeout: float = 300,  # noqa: ASYNC109 - spec 03 §4 signature
) -> Task:
    from a2a.types import GetTaskRequest

    deadline = time.monotonic() + timeout
    while True:
        task = await c.client.get_task(GetTaskRequest(id=task_id))
        if task.status.state in TERMINAL:
            return task
        if time.monotonic() > deadline:
            raise TimeoutError(f"task {task_id} still {state_name(task.status.state)}")
        await asyncio.sleep(poll_interval)


async def wait_for_cards(
    services: Sequence[str],
    *,
    timeout: float = 120,  # noqa: ASYNC109 - spec 03 §4 signature
) -> None:
    """Poll each agent card; on timeout say which are down and how to start them."""
    deadline = time.monotonic() + timeout
    pending = list(services)
    async with httpx.AsyncClient(timeout=3) as http:
        while pending and time.monotonic() < deadline:
            for s in list(pending):
                try:
                    r = await http.get(_base_url(s) + "/.well-known/agent-card.json")
                    if r.status_code == 200:
                        pending.remove(s)
                except httpx.HTTPError:
                    pass
            if pending:
                await asyncio.sleep(1)
    if pending:
        lab = pending[0].split("-")[0]
        raise RuntimeError(
            f"agents not reachable: {', '.join(pending)}. Start them with: make up LAB={lab}"
        )


def result_text(result: Task | Message) -> str:
    if isinstance(result, Message):
        return get_message_text(result)
    # Streamed chunks arrive as appended parts and must concatenate seamlessly. Skip data/raw
    # parts (ADK tool calls) and ADK "thought" parts, which are the model's private reasoning.
    texts = (
        "".join(p.text for p in a.parts if p.text and not _is_thought(p)) for a in result.artifacts
    )
    return "\n".join(t for t in texts if t)


def _is_thought(part: Any) -> bool:
    md = part.metadata  # a protobuf Struct: no .get()
    return bool(part.HasField("metadata") and "adk_thought" in md and md["adk_thought"])
