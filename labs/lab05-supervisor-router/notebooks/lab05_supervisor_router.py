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
# # Lab 05 · Supervisor / router (pattern 4)
#
# **Goal:** put one agent in front of several others, let it *discover* them from their Agent Cards, *route* work with a decision model, and *track every remote conversation separately*.
# **Time:** about 6 minutes with real models (the multi-intent and LLM-router steps are the slow ones) · **Prerequisites:** Labs 01–04 · `REASONING_THINK=false make up LAB=lab05`
#
# **What you'll learn**
# - Routing options built from **skills in Agent Cards**; nothing about a specialist is hard-coded.
# - Three routing strategies side by side: a **decision model** (Nimble `choice` in a LangGraph conditional edge), an **LLM tool call** (ADK `transfer_to_agent`) and **deterministic graph edges** (Strands Graph).
# - The **Delegation Ledger**: per remote agent, its own `context_id`, active `task_id`, last state and task history.
# - **Fan-out** of a multi-intent request to several specialists in parallel.
# - What happens when a specialist **asks a question** or is **down**.
#
# Spec: [A2A specification](https://a2a-protocol.org/latest/specification/) · [LangGraph `Send`](https://docs.langchain.com/oss/python/langgraph/graph-api#send)

# %% [markdown]
# ## 1. Architecture
#
# ```mermaid
# flowchart LR
#   NB[Notebook] --> S[LangGraph supervisor :10501<br/>Nimble router + ledger]
#   NB --> C[ADK coordinator :10505<br/>LLM transfer]
#   NB --> G[Strands Graph :10506<br/>conditional edges]
#   S & C & G -->|A2A| CU[Currency :10502<br/>LangGraph]
#   S & C & G -->|A2A| W[Weather :10503<br/>Strands]
#   S & C & G -->|A2A| F[Facts :10504<br/>ADK]
# ```
#
# | Service | Port | Role |
# |---|---|---|
# | `lab05-supervisor` | 10501 | **LangGraph** supervisor: discover → triage → route → delegate × N → synthesize |
# | `lab05-adk-coordinator` | 10505 | ADK `Agent(sub_agents=[RemoteA2aAgent × 3])`, routing by LLM transfer |
# | `lab05-strands-graph-supervisor` | 10506 | Strands `GraphBuilder`: classify → one specialist → summarize |
# | `lab05-currency` / `-weather` / `-facts` | 10502–10504 | the three Lab 02 specialists, one per framework, unchanged |
#
# The next cell draws the supervisor graph from LangGraph itself.

# %% a2a_coverage=["pattern:4"]
# Render the supervisor graph with LangGraph's own Mermaid export
from lab05_supervisor.agents.supervisor import Supervisor
from lab05_supervisor.discovery import Directory

from a2a_labs_common import display

display.mermaid(Supervisor(directory=Directory([])).build().get_graph().draw_mermaid())

# %% tags=["parameters"]
QUICK_DEMO = False

# %% [markdown]
# ## 2. Setup

# %%
# Initialise telemetry, show the models and wait for all six services
import json
import shutil
import subprocess
import time
import uuid

import httpx
from a2a.types import ListTasksRequest

from a2a_labs_common.a2a.client import connect, send_text, stream_text, wait_for_cards
from a2a_labs_common.a2a.states import state_name
from a2a_labs_common.config import endpoint, find_config_dir
from a2a_labs_common.telemetry import init_telemetry

from lab05_supervisor.prompts import CURRENCY, FACTS, ROUTING_TABLE, WEATHER

tel = init_telemetry("notebook-lab05", framework="notebook", lab_id="lab05")
display.show_models()
SUP, ADK, STR = "lab05-supervisor", "lab05-adk-coordinator", "lab05-strands-graph-supervisor"
SPECIALISTS = ["lab05-currency", "lab05-weather", "lab05-facts"]
await wait_for_cards([*SPECIALISTS, SUP, ADK, STR], timeout=180)  # prints `make up LAB=lab05` if down
SUP_URL = endpoint(SUP)
sup = await connect(SUP, streaming=False)


async def debug(path: str):
    """CUSTOM, non-A2A debug routes of the supervisors (state, ledger, history)."""
    async with httpx.AsyncClient(timeout=30) as http:
        return (await http.get(f"{endpoint(path.split('/')[0])}/{path.split('/', 1)[1]}")).json()


def bars(probs, width=30):
    for name, p in sorted((probs or {}).items(), key=lambda kv: -kv[1]):
        print(f"  {name:22} {'█' * round(p * width):<{width}} {p:.2f}")


def show_ledger(entries):
    display.table(
        [
            {
                "agent": e["agent"],
                "remote context": e["remote_context_id"][:8],
                "active task": (e["active_task_id"] or "-")[:8],
                "last state": e["last_state"],
                "tasks so far": len(e["task_ids"]),
            }
            for e in entries.values()
        ]
    )


# %% [markdown]
# ## 3. Concept: three ways to route, and why the ledger exists
#
# | | LangGraph supervisor | ADK coordinator | Strands Graph |
# |---|---|---|---|
# | Who decides | a **decision model** (Nimble `choice`) in a conditional edge | the **reasoning LLM** calls `transfer_to_agent` | **Python predicates** on edges, after a one-off classification |
# | You get | probabilities, thresholds, a trace | a transfer and an explanation | an inspectable graph |
# | Multi-intent | fan-out with `Send()` | one specialist per transfer | one specialist per request |
# | Remote `INPUT_REQUIRED` | local `interrupt()` → the user is asked | the remote agent takes over the conversation | relayed as text |
# | Per-remote tracking | **explicit ledger** | implicit (ADK session events) | the `RemoteAgent` helper |
#
# **Why per-remote IDs matter.** Every specialist has *its own* `contextId` and *its own* tasks. A supervisor that reused one agent's `task_id` for another, or opened a new context on every turn, would lose the remote conversation or send a message to a task that does not exist. The **Delegation Ledger** keeps, per (supervisor context, remote agent): the remote `context_id`, the `active_task_id` (set only while the remote task is open), the last state and the list of task ids.

# %% [markdown]
# ## 4. Discovery: the cards are the routing table
# The supervisor was configured with three service names and nothing else. Ask it what it found.

# %% a2a_coverage=["pattern:4", "op:Discovery"]
# Read the supervisor's discovery table (a CUSTOM route) and the exact text Nimble sees per option
async with httpx.AsyncClient() as http:
    found = (await http.get(f"{SUP_URL}/specialists")).json()
display.table(
    [{"routing key (card.name)": r["name"], "service": r["service"], "skills": ", ".join(r["skills"])} for r in found]
)
print("what the decision model reads for the first option:\n  ", found[0]["criteria"])

# %% [markdown]
# **What just happened**
# - `discover` fetched each Agent Card and built `{card.name: "description. Skills: …"}`. That dict *is* the `choice` question's criteria.
# - Add a specialist and the options change; there is no routing code to edit. Step 9 does exactly that.

# %% [markdown]
# ## 5. Single intent: routing with Nimble
# One request, one specialist. Look at the probabilities, not just the winner.

# %% a2a_coverage=["pattern:4", "decision", "op:SendMessage", "context:multiturn"]
# Route "Convert 300 CAD to EUR" and read the decision trace and the ledger from the supervisor
CTX1 = str(uuid.uuid4())
res = await send_text(sup, "Convert 300 CAD to EUR", context_id=CTX1)
print(state_name(res.state), "|", res.text)
values = (await debug(f"{SUP}/debug/state/{CTX1}"))["values"]
route = next(t for t in values["decision_trace"] if t["question"] == "route")
print(f"\nrouting probabilities ({route['backend']}, {route['model']}, {route['latency_ms']:.0f} ms):")
bars(route["probabilities"])
print("\ndelegation ledger:")
show_ledger(values["ledger"])

# %% [markdown]
# **What just happened**
# - The `route` node asked one *choice* question (over the card names plus `none`) and one *noul* question ("several distinct tasks?"). A conditional edge turned the answer into a `Send("delegate", …)`.
# - `delegate` opened a **derived remote context** (uuid5 of supervisor context + agent), so the specialist's `contextId` differs from yours, and recorded its task in the ledger.
# - A model that returns probabilities lets you act on *how sure it is*: the `triage` node asks the user to clarify when P(ambiguous) > 0.6 (step 10).
# - ⚠️ Pitfall: with Nimble the **key** of a question is part of its input. The triage question gave P=0.66 for "Convert 100 USD" under the key `ambiguous` and 0.14 under `unclear_topic`, with identical wording. Name keys neutrally and calibrate on real prompts.

# %% [markdown]
# ## 6. Multi-intent: fan out in parallel
# One request that needs all three specialists. Stream it to watch the delegations start together.

# %% a2a_coverage=["pattern:4", "op:SendStreamingMessage", "decision", "pattern:graphnode"]
# Stream a three-part request; the delegate nodes run in the SAME superstep (LangGraph Send API)
MULTI = ROUTING_TABLE[4].prompt
CTX2 = str(uuid.uuid4())
stream = await connect(SUP, streaming=True)
t0 = time.perf_counter()
async for ev in stream_text(stream, MULTI, context_id=CTX2):
    if ev.WhichOneof("payload") == "status_update" and ev.status_update.status.HasField("message"):
        text = ev.status_update.status.message.parts[0].text
        print(f"{time.perf_counter() - t0:5.1f}s  {state_name(ev.status_update.status.state):10} {text}")
values = (await debug(f"{SUP}/debug/state/{CTX2}"))["values"]
print("\nsplit by the quick model:", [t for t in values["decision_trace"] if t["question"] == "split"])
show_ledger(values["ledger"])
print("\nmerged answer:\n", values["results"])

# %% [markdown]
# **What just happened**
# - `route` saw P(multi) above 0.5 and went to `split`: the quick model returned one sub-request per specialist as JSON (with a clause-split + Nimble fallback if it does not).
# - The edge returned three `Send`s, so `delegate` ran three times in one superstep; the ledger now has **three entries with three different remote contexts and tasks**. Open the trace in step 12 to see the parallel spans.
# - `synthesize` (the reasoning model) merged the results. With a single result it passes the text through untouched.
# - With real models you may see a **second** `route → split → delegate × 3` round and `tasks so far` = 2. That is `check_complete`: Nimble judged that the results did not answer every part (the small weather model sometimes reports another city), and the supervisor retries once. A retry re-runs every part; a production router would re-run only the missing one.

# %% [markdown]
# ## 7. Continuing a remote task that asks a question
# "Convert 100 USD" has no target currency, so the **currency specialist** pauses with `INPUT_REQUIRED`. The supervisor turns that into its own `interrupt()`.

# %% a2a_coverage=["pattern:4", "state:INPUT_REQUIRED", "context:multiturn"]
# The remote question surfaces here; the LIVE ledger already shows the paused remote task
CTX3 = str(uuid.uuid4())
ask = await send_text(sup, "Convert 100 USD", context_id=CTX3)
print("supervisor:", state_name(ask.state), "|", ask.task.status.message.parts[0].text)
live = await debug(f"{SUP}/debug/ledger/{CTX3}")
show_ledger(live)
remote_task = live[CURRENCY]["active_task_id"]

# %% a2a_coverage=["pattern:4", "state:COMPLETED"]
# Answer on the SAME supervisor task; the supervisor forwards it to the SAME remote task
done = await send_text(sup, "JPY", context_id=CTX3, task_id=ask.task_id)
print("supervisor:", state_name(done.state), "|", done.text)
final = (await debug(f"{SUP}/debug/ledger/{CTX3}"))[CURRENCY]
print("same remote task continued:", final["task_ids"] == [remote_task], "| now", final["last_state"])

# %% [markdown]
# **What just happened**
# - The remote task went `INPUT_REQUIRED` → `COMPLETED` **without a second task**: `task_ids` still has one id. Sending the answer as a *new* task would have lost "100 USD".
# - The ledger saw the pause the moment it happened (`active task` set), not only when the node finished. `RemoteA2ANode` reports each remote reply through a hook, and remembers the remote task so LangGraph's re-run of the node after `interrupt()` does not send the request twice.
# - Limit: if several parallel branches pause at once, the supervisor resumes them one at a time.

# %% [markdown]
# ## 8. Context reuse
# A follow-up in the same conversation goes to the same remote `context_id`, as a **new task**. Ask the currency specialist itself what it has for that context.

# %% a2a_coverage=["pattern:4", "context:multiturn", "op:ListTasks"]
# Second currency request in the same supervisor context, then list the specialist's own tasks
before = (await debug(f"{SUP}/debug/ledger/{CTX3}"))[CURRENCY]
again = await send_text(sup, "How much is 250 GBP in JPY?", context_id=CTX3)
after = (await debug(f"{SUP}/debug/ledger/{CTX3}"))[CURRENCY]
print(again.text)
print("same remote context:", before["remote_context_id"] == after["remote_context_id"], "| tasks:", len(before["task_ids"]), "->", len(after["task_ids"]))
async with await connect("lab05-currency", streaming=False) as cur:
    theirs = (await cur.client.list_tasks(ListTasksRequest(context_id=after["remote_context_id"]))).tasks
print("the specialist's own view of that context:", [(t.id[:8], state_name(t.status.state)) for t in theirs])

# %% [markdown]
# **What just happened**
# - The first task ended in a terminal state, so `active_task_id` was cleared and the follow-up started a **new task in the same context**: the specialist keeps the conversation (its LangGraph checkpoint is keyed by that context), and you can see both tasks from its side.
# - Rule of thumb the ledger enforces: continue a task only while it is `INPUT_REQUIRED`; otherwise reuse the *context*, not the task.

# %% [markdown]
# ## 9. Add a specialist at runtime
# Remove the facts specialist from the supervisor's directory, ask a facts question, then register it again. The only change is data.

# %% a2a_coverage=["pattern:4", "op:Discovery"]
# DELETE then POST the CUSTOM /specialists route, asking the same question each time
FACTS_Q = "Tell me some facts about Lisbon."


async def registry(method, service=None):
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.request(method, f"{SUP_URL}/specialists", **({"json": {"service": service}} if service else {}))
    return [row["name"] for row in r.json()]


async def facts_route(label):
    ctx = str(uuid.uuid4())
    out = await send_text(sup, FACTS_Q, context_id=ctx)
    st = (await debug(f"{SUP}/debug/state/{ctx}"))["values"]
    delegated = list(st.get("ledger") or {}) or ["(answered directly)"]
    print(f"{label:28} options={len(st['catalog'])} → {delegated} | {out.text[:70]!r}")


print("registered:", await registry("DELETE", "lab05-facts"))
await facts_route("without the facts agent:")
print("registered:", await registry("POST", "lab05-facts"))
await facts_route("after registering it again:")

# %% [markdown]
# **What just happened**
# - With two options the best the router can do is `none` (answer directly) or a wrong specialist. After `POST /specialists` the next discovery pass read the facts card and the same question reached the facts specialist. No code, no redeploy.
# - Production variant: replace the static list with a registry (Lab 06).

# %% [markdown]
# ## 10. Compare the routers
# The same six prompts through the three deployed supervisors, plus the LangGraph supervisor with `ROUTER_MODE=llm` (the reasoning model answers the same two questions instead of Nimble). Expected agents come from `ROUTING_TABLE`; the ADK and Strands supervisors can serve only **one** specialist per request, so for the multi-intent prompt they pass by taking the first intent (or, when a closed `choice` says `none` to a compound request, by answering directly). Either way they drop two thirds of it, which is what fan-out is for.

# %% a2a_coverage=["pattern:4", "pattern:subagent", "pattern:graphnode", "decision"]
# Run the table; read what each supervisor chose from its own debug route
from langgraph.checkpoint.memory import InMemorySaver

ADK_NAME = {"currency_agent": CURRENCY, "weather_agent": WEATHER, "facts_agent": FACTS}
NODE_NAME = {"currency": CURRENCY, "weather": WEATHER, "facts": FACTS}
llm_graph = Supervisor(router_mode="llm").build(InMemorySaver())


async def run_supervisor(name, prompt):
    ctx = str(uuid.uuid4())
    t0 = time.perf_counter()
    if name == "LangGraph + LLM router":
        out = await llm_graph.ainvoke({"messages": [("user", prompt)]}, {"configurable": {"thread_id": ctx}})
        chosen = set(out.get("ledger") or {})
    else:
        async with await connect({"LangGraph + Nimble": SUP, "ADK transfer": ADK, "Strands Graph": STR}[name], streaming=False) as c:
            await send_text(c, prompt, context_id=ctx)
        elapsed = time.perf_counter() - t0
        if name == "LangGraph + Nimble":
            chosen = set((await debug(f"{SUP}/debug/state/{ctx}"))["values"].get("ledger") or {})
        elif name == "ADK transfer":
            chosen = {ADK_NAME.get(a, a) for a in await debug(f"{ADK}/debug/ledger/{ctx}")}
        else:
            hist = await debug(f"{STR}/debug/history/{ctx}")
            chosen = {NODE_NAME[d["node"]] for d in hist[-1]["delegations"]}
        return chosen, elapsed
    return chosen, time.perf_counter() - t0


NAMES = ["LangGraph + Nimble", "LangGraph + LLM router", "ADK transfer", "Strands Graph"]
rows, right = [], dict.fromkeys(NAMES, 0)
for case in ROUTING_TABLE:
    row = {"prompt": case.prompt[:34]}
    for name in NAMES:
        chosen, secs = await run_supervisor(name, case.prompt)
        ok = chosen == set(case.langgraph) if name.startswith("LangGraph") else case.accepts_single(chosen)
        right[name] += ok
        row[name] = f"{'✓' if ok else '✗'} {', '.join(sorted(c.split()[0] for c in chosen)) or 'direct'} · {secs:.1f}s"
    rows.append(row)
display.table(rows)
print({n: f"{k}/{len(ROUTING_TABLE)}" for n, k in right.items()})

# %% [markdown]
# **What just happened**
# - Nimble answers a *closed* question in one short call and gives probabilities; the LLM router has to be prompted, parsed and may ramble, which shows in the latency and sometimes in the answer. (With `LLM_PROVIDER=fake` the LLM router cannot produce JSON and answers `none` every time; run with real models for the real comparison.)
# - ADK transfers whole conversations and needs no router code, but gives you no probability to threshold and only one specialist per transfer.
# - The Strands graph is the most predictable: the *edges* are code. Its one fuzzy step is the classification, which uses the same decision model.
# - **Calibration:** only the decision model exposes uncertainty. Ambiguous requests ("Help me with Rome") can be thresholded into a clarifying question; try it:

# %% a2a_coverage=["pattern:4", "decision", "state:INPUT_REQUIRED"]
# An ambiguous request: P(ambiguous) is above the 0.6 threshold, so the supervisor asks first
CTX4 = str(uuid.uuid4())
vague = await send_text(sup, "Help me with Rome", context_id=CTX4)
print(state_name(vague.state), "|", vague.task.status.message.parts[0].text)
tri = [t for t in (await debug(f"{SUP}/debug/state/{CTX4}"))["values"]["decision_trace"] if t["question"] == "unclear_topic"]
print("P(ambiguous):", tri and tri[0]["p"])
_ = await send_text(sup, "What is the weather in Tokyo?", context_id=CTX4, task_id=vague.task_id)  # answer it to finish the task

# %% [markdown]
# ## 11. When a specialist is down
# Stop the weather container, ask a weather question, then bring it back. The supervisor must answer, not fail.

# %% a2a_coverage=["pattern:4", "state:FAILED", "state:COMPLETED"]
# Needs the docker CLI on this machine; otherwise the cell prints the commands to run yourself
ROOT = find_config_dir().parent


def compose(*args):
    return subprocess.run(["docker", "compose", "--profile", "lab05", *args], cwd=ROOT, capture_output=True, text=True)


if shutil.which("docker") is None:
    print("docker CLI not found here. On the host run: docker compose --profile lab05 stop lab05-weather")
else:
    print("stopping weather:", compose("stop", "lab05-weather").returncode == 0)
    try:
        CTX5 = str(uuid.uuid4())
        out = await send_text(sup, "What is the weather in Tokyo?", context_id=CTX5)
        print(state_name(out.state), "|", out.text)
        show_ledger((await debug(f"{SUP}/debug/state/{CTX5}"))["values"]["ledger"])
    finally:
        print("restarting weather:", compose("start", "lab05-weather").returncode == 0)
        await wait_for_cards(["lab05-weather"], timeout=120)

# %% [markdown]
# **What just happened**
# - The connection error became a ledger entry with state `UNREACHABLE` (no A2A task ever existed) and a recorded failure; the supervisor's own task still ended `COMPLETED` with an answer that names the unavailable specialist. A remote `FAILED` or `REJECTED` is handled the same way.
# - The supervisor keeps the **last known card** of a specialist that stops answering discovery. Otherwise an outage would silently remove it from the options and the request would be routed to the wrong agent.
# - `check_complete` skips its retry when there are failures: asking the same dead agent again helps nobody.

# %% [markdown]
# ## 12. See it in Grafana

# %%
# One cell, one trace: a fan-out request under a single notebook span
with display.cell_span("fan-out supervisor run") as span:
    cc = await connect(SUP, streaming=False)
    await send_text(cc, MULTI, context_id=str(uuid.uuid4()))
    await cc.aclose()
tel.flush()
display.trace_link(span)

# %% [markdown]
# Try in Grafana → Explore:
# - **TraceQL:** `{ name = "supervisor.route" }`, then read `supervisor.route.choice` and `supervisor.route.multi_p`; the child `decision.decide` span has the per-question attributes.
# - **Parallel spans:** `{ name = "supervisor.delegate" }` on the trace from the cell above shows three overlapping spans, one per specialist, each with `supervisor.remote_state`.
# - **Metric:** the `decision.choice` counter by `question` and `choice` (Prometheus datasource) is the routing mix over time.

# %% [markdown]
# ## 13. Exercises
# - 🟢 **Easy:** make `none` answer directly only when its probability is above 0.5; otherwise ask the user to clarify. (`route` in `agents/supervisor.py`, the `trace_entry["probabilities"]`.)
# - 🟡 **Medium:** persist the ledger in the checkpointer and show it surviving a supervisor restart. (Hint: `delegate` already returns the entry into graph state, and `Ledger.seed` reloads it; make `discover` seed every entry.)
# - 🔴 **Stretch:** add a cost-aware route: a decision-model `score` question ("how expensive is this specialist?") that prefers the cheaper agent when two fit.

# %% tags=["skip-ci"] jupyter={"source_hidden": true}
# Hidden solution (easy): in Supervisor.route, after computing `choice`
#     p_none = (res.answers["route"].probabilities or {}).get(NONE, 0.0)
#     if choice == NONE and p_none <= 0.5:
#         -> return {"ambiguous": True, ...} and add an edge route -> clarify when ambiguous

# %% [markdown]
# ## 14. Recap
#
# | Pattern 4 | |
# |---|---|
# | **Use it when** | One front door should reach several independently owned agents, and the set of agents changes |
# | **Not when** | The set is fixed and tiny (call them directly, Lab 01) or the LLM already holds the tools (Lab 02) |
# | Key mechanics | Cards → options; a decision model → a conditional edge; `Send()` → parallel delegations; a **ledger** → per-remote context and task |
# | Rule | One `context_id` per (conversation, remote agent); continue a task only while `INPUT_REQUIRED`; never send one agent's `task_id` to another |
#
# **Pitfalls:** an agent card's `description` is your routing prompt, so write it for the router; always give the `choice` a `none`; do not let an outage remove an agent from the options; parallel branches that pause together resume one at a time.
#
# **Next:** Lab 06 replaces the static specialist list with a registry and adds trust checks.

# %% [markdown]
# ## Cleanup

# %%
# Close the clients and flush telemetry
for client in (sup, stream):
    await client.aclose()
tel.flush()
