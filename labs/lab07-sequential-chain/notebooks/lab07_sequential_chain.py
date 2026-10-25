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
# # Lab 07 · Sequential chaining / multi-hop (pattern 6)
#
# **Goal:** pass work along a chain of agents from three different frameworks and keep it **explicit**: structured Parts between hops, one task per hop, and a trail of `reference_task_ids`.
# **Time:** about 6 minutes with real models (the writer is the slow hop) · **Prerequisites:** Labs 01, 04 and 05 · `REASONING_THINK=false make up LAB=lab07`
#
# **What you'll learn**
# - Artifacts from one hop become Parts of the next hop's **Message**; the outline stays a JSON **DataPart**.
# - `reference_task_ids` as provenance, and **each hop owns its own task and context**.
# - **Orchestrated** (hub-and-spoke) vs **relay** (peer hops) topologies, and what each costs.
# - A **quality loop**: the decision model scores the editor's review and can send the draft back to the writer.
# - What ADK, LangGraph and Strands give you for free, and what they leave implicit.
#
# Spec: [A2A specification](https://a2a-protocol.org/latest/specification/) (messages, artifacts, `referenceTaskIds`)

# %% [markdown]
# ## 1. Architecture
#
# ```mermaid
# sequenceDiagram
#   participant C as Client
#   participant O as Orchestrator (T0)
#   participant P as Planner (ADK)
#   participant W as Writer (Strands)
#   participant E as Editor (LangGraph)
#   C->>O: topic
#   O->>P: topic
#   P-->>O: outline (DataPart) [T1]
#   O->>W: outline, refs=[T1]
#   W-->>O: draft (chunks) [T2]
#   O->>E: draft, refs=[T2]
#   E-->>O: final + review [T3]
#   Note over O: score the review; low score → back to W (refs=[T2,T3])
#   O-->>C: final + provenance
# ```
#
# ```mermaid
# sequenceDiagram
#   participant C as Client
#   participant P as Planner (T1)
#   participant W as Writer (T2)
#   participant E as Editor (T3)
#   C->>P: topic + route in metadata
#   P->>W: outline, refs=[T1], route=[editor]
#   W->>E: draft, refs=[T2], route=[]
#   E-->>W: final + review
#   W-->>P: draft + downstream-final
#   P-->>C: outline + downstream-final
# ```
#
# | Service | Port | Framework | Output |
# |---|---|---|---|
# | `lab07-adk-planner` | 10701 | ADK | `outline`: DataPart `{title, audience, sections}` + text |
# | `lab07-strands-writer` | 10702 | Strands | `draft`: markdown, streamed in chunks |
# | `lab07-langgraph-editor` | 10703 | LangGraph | `final` (text) + `review` (DataPart `{issues, summary}`) |
# | `lab07-chain-orchestrator` | 10704 | a2a-sdk | `final` + `provenance` |

# %% tags=["parameters"]
QUICK_DEMO = False

# %% [markdown]
# ## 2. Setup

# %% a2a_coverage=["op:Discovery"]
# Initialise telemetry, show the models and wait for the four services
import asyncio
import json
import time
import uuid

from a2a.types import CancelTaskRequest, GetTaskRequest, ListTasksRequest, TaskState
from google.protobuf.json_format import MessageToDict

from a2a_labs_common import display
from a2a_labs_common.a2a.client import connect, send_text, stream_text, wait_for_cards
from a2a_labs_common.a2a.states import state_name
from a2a_labs_common.telemetry import init_telemetry

from lab07_chain import native
from lab07_chain.executors.chain_orchestrator import artifact_data, artifact_text

tel = init_telemetry("notebook-lab07", framework="notebook", lab_id="lab07")
display.show_models()
PLANNER, WRITER, EDITOR, ORCH = "lab07-adk-planner", "lab07-strands-writer", "lab07-langgraph-editor", "lab07-chain-orchestrator"
await wait_for_cards([PLANNER, WRITER, EDITOR, ORCH], timeout=180)  # prints `make up LAB=lab07` if down
TOPIC = "Explain how vaccines work in three paragraphs"
HARD = "Explain quantum entanglement to a 10-year-old in 3 paragraphs, using only words under 3 syllables"

# %% [markdown]
# ## 3. Concept: what travels along a chain
#
# | | Artifact | Message |
# |---|---|---|
# | Direction | a hop's **output** | the next hop's **input** |
# | Carries | named results (`outline`, `draft`, `final`) | Parts: text, **data** (JSON), files |
# | Link | | `reference_task_ids` = "this message was built from the output of those tasks" |
#
# Three rules the orchestrator follows:
# 1. **Each hop owns its tasks.** The orchestrator never sends one hop's `task_id` to another hop; provenance travels in `reference_task_ids` and in its own `provenance` artifact.
# 2. **Each hop has its own context**, derived from the chain's context (uuid5 of context + hop name), so a hop that is called again (the writer, in a revision loop) remembers its earlier work.
# 3. **Structure survives.** The outline is a DataPart, not prose that the writer has to re-parse.

# %% [markdown]
# ## 4. Hop by hop, by hand
# Before any automation: call the three agents yourself.

# %% a2a_coverage=["pattern:6", "op:SendMessage", "part:data"]
# Planner: the topic goes in as text; the outline comes back as a DataPart plus its text rendering
planner = await connect(PLANNER, streaming=False)
plan = await send_text(planner, TOPIC)
outline = artifact_data(plan, "outline")
print(state_name(plan.state), "| task", plan.task_id[:8], "| artifact parts:", [("data" if p.HasField("data") else "text") for p in plan.task.artifacts[0].parts])
print(json.dumps(outline, indent=1)[:420])

# %% a2a_coverage=["pattern:6", "op:SendStreamingMessage", "op:GetTask", "artifact:chunked", "refs"]
# Writer: the outline as a DataPart, linked to the planner's task; the draft streams back in chunks
writer = await connect(WRITER, streaming=True)
chunks, draft_events = 0, []
async for ev in stream_text(writer, "Write the article from the attached outline.", data=outline, reference_task_ids=[plan.task_id]):
    draft_events.append(ev)
    chunks += ev.WhichOneof("payload") == "artifact_update"
from a2a_labs_common.a2a.client import collect

draft = collect(draft_events)
print(state_name(draft.state), "| task", draft.task_id[:8], "| draft chunks:", chunks, "| chars:", len(artifact_text(draft, "draft")))
wt = await writer.client.get_task(GetTaskRequest(id=draft.task_id, history_length=5))
print("writer's task remembers where its input came from:", [r[:8] for r in wt.history[0].reference_task_ids], "== planner task", plan.task_id[:8])

# %% a2a_coverage=["pattern:6"]
# Editor: the draft text in; two artifacts out (final text and a structured review)
editor = await connect(EDITOR, streaming=False)
edit = await send_text(editor, artifact_text(draft, "draft"), reference_task_ids=[draft.task_id])
print(state_name(edit.state), "| artifacts:", [a.name for a in edit.task.artifacts])
print("review:", artifact_data(edit, "review"))

# %% [markdown]
# **What just happened**
# - Three agents, three frameworks, three **tasks** with their own ids. The only things that crossed hops were Parts and `reference_task_ids`.
# - The writer is a text-only Strands agent: our executor wrapper folds the outline DataPart into its prompt. A2A carries the structure; what a framework does with a DataPart is its executor's job.

# %% [markdown]
# ## 5. The orchestrated chain
# The orchestrator does all of that, owns the overall task, and streams progress.

# %% a2a_coverage=["pattern:6", "op:SendStreamingMessage", "state:WORKING", "state:COMPLETED"]
# Stream from the orchestrator; every update comes from its single top-level task
orch = await connect(ORCH, streaming=True)
evs, t0 = [], time.perf_counter()
async for ev in stream_text(orch, TOPIC, context_id=str(uuid.uuid4())):
    evs.append(ev)
    if ev.WhichOneof("payload") == "status_update" and ev.status_update.status.HasField("message"):
        print(f"{time.perf_counter() - t0:5.1f}s  {state_name(ev.status_update.status.state):10} {ev.status_update.status.message.parts[0].text}")
chain = collect(evs)
print("\nfinal state:", state_name(chain.state), "| artifacts:", [a.name for a in chain.task.artifacts])

# %% a2a_coverage=["pattern:6", "op:GetTask"]
# The provenance artifact, and a GetTask on each hop to check it against the hop's own record
prov = artifact_data(chain, "provenance")
display.table([{"hop": h["agent"], "task": h["task_id"][:8], "context": h["context_id"][:8], "state": h["state"], "refs": [r[:8] for r in h["references"]], "ms": h["ms"]} for h in prov["hops"]])
for h in prov["hops"][1:]:
    async with await connect(h["service"], streaming=False) as hc:
        t = await hc.client.get_task(GetTaskRequest(id=h["task_id"], history_length=5))
    print(f"{h['agent']:7} task {t.id[:8]} is {state_name(t.status.state)}; it records reference_task_ids {[r[:8] for r in t.history[0].reference_task_ids]}")

# %% [markdown]
# **What just happened**
# - Three different `context` ids: one per hop, none equal to the chain's. Three different task ids, each verified on the hop that owns it.
# - The writer's chunks were relayed as `writer: writing… N chars` status updates, so the end client sees progress without knowing there are hops.

# %% [markdown]
# ## 6. The quality loop
# The decision model scores the editor's review (`unusable / major / minor / publishable`). Below `minor`, the draft goes back to the writer **in the writer's own context**, as a new task that references the previous two.

# %% a2a_coverage=["pattern:6", "decision", "op:ListTasks"]
# A request that is hard to satisfy; watch the scores and the revision loops
async with await connect(ORCH, streaming=False, timeout=900) as oc:  # the whole loop, minutes on real models
    hard = await send_text(oc, HARD, context_id=str(uuid.uuid4()))
hp = artifact_data(hard, "provenance")
print(state_name(hard.state), "| loops:", int(hp["loops"]), "| scores per review (0-3):", [round(s, 2) for s in hp["scores"]])
display.table([{"#": i + 1, "hop": h["agent"], "task": h["task_id"][:8], "context": h["context_id"][:8], "refs": [r[:8] for r in h["references"]]} for i, h in enumerate(hp["hops"])])
wctx = next(h["context_id"] for h in hp["hops"] if h["agent"] == "writer")
async with await connect(WRITER, streaming=False) as wc:
    wtasks = (await wc.client.list_tasks(ListTasksRequest(context_id=wctx))).tasks
print(f"the writer's own view of its context: {len(wtasks)} task(s)", [t.id[:8] for t in wtasks])

# %% [markdown]
# **What just happened**
# - With real models you will often see one or two revision rounds; with scripted fakes (`DECISION_PROVIDER=fake`) the default score is 3 and there is none (the unit tests script a score of 1 and see exactly two loops, the `MAX_LOOPS` cap).
# - A revision is a **new task** in the **same writer context**: the writer remembers its earlier draft, and the new task's `reference_task_ids` list the previous draft and the review that triggered it.

# %% [markdown]
# ## 7. `INPUT_REQUIRED` in the middle of a chain
# A vague topic makes the planner ask a question. The orchestrator surfaces it and forwards your answer to *the planner's* task.

# %% a2a_coverage=["pattern:6", "state:INPUT_REQUIRED"]
# Ask something vague, then answer on the same orchestrator task
async with await connect(ORCH, streaming=False, timeout=900) as oc:  # answering runs the whole chain
    ask = await send_text(oc, "Write about it")
    print("orchestrator:", state_name(ask.state), "|", ask.task.status.message.parts[0].text)
    done = await send_text(oc, "ten-year-olds learning about volcanoes", task_id=ask.task_id, context_id=ask.context_id)
print("after the answer:", state_name(done.state), "| same orchestrator task:", done.task_id == ask.task_id)
hp2 = artifact_data(done, "provenance")["hops"]
print("planner hop:", [(h["state"], h["task_id"][:8]) for h in hp2 if h["agent"] == "planner"], "(one task, paused then completed)")

# %% [markdown]
# ## 8. Cancelling mid-chain
# Cancel the orchestrator while the writer works. The cancel must reach the hop's task.

# %% a2a_coverage=["pattern:6", "op:CancelTask", "state:CANCELED"]
# Start without waiting, wait for a writer task to exist, cancel, then look at the writer's task
async with await connect(ORCH, streaming=False) as oc, await connect(WRITER, streaming=False) as wc:
    before = {t.id for t in (await wc.client.list_tasks(ListTasksRequest())).tasks}
    run = await send_text(oc, TOPIC, return_immediately=True)
    new = []
    for _ in range(100):
        new = [t for t in (await wc.client.list_tasks(ListTasksRequest())).tasks if t.id not in before]
        if new:
            break
        await asyncio.sleep(0.1)
    try:
        res = await oc.client.cancel_task(CancelTaskRequest(id=run.task_id))
        print("orchestrator:", state_name(res.status.state))
    except Exception as exc:  # fake models finish before we can cancel
        print("too late to cancel (the chain already finished):", type(exc).__name__)
    await asyncio.sleep(1)
    for t in new:
        print("writer task", t.id[:8], "→", state_name((await wc.client.get_task(GetTaskRequest(id=t.id))).status.state))

# %% [markdown]
# **What just happened**
# - `CancelTask` on the orchestrator's `T0` made it send `CancelTask` to the writer task it was waiting on. Note the order: the cancel is propagated by the orchestrator, because the writer knows nothing about `T0`.
# - With scripted fake models the whole chain takes milliseconds, so "too late" is the normal output there.

# %% [markdown]
# ## 9. Relay mode
# The same chain without an orchestrator: you talk to the planner, and the route travels in the message metadata.

# %% a2a_coverage=["pattern:6", "op:SendStreamingMessage"]
# One request to the first hop; the chain completes behind it
route = {"relay": {"route": [WRITER, EDITOR], "origin_task": "", "hop": 0}}
async with await connect(PLANNER, streaming=True) as pc:
    evs, t0 = [], time.perf_counter()
    async for ev in stream_text(pc, TOPIC, metadata=route):
        evs.append(ev)
        if ev.WhichOneof("payload") == "status_update" and ev.status_update.status.HasField("message"):
            print(f"{time.perf_counter() - t0:5.1f}s  {ev.status_update.status.message.parts[0].text[:100]}")
relayed = collect(evs)
print("\nclient sees ONE task:", relayed.task_id[:8], state_name(relayed.state), "| artifacts:", [a.name for a in relayed.task.artifacts])
print("downstream-final starts with:", repr(artifact_text(relayed, "downstream-final")[:60]))

# %% [markdown]
# | | Orchestrated | Relay |
# |---|---|---|
# | Who knows the route | the orchestrator | the **message** (metadata) |
# | Failure handling | central: one place names the failed hop | each hop; a failure surfaces on the first task |
# | Quality loop | easy (the orchestrator decides) | not possible without a controller |
# | Coupling | hops are independent | every hop must understand `relay` metadata |
# | Observability | one fan-out trace | a deep nested trace |
# | Typical use | workflows with QA loops | pipelines across organisation boundaries |

# %% [markdown]
# ## 10. The same chain with each framework's own constructs
# ADK's `SequentialAgent`, a Strands `Graph` and a LangGraph graph of `RemoteA2ANode`s, calling the same three agents.

# %% a2a_coverage=["pattern:6", "pattern:subagent", "pattern:graphnode"]
# Run all three native chains, then inspect what each one actually sent to the writer
async def writer_inputs():
    async with await connect(WRITER, streaming=False) as wc:
        tasks = sorted((await wc.client.list_tasks(ListTasksRequest())).tasks, key=lambda t: (t.status.timestamp.seconds, t.status.timestamp.nanos))
    t = tasks[-1]
    parts = [("data" if p.HasField("data") else "text") for p in t.history[0].parts]
    return {"parts": parts, "refs": len(t.history[0].reference_task_ids), "text": t.history[0].parts[0].text[:70].replace("\n", " ")}

rows = []
adk_events = await native.run_adk_chain(TOPIC)
rows.append({"chain": "ADK SequentialAgent", **await writer_inputs(), "answered by": [a for a, _ in adk_events][-1]})
final_text, sent = await native.run_strands_chain(TOPIC, scope=str(uuid.uuid4()))
rows.append({"chain": "Strands Graph", **await writer_inputs(), "answered by": "editor"})
lg = native.build_langgraph_chain()
out = await lg.ainvoke({"topic": TOPIC}, {"configurable": {"thread_id": str(uuid.uuid4())}})
rows.append({"chain": "LangGraph RemoteA2ANodes", **await writer_inputs(), "answered by": "editor"})
display.table(rows)

# %% [markdown]
# **What just happened**
# - **ADK** sent every remote only the *user's original message*: the planner's outline stayed in ADK's own session history and never became a Part for the writer. The hand-off is implicit, and a text-only remote simply did not get its input.
# - **Strands** and **LangGraph** passed the previous node's *text* (the outline's text rendering). That works, but the structure is gone.
# - None of them sent a DataPart or a `reference_task_ids` entry, so nothing links the three tasks afterwards.
# - That is the trade: framework constructs are the least code; the orchestrator is what makes the chain **auditable** (explicit Parts, provenance, per-hop contexts, cancel propagation).

# %% [markdown]
# ## 11. See it in Grafana

# %%
# One trace per topology, side by side
with display.cell_span("orchestrated chain") as s1:
    async with await connect(ORCH, streaming=False, timeout=900) as oc:
        await send_text(oc, TOPIC, context_id=str(uuid.uuid4()))
with display.cell_span("relay chain") as s2:
    async with await connect(PLANNER, streaming=True) as pc:
        await send_text(pc, TOPIC, metadata=route)
tel.flush()
display.trace_link(s1)
display.trace_link(s2)

# %% [markdown]
# Compare the two traces in Grafana → Explore:
# - **Orchestrated:** the orchestrator's `a2a.server.execute` has three sibling client spans (planner, writer, editor): a **wide, shallow** tree.
# - **Relay:** planner → writer → editor each nested inside the previous one's execute span: a **deep** tree, and the first hop's span lasts as long as the whole chain.
# - **TraceQL:** `{ name = "a2a.server.execute" && resource.service.name =~ "lab07-.*" }`.

# %% [markdown]
# ## 12. Exercises
# - 🟢 **Easy:** add a fact-checker hop between writer and editor (one more entry in `HOPS` and one more step in the orchestrator).
# - 🟡 **Medium:** after the writer, run the editor and an "SEO" hop **in parallel** and merge their artifacts.
# - 🔴 **Stretch:** make relay mode resumable: record the remaining route in the failed task so the client can restart from the failed hop.

# %% tags=["skip-ci"] jupyter={"source_hidden": true}
# Hidden solution (easy): in ChainOrchestrator._drive add a step "check" between "write" and
# "edit" that calls a new service with refs=[run.last["writer"]] and sets run.draft from its artifact.

# %% [markdown]
# ## 13. Recap
#
# | Pattern 6 | |
# |---|---|
# | **Use it when** | A result needs several specialised steps, possibly from different teams or frameworks |
# | **Not when** | One agent could do all steps (a chain multiplies latency and failure points) |
# | Key mechanics | Artifacts → Parts of the next Message; `reference_task_ids` for provenance; one task + one context per hop |
# | Choose | **Orchestrated** when you need loops, central failure handling and cancel; **relay** for fire-and-forget pipelines |
#
# **Pitfalls:** passing a hop's `task_id` to another hop; one shared context for every hop (they then trample each other's memory); flattening DataParts to text; forgetting that cancelling the top-level task does nothing to a running hop unless you propagate it.
#
# **Next:** Lab 08 makes long-running work asynchronous with push notifications.

# %% [markdown]
# ## Cleanup

# %%
# Close the clients and flush telemetry
for client in (planner, writer, editor, orch):
    await client.aclose()
tel.flush()
