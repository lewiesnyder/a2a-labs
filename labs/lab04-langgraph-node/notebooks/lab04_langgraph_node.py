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
# # Lab 04 · LangGraph as a framework node (pattern 3)
#
# **Goal:** map a LangGraph graph onto A2A tasks and states, in both directions: a graph **served** as an A2A agent, and a graph that **contains a remote A2A agent as a node**.
# **Time:** about 3 minutes (the planner deliberately sleeps 0.8 s per search so progress is visible) · **Prerequisites:** Labs 00–01 · `REASONING_THINK=false make up LAB=lab04`
#
# **What you'll learn**
# - The mapping table: node → `WORKING`, `interrupt()` → `INPUT_REQUIRED`, `Command(resume=…)` → the next message, END → an artifact, `thread_id` = `contextId`.
# - How a **decision model** (Nimble) can drive a conditional edge, and how its probabilities show up in state and traces.
# - How to cancel a graph mid-run and what the checkpoint keeps.
# - How an interrupt **propagates across an A2A boundary** (A2A → LangGraph → A2A → LangGraph).
# - What the LangGraph Agent Server's built-in A2A endpoint does and does not do.
#
# Spec: [A2A specification](https://a2a-protocol.org/latest/specification/) · [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

# %% [markdown]
# ## 1. Architecture
#
# ```mermaid
# flowchart LR
#   NB[Notebook] -->|A2A| C[Concierge :10402<br/>LangGraph]
#   C -->|"RemoteA2ANode (A2A)"| P[Trip Planner :10401<br/>LangGraph]
#   NB -->|A2A| P
#   NB -.->|optional| AS[Agent Server :10403<br/>built-in A2A]
#   P -->|decision model| N[(Nimble)]
# ```
#
# | Service | Port | Role |
# |---|---|---|
# | `lab04-langgraph-travel` | 10401 | **Trip Planner** graph served through `LangGraphA2AExecutor` |
# | `lab04-langgraph-orchestrator` | 10402 | **Concierge** graph with a *remote node* that calls the planner |
# | `lab04-langgraph-agent-server` | 10403 | optional: the same planner on the LangGraph Agent Server |
#
# The next cell draws the planner graph straight from LangGraph.

# %% a2a_coverage=["pattern:3"]
# Render the planner graph with LangGraph's own Mermaid export
from lab04_langgraph.agents.trip_planner import build_graph

from a2a_labs_common import display

display.mermaid(build_graph().get_graph().draw_mermaid())

# %% tags=["parameters"]
QUICK_DEMO = False

# %% [markdown]
# ## 2. Setup

# %% a2a_coverage=["op:Discovery"]
# Initialise telemetry, show the models and wait for the services
import asyncio
import json

import httpx
from a2a.types import CancelTaskRequest, GetTaskRequest, ListTasksRequest
from google.protobuf.json_format import MessageToDict

from a2a_labs_common.a2a import events
from a2a_labs_common.a2a.client import collect, connect, send_text, stream_text, wait_for_cards
from a2a_labs_common.a2a.states import state_name
from a2a_labs_common.config import endpoint
from a2a_labs_common.telemetry import init_telemetry

tel = init_telemetry("notebook-lab04", framework="notebook", lab_id="lab04")
display.show_models()
PLANNER, CONCIERGE = "lab04-langgraph-travel", "lab04-langgraph-orchestrator"
await wait_for_cards([PLANNER, CONCIERGE], timeout=180)  # prints `make up LAB=lab04` if down
PLANNER_URL, CONCIERGE_URL = endpoint(PLANNER), endpoint(CONCIERGE)
FULL = "Plan a trip from Chicago to Lisbon, 12-16 May, budget $2000, 2 travelers"
OVER = "Plan a trip from Chicago to Lisbon, 12-16 May, budget $1500, 2 travelers"
display.table([{"service": s, "url": endpoint(s)} for s in (PLANNER, CONCIERGE)])

# %% [markdown]
# ## 3. Concept: LangGraph ↔ A2A
#
# | LangGraph | A2A on the wire |
# |---|---|
# | Graph run for a `thread_id` | a **Task** inside a `contextId` (`thread_id = context_id`) |
# | Node starts (`stream_mode="tasks"`) | `status_update` `WORKING`, text `▶ node: search_flights`, `metadata.langgraph_node` |
# | A tool call inside a node | `WORKING` `🔧 tool: …` |
# | `interrupt(value)` | `INPUT_REQUIRED`; the message is the question and a **DataPart** carries the structured payload |
# | `Command(resume=…)` | the next `SendMessage` with the **same `task_id`**; text, or a DataPart `{"resume": …}` |
# | State snapshot | optional artifact `state-snapshot` (replaced with `append=False`) |
# | Token stream (`stream_mode="messages"`) | artifact chunks (`append=True`, `last_chunk` at the end) |
# | END | artifact `itinerary` + `COMPLETED` |
# | Exception in a node | `FAILED` with the message |
# | Task cancel | the asyncio task running `astream` is cancelled → `CANCELED`; the checkpoint stays at the last superstep |
# | Checkpointer | durability of the context across tasks (required for interrupts) |

# %% [markdown]
# ## 4. The graph on its own, without A2A
# The baseline: run it locally with `astream` and read the node updates.

# %% a2a_coverage=["pattern:3", "decision"]
# Run the planner in-process with a complete, comfortably in-budget request
from langgraph.checkpoint.memory import InMemorySaver

local = build_graph(InMemorySaver())
cfg = {"configurable": {"thread_id": "baseline"}}
async for chunk in local.astream({"messages": [("user", "Plan a trip from Chicago to Lisbon, 12-16 May, budget $5000, 2 travelers")]}, cfg, stream_mode="updates"):
    for node, update in chunk.items():
        keys = [k for k in update if k != "messages"] if isinstance(update, dict) else "interrupt"
        print(f"node {node:14} wrote {keys}")
state = (await local.aget_state(cfg)).values
print(f"\nrisk score {state['risk']:.2f} (threshold 1.5) · probabilities {state['risk_detail']['probabilities']}")

# %% [markdown]
# **What just happened**
# - Each node returned a partial state update; `risk_gate` asked the **decision model** to score the plan (`low`/`medium`/`high`) and a conditional edge compared the score with 1.5.
# - At $5000 the plan is far under budget, so the score is low and the graph went straight to `compose`.

# %% [markdown]
# ## 5. The same graph over A2A
# Stream the request and watch each node start as a `WORKING` event.

# %% a2a_coverage=["pattern:3", "op:SendStreamingMessage", "state:WORKING"]
# Stream the planner and print the node events with their langgraph_node metadata
planner = await connect(PLANNER, streaming=True)
evs = []
async for ev in stream_text(planner, FULL):
    evs.append(ev)
    if events.kind(ev) == "status_update" and ev.status_update.status.HasField("message"):
        text = ev.status_update.status.message.parts[0].text
        print(f"{state_name(ev.status_update.status.state):9} {text:28} {dict(MessageToDict(ev.status_update.metadata))}")
final = collect(evs)
print("\nfinal state:", state_name(final.state), "| artifact:", [a.name for a in final.task.artifacts])
print(final.text)

# %% [markdown]
# **What just happened**
# - One `WORKING` event per node start, each tagged with `langgraph_node`: the graph's execution is visible to any A2A client without it knowing LangGraph exists.
# - END produced the `itinerary` artifact and `COMPLETED`. The footer line `Total: $… (budget $…)` is deterministic so other agents can rely on it.
# - This request is $200 over budget, yet it did not ask for approval: Nimble rates a small overrun *medium* (below 1.5).

# %% [markdown]
# ## 6. `INPUT_REQUIRED` from an interrupt
# A request with missing details makes the graph call `interrupt()`.

# %% a2a_coverage=["pattern:3", "state:INPUT_REQUIRED", "op:SendMessage", "part:data"]
# Ask for a trip with no origin, dates, budget or travelers
plain = await connect(PLANNER, streaming=False)
first = await send_text(plain, "Plan a trip to Lisbon")
parts = first.task.status.message.parts
print(state_name(first.state), "|", parts[0].text)
print("structured payload:", MessageToDict(parts[1].data))

# %% a2a_coverage=["pattern:3", "context:multiturn"]
# Reply on the SAME task_id; the graph resumes from the interrupt and, once complete, runs on
second = await send_text(plain, "from Chicago, 12-16 May, budget $5000, 2 travelers", task_id=first.task_id, context_id=first.context_id)
print(state_name(second.state), "| same task:", second.task_id == first.task_id)
print(second.text[-70:])

# %% a2a_coverage=["pattern:3"]
# thread_id == context_id: read the planner's own checkpoint with the A2A context id
async with httpx.AsyncClient() as http:
    dbg = (await http.get(f"{PLANNER_URL}/debug/state/{first.context_id}")).json()  # CUSTOM debug route
print("thread_id:", dbg["thread_id"] == first.context_id, "| next nodes:", dbg["next"])
print("request in the checkpoint:", dbg["values"]["request"])

# %% [markdown]
# **What just happened**
# - `interrupt()` became `INPUT_REQUIRED`; the question is the text part and the payload (`missing` fields) is a **DataPart**.
# - The client answered with a new message carrying the same `task_id`; the executor detected a pending interrupt (`aget_state(...).next`) and resumed with `Command(resume=…)`.
# - The checkpoint is keyed by the A2A `context_id` (the debug route is a `# CUSTOM` convenience, not part of A2A).

# %% [markdown]
# ## 7. Approval driven by the decision model
# A clearly over-budget plan scores high and routes to `approval`, a second interrupt.

# %% a2a_coverage=["pattern:3", "decision", "state:INPUT_REQUIRED"]
# Over budget: the risk gate scores it, the approval node asks the user
a = await send_text(plain, OVER)
print(state_name(a.state), "|", a.task.status.message.parts[0].text)
print("options:", MessageToDict(a.task.status.message.parts[1].data)["options"])
async with httpx.AsyncClient() as http:
    dbg = (await http.get(f"{PLANNER_URL}/debug/state/{a.context_id}")).json()
print(f"risk score {dbg['values']['risk']:.2f}, probabilities {dbg['values']['risk_detail']['probabilities']}")

# %% a2a_coverage=["pattern:3", "state:COMPLETED"]
# "no" loops back to cheaper hotels once; then "yes" completes
b = await send_text(plain, "no", task_id=a.task_id, context_id=a.context_id)
print("after 'no':", state_name(b.state), "|", b.task.status.message.parts[0].text)
c = await send_text(plain, "yes", task_id=a.task_id, context_id=a.context_id)
print("after 'yes':", state_name(c.state), "|", c.text.splitlines()[-1])

# %% [markdown]
# **What just happened**
# - The decision model returned probabilities over `low / medium / high`; the **score** is their probability-weighted level. 1.5 is the line between "just compose" and "ask first". Look for the `decision.risk.*` span attributes in Grafana.
# - `no` re-ran `search_hotels` with a cheaper preference (one loop at most) and asked again with the new total.

# %% [markdown]
# ## 8. Cancel mid-graph
# Cancel while `search_hotels` is sleeping, then look at what the checkpoint kept.

# %% a2a_coverage=["pattern:3", "op:CancelTask", "state:CANCELED", "op:GetTask"]
# Start, wait until flights are done but hotels are not, cancel, then read the checkpoint
run = await send_text(plain, FULL, return_immediately=True)
await asyncio.sleep(1.2)
canceled = await plain.client.cancel_task(CancelTaskRequest(id=run.task_id))
print("after cancel:", state_name(canceled.status.state))
print("GetTask     :", state_name((await plain.client.get_task(GetTaskRequest(id=run.task_id))).status.state))
async with httpx.AsyncClient() as http:
    kept = (await http.get(f"{PLANNER_URL}/debug/state/{run.context_id}")).json()
print("checkpoint keeps:", sorted(kept["values"]), "| a resume would continue at:", kept["next"])

# %% [markdown]
# **What just happened**
# - The executor cancelled the asyncio task running `astream`; A2A reported `CANCELED`.
# - LangGraph checkpoints after each superstep, so the flights are kept and `search_hotels` is still pending: the work is interrupted, not lost.

# %% [markdown]
# ## 9. Nested: Concierge → Trip Planner
# The Concierge's graph has a **remote node**. When the remote planner interrupts, the node interrupts locally, so the pause reaches *you*.

# %% a2a_coverage=["pattern:3", "pattern:graphnode", "state:INPUT_REQUIRED"]
# Ask the concierge for an over-budget trip; the planner's approval question surfaces here
concierge = await connect(CONCIERGE, streaming=False)
n1 = await send_text(concierge, OVER)
payload = MessageToDict(n1.task.status.message.parts[1].data)
print("concierge:", state_name(n1.state), "|", n1.task.status.message.parts[0].text)
print("payload  :", payload)
print("concierge task:", n1.task_id[:8], "| remote (planner) task:", payload["task_id"][:8])

# %% a2a_coverage=["pattern:3", "pattern:graphnode", "state:COMPLETED"]
# One reply resolves both levels; the remote node forwards "yes" to the SAME planner task
n2 = await send_text(concierge, "yes", task_id=n1.task_id, context_id=n1.context_id)
print(state_name(n2.state), "|", n2.text.splitlines()[-1])
async with httpx.AsyncClient() as http:
    st = (await http.get(f"{CONCIERGE_URL}/debug/state/{n1.context_id}")).json()["values"]
print("remote_tasks in the concierge state:", json.dumps(st["remote_tasks"]))
print("progress captured from the planner :", st["progress"][:3], "...")

# %% a2a_coverage=["op:ListTasks", "op:GetTask"]
# The planner served the whole nested exchange with ONE task: find it by the id from the payload
remote_id = payload["task_id"]
mine = [t for t in (await plain.client.list_tasks(ListTasksRequest())).tasks if t.id == remote_id]
print("planner task", remote_id[:8], "->", [state_name(t.status.state) for t in mine])

# %% [markdown]
# **What just happened**
# - Four hops of state: remote `INPUT_REQUIRED` → local `interrupt()` → the concierge's own `INPUT_REQUIRED` → your reply → forwarded to the **same** remote `task_id` → both `COMPLETED`.
# - `RemoteA2ANode` records `{context_id, task_id, state}` per remote in `remote_tasks`, and keeps the remote's status texts in `progress`.
# - LangGraph re-runs a node from its first line after an interrupt, which would re-send the request. The node remembers the remote task and how many answers it forwarded, then reads the remote task's *current* state instead.
# - Note the budget check: the concierge's *local* node says the plan is over budget even though you approved it, because approval is a policy decision and the check is arithmetic.

# %% [markdown]
# ## 10. The LangGraph Agent Server's built-in A2A endpoint
# The same planner graph can be deployed on the Agent Server (`langgraph.json`), which serves A2A by itself. This step runs only if you started it: `make up LAB=lab04-agentserver`.

# %% a2a_coverage=["pattern:3", "compat:v03", "op:SubscribeToTask"]
# If the optional profile is up: send the request, then try SubscribeToTask
import uuid

AS = "http://localhost:10403"
try:
    up = httpx.get(f"{AS}/ok", timeout=2).status_code == 200
except httpx.HTTPError:
    up = False
if not up:
    print("Agent Server not running; start it with: make up LAB=lab04-agentserver")
else:
    aid = httpx.post(f"{AS}/assistants/search", json={"graph_id": "trip_planner"}).json()[0]["assistant_id"]
    url = f"{AS}/a2a/{aid}"  # the A2A path takes the assistant UUID, not the graph name
    def rpc(method, params):
        return httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=60).json()
    msg = {"role": "user", "parts": [{"kind": "text", "text": OVER}], "messageId": str(uuid.uuid4()), "kind": "message", "contextId": str(uuid.uuid4())}
    r = rpc("message/send", {"message": msg})["result"]
    print("state:", r["status"]["state"], "| shape: kind =", r["kind"], "| artifact:", r["artifacts"][0]["name"])
    print("interrupt payload:", r["artifacts"][0]["parts"][0]["data"]["value"])
    print("SubscribeToTask ->", rpc("SubscribeToTask", {"id": r["id"]})["error"])

# %% [markdown]
# | | Our `LangGraphA2AExecutor` | Agent Server `/a2a/{assistant}` (langgraph-api 0.16) |
# |---|---|---|
# | Interrupt | `INPUT_REQUIRED` + question + DataPart | `input-required` + an **`Interrupt` artifact** |
# | Resume | text, or DataPart `{"resume": …}` | text, or DataPart `{"resume": …}` |
# | `SubscribeToTask` | yes | **no** (`-32601`) |
# | Push notifications | via the SDK | **no** |
# | Bindings | JSON-RPC, REST, gRPC available | JSON-RPC only |
# | Wire shape | v1.0 (ProtoJSON) | v0.3 shapes (`kind`, lowercase states), mixed with a v1.0 card |
# | `contextId` | any string | must be a **UUID** |
# | `historyLength` | any | at most **10** |
# | Assistant in the URL | n/a | the assistant **UUID** (not the graph name) |
# | Card URL | accurate | advertises `http://0.0.0.0:…` (fix before use) |
#
# > ⚠️ Pitfall: the Agent Server is the quickest way to expose a graph, but check the table before relying on it for streaming resubscription or push.

# %% [markdown]
# ## 11. See it in Grafana

# %%
# One cell, one trace: the nested concierge run under a single notebook span
with display.cell_span("nested concierge run") as span:
    cc = await connect(CONCIERGE, streaming=False)
    q = await send_text(cc, OVER)
    if q.task.status.state == 6:  # INPUT_REQUIRED
        await send_text(cc, "yes", task_id=q.task_id, context_id=q.context_id)
    await cc.aclose()
tel.flush()
display.trace_link(span)

# %% [markdown]
# Try in Grafana → Explore:
# - **TraceQL:** `{ resource.service.name = "lab04-langgraph-travel" && name = "a2a.server.execute" }`: the planner's work inside the concierge's trace.
# - **TraceQL:** `{ span.a2a.task.final_state = "COMPLETED" }`, then open a span and read the **events** `a2a.task.state_change` (`from` → `to`), one per transition.
# - **Decision spans:** `{ name = "decision.decide" }` and the attributes `decision.risk.p` and `decision.risk.score`.

# %% [markdown]
# ## 12. Exercises
# - 🟢 **Easy:** add a `weather` node after `search_hotels` (a plain function adding `weather` to the state) and see it appear as `▶ node: weather`.
# - 🟡 **Medium:** run the planner with `TRIP_SNAPSHOTS=true` and watch the `state-snapshot` DataPart artifact being replaced after each node.
# - 🔴 **Stretch:** swap `InMemorySaver` for `AsyncSqliteSaver`, restart the planner mid-task and resume.

# %% tags=["skip-ci"] jupyter={"source_hidden": true}
# Hidden solution (easy): in agents/trip_planner.py
# async def weather(state):
#     return {"weather": "sunny, 24C"}
# g.add_node("weather", weather); replace the edge search_hotels -> risk_gate with
# search_hotels -> weather -> risk_gate. Add `weather: str` to State.

# %% [markdown]
# ## 13. Recap
#
# | Pattern 3 | |
# |---|---|
# | **Use it when** | You already have (or want) a stateful graph and need to expose or consume it over A2A with tasks, pauses and cancellation mapped faithfully |
# | **Not when** | A one-shot function is enough (use a tool, Lab 02), or you need the Agent Server's limits to disappear |
# | Key mapping | `thread_id` = `contextId`; `interrupt` = `INPUT_REQUIRED`; resume = same `task_id`; END = artifact |
# | Remote node | Never re-send after an interrupt; remember the remote `task_id` and read its current state |
#
# **Pitfalls:** interrupts need a checkpointer; node code before an `interrupt()` runs again on resume; a `config` parameter must be annotated exactly `RunnableConfig` or LangGraph will not inject it; `graph.stream()` inside an async executor blocks the event loop.
#
# **Next:** Lab 05 puts a supervisor in front of several agents and routes with the decision model.

# %% [markdown]
# ## Cleanup

# %%
# Close the clients and flush telemetry
for client in (planner, plain, concierge):
    await client.aclose()
tel.flush()
