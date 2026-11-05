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
# # Lab 11 · Human-in-the-loop (HITL) across agents
#
# **Goal:** put a person into a multi-agent workflow without losing the plot. A request goes to an **orchestrator**, which delegates to several agents; some of them need a human. You will relay every question, route every answer to the right delegate task, and then do the same **asynchronously**, with the client long gone.
# **Time:** about 4 minutes · **Prerequisites:** Labs 05, 07, 08 and 10 · `make up LAB=lab11`
#
# **What you'll learn**
# - A2A's two **interrupted states**, `INPUT_REQUIRED` and `AUTH_REQUIRED`, and the **resume by `task_id`** mechanic.
# - A small **structured convention** (`{"hitl": …}` data parts) for approvals, choices and consent, next to the readable text.
# - **Relaying through an orchestrator**: parallel delegates, several open questions at once, answers routed one at a time or together, a second round of questions in the next stage.
# - **Outcomes**: approve, reject (and compensate), **edit**, an unclear free-text reply, cancel, and a **deadline**.
# - **Asynchronous HITL**: push notifications into a **human-task inbox**; the person answers hours later.

# %% [markdown]
# ## 1. The ideas
#
# | | `INPUT_REQUIRED` | `AUTH_REQUIRED` |
# |---|---|---|
# | Meaning | the agent needs **information or a decision** | the agent needs the person's **consent / credentials** |
# | Task | paused, **same `task_id`** | paused, same `task_id` |
# | Resume | `SendMessage` with that `task_id` (+ `context_id`) | the same |
# | In this lab | choose a seat, approve a payment | allow reading the passport |
#
# A2A defines the states and the resume. It does **not** define what the question looks like. This lab adds a convention, one data part per message:
#
# | Direction | Data part |
# |---|---|
# | agent → human | `{"hitl": {"kind": "approval│choice│input│consent", "prompt", "options", "proposal", "deadline"}}` |
# | human → agent | `{"hitl": {"answer": {"decision", "choice", "edit", "text"}}}` |
# | orchestrator → human | `{"hitl": {"interrupts": [{id, agent, task_id, state, prompt, …}]}}` |
# | human → orchestrator | `{"hitl": {"answers": {"<interrupt id>": {…}}}}` (or plain text when only one question is open) |
#
# > 💡 **Tip.** The text part always says the same thing in words, so a plain chat client or an LLM can still take part; the data part is for UIs.

# %% [markdown]
# ## 2. Architecture
#
# ```mermaid
# sequenceDiagram
#   participant H as Human / client
#   participant O as Trip Desk (orchestrator)
#   participant F as flights
#   participant L as hotels (LangGraph)
#   participant P as payments
#   H->>O: Book a trip to Lisbon
#   par stage 1, in parallel
#     O->>F: Book a flight
#     F-->>O: INPUT_REQUIRED (seat?)
#   and
#     O->>L: Book a hotel
#     L-->>O: INPUT_REQUIRED (hotel?)
#   end
#   O-->>H: INPUT_REQUIRED, interrupts = [seat?, hotel?]
#   H->>O: answers {flights:…, hotels:…} (same task)
#   O->>F: resume (flights task)
#   O->>L: resume (hotels task)
#   O->>P: stage 2: pay $1460
#   P-->>O: INPUT_REQUIRED (approve?)
#   O-->>H: INPUT_REQUIRED, interrupts = [approve?]
#   H->>O: approve
#   O->>P: resume
#   O-->>H: COMPLETED
# ```
#
# | Service | Port | Framework | Needs a human when |
# |---|---|---|---|
# | `lab11-orchestrator` | 11101 | a2a-sdk | relays; owns the ledger and the deadline reaper |
# | `lab11-flights` | 11102 | a2a-sdk | seat not stated (`choice`) |
# | `lab11-hotels` | 11103 | LangGraph | hotel not stated (`choice`, via `interrupt()`) |
# | `lab11-docs` | 11104 | a2a-sdk | passport access (`AUTH_REQUIRED`, `consent`) |
# | `lab11-payments` | 11105 | a2a-sdk | the decision model scores the total as risky (`approval`) |
# | `lab11-inbox` | 11106 | FastAPI | the **human-task inbox** for the async scenario |
#
# **Who owns the task?** The orchestrator owns *its* task. Each delegate owns *its own*. The orchestrator's **ledger** maps each question to the delegate task that asked it.

# %% tags=["parameters"]
QUICK_DEMO = False

# %% [markdown]
# ## 3. Setup

# %%
# Telemetry, helpers, and a wait for all six services
import asyncio
import time

import httpx
from a2a.helpers import get_data_parts
from a2a.types import CancelTaskRequest, GetTaskRequest, TaskPushNotificationConfig

from a2a_labs_common import display
from a2a_labs_common.a2a.client import connect, send_text, wait_for_cards
from a2a_labs_common.a2a.states import state_name
from a2a_labs_common.hitl import Answer, extract_prompt
from a2a_labs_common.telemetry import init_telemetry

from lab11_hitl.clients import answer, inbox, inbox_respond, ledger, open_questions

tel = init_telemetry("notebook-lab11", framework="notebook", lab_id="lab11")
display.show_models()
ORCH, FLIGHTS, HOTELS, DOCS, PAYMENTS = ("lab11-" + n for n in ("orchestrator", "flights", "hotels", "docs", "payments"))
await wait_for_cards([ORCH, FLIGHTS, HOTELS, DOCS, PAYMENTS], timeout=180)  # prints `make up LAB=lab11` if down
TRIP = "Book a trip to Lisbon for 4 nights"


def show_questions(res):
    display.table(
        [
            {"id": q.id, "from": q.agent.removeprefix("lab11-"), "state": q.state, "kind": q.prompt.kind,
             "question": q.prompt.prompt, "options": [o.id for o in q.prompt.options]}
            for q in open_questions(res.task)
        ],
        title=f"{state_name(res.state)} · orchestrator task {res.task_id[:8]}",
    )


# %% [markdown]
# ## 4. One agent, one question: payments
# The payments agent asks the **decision model** whether a payment is risky. A small total goes straight through; a large one pauses the task and asks a person to approve, reject or **edit** it.

# %% a2a_coverage=["hitl", "state:INPUT_REQUIRED", "decision", "part:data"]
# A small and a large payment, straight to the payments agent
pay = await connect(PAYMENTS, streaming=False)
small = await send_text(pay, "Pay for the trip", data={"payment": {"amount": 600}})
print("$600  ->", state_name(small.state), "|", small.text)
big = await send_text(pay, "Pay for the trip", data={"payment": {"amount": 1460}})
prompt = extract_prompt(list(big.task.status.message.parts))
print("$1460 ->", state_name(big.state), "| kind:", prompt.kind, "| options:", [o.id for o in prompt.options])
print("         question:", prompt.prompt, "| proposal:", prompt.proposal)

# %% [markdown]
# The task is paused, not finished. Now the three decisions: **edit** (change the amount: the agent re-scores and may ask again), **approve**, and **reject** (on a second request).

# %% a2a_coverage=["hitl", "state:INPUT_REQUIRED", "state:COMPLETED", "state:REJECTED"]
# Edit twice (still risky, then low enough), then a separate request that is rejected
async def reply(conn, res, **ans):
    return await send_text(conn, "human answer", task_id=res.task_id, context_id=res.context_id,
                           data={"hitl": {"answer": ans}})


still = await reply(pay, big, decision="edit", edit={"amount": 1200})
print("edit to $1200:", state_name(still.state), "| same task:", still.task_id == big.task_id,
      "| new proposal:", extract_prompt(list(still.task.status.message.parts)).proposal["amount"])
paid = await reply(pay, still, decision="edit", edit={"amount": 800})
print("edit to $800 :", state_name(paid.state), "|", paid.text)
again = await send_text(pay, "Pay for the trip", data={"payment": {"amount": 1460}})
no = await reply(pay, again, decision="reject", text="over my budget")
print("reject       :", state_name(no.state), "|", no.task.status.message.parts[0].text)

# %% [markdown]
# **What just happened**
# - `edit` is an answer, not a new task: same `task_id`, the agent re-evaluates and either pays or asks again with the **new proposal**.
# - `REJECTED` is the agent's honest terminal state for "a person said no" (Lab 10: policy decision, not an error).
# - The human could have been anything: a button in a UI, a Slack thread, a script. The agent only sees an answer part.

# %% [markdown]
# ## 5. A LangGraph agent: `interrupt()` is `INPUT_REQUIRED`
# The hotel agent is a LangGraph graph. Its `interrupt()` call pauses the graph; the bridge from Lab 04 turns that into `INPUT_REQUIRED`, and the resume message becomes the value `interrupt()` returns.

# %% a2a_coverage=["hitl", "state:INPUT_REQUIRED", "state:COMPLETED"]
# Ask for a hotel, get the question, answer it
hotels = await connect(HOTELS, streaming=False)
ask = await send_text(hotels, "Book a hotel for 3 nights")
p = extract_prompt(list(ask.task.status.message.parts))
print(state_name(ask.state), "|", p.prompt, "|", [o.label for o in p.options])
done = await reply(hotels, ask, choice="harbor")
print(state_name(done.state), "|", done.text, "|", get_data_parts(done.task.artifacts[-1].parts)[0])

# %% [markdown]
# ## 6. Consent is `AUTH_REQUIRED`
# Asking whether a person agrees to something sensitive is not the same as asking for information. The documents agent needs to read a passport, so it uses `AUTH_REQUIRED`. The mechanics are identical; the **state tells a UI which kind of screen to show**.

# %% a2a_coverage=["hitl", "state:AUTH_REQUIRED", "state:REJECTED"]
# Allow once, deny once
docs = await connect(DOCS, streaming=False)
c1 = await send_text(docs, "Check my travel documents")
print(state_name(c1.state), "|", extract_prompt(list(c1.task.status.message.parts)).prompt)
ok = await reply(docs, c1, decision="approve")
print("allowed:", state_name(ok.state), "|", ok.text)
c2 = await send_text(docs, "Check my travel documents")
denied = await reply(docs, c2, decision="reject", text="not today")
print("denied :", state_name(denied.state), "|", denied.task.status.message.parts[0].text)

# %% [markdown]
# ## 7. The orchestrator: parallel delegates, several questions at once
# Now the real scenario. `Book a trip to Lisbon for 4 nights` goes to the orchestrator. Stage 1 runs **flights** and **hotels** in parallel, and both need the human. The orchestrator pauses *its* task and lists **both** questions, each with an id, the delegate that asked, and a deadline.

# %% a2a_coverage=["hitl", "pattern:4", "state:INPUT_REQUIRED", "op:SendMessage"]
# Book the trip; two delegates ask at once
oc = await connect(ORCH, streaming=False, timeout=300)
res = await send_text(oc, TRIP)
print(res.task.status.message.parts[0].text)
show_questions(res)
q = {x.agent.removeprefix("lab11-"): x.id for x in open_questions(res.task)}

# %% [markdown]
# Answer **one** question. The orchestrator forwards it to *that* delegate's task, and stays `INPUT_REQUIRED` with the remaining question. Then answer the other: stage 1 is complete, **stage 2 starts**, and payments raises a **new** question (an approval), because the total is 420 + 4 × $260 = $1460.

# %% a2a_coverage=["hitl", "pattern:4", "state:INPUT_REQUIRED"]
# One answer at a time
one = await answer(oc, res, {q["flights"]: Answer(choice="aisle")})
print("after the flights answer:", state_name(one.state), "| same task:", one.task_id == res.task_id)
show_questions(one)
two = await answer(oc, one, {q["hotels"]: Answer(choice="palace")})
print("after the hotels answer:")
show_questions(two)

# %% a2a_coverage=["hitl", "pattern:4", "state:COMPLETED", "op:GetTask"]
# Approve the payment; then follow the ledger to every delegate task
(pq,) = open_questions(two.task)
final = await answer(oc, two, {pq.id: Answer(decision="approve")})
print(state_name(final.state), "|", final.text.splitlines()[-1])
display.table(ledger(res.task_id), title="Orchestrator ledger: one row per delegate task")

# %% [markdown]
# **What just happened**
# - **Four tasks** were involved (the orchestrator's and three delegates'), each with its own id. The ledger is how an answer finds its delegate.
# - Answers can arrive **together or one by one**, in any order. The orchestrator never blocks the delegates that could finish: flights' result was on the orchestrator's task before the human answered anything.
# - Stage 2 only started when stage 1 had **no open question left**, and its own question came as a *second* round. A workflow can interrupt as many times as it needs.
# - Compare Lab 05, whose supervisor resumed one parallel interrupt at a time.

# %% [markdown]
# ## 8. Skipping the human
# A question is only asked when the request does not already answer it, and the payment only when it looks risky.

# %% a2a_coverage=["hitl", "decision", "state:COMPLETED"]
# State your preferences up front: nothing to ask, and $860 is under the limit
res2 = await send_text(oc, TRIP + ", aisle seat, Harbor Inn")
print(state_name(res2.state), "|", res2.text.splitlines()[-1])

# %% [markdown]
# ## 9. Free text, read by a decision model
# People do not type JSON. With exactly one open question, plain text is enough: a **decision model** reads it as `approve`, `reject` or `unclear`, and an unclear reply is asked again instead of guessed.

# %% a2a_coverage=["hitl", "decision", "state:INPUT_REQUIRED"]
# Only the payment is open; reply in words
res3 = await send_text(oc, TRIP + ", aisle, palace")
for words in ("purple monkey dishwasher", "sure, go ahead"):
    res3 = await answer(oc, res3, text=words)
    msg = res3.task.status.message.parts[0].text if res3.task.status.HasField("message") else res3.text
    print(f"{words!r:32} ->", state_name(res3.state), "|", msg.splitlines()[0][:90])

# %% [markdown]
# ## 10. A reject, and compensation
# If a person says no to one delegate, the other open delegates are pointless. The orchestrator **cancels** their tasks (`CancelTask`) so nothing is left waiting for a human who has moved on, and ends `REJECTED`.

# %% a2a_coverage=["hitl", "state:REJECTED", "state:CANCELED", "op:CancelTask"]
# Ask for flights, hotels and documents; refuse the hotel question
res4 = await send_text(oc, TRIP + ", check my documents")
print("open:", [(x.agent.removeprefix("lab11-"), x.state) for x in open_questions(res4.task)])
hid = next(x.id for x in open_questions(res4.task) if x.agent == HOTELS)
end = await answer(oc, res4, {hid: Answer(decision="reject", text="changed my mind")})
print(state_name(end.state), "|", end.task.status.message.parts[0].text)
display.table(ledger(res4.task_id))

# %% [markdown]
# ## 11. Cancelling a waiting task
# The client can also walk away: `CancelTask` on the orchestrator propagates to the open delegate tasks.

# %% a2a_coverage=["hitl", "op:CancelTask", "state:CANCELED", "op:GetTask"]
# Start a trip, then cancel it while it waits
res5 = await send_text(oc, TRIP)
await oc.client.cancel_task(CancelTaskRequest(id=res5.task_id))
print("orchestrator:", state_name((await oc.client.get_task(GetTaskRequest(id=res5.task_id))).status.state))
print("delegates   :", [(r["agent"].removeprefix("lab11-"), r["state"]) for r in ledger(res5.task_id)])

# %% [markdown]
# ## 12. Asynchronous HITL: the client leaves, the question waits
# Everything so far kept a connection to the orchestrator open. A person may take hours. For that:
# 1. the client submits with **`return_immediately`** and a **push config** that points at a **human-task inbox**;
# 2. the client **disconnects**;
# 3. the orchestrator pushes `INPUT_REQUIRED` to the inbox, which files one item per open question;
# 4. a person opens the inbox later and answers; the inbox sends a normal `SendMessage` carrying the task id;
# 5. the next stage's question and the final result are pushed to the inbox too.
#
# ```mermaid
# sequenceDiagram
#   participant C as Client
#   participant O as Orchestrator
#   participant I as Inbox
#   participant P as Person
#   C->>O: SendMessage(return_immediately, push config → inbox)
#   O-->>C: Task (WORKING)
#   Note over C: disconnects
#   O->>I: push: INPUT_REQUIRED {interrupts}
#   P->>I: GET /items  (later)
#   P->>I: respond "yes, go ahead"
#   I->>O: SendMessage(task_id, answer)
#   O->>I: push: COMPLETED
# ```

# %% a2a_coverage=["hitl", "op:CreatePushConfig", "op:SendMessage", "state:INPUT_REQUIRED"]
# Submit and disconnect; the question appears in the inbox
HOOK = "http://lab11-inbox:11106/webhooks/a2a"  # the address the AGENT uses to reach the inbox
cfg = TaskPushNotificationConfig(url=HOOK, token="lab11-inbox-token")
async with await connect(ORCH, streaming=False, push_config=cfg) as gone:
    sub = await send_text(gone, TRIP + ", aisle, palace", return_immediately=True)
print("submitted:", state_name(sub.state), "| task", sub.task_id[:8], "| connection closed")
for _ in range(40):
    mine = [i for i in inbox(status="open") if i["task_id"] == sub.task_id]
    if mine:
        break
    await asyncio.sleep(0.5)
display.table([{"item": i["id"], "from": i["agent"], "kind": i["kind"], "state": i["state"], "question": i["prompt"]["prompt"]} for i in mine], title="Inbox (open items)")

# %% a2a_coverage=["hitl", "decision", "state:COMPLETED", "op:GetTask"]
# Later, a person answers in plain words; the task completes without the client
out = inbox_respond(mine[0]["id"], {"text": "yes, go ahead", "by": "ana"})
print("inbox said:", out.json())
async with await connect(ORCH, streaming=False) as later:
    t = await later.client.get_task(GetTaskRequest(id=sub.task_id))
print("task:", state_name(t.status.state), "| events seen by the inbox:", [e["state"] for e in inbox("/events") if e["task_id"] == sub.task_id])

# %% [markdown]
# **What just happened**
# - Nobody held a connection. The **task** held the state, the **push config** held the address, the **inbox** held the question.
# - The inbox could be Slack, a ticket system or a mobile app. All it needs is the webhook, the task id and the orchestrator's `SendMessage`.
# - The answer was typed in words; the same decision model that read it in step 9 decided it meant approve.
# - Authenticate the inbox user and the webhook (Lab 08's signed pushes, Lab 10's bearer credentials): here the webhook uses only a token.

# %% [markdown]
# ## 13. Nobody answers: the deadline
# A question that waits forever blocks resources and the people behind it. Each question carries a **deadline**. A **reaper** inside the orchestrator answers *"reject: timed out"* when it passes, through the **same resume path** a person uses, so every rule you saw (cancel the others, push the result) applies. Here the request asks for a 6 second deadline.

# %% a2a_coverage=["hitl", "state:REJECTED", "op:GetTask"]
# Submit with a short deadline and wait for the reaper
async with await connect(ORCH, streaming=False, push_config=cfg) as gone:
    late = await send_text(gone, TRIP + ", deadline 6s", return_immediately=True)
t0 = time.monotonic()
async with await connect(ORCH, streaming=False) as watcher:
    while True:
        t = await watcher.client.get_task(GetTaskRequest(id=late.task_id))
        if state_name(t.status.state) not in ("WORKING", "INPUT_REQUIRED", "SUBMITTED") or time.monotonic() - t0 > 60:
            break
        await asyncio.sleep(1)
print(f"after {time.monotonic() - t0:.0f}s:", state_name(t.status.state), "|", t.status.message.parts[0].text)
print("inbox items for it:", {i["status"] for i in inbox("/items") if i["task_id"] == late.task_id})

# %% [markdown]
# ## 14. Design notes
#
# | Question | This lab | In production |
# |---|---|---|
# | Who is "the human"? | whoever calls `SendMessage` with the task id | an authenticated user in an approvals app; the inbox must check **who** may answer which item (Lab 10) |
# | Where does the question wait? | in the paused **task**, plus the inbox | a durable task store (Lab 08's SQLite/DB). Here the orchestrator ledger and the payment proposal are in memory: a restart loses them (documented, `Trip` is rebuilt as "lost track") |
# | How does the human learn about it? | a push to the inbox | push + e-mail/Slack/mobile; reminders and escalation before the deadline |
# | What if two people answer? | the second gets `409` from the inbox | idempotency keys; 4-eyes rules for large amounts |
# | What if nobody answers? | the reaper rejects at the deadline | auto-escalate first; choose reject vs. approve-by-default per risk |
# | Which state? | `INPUT_REQUIRED` for decisions, `AUTH_REQUIRED` for consent | the same; a UI picks the screen from the state and `kind` |
# | Can an LLM sit in the middle? | the orchestrator is deterministic; a decision model reads replies | an LLM orchestrator must pass questions through **verbatim** and never answer them itself |
#
# > ⚠️ **Pitfall.** Never let an agent resume its *own* `INPUT_REQUIRED` task with an answer it invented. The reaper is the only non-human responder here, and its answer is always a reject.

# %% [markdown]
# ## 15. See it in Grafana

# %%
# One trace across the orchestrator, the delegates and the resume
with display.cell_span("hitl trip") as span:
    r = await send_text(oc, TRIP + ", aisle, palace")
    await answer(oc, r, text="yes, go ahead")
tel.flush()
display.trace_link(span)

# %% [markdown]
# Try in Grafana → Explore:
# - **TraceQL:** `{ name = "hitl.delegate" }` shows each delegate call (`hitl.agent`, `hitl.resume`); `{ name = "hitl.resume" }` shows how many answers one message carried.
# - **TraceQL:** `{ name = "decision.decide" && span.decision.reply.choice != "" }` finds the replies that were read from free text.
# - **Metrics:** `a2a.server.task.state_transitions` with `to="INPUT_REQUIRED"` against `to="REJECTED"` shows how often people are the bottleneck.

# %% [markdown]
# ## 16. Exercises
# - 🟢 **Easy:** add a `lab11-visa` delegate that asks one `input` question ("which passport country?"). It only needs a new executor, a card and a line in `_start`.
# - 🟡 **Medium:** add a reminder: when half the deadline has passed, the reaper posts a notification to the inbox (`reminder` event) without answering.
# - 🔴 **Stretch:** make the orchestrator's ledger durable (a `DatabaseTaskStore` plus a table for runs) and show a question surviving an orchestrator restart.

# %% tags=["skip-ci"] jupyter={"source_hidden": true}
# Hidden solution (easy): agents/visa.py like flights.py (kind="input"; the answer's `text` is the country),
# servers/visa.py, a card in cards.CARDS, config/endpoints.yaml + compose.yaml entries, and in
# orchestrator._start: trip.runs[VISA] = Run(VISA, 1, deadline_s=deadline_s) when "visa" is in the request.

# %% [markdown]
# ## 17. Recap
#
# | | |
# |---|---|
# | **Use it when** | A workflow needs a person's information, decision or consent partway through, possibly long after the request |
# | Mechanics | `INPUT_REQUIRED`/`AUTH_REQUIRED` + resume by `task_id`; one task per delegate; an orchestrator ledger; push for async |
# | Orchestrator rules | keep what finished, list every open question, route each answer, compensate on reject, enforce deadlines |
# | Decision models | decide *whether* a human is needed (risk) and *what* the human meant (free text) |
#
# **Pitfalls:** treating a paused task as failed; resuming without the `task_id` (it starts a new task); answering for the human; letting a question wait forever; confusing consent (`AUTH_REQUIRED`) with information (`INPUT_REQUIRED`).
#
# **Next:** hardening is done; build your own workflow by copying `orchestrator.py`.

# %% [markdown]
# ## Cleanup

# %%
# Close clients and flush telemetry
for c in (pay, hotels, docs, oc):
    await c.aclose()
tel.flush()
