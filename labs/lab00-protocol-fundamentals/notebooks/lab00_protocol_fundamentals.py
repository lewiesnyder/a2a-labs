# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# %% [markdown]
# # Lab 00 · Protocol fundamentals
#
# **Goal:** use *every* A2A operation, task state, part kind and error against one scripted agent, on all three transports.
# **Time:** about 2 minutes to run (no LLM) · **Prerequisites:** root notebook `00-setup-and-observability`, `make up LAB=lab00`
#
# **What you'll learn**
# - How to read an **Agent Card** and what its interfaces, capabilities and skills promise.
# - The eleven v1.0 operations, including the seven you may know by their legacy names (`tasks/send`, `tasks/get`, ...).
# - Every `TaskState`, and how chunked **Artifacts**, multi-turn `contextId`s and `referenceTaskIds` work.
# - What an A2A error looks like on the wire, and how versions are negotiated.
# - That JSON-RPC, REST and gRPC are three doors into the same agent.
#
# Spec: [A2A specification](https://a2a-protocol.org/latest/specification/)

# %% [markdown]
# ## 1. Architecture
#
# ```mermaid
# flowchart LR
#   NB[Notebook] -->|JSON-RPC :10001| A[lab00-protocol-agent]
#   NB -->|REST :10001/rest| A
#   NB -->|gRPC :10002| A
#   NB --> NS[lab00-protocol-agent-nostream :10003]
#   NB --> NP[lab00-protocol-agent-nopush :10004]
#   A -->|push notifications| W[webhook-receiver :9100]
# ```
#
# | Service | Port | Streaming | Push | Extended card | Purpose |
# |---|---|---|---|---|---|
# | `lab00-protocol-agent` | 10001 (+ gRPC 10002) | yes | yes | yes | The main playground agent |
# | `lab00-protocol-agent-nostream` | 10003 | **no** | yes | no | Shows `UnsupportedOperationError` |
# | `lab00-protocol-agent-nopush` | 10004 | yes | **no** | no | Shows `PushNotificationNotSupportedError` |
# | `webhook-receiver` | 9100 | n/a | n/a | n/a | Receives push notifications |
#
# **Who owns the task?** The *server*. The agent creates the `Task`, assigns its ID and stores it; clients only hold the ID.
# The agent is scripted (no LLM), so every run behaves the same.
#
# Its states, and the command that triggers each transition:
#
# ```mermaid
# stateDiagram-v2
#   [*] --> SUBMITTED: any task command
#   SUBMITTED --> WORKING: work / slow / ask / auth
#   SUBMITTED --> REJECTED: reject
#   WORKING --> COMPLETED: work / slow finishing
#   WORKING --> FAILED: fail
#   WORKING --> CANCELED: CancelTask during work or slow
#   WORKING --> INPUT_REQUIRED: ask
#   WORKING --> AUTH_REQUIRED: auth
#   INPUT_REQUIRED --> WORKING: reply with the same task_id
#   AUTH_REQUIRED --> WORKING: reply with a token
#   COMPLETED --> [*]
#   FAILED --> [*]
#   CANCELED --> [*]
#   REJECTED --> [*]
# ```

# %% tags=["parameters"]
QUICK_DEMO = False

# %% [markdown]
# ## 2. Setup
# Telemetry first, so every call below lands in Grafana.

# %%
# Initialise telemetry, import helpers and wait for the agents
import asyncio
import json

import httpx
from a2a.types import (
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTasksRequest,
    SubscribeToTaskRequest,
    TaskPushNotificationConfig,
    TaskState,
)
from google.protobuf.json_format import MessageToDict

from a2a_labs_common import display
from a2a_labs_common.a2a import events
from a2a_labs_common.a2a.client import (
    collect,
    connect,
    result_text,
    send_text,
    stream_text,
    wait_for_cards,
    wait_for_terminal,
)
from a2a_labs_common.a2a.ids import new_context_id
from a2a_labs_common.a2a.states import state_name
from a2a_labs_common.config import endpoint
from a2a_labs_common.telemetry import init_telemetry
from lab00_protocol.wire import envelope, error_row, message_params, post, rpc

tel = init_telemetry("notebook-lab00", framework="notebook", lab_id="lab00")
AGENT, NOSTREAM, NOPUSH = (
    "lab00-protocol-agent",
    "lab00-protocol-agent-nostream",
    "lab00-protocol-agent-nopush",
)
PUSH_URL = "http://webhook-receiver:9100/webhooks/a2a"  # as the *agent* sees the receiver
await wait_for_cards(
    [AGENT, NOSTREAM, NOPUSH], timeout=120
)  # prints `make up LAB=lab00` on failure
display.table(
    [{"service": s, "url": endpoint(s)} for s in (AGENT, NOSTREAM, NOPUSH, "webhook-receiver")]
)

# %% [markdown]
# ## 3. The crosswalk: legacy → v0.3 → v1.0
# A2A was renamed twice. The first public drafts (v0.1) used `tasks/send`. Version 0.3 moved to `message/send`.
# **Version 1.0** (March 2026) uses PascalCase names and added operations (`ListTasks`, push-config list/delete, the extended card).
# The labs use v1.0 names everywhere; the older ones appear here so you can read older blog posts and code.

# %% a2a_coverage=["op:Discovery"]
# Show the full operation crosswalk (seven requested methods are marked with a star)
display.crosswalk_table()

# %% [markdown]
# ## 4. Discover the agent
# Discovery is **not** an RPC: it is a plain `GET /.well-known/agent-card.json`. The card says who the agent is and how to talk to it.

# %% a2a_coverage=["op:Discovery"]
# Fetch the Agent Card by hand, then with the SDK's resolver
from a2a.client import A2ACardResolver

raw_card = httpx.get(f"{endpoint(AGENT)}/.well-known/agent-card.json").json()
print(
    json.dumps(
        {k: raw_card[k] for k in ("name", "version", "supportedInterfaces", "capabilities")},
        indent=2,
    )
)
async with httpx.AsyncClient() as http:
    card = await A2ACardResolver(http, endpoint(AGENT)).get_agent_card()
display.show_card(card)

# %% [markdown]
# **What just happened**
# - `supportedInterfaces[]` lists every door: JSON-RPC, REST (`/rest`) and gRPC, each with `protocolBinding` and `protocolVersion`.
#   (Docker names are rewritten to `localhost` for notebooks on your host.)
# - `capabilities` is a contract: `streaming`, `pushNotifications` and `extendedAgentCard` gate operations.
# - `skills[]` advertise what the agent is for; clients and registries use them to choose agents.

# %% [markdown]
# ## 5. The raw wire, three ways
# Before using the SDK, look at the JSON-RPC envelope. The same request is sent with three different method names.

# %% a2a_coverage=["op:SendMessage"]
# (a) v1.0: PascalCase method, A2A-Version header, SCREAMING_SNAKE enums
req = envelope("SendMessage", message_params("work 1"))
status, resp = await post(AGENT, req)
display.show_wire(req, resp)

# %% a2a_coverage=["compat:v03"]
# (b) v0.3: `message/send`, no version header (a missing header means 0.3). Note `kind` and lowercase states
v03 = envelope(
    "message/send",
    {
        "message": {
            "kind": "message",
            "messageId": "w2",
            "role": "user",
            "parts": [{"kind": "text", "text": "work 1"}],
        }
    },
)
async with httpx.AsyncClient(base_url=endpoint(AGENT)) as http:
    resp03 = (await http.post("/", json=v03)).json()
display.show_wire(v03, resp03)

# %% a2a_coverage=["legacy:rejected"]
# (c) Legacy v0.1 `tasks/send`: current servers answer -32601 Method not found
legacy = envelope("tasks/send", message_params("work 1"))
_, resp_legacy = await post(AGENT, legacy)
display.show_wire(legacy, resp_legacy)

# %% [markdown]
# **What just happened**
# - (a) `SendMessage` returned a `Task` with `TASK_STATE_COMPLETED` (ProtoJSON enum names).
# - (b) The v0.3 name still works through the SDK's *compat mode*, but the shape is different: `kind` discriminators and `"completed"`.
# - (c) `tasks/send` is history: no SDK serves it, so the server answers `-32601`.
#
# > ⚠️ Pitfall: tutorials using `tasks/send`, `TextPart` or lowercase states target old versions. Translate them.

# %% [markdown]
# ## 6. SendMessage (legacy `tasks/send`)
# `SendMessage` returns either a **Message** (a direct reply, no task) or a **Task**. By default it *blocks* until the task finishes.
# With `return_immediately=True` it returns as soon as the task exists (v0.3 called this the inverse flag, `blocking`).

# %% a2a_coverage=["op:SendMessage", "part:text"]
# Direct Message reply, then a blocking Task with an artifact
c = await connect(AGENT, streaming=False)
echo = await send_text(c, "echo hello", metadata={"demo": True})
print("echo ->", echo.kind, "|", echo.text)
work = await send_text(c, "work 3")
print("work ->", work.kind, state_name(work.state), "|", repr(work.text))
display.show_task(work.task)

# %% a2a_coverage=["op:SendMessage", "state:SUBMITTED", "state:WORKING"]
# Return immediately: the task is created but not finished
slow = await send_text(c, "slow 6", return_immediately=True)
print("returned at", state_name(slow.state), "- task", slow.task_id)

# %% [markdown]
# **What just happened**
# - `echo` replied with a `Message` only: no `Task`, no task ID, nothing to poll.
# - `work 3` created a `Task` and the blocking call returned once it was `COMPLETED`, with results in an **Artifact** (never in a status message).
# - `slow 6` came back while the task was still active; we keep its ID for the next steps.

# %% [markdown]
# ## 7. GetTask (legacy `tasks/get`)
# `GetTask` returns a snapshot. `history_length` limits how many messages come back (`0` means none).

# %% a2a_coverage=["op:GetTask", "state:COMPLETED"]
# Poll the slow task until it is terminal, then compare history lengths
done = await wait_for_terminal(c, slow.task_id, poll_interval=0.5, timeout=30)
print("final state:", state_name(done.status.state))
for n in (0, 10):
    t = await c.client.get_task(GetTaskRequest(id=slow.task_id, history_length=n))
    print(f"history_length={n} -> {len(t.history)} message(s)")

# %% a2a_coverage=["op:GetTask", "errors"]
# An unknown task ID is a TaskNotFoundError (-32001)
try:
    await c.client.get_task(GetTaskRequest(id="does-not-exist"))
except Exception as err:
    print(type(err).__name__, "-", err)

# %% [markdown]
# ## 8. ListTasks
# Cursor pagination: ask for `page_size` items and follow `next_page_token` until it is empty.

# %% a2a_coverage=["op:ListTasks"]
# List everything, filter by context and status, then paginate two at a time
everything = await c.client.list_tasks(ListTasksRequest())
print("total tasks:", len(everything.tasks))
by_ctx = await c.client.list_tasks(ListTasksRequest(context_id=work.context_id))
print("in work's context:", len(by_ctx.tasks))
completed = await c.client.list_tasks(ListTasksRequest(status=TaskState.TASK_STATE_COMPLETED))
print("COMPLETED:", len(completed.tasks))
pages, token = 0, ""
while True:
    page = await c.client.list_tasks(ListTasksRequest(page_size=2, page_token=token))
    pages, token = pages + 1, page.next_page_token
    if not token:
        break
print("pages of 2:", pages)

# %% [markdown]
# ## 9. SendStreamingMessage (legacy `tasks/sendSubscribe`, v0.3 `message/stream`)
# The stream starts with the `Task`, then status updates and artifact chunks. v1.0 has **no `final` flag**: the stream simply closes.
# Streaming works only if the client asks for it *and* the card says `streaming: true`.

# %% a2a_coverage=["op:SendStreamingMessage", "artifact:chunked", "transport:jsonrpc"]
# Stream `work 5` and print every event
cs = await connect(AGENT, streaming=True)
evs = []
async for ev in stream_text(cs, "work 5"):
    display.show_event(ev)
    evs.append(ev)

# %% a2a_coverage=["artifact:chunked"]
# Reassemble the chunked artifact: first chunk append=False, later append=True, last has last_chunk=True
chunks = [e.artifact_update for e in evs if events.kind(e) == "artifact_update"]
display.table(
    [
        {"append": x.append, "last_chunk": x.last_chunk, "text": x.artifact.parts[0].text.strip()}
        for x in chunks
    ]
)
print(repr(collect(evs).text))

# %% [markdown]
# **What just happened**
# - First event: the `Task`. Then `status_update`s (`step i/5`) and `artifact_update`s, one per chunk.
# - The artifact kept one `artifact_id`; `append` and `last_chunk` tell the client how to stitch it.

# %% [markdown]
# ## 10. CancelTask (legacy `tasks/cancel`)
# Cancellation is a request: the executor decides how to stop. Terminal tasks cannot be cancelled (`-32002`).

# %% a2a_coverage=["op:CancelTask", "state:CANCELED", "errors"]
# Start a long task, cancel it, then try to cancel it again
long = await send_text(c, "slow 30", return_immediately=True)
await asyncio.sleep(2)
canceled = await c.client.cancel_task(CancelTaskRequest(id=long.task_id))
print("after cancel:", state_name(canceled.status.state))
try:
    await c.client.cancel_task(CancelTaskRequest(id=long.task_id))
except Exception as err:
    print("second cancel ->", type(err).__name__)

# %% a2a_coverage=["op:CancelTask", "op:SendStreamingMessage"]
# Cancel while a stream is attached: the CANCELED status arrives on the stream
async for ev in stream_text(cs, "slow 30"):
    if events.kind(ev) == "task":
        await cs.client.cancel_task(CancelTaskRequest(id=ev.task.id))
print("last event on the stream:", events.kind(ev), events.state_of(ev))

# %% [markdown]
# ## 11. SubscribeToTask (legacy `tasks/resubscribe`)
# If a stream drops, `SubscribeToTask` re-attaches. The **first event is the current `Task` snapshot**, then live updates.
# Several clients can subscribe at once; subscribing to a finished task is an error.

# %% a2a_coverage=["op:SubscribeToTask", "state:WORKING"]
# Start a streamed task, drop the connection after 3 events, then re-attach
seen, task_id = 0, None
async for ev in stream_text(cs, "slow 8"):
    task_id = ev.task.id if events.kind(ev) == "task" else task_id
    seen += 1
    if seen == 3:
        break  # simulated network drop
resumed = [ev async for ev in cs.client.subscribe(SubscribeToTaskRequest(id=task_id))]
print("first event after re-attach:", events.kind(resumed[0]), events.state_of(resumed[0]))
print("last event:", events.kind(resumed[-1]), events.state_of(resumed[-1]))

# %% a2a_coverage=["op:SubscribeToTask", "errors"]
# Two concurrent subscribers see the same stream; a finished task cannot be subscribed to
first = await anext(aiter(stream_text(cs, "slow 4", return_immediately=True)))
tid = first.task.id


async def watch():
    return [
        f"{events.kind(e)}:{events.state_of(e)}"
        async for e in cs.client.subscribe(SubscribeToTaskRequest(id=tid))
    ]


a, b = await asyncio.gather(watch(), watch())
print("both end the same way:", a[-1] == b[-1], "|", a[-1])
try:
    await watch()
except Exception as err:
    print("subscribe to finished task ->", type(err).__name__)

# %% [markdown]
# ## 12. Push notifications (legacy `tasks/pushNotification/set` and `get`)
# Instead of holding a connection open, the client registers a **webhook**. The agent POSTs each update to it.
# The *token* we register comes back in the `X-A2A-Notification-Token` header so the receiver can check who is calling.
# This step also uses the three operations the legacy list never had: **List**, **Delete** and the inline form.

# %% a2a_coverage=["op:CreatePushConfig", "op:GetPushConfig", "op:ListPushConfigs"]
# Start a task, register a webhook for it, then read the config back and list all configs
rx = httpx.Client(base_url=endpoint("webhook-receiver"))
rx.delete("/notifications")
job = await send_text(c, "slow 5", return_immediately=True)
rx.post("/expectations", json={"task_id": job.task_id, "token": "lab00-token"})
cfg = TaskPushNotificationConfig(task_id=job.task_id, id="main", url=PUSH_URL, token="lab00-token")
created = await c.client.create_task_push_notification_config(cfg)
got = await c.client.get_task_push_notification_config(
    GetTaskPushNotificationConfigRequest(task_id=job.task_id, id="main")
)
listed = await c.client.list_task_push_notification_configs(
    ListTaskPushNotificationConfigsRequest(task_id=job.task_id)
)
print(
    "created id:", created.id, "| get url:", got.url, "| configs:", [x.id for x in listed.configs]
)

# %% a2a_coverage=["op:CreatePushConfig"]
# Watch the notifications arrive at the receiver (it also verifies the token)
for _ in range(100):
    rows = rx.get("/notifications", params={"task_id": job.task_id}).json()
    if any(r["state"] == "COMPLETED" for r in rows):
        break
    await asyncio.sleep(0.2)
display.table(
    [{"kind": r["kind"], "state": r["state"], "token_valid": r["token_valid"]} for r in rows]
)

# %% a2a_coverage=["op:DeletePushConfig"]
# Delete the config and list again (empty)
await c.client.delete_task_push_notification_config(
    DeleteTaskPushNotificationConfigRequest(task_id=job.task_id, id="main")
)
after = await c.client.list_task_push_notification_configs(
    ListTaskPushNotificationConfigsRequest(task_id=job.task_id)
)
print("configs after delete:", len(after.configs))

# %% a2a_coverage=["op:CreatePushConfig", "op:SendMessage"]
# Inline variant: attach the push config to the SendMessage request itself
inline_cfg = TaskPushNotificationConfig(url=PUSH_URL, token="inline-token")
ci = await connect(AGENT, streaming=False, push_config=inline_cfg)
inline = await send_text(ci, "work 2", return_immediately=True)
rx.post("/expectations", json={"task_id": inline.task_id, "token": "inline-token"})
for _ in range(100):
    rows = rx.get("/notifications", params={"task_id": inline.task_id}).json()
    if any(r["state"] == "COMPLETED" for r in rows):
        break
    await asyncio.sleep(0.2)
print("inline push: received", len(rows), "notifications, last state:", rows[-1]["state"])

# %% a2a_coverage=["errors"]
# An agent whose card says pushNotifications=false rejects every push operation (-32003)
np_client = await connect(NOPUSH, streaming=False)
try:
    await np_client.client.create_task_push_notification_config(
        TaskPushNotificationConfig(task_id="x", url=PUSH_URL)
    )
except Exception as err:
    print(type(err).__name__, "-", err)

# %% [markdown]
# **What just happened**
# - We created a push config for an already-running task, read it back, listed it, then deleted it.
# - The receiver got `status_update` and `artifact_update` bodies as ProtoJSON `StreamResponse`s, with `token_valid=True`.
# - Push auth (JWTs signed by the agent) is covered in Lab 08. Treat webhook URLs as untrusted input (SSRF).

# %% [markdown]
# ## 13. Multi-turn conversations and context
# A task that needs more input pauses in `INPUT_REQUIRED`. The client continues by sending a message with the **same `task_id`**.
# A new message with only the `context_id` starts a *new task in the same conversation*.

# %% a2a_coverage=["context:multiturn", "state:INPUT_REQUIRED", "state:COMPLETED"]
# Ask a question, answer it, then start a second task in the same context
q = await send_text(c, "ask")
print("1:", state_name(q.state), "-", q.task.status.message.parts[0].text)
a1 = await send_text(c, "green", task_id=q.task_id, context_id=q.context_id)
print("2:", state_name(a1.state), "- same task:", a1.task_id == q.task_id, "-", a1.text)
nxt = await send_text(c, "work 1", context_id=q.context_id)
ctx_tasks = await c.client.list_tasks(ListTasksRequest(context_id=q.context_id))
print("3: new task:", nxt.task_id != q.task_id, "| tasks in context:", len(ctx_tasks.tasks))

# %% a2a_coverage=["refs", "errors"]
# referenceTaskIds point at earlier tasks; a mismatched context/task pair is rejected
refs = await send_text(c, "refs", reference_task_ids=[q.task_id])
print(refs.text)
try:
    await send_text(c, "oops", task_id=q.task_id, context_id=new_context_id())
except Exception as err:
    print("mismatched ids ->", type(err).__name__, "-", err)

# %% [markdown]
# ## 14. AUTH_REQUIRED, FAILED and REJECTED
# `AUTH_REQUIRED` is for *in-task* authentication (for example a downstream API that needs the user's token).
# It is not how you authenticate to the agent itself (that is HTTP-level, Lab 10).

# %% a2a_coverage=["state:AUTH_REQUIRED", "state:COMPLETED", "state:FAILED", "part:data"]
# Resume an auth task with a data part: the right token completes it, a wrong one fails it
for token in ("letmein", "wrong"):
    ar = await send_text(c, "auth")
    resumed_auth = await send_text(
        c, "here", task_id=ar.task_id, context_id=ar.context_id, data={"token": token}
    )
    print(
        f"{token!r}: {state_name(ar.state)} -> {state_name(resumed_auth.state)}", resumed_auth.text
    )

# %% a2a_coverage=["state:FAILED", "state:REJECTED"]
# The remaining terminal states
for cmd in ("fail", "reject"):
    r = await send_text(c, cmd)
    print(cmd, "->", state_name(r.state), "|", r.task.status.message.parts[0].text)

# %% [markdown]
# ## 15. Parts gallery
# A `Part` holds exactly one of: `text`, `raw` (bytes), `url` or `data` (JSON). `media_type` says how to read it.

# %% a2a_coverage=["part:text", "part:raw", "part:url", "part:data"]
# Request every part kind and show each one
from IPython.display import Image, display as show

gallery = (await send_text(c, "parts")).task
for art in gallery.artifacts:
    part = art.parts[0]
    kind = part.WhichOneof("content")
    print(f"[{art.name}] oneof={kind} media_type={part.media_type or '-'}")
    if kind == "raw":
        show(Image(data=part.raw))
    elif kind in ("url", "text"):
        print("   ", getattr(part, kind))
    else:
        print("   ", MessageToDict(part.data))

# %% a2a_coverage=["errors"]
# accepted_output_modes is a hint: this SDK does not enforce it, the agent is responsible
from a2a.types import SendMessageConfiguration, SendMessageRequest
from a2a.helpers import new_text_message
from a2a.types import Role

req = SendMessageRequest(
    message=new_text_message("echo hi", role=Role.ROLE_USER),
    configuration=SendMessageConfiguration(accepted_output_modes=["image/png"]),
)
reply = [e async for e in c.client.send_message(req)]
print("agent replied anyway with:", events.event_text(reply[0]))

# %% [markdown]
# ## 16. Error catalog
# Every A2A error has a numeric code and, in v1.0, a machine-readable `reason` inside `error.data`.
# This one cell triggers them all.

# %% a2a_coverage=["errors"]
# Trigger each error and tabulate code, reason and message
img = {
    "message": {
        "messageId": "m",
        "role": "ROLE_USER",
        "parts": [{"text": "x", "mediaType": "image/png"}],
    }
}
rows = []
for name, svc, body, hdr in [
    ("malformed JSON", AGENT, "{not json", None),
    (
        "missing jsonrpc field",
        AGENT,
        {"id": 1, "method": "SendMessage", "params": message_params("echo")},
        None,
    ),
    ("unknown method", AGENT, envelope("tasks/send", message_params("echo")), None),
    (
        "invalid params",
        AGENT,
        envelope("SendMessage", {"message": {"messageId": "m", "role": "ROLE_USER", "parts": []}}),
        None,
    ),
    ("TaskNotFound", AGENT, envelope("GetTask", {"id": "nope"}), None),
    (
        "PushNotSupported",
        NOPUSH,
        envelope("ListTaskPushNotificationConfigs", {"taskId": "x"}),
        None,
    ),
    (
        "streaming on nostream",
        NOSTREAM,
        envelope("SendStreamingMessage", message_params("echo hi")),
        None,
    ),
    ("ContentTypeNotSupported", NOSTREAM, envelope("SendMessage", img), None),
    ("extended card, none configured", NOSTREAM, envelope("GetExtendedAgentCard"), None),
    (
        "VersionNotSupported (9.9)",
        AGENT,
        envelope("SendMessage", message_params("echo hi")),
        {"A2A-Version": "9.9"},
    ),
    ("executor crash", AGENT, envelope("SendMessage", message_params("crash")), None),
]:
    status, resp = await post(svc, body, hdr)
    rows.append(error_row(name, resp, status))
display.table(rows, "A2A errors")

# %% [markdown]
# **What just happened**
# - Transport-level problems (`-32700`, `-32600`, `-32601`) come from JSON-RPC itself; `-32001` to `-32009` are A2A's own.
# - A capability that the card does not advertise is rejected by the SDK before your executor runs.
# - A crashing executor becomes `-32603`. Notice the SDK returns the exception text to the caller.
# - The spec table lists `-32007` for "no extended card"; this SDK answers `-32004` when the capability is off (see `docs/spec-deviations.md`).
#
# > ⚠️ Pitfall: don't put secrets in exception messages; they travel to the client.

# %% [markdown]
# ## 17. One agent, three transports
# The same handler and the same `TaskStore` sit behind JSON-RPC, REST and gRPC. A task created over one is visible over the others.

# %% a2a_coverage=["transport:jsonrpc", "transport:rest", "transport:grpc"]
# Send `work 3` over each binding with the SDK client
results = {}
for binding in ("JSONRPC", "HTTP+JSON", "GRPC"):
    async with await connect(AGENT, streaming=False, transport=binding) as tc:
        r = await send_text(tc, "work 3")
        results[binding] = r
        print(f"{binding:10} -> {state_name(r.state)} {r.text!r}")

# %% a2a_coverage=["transport:rest"]
# The same call as a raw REST request (curl equivalent)
async with httpx.AsyncClient() as http:
    rest = await http.post(
        f"{endpoint(AGENT)}/rest/message:send",
        headers={"A2A-Version": "1.0"},
        json={
            "message": {
                "messageId": "r1",
                "role": "ROLE_USER",
                "parts": [{"text": "echo over REST"}],
            }
        },
    )
print(rest.status_code, rest.headers["content-type"])
print(json.dumps(rest.json(), indent=2)[:300])

# %% a2a_coverage=["transport:grpc", "transport:rest", "op:GetTask"]
# A task created over gRPC is fetched over REST
async with await connect(AGENT, streaming=False, transport="HTTP+JSON") as rest_client:
    t = await rest_client.client.get_task(GetTaskRequest(id=results["GRPC"].task_id))
print(
    "created over gRPC, read over REST:",
    t.id == results["GRPC"].task_id,
    state_name(t.status.state),
)

# %% [markdown]
# ## 18. Version negotiation
# The client announces its version in the `A2A-Version` header. No header means 0.3. An unknown version is an error.

# %% a2a_coverage=["versioning"]
# Same request with 1.0, no header and 9.9
body = envelope("SendMessage", message_params("echo hi"))
for label, hdr in [
    ("A2A-Version: 1.0", {}),
    ("no header", {"A2A-Version": ""}),
    ("A2A-Version: 9.9", {"A2A-Version": "9.9"}),
]:
    status, resp = await post(AGENT, body, hdr)
    outcome = (
        resp["error"]["message"][:70]
        if "error" in resp
        else "OK (" + ",".join(resp["result"]) + ")"
    )
    print(f"{label:18} -> {outcome}")

# %% [markdown]
# **What just happened**
# - With `A2A-Version: 1.0` the v1.0 request works.
# - With no header the server speaks **0.3**, so a request using v1.0 *names and shapes* is refused. A real 0.3 client sends `message/send` (step 5b) and works.
# - `9.9` is rejected with `VersionNotSupportedError` (`-32009`). Always send the header; never rely on the default.
#
# > ⚠️ Pitfall: the spec also allows a version query parameter, but this SDK ignores it. Use the header.

# %% [markdown]
# ## 19. The extended card
# An agent can publish a richer card to authenticated callers only. Here the extended card adds an `admin` skill and unlocks `stats`.

# %% a2a_coverage=["op:GetExtendedAgentCard"]
# Without a token the request is refused (HTTP 401); with the bearer token we get the extended card
try:
    await c.client.get_extended_agent_card(GetExtendedAgentCardRequest())
except Exception as err:
    print("anonymous ->", type(err).__name__)
admin = await connect(AGENT, streaming=False, headers={"Authorization": "Bearer lab00-admin"})
ext = await admin.client.get_extended_agent_card(GetExtendedAgentCardRequest())
print("extended skills:", [s.id for s in ext.skills])
stats = await send_text(admin, "stats")
print("stats ->", MessageToDict(stats.task.artifacts[0].parts[0].data))

# %% [markdown]
# > 🔍 Under the hood: the SDK scopes tasks to the authenticated caller. Tasks you created anonymously are invisible to the admin identity (try `GetTask` with `admin` on an earlier task ID).

# %% [markdown]
# ## 20. See it in Grafana
# Every call above was traced. Open the notebook trace, or search by method.

# %%
# Flush telemetry and print a link to this notebook's last span
with display.cell_span("lab00 grafana link") as span:
    pass
tel.flush()
display.trace_link(span)

# %% [markdown]
# Try these in Grafana → Explore:
# - **TraceQL:** `{ resource.service.name = "lab00-protocol-agent" && span.rpc.method = "SubscribeToTask" }`
# - **LogQL:** `{service_name="lab00-protocol-agent"} |= "task state"`
# - **PromQL:** `sum by (to) (a2a_server_task_state_transitions_total)`
#
# *Screenshot placeholder:* the trace waterfall shows `a2a.client SubscribeToTask` as the parent of the server's `a2a.server.execute` span.

# %% [markdown]
# ## 21. Exercises
# - 🟢 **Easy:** add a `countdown <n>` command that streams the numbers as artifact chunks.
# - 🟡 **Medium:** make `slow` honour a `deadline` in message metadata and fail when it is exceeded.
# - 🔴 **Stretch:** swap `InMemoryTaskStore` for `DatabaseTaskStore` (SQLite), restart the container mid-task and show that `GetTask` still works.

# %% tags=["skip-ci"] jupyter={"source_hidden": true}
# Hidden solution (easy): in executors/playground.py add `_cmd_countdown`
# async def _cmd_countdown(self, u, ctx, arg, cancel):
#     n = _int(arg, 3)
#     await u.start_work()
#     for i in range(n, 0, -1):
#         await u.add_artifact([new_text_part(f"{i}\n")], artifact_id="countdown",
#                              append=i != n, last_chunk=i == 1)
#     await u.complete()
# and add "countdown" to _TASK_COMMANDS.

# %% [markdown]
# ## 22. Recap
#
# | Operation (legacy) | Use it when |
# |---|---|
# | `SendMessage` (`tasks/send`) | One request, one answer; blocking is fine |
# | `SendStreamingMessage` (`tasks/sendSubscribe`) | You want progress and partial artifacts live |
# | `GetTask` (`tasks/get`) | Polling, or checking a task you hold an ID for |
# | `ListTasks` | Finding tasks by context or status |
# | `CancelTask` (`tasks/cancel`) | The user changed their mind |
# | `SubscribeToTask` (`tasks/resubscribe`) | A stream dropped, or a second viewer joins |
# | Push config create/get/list/delete | Long jobs: let the agent call *you* |
# | `GetExtendedAgentCard` | Authenticated callers need more capabilities |
#
# **Pitfalls:** legacy names; `final` flags (gone in 1.0); results in status messages (use Artifacts); trusting webhook URLs; assuming `accepted_output_modes` is enforced.
#
# **Next:** Lab 01 talks to agents built with ADK, Strands and LangGraph using exactly this client code.

# %% [markdown]
# ## Cleanup

# %%
# Close clients and flush telemetry
for client in (c, cs, ci, np_client, admin):
    await client.aclose()
tel.flush()
