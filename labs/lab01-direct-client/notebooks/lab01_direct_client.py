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
# # Lab 01 · Direct client → agent (pattern 1)
#
# **Goal:** talk to four agents built with four different stacks using the *same* A2A client code.
# **Time:** about 5 minutes (a local `llama3.2` model; the first call may take 30–90 s while Ollama loads it)
# **Prerequisites:** Lab 00 · `make up LAB=lab01`
#
# **What you'll learn**
# - How to resolve an **Agent Card** and compare what each framework advertises.
# - When an agent answers with a **Message** (immediate, stateless) and when with a **Task** (stateful, trackable).
# - How to expose an agent with the official adapter in ADK, and with `a2a-sdk` executors for Strands and LangGraph.
# - How `contextId` gives multi-turn memory in each framework, and what it maps to inside.
#
# Spec: [A2A specification](https://a2a-protocol.org/latest/specification/)

# %% [markdown]
# ## 1. Architecture
#
# ```mermaid
# flowchart LR
#   NB[Notebook client] -->|A2A JSON-RPC| E[lab01-sdk-echo<br/>a2a-sdk]
#   NB --> A[lab01-adk-agent<br/>ADK to_a2a]
#   NB --> S[lab01-strands-agent<br/>Strands + StrandsA2AExecutor]
#   NB --> L[lab01-langgraph-agent<br/>LangGraph + LangGraphA2AExecutor]
#   A & S & L --> O[(Ollama · quick role)]
# ```
#
# | Service | Port | Framework and adapter | Replies with |
# |---|---|---|---|
# | `lab01-sdk-echo` | 10101 | `a2a-sdk` `AgentExecutor`, no LLM | **Message** always |
# | `lab01-adk-agent` | 10102 | ADK `Agent` → `to_a2a(...)` | **Task** |
# | `lab01-strands-agent` | 10103 | Strands `Agent` → `StrandsA2AExecutor` | **Task** |
# | `lab01-langgraph-agent` | 10104 | LangGraph `create_agent` → `LangGraphA2AExecutor` | **Task** |
#
# The three LLM agents share one skill, **Trip Tip** (one practical tip for a city) and one deterministic tool, `local_time(city)`.
# **Who owns the task?** The server. The client only ever sees IDs and events.
#
# > 🔍 Under the hood: Strands normally ships `A2AServer`, but it cannot be imported next to `a2a-sdk` 1.x, so this repo wraps Strands agents in its own small `AgentExecutor` (`docs/spec-deviations.md`, DEV-1).

# %% tags=["parameters"]
QUICK_DEMO = False

# %% [markdown]
# ## 2. Setup

# %%
# Initialise telemetry, show the models in use and wait for the four servers
import time

from a2a.types import GetTaskRequest

from a2a_labs_common import display
from a2a_labs_common.a2a import events
from a2a_labs_common.a2a.client import connect, send_text, stream_text, wait_for_cards
from a2a_labs_common.a2a.ids import new_context_id
from a2a_labs_common.a2a.states import state_name
from a2a_labs_common.telemetry import init_telemetry

tel = init_telemetry("notebook-lab01", framework="notebook", lab_id="lab01")
display.show_models()
ECHO = "lab01-sdk-echo"
AGENTS = {"adk": "lab01-adk-agent", "strands": "lab01-strands-agent", "langgraph": "lab01-langgraph-agent"}
SERVICES = [ECHO, *AGENTS.values()]
await wait_for_cards(SERVICES, timeout=120)  # prints `make up LAB=lab01` if a server is down

# %% [markdown]
# ## 3. Concept: Message vs Task
#
# Every `SendMessage` returns one of two things:
#
# | | Message | Task |
# |---|---|---|
# | Shape | One reply, nothing else | A server-owned unit of work with an ID and a lifecycle |
# | State | None; the exchange is over | `SUBMITTED` → `WORKING` → `COMPLETED` (or others) |
# | Results | In the message parts | In **Artifacts** |
# | Follow-up | Send another message with the `contextId` | `GetTask`, `SubscribeToTask`, cancel, push |
# | Chosen by | The agent: quick, stateless answers | The agent: anything that takes time or needs tracking |
#
# ```mermaid
# sequenceDiagram
#   participant C as Client
#   participant E as sdk-echo
#   participant A as ADK / Strands / LangGraph agent
#   C->>E: SendMessage "hi"
#   E-->>C: Message "[echo] hi"
#   C->>A: SendMessage "tip for Lisbon"
#   A-->>C: Task COMPLETED + Artifact "answer"
# ```
#
# The client does not decide which one it gets; it must handle both. `SendResult.kind` tells you which.

# %% [markdown]
# ## 4. Discover all four cards
# Same discovery call for every framework: `GET /.well-known/agent-card.json`. Some servers *build* their card (ours), others let the framework generate it (ADK).

# %% a2a_coverage=["op:Discovery", "pattern:1"]
# Open a connection to each server and show the cards side by side
conns = {name: await connect(name, streaming=True) for name in SERVICES}
display.compare_cards([c.card for c in conns.values()])

# %% a2a_coverage=["extensions"]
# What each card says about skills, extensions and field filling
display.table([
    {"service": n, "skills": ",".join(s.id for s in c.card.skills),
     "tags": sum(len(s.tags) for s in c.card.skills),
     "extensions": ",".join(e.uri for e in c.card.capabilities.extensions) or "none",
     "interface": c.card.supported_interfaces[0].protocol_binding}
    for n, c in conns.items()
])
display.show_card(conns["lab01-adk-agent"].card)

# %% [markdown]
# **What just happened**
# - All four cards have the same shape (`supportedInterfaces`, `capabilities`, `skills`) even though four stacks produced them.
# - ADK built its card from the agent definition: one skill for the agent (`trip_tip_adk`) and one *per tool* (`trip_tip_adk-local_time`), with tags it chose (`llm`, `tools`). The other cards were hand-built with `build_card`, which is the way to get a deterministic card.
# - No card advertises an extension here; ADK's A2A extension is negotiated per request (Lab 10 covers extensions).

# %% [markdown]
# ## 5. Same question to four agents
# One loop, one client helper, four different frameworks. Watch what comes back.

# %% a2a_coverage=["op:SendMessage", "pattern:1", "state:COMPLETED"]
# Ask every agent for a Lisbon tip and tabulate kind, state, artifacts and latency
rows = []
for name, c in conns.items():
    t0 = time.perf_counter()
    r = await send_text(c, "Give me a tip for Lisbon")
    rows.append({
        "service": name, "kind": r.kind, "state": state_name(r.state) if r.state else "-",
        "artifacts": len(r.task.artifacts) if r.task else 0,
        "seconds": round(time.perf_counter() - t0, 1), "text": r.text.replace("\n", " ")[:80],
    })
display.table(rows)

# %% [markdown]
# **What just happened**
# - `sdk-echo` answered with a **Message**: no task, no artifacts, no state.
# - The three framework agents each created a **Task**, did their work and returned a `COMPLETED` task with the answer in an **Artifact**.
# - Every LLM agent called `local_time` first (look for the time in the text; the tool call is visible in Grafana).
# - ADK returned **three** artifacts, the others one. ADK maps every internal event to an artifact: the tool call (a `data` part), the tool result (`data`) and the final answer (`text`). Strands and LangGraph publish only the final answer, as a single `answer` artifact.
#
# > ⚠️ Pitfall: with a 3B model the tool call is not guaranteed. Small models skip tools sometimes; the plumbing is the same either way.

# %% [markdown]
# ## 6. Streaming from each
# Streaming is available only if the client asks *and* the card says `streaming: true`. Compare the event shapes.

# %% a2a_coverage=["op:SendStreamingMessage", "state:WORKING", "artifact:chunked"]
# Stream a request to every agent and show the event kinds in order
for name in SERVICES:
    evs = [e async for e in stream_text(conns[name], "Give me a tip for Tokyo")]
    shape = " → ".join(f"{events.kind(e)}" + (f":{events.state_of(e)}" if events.state_of(e) else "") for e in evs)
    print(f"{name}\n   {shape}")

# %% [markdown]
# **What just happened**
# - `sdk-echo`: exactly **one** `message` event, then the stream closes.
# - The others start with the `task` event, then `status_update`s (`WORKING`), then `artifact_update`s, and finally `COMPLETED`.
# - **Strands** streams the answer as many small chunks (`append=True`), one per model token batch: spec-compliant chunked artifacts. ADK sends a few whole artifacts; LangGraph sends the answer once at the end (unless token streaming is switched on).
# - The LangGraph executor also emits a `WORKING` status like "Calling tool local_time" when its graph calls the tool.
# - Set `STREAM_TOKENS_AS_ARTIFACT=true` on the LangGraph server and the answer arrives token by token as appended artifact chunks.

# %% [markdown]
# ## 7. Multi-turn with `contextId`
# A `contextId` groups messages into one conversation. Each framework maps it to its own memory:
#
# | Framework | `contextId` becomes |
# |---|---|
# | ADK | the ADK **session** id |
# | Strands | the key of `agent_factory(context_id)`: one agent (and its history) per context |
# | LangGraph | the checkpointer **`thread_id`** |
#
# > 💡 Tip: small local models are bad at recall. If an answer is vague, the memory plumbing still worked; the unit tests prove it with a fake model.

# %% a2a_coverage=["context:multiturn"]
# Turn 1 introduces a name; turn 2 (same context) asks for it; a new context should not know it
for name, svc in AGENTS.items():
    c = conns[svc]
    t1 = await send_text(c, "My name is Ada. Give me a tip for Paris.")
    t2 = await send_text(c, "What is my name?", context_id=t1.context_id)
    t3 = await send_text(c, "What is my name?", context_id=new_context_id())
    print(f"{name:10} same context: {t2.text[:90]!r}\n{'':10} new context : {t3.text[:90]!r}")

# %% [markdown]
# ## 8. Inspect the task afterwards
# A `Task` is stored by the server, so you can fetch it later with `GetTask`. `history_length` bounds the messages returned.

# %% a2a_coverage=["op:GetTask"]
# Fetch the Lisbon task from each Task-returning agent and compare the history
for name, svc in AGENTS.items():
    r = await send_text(conns[svc], "Give me a tip for Sydney")
    t = await conns[svc].client.get_task(GetTaskRequest(id=r.task_id, history_length=5))
    roles = [m.role for m in t.history]
    print(f"{name:10} state={state_name(t.status.state)} history={len(t.history)} message(s) artifacts={len(t.artifacts)}")

# %% [markdown]
# ## 9. Writing your own client loop
# The helpers hide very little. This is the same exchange with the raw `a2a-sdk` (about 15 lines): copy it into your own code.

# %%
# The raw a2a-sdk client: resolve the card, create a client, send, read events
import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_message_text, new_text_message
from a2a.types import Role, SendMessageRequest

from a2a_labs_common.a2a.client import bridge_card_urls
from a2a_labs_common.config import endpoint

async with httpx.AsyncClient(timeout=120) as http:
    card = await A2ACardResolver(http, endpoint(ECHO)).get_agent_card()
    bridge_card_urls(card)  # CUSTOM: docker names -> localhost for a notebook on the host
    client = await create_client(card, client_config=ClientConfig(streaming=False, httpx_client=http))
    request = SendMessageRequest(message=new_text_message("raw client says hi", role=Role.ROLE_USER))
    async for event in client.send_message(request):
        print(events.kind(event), "->", get_message_text(event.message))
    await client.close()

# %% [markdown]
# ## 10. See it in Grafana
# The whole notebook is traced. One cell fans out to four agents; find them under one notebook span.

# %%
# Run one cell that calls all four agents under a single span, then print the Grafana link
with display.cell_span("ask all four") as span:
    for c in conns.values():
        await send_text(c, "Give me a tip for Lisbon")
tel.flush()
display.trace_link(span)

# %% [markdown]
# Try in Grafana → Explore:
# - **TraceQL:** `{ name = "notebook.cell ask all four" } >> { name =~ "a2a.client SendMessage" }`: four parallel client spans under one notebook span.
# - **TraceQL:** `{ resource.service.name =~ "lab01-.*" && name =~ "chat .*" }`: the LLM `chat llama3.2` span under each server.
# - **LogQL:** `{service_namespace="a2a-labs"} |= "task state"`

# %% [markdown]
# ## 11. Exercises
# - 🟢 **Easy:** add a second skill to the Strands card (`cards.strands_card`) and re-run step 4 to see the card change.
# - 🟡 **Medium:** make `sdk-echo` return a **Task** when the text starts with `task:` (hint: `new_task_from_user_message` first, then `TaskUpdater`).
# - 🔴 **Stretch:** add the A2A Inspector: `docker compose --profile tools up -d`, open <http://localhost:5001> and inspect `http://lab01-adk-agent:10102`.

# %% tags=["skip-ci"] jupyter={"source_hidden": true}
# Hidden solution (medium): in servers/sdk_echo.py
# async def execute(self, context, event_queue):
#     text = context.get_user_input()
#     if text.startswith("task:"):
#         task = new_task_from_user_message(context.message)
#         await event_queue.enqueue_event(task)                 # Task FIRST
#         u = TaskUpdater(event_queue, task.id, task.context_id)
#         await u.add_artifact([new_text_part(text[5:])], name="echo")
#         await u.complete()
#         return
#     ...existing Message path...

# %% [markdown]
# ## 12. Recap
#
# | Pattern 1 | |
# |---|---|
# | **Use it when** | One client talks straight to one known agent |
# | **Not when** | You need to choose among many agents (Lab 05), or the agent should call others (Labs 02–04) |
# | Message vs Task | Handle both; results live in Artifacts |
# | `contextId` | The conversation key; each framework maps it to its own memory |
#
# **Pitfalls:** assuming every agent returns a Task; reading results from status messages; forgetting that streaming needs both client and card; trusting a small model to call tools every time.
#
# **Next:** Lab 02 turns these agents into *tools* that another agent calls.

# %% [markdown]
# ## Cleanup

# %%
# Close every connection and flush telemetry
for c in conns.values():
    await c.aclose()
tel.flush()
