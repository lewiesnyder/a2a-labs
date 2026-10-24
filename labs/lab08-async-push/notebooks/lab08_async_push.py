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
# # Lab 08 · Async work and push notifications (pattern 7)
#
# **Goal:** don't hold the wire. Start long work with `return_immediately`, get **signed** notifications when it moves, and confirm with `GetTask` because **push is a hint, `GetTask` is the truth**.
# **Time:** about 4 minutes (agents sleep a few seconds per step on purpose) · **Prerequisites:** Labs 00 and 01 · `make up LAB=lab08`
#
# **What you'll learn**
# - Push config lifecycle: inline on `SendMessage`, then create / get / list / delete, with **several configs per task**.
# - How a notification is secured in both directions: an **SSRF guard** on the agent side, a **JWT signed by the agent** (verifiable with its JWKS) on the receiver side.
# - Five ways to attack a webhook, and which check stops each.
# - A client **work queue**, a **lost-push** safety net, **re-attaching** with `SubscribeToTask`, and a **durable** task store.
#
# Spec: [A2A specification, push notifications](https://a2a-protocol.org/latest/specification/)

# %% [markdown]
# ## 1. Architecture
#
# ```mermaid
# sequenceDiagram
#   participant C as Client
#   participant A as Agent
#   participant W as Client's webhook
#   C->>A: SendMessage(return_immediately, push config)
#   A-->>C: Task (SUBMITTED/WORKING) at once
#   A->>W: POST status/artifact updates (Bearer JWT, token header)
#   W->>W: verify JWT with the agent's JWKS, iat, body hash, token, jti
#   A->>W: POST COMPLETED
#   W->>A: GetTask (confirm, source of truth)
# ```
#
# | Service | Port | Framework | Long-running part |
# |---|---|---|---|
# | `lab08-sdk-report-agent` | 10801 | a2a-sdk | 6 phases × `PHASE_SECONDS`; **SQLite** task + push-config stores |
# | `lab08-strands-batch-agent` | 10802 | Strands | summarises 5 documents one at a time |
# | `lab08-adk-research-agent` | 10803 | ADK | a slow `research` tool |
# | `lab08-langgraph-pipeline-agent` | 10804 | LangGraph | extract → transform → validate → load |
#
# **Where the webhook lives:** in production the *client* owns it. This notebook starts an **embedded receiver** in its own process (`require_auth=True`), and the agent containers reach it at `host.docker.internal` (or `jupyter` inside compose).

# %% tags=["parameters"]
QUICK_DEMO = False

# %% [markdown]
# ## 2. Setup

# %% a2a_coverage=["op:Discovery"]
# Telemetry, models, the four agents, and the embedded receiver
import asyncio
import base64
import hashlib
import json
import shutil
import subprocess
import time
import uuid

import httpx
import jwt
from a2a.types import (
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    SubscribeToTaskRequest,
    TaskPushNotificationConfig,
)

from a2a_labs_common import display
from a2a_labs_common.a2a.client import connect, send_text, wait_for_cards
from a2a_labs_common.a2a.states import state_name
from a2a_labs_common.config import endpoint, find_config_dir
from a2a_labs_common.telemetry import init_telemetry

from lab08_push.receivers import EmbeddedReceiver
from lab08_push.work_queue import PushWorkQueue

tel = init_telemetry("notebook-lab08", framework="notebook", lab_id="lab08")
display.show_models()
REPORT, BATCH, RESEARCH, PIPELINE = "lab08-sdk-report-agent", "lab08-strands-batch-agent", "lab08-adk-research-agent", "lab08-langgraph-pipeline-agent"
AGENTS = [REPORT, BATCH, RESEARCH, PIPELINE]
await wait_for_cards(AGENTS, timeout=180)  # prints `make up LAB=lab08` if down
receiver = await EmbeddedReceiver().start()
print("receiver:", receiver.base, "| agents reach it at", receiver.agent_base)


async def notes(**kw):
    return await receiver.notifications(**kw)


# %% [markdown]
# ## 3. Concept: don't hold the wire
#
# | | Polling `GetTask` | Streaming | `SubscribeToTask` | **Push** |
# |---|---|---|---|---|
# | Latency | the poll interval | immediate | immediate | immediate |
# | Connection cost | a request per poll | one open connection per task | one open connection | **none** while waiting |
# | Survives a client restart | yes | no | no (re-attach) | **only if the webhook is up** |
# | Firewall needs | outbound only | outbound only | outbound only | the agent must reach **you** |
# | Reliability | high | medium | medium | at-most-once-ish: **a hint** |
#
# So use push to *wake you up*, then `GetTask` to *find out*, and poll slowly as a safety net.

# %% [markdown]
# ## 4. An inline push config on `SendMessage`
# The config (URL + a `token`) rides inside the request. The receiver is told which token and which JWKS to expect **before** the task exists, so the first notification cannot race the setup.

# %% a2a_coverage=["pattern:7", "op:SendMessage", "op:CreatePushConfig", "state:SUBMITTED", "state:WORKING", "artifact:chunked", "state:COMPLETED"]
# Submit with return_immediately and watch notifications arrive while the task runs
token = str(uuid.uuid4())
await receiver.expect(token, receiver.jwks_url(REPORT))  # registered by TOKEN, before the task exists
inline = TaskPushNotificationConfig(url=receiver.webhook_url("main"), token=token, id="main")
rc = await connect(REPORT, streaming=False, push_config=inline)
t0 = time.perf_counter()
run = await send_text(rc, "Build the Q3 report", return_immediately=True)
print(f"returned in {time.perf_counter() - t0:.2f}s with state {state_name(run.state)}; the report takes ~30 s")
seen = 0
while True:
    fresh = [n for n in await notes(task_id=run.task_id) if n["seq"] > seen]
    for n in fresh:
        seen = n["seq"]
        text = ""
        if n["kind"] == "status_update":
            msg = n["payload"]["statusUpdate"]["status"].get("message")
            text = msg["parts"][0].get("text", "") if msg else ""
        print(f"+{n['received_at'] - n['received_at'] + (time.perf_counter() - t0):5.1f}s  {n['kind']:16} {n['state'] or '':10} {text}")
    if any(n["state"] == "COMPLETED" for n in fresh):
        break
    await asyncio.sleep(0.5)

# %% [markdown]
# **What just happened**
# - `SendMessage` returned a `Task` immediately; the agent then POSTed every status and artifact update to *our* webhook. The last one carries `COMPLETED`.
# - Each notification was authenticated (see step 6) before it was stored; the receiver runs with `require_auth=True`, so an unauthenticated POST would have been refused.

# %% [markdown]
# ## 5. CRUD: a second channel for the same task
# Start another report, add a **second** push config to the running task, look at both, delete one, and see that only the other keeps receiving.

# %% a2a_coverage=["pattern:7", "op:CreatePushConfig", "op:GetPushConfig", "op:ListPushConfigs", "op:DeletePushConfig"]
# Create, Get, List, Delete: two channels, then one
tok_a, tok_b = str(uuid.uuid4()), str(uuid.uuid4())
for tok in (tok_a, tok_b):
    await receiver.expect(tok, receiver.jwks_url(REPORT))
a_cfg = TaskPushNotificationConfig(url=receiver.webhook_url("a"), token=tok_a, id="chan-a")
async with await connect(REPORT, streaming=False, push_config=a_cfg) as c:
    job = await send_text(c, "Build the Q3 report", return_immediately=True)
    await c.client.create_task_push_notification_config(
        TaskPushNotificationConfig(task_id=job.task_id, id="chan-b", url=receiver.webhook_url("b"), token=tok_b)
    )
    got = await c.client.get_task_push_notification_config(GetTaskPushNotificationConfigRequest(task_id=job.task_id, id="chan-b"))
    listed = await c.client.list_task_push_notification_configs(ListTaskPushNotificationConfigsRequest(task_id=job.task_id))
    print("Get   :", got.id, "→", got.url)
    print("List  :", [x.id for x in listed.configs])
    await asyncio.sleep(8)
    await c.client.delete_task_push_notification_config(DeleteTaskPushNotificationConfigRequest(task_id=job.task_id, id="chan-b"))
    cut = max(n["seq"] for n in await notes())
    print("Delete: chan-b; List now →", [x.id for x in (await c.client.list_task_push_notification_configs(ListTaskPushNotificationConfigsRequest(task_id=job.task_id))).configs])
    while (await c.client.get_task(GetTaskRequest(id=job.task_id))).status.state != 3:  # COMPLETED
        await asyncio.sleep(1)
rows = await notes(task_id=job.task_id)
print("notifications per channel:", {ch: sum(n["channel"] == ch for n in rows) for ch in ("a", "b")}, "| after the delete:", {n["channel"] for n in rows if n["seq"] > cut})

# %% [markdown]
# **What just happened**
# - One task, **two independent channels** (say, a dashboard and an audit log). The `id` of a config is what Get and Delete address.
# - After `Delete` the agent stopped sending to `chan-b`; `chan-a` still received the final `COMPLETED`.

# %% [markdown]
# ## 6. Verify a notification by hand
# Take one received notification and do what the receiver did: read the JWT, fetch the JWKS, verify the signature, recompute the body hash, compare the token. To have the raw bytes and headers, point one task at a tiny **capture** webhook first.

# %% a2a_coverage=["pattern:7"]
# Capture one real delivery (raw body + headers), then verify it step by step
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
import threading, uvicorn, socket

captured = []


async def hook(request: Request):
    captured.append((await request.body(), dict(request.headers)))
    return JSONResponse({"ok": True})


with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    cap_port = s.getsockname()[1]
cap_server = uvicorn.Server(uvicorn.Config(Starlette(routes=[Route("/{p:path}", hook, methods=["POST"])]), host="0.0.0.0", port=cap_port, log_level="warning"))
threading.Thread(target=cap_server.run, daemon=True).start()
while not cap_server.started:
    await asyncio.sleep(0.05)
cap_token = str(uuid.uuid4())
cap_url = f"{receiver.agent_base.rsplit(':', 1)[0]}:{cap_port}/hook"
async with await connect(PIPELINE, streaming=False, push_config=TaskPushNotificationConfig(url=cap_url, token=cap_token)) as c:
    cap_run = await send_text(c, "Run the nightly pipeline", return_immediately=True)
    while len(captured) < 2:
        await asyncio.sleep(0.3)
body, headers = captured[0]
bearer = headers["authorization"].removeprefix("Bearer ")
header = jwt.get_unverified_header(bearer)
jwks = httpx.get(receiver.jwks_url(PIPELINE)).json()
key = jwt.PyJWK(next(k for k in jwks["keys"] if k["kid"] == header["kid"]))
claims = jwt.decode(bearer, key.key, algorithms=["ES256"], options={"verify_aud": False})
print("header :", header)
print("claims :", {k: (v[:36] + "…") if isinstance(v, str) and len(v) > 36 else v for k, v in claims.items()})
print("signature valid        : yes (jwt.decode succeeded with the agent's JWKS key)")
print("body hash matches      :", claims["request_body_sha256"] == hashlib.sha256(body).hexdigest())
print("token header matches   :", headers["x-a2a-notification-token"] == cap_token)
print("fresh (iat within 5min):", abs(time.time() - claims["iat"]) < 300)

# %% [markdown]
# **What just happened**
# - The JWT binds this notification: `aud` is the webhook URL, `task_id` the task, `request_body_sha256` the exact bytes, `jti` a one-time id and `iat` its age.
# - The signature is checked against the **agent's own JWKS** (`/.well-known/jwks.json`). Trusting a key just because the *notification* carries it would prove nothing; the receiver learns where the JWKS is from its own expectation, registered when it submitted the task.

# %% [markdown]
# ## 7. Attacks
# Five things an attacker (or a bug) can try. Each one is stopped by a different check.

# %% a2a_coverage=["pattern:7", "state:REJECTED", "push:security"]
# (a) SSRF target as a push URL; (b) forged JWT; (c) replay; (d) stale iat; (e) tampered body
await receiver.expect(cap_token, receiver.jwks_url(PIPELINE))
good = {k: v for k, v in headers.items() if k in ("authorization", "x-a2a-notification-token", "content-type")}


def post(content, hdrs):
    return httpx.post(f"{receiver.base}/webhooks/a2a/attack", content=content, headers=hdrs).status_code


results = {}
async with await connect(REPORT, streaming=False) as c:
    victim = await send_text(c, "Build the Q3 report", return_immediately=True)
    try:
        await c.client.create_task_push_notification_config(TaskPushNotificationConfig(task_id=victim.task_id, id="meta", url="http://169.254.169.254/latest/meta-data"))
        results["(a) metadata IP as webhook"] = "ACCEPTED (bad!)"
    except Exception as exc:
        results["(a) metadata IP as webhook"] = f"rejected: {type(exc).__name__}"
    try:
        await c.client.cancel_task(CancelTaskRequest(id=victim.task_id))  # tidy up
    except Exception:  # already finished
        pass
from a2a_labs_common.a2a.push import PushSigner

forged = PushSigner(PIPELINE).sign({"iat": int(time.time()), "jti": "forged"})
results["(b) forged JWT (own key)"] = post(body, {**good, "authorization": f"Bearer {forged}"})
first = post(body, good)
results["(c) the genuine notification"] = first
results["(c) the same one replayed"] = post(body, good)
results["(d) stale iat"] = "needs the agent's key; unit-tested (see tests)"
results["(e) body changed after signing"] = post(body.replace(b'"contextId":"', b'"contextId":"x', 1), {**good, "authorization": f"Bearer {bearer}"})
display.table([{"attack": k, "result": v} for k, v in results.items()])

# %% [markdown]
# | Attack | Stopped by |
# |---|---|
# | (a) push URL = cloud metadata | agent: `push_url_validator` (SDK screen + a small allowlist of compose hosts) |
# | (b) forged JWT | receiver: signature vs the **agent's** JWKS (unknown key → 401) |
# | (c) replay | receiver: a `jti` seen before → 401 |
# | (d) stale `iat` | receiver: older than ±5 min → 401 |
# | (e) tampered body | receiver: `request_body_sha256` mismatch → 401 |
#
# (The first delivery of (c) may show 401 if the captured notification was already used above; the replayed one must always be refused.)

# %% [markdown]
# ## 8. Push in every framework
# One job to each agent, with the same push config shape. Compare what each framework actually pushes.

# %% a2a_coverage=["pattern:7", "pattern:graphnode", "pattern:subagent"]
# Submit one job per agent and tabulate the notifications each produced
q = PushWorkQueue(receiver)
ids = {svc: await q.submit(svc, "Research solid-state batteries") for svc in AGENTS}
t_start = time.perf_counter()
finished = {}
async for job in q.completions():
    finished[job.service] = (job, time.perf_counter() - t_start)
rows = []
for svc, tid in ids.items():
    got = await notes(task_id=tid)
    job, secs = finished[svc]
    rows.append({"agent": svc.replace("lab08-", ""), "pushes": len(got), "kinds": ", ".join(sorted({n["kind"] for n in got})), "final": state_name(job.done.status.state), "confirmed by": job.confirmed_by, "seconds": round(secs, 1)})
display.table(rows)
await q.aclose()

# %% [markdown]
# **What just happened**
# - Same wire shape from four frameworks. They differ in *what moves*: the SDK, LangGraph and ADK agents emit **status updates** (phases, nodes, tool calls) plus artifact chunks; the Strands batch agent's executor sends no per-step status messages (only the task, artifact chunks and the final status), so it pushes fewer.
# - ADK's `to_a2a` accepts a `push_config_store` but has no way to give it a push *sender*, so this agent is built with our server stack around ADK's own executor (DEV-52).

# %% [markdown]
# ## 9. The async work queue
# Eight jobs across the four agents; handle each completion as it arrives (out of order), after `GetTask` confirms it.

# %% a2a_coverage=["pattern:7", "op:GetTask"]
# Fan out 8 jobs, consume completions out of order, draw a tiny timeline
q = PushWorkQueue(receiver)
t0 = time.perf_counter()
submitted = {}
for i in range(8):
    tid = await q.submit(AGENTS[i % 4], f"job {i}")
    submitted[tid] = (i, time.perf_counter() - t0)
timeline = []
async for job in q.completions():
    i, start = submitted[job.task_id]
    timeline.append((i, job.service.replace("lab08-", ""), start, time.perf_counter() - t0, job.confirmed_by))
await q.aclose()
scale = 40 / max(end for *_, end, _ in timeline)
for i, svc, start, end, how in sorted(timeline):
    print(f"job {i} {svc:22} |{' ' * int(start * scale)}{'█' * max(1, int((end - start) * scale))}  done at {end:4.1f}s via {how}")
print("completion order:", [i for i, *_ in sorted(timeline, key=lambda r: r[3])])

# %% [markdown]
# ## 10. A lost push, and the safety net
# Stop the webhook mid-task. The agent's deliveries fail (and retry, then give up); `reconcile()` still finds the task through `GetTask`.

# %% a2a_coverage=["pattern:7", "op:GetTask"]
# Kill the receiver while a task runs; the queue's reconcile() finds the result by polling
lost_rx = await EmbeddedReceiver().start()
lq = PushWorkQueue(lost_rx, quiet_after_s=3, poll_s=0.5)
await lq.submit(PIPELINE, "Run the nightly pipeline")
lost_rx.stop()  # the webhook disappears; every push from now on fails
async for job in lq.completions():
    print("found by:", job.confirmed_by, "| state:", state_name(job.done.status.state), "(no push could have told us)")
await lq.aclose()

# %% [markdown]
# **What just happened**
# - With the receiver down, the SDK sender retried and then logged a failure; the task itself was unaffected. Our queue noticed that task went quiet for `quiet_after_s`, asked `GetTask`, and got the truth.
# - That is why the queue **always** confirms a push with `GetTask` too: a notification can be late, duplicated or lost, and a forged one never gets that far.

# %% [markdown]
# ## 11. Re-attach with `SubscribeToTask`
# A user opens the page while the job is running: swap push for a live stream, then (when it ends) go back to waiting for push.

# %% a2a_coverage=["pattern:7", "op:SubscribeToTask", "op:SendStreamingMessage"]
# Start a job without streaming, then attach a stream to it
async with await connect(PIPELINE, streaming=False) as c:
    sub_run = await send_text(c, "Run the nightly pipeline", return_immediately=True)
await asyncio.sleep(1)
async with await connect(PIPELINE, streaming=True) as c:
    try:
        async for ev in c.client.subscribe(SubscribeToTaskRequest(id=sub_run.task_id)):
            kind = ev.WhichOneof("payload")
            st = state_name(ev.status_update.status.state) if kind == "status_update" else (state_name(ev.task.status.state) if kind == "task" else "")
            text = ev.status_update.status.message.parts[0].text if kind == "status_update" and ev.status_update.status.HasField("message") else ""
            print(f"{kind:16} {st:10} {text}")
    except Exception as exc:  # a finished task cannot be subscribed to (fast fake runs end first)
        print("nothing to attach to:", type(exc).__name__, "- the task had already finished; use GetTask")

# %% [markdown]
# **What just happened**
# - `SubscribeToTask` first sent the task's **current state**, then every later event until the end, so a late joiner needs no replay. On a task that already finished it fails (`UnsupportedOperationError`): use `GetTask` instead.

# %% [markdown]
# ## 12. Durability
# Let a task finish, restart the agent, and ask again. The SDK agent keeps tasks (and push configs) in SQLite on a volume.

# %% a2a_coverage=["pattern:7", "op:GetTask", "taskstore:db"]
# GetTask before and after `docker compose restart` (needs the docker CLI)
async with await connect(REPORT, streaming=False) as c:
    done = await send_text(c, "Build the Q3 report")
print("before restart:", state_name(done.state), "| artifacts:", [a.name for a in done.task.artifacts])
if shutil.which("docker") is None:
    print("docker CLI not found: run `docker compose --profile lab08 restart lab08-sdk-report-agent` yourself, then GetTask again")
else:
    root = find_config_dir().parent
    await asyncio.to_thread(subprocess.run, ["docker", "compose", "--profile", "lab08", "restart", REPORT], cwd=root, capture_output=True)
    await wait_for_cards([REPORT], timeout=120)
    async with await connect(REPORT, streaming=False) as c:
        again = await c.client.get_task(GetTaskRequest(id=done.task_id))
    print("after restart :", state_name(again.status.state), "| artifacts:", [a.name for a in again.artifacts])

# %% [markdown]
# **What just happened**
# - The completed task came back from SQLite. A task that was **in flight** at the restart is *not* resumed: its process is gone, so it stays in its last stored state (usually `WORKING`) until something fails it. A production agent needs a reaper or a resumable executor.

# %% [markdown]
# ## 13. Cancel an async task and watch the push

# %% a2a_coverage=["pattern:7", "op:CancelTask", "state:CANCELED"]
# Cancel a running job; the CANCELED state arrives by push as well
cq = PushWorkQueue(receiver)
cid = await cq.submit(REPORT, "Build the Q3 report")
await asyncio.sleep(3)
try:
    await cq.jobs[cid].conn.client.cancel_task(CancelTaskRequest(id=cid))
except Exception as exc:  # the job already finished (fast settings)
    print('too late to cancel:', type(exc).__name__)
async for job in cq.completions():
    print("final:", state_name(job.done.status.state), "| last push:", (await notes(task_id=cid))[-1]["state"])
await cq.aclose()

# %% [markdown]
# ## 14. See it in Grafana

# %%
# One trace for a submit + its pushes
with display.cell_span("async submit") as span:
    sq = PushWorkQueue(receiver)
    await sq.submit(PIPELINE, "Run the nightly pipeline")
    async for _ in sq.completions():
        pass
    await sq.aclose()
tel.flush()
display.trace_link(span)

# %% [markdown]
# Try in Grafana → Explore:
# - **TraceQL:** `{ name = "a2a.push.deliver" }` shows each delivery with `a2a.push.outcome`, the HTTP status and the attempt count; the failed ones from step 10 retried three times.
# - **TraceQL:** `{ name = "a2a.push.receive" && span.a2a.push.jwt.valid = false }` finds refused notifications on the receiver side (step 7).
# - **Metrics:** `a2a.push.deliveries` by `outcome`, next to `a2a.push.received` by `valid`.

# %% [markdown]
# ## 15. Exercises
# - 🟢 **Easy:** add a `channel` filter to `PushWorkQueue` so two consumers can share one receiver.
# - 🟡 **Medium:** add HMAC-signed pushes (a shared secret per config) as an alternative to JWT and compare what each protects against.
# - 🔴 **Stretch:** back the receiver's queue with Redis Streams (add a `redis` service to the profile).

# %% tags=["skip-ci"] jupyter={"source_hidden": true}
# Hidden solution (easy): give Job a `channel` (= token[:8]) and make completions(channel=None)
# skip jobs whose channel differs; the receiver already stores `channel` on every notification.

# %% [markdown]
# ## 16. Recap
#
# | Pattern 7 | |
# |---|---|
# | **Use it when** | Work takes longer than a request should stay open, or the client may not be around when it ends |
# | **Not when** | The answer is quick (just wait) or the agent cannot reach any webhook of yours (poll / subscribe instead) |
# | Key mechanics | `return_immediately` + push config; signed notifications; confirm with `GetTask`; reconcile the quiet ones |
# | Rules | Validate push URLs (SSRF); verify against the agent's JWKS, not the notification's; dedupe by `jti`; push is a hint |
#
# **Pitfalls:** registering the expectation after the task exists (the first pushes race it); trusting a notification without `GetTask`; letting a webhook URL point at internal services; assuming an in-flight task resumes after a restart.
#
# **Next:** Lab 10 adds real authentication and extensions; Lab 09 builds hierarchical squads.

# %% [markdown]
# ## Cleanup

# %%
# Stop the embedded receiver and the capture server, close clients, flush telemetry
cap_server.should_exit = True
receiver.stop()
await rc.aclose()
tel.flush()
