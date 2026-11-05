# Lab 11: Human-in-the-Loop (HITL) across agents

**Package:** `labs/lab11-hitl` → import `lab11_hitl`
**Profile:** `lab11`
**Models:** `decision` (risk score, reading the human's free-text reply); `quick` is not needed
**Frameworks:** a2a-sdk (orchestrator, flights, docs, payments, inbox), LangGraph (hotels, via `interrupt()`)

> Added after the original build plan (branch `hitl`). It builds on Lab 05 (per-remote task tracking), Lab 07 (a question in the middle of a chain), Lab 08 (async push) and Lab 10 (`AUTH_REQUIRED`).

## 1. Learning objectives

1. Know the two A2A **interrupted states**: `INPUT_REQUIRED` (the agent needs information or a decision) and `AUTH_REQUIRED` (it needs the person's consent). Both pause the **same task**; the client resumes it with a new `SendMessage` carrying that `task_id`.
2. Use a **structured prompt/answer convention** in data parts (`{"hitl": …}`) so a UI can render an approval, a choice, a form or an edit, while the text part stays readable for people and LLMs.
3. **Relay HITL through an orchestrator.** A request goes to an orchestrator, which delegates to several agents in parallel. Some of them need the human. The orchestrator:
   - keeps the delegates that can finish running and keeps their results;
   - surfaces **every** pending question at once (a list), each with an id, the delegate that asked, and a deadline;
   - routes each answer to the **right delegate task** (a ledger of delegate task ids), possibly one answer at a time;
   - runs the next stage when a stage's interrupts are resolved, which may raise new ones (an approval after the hotel is chosen).
4. Handle the **decision outcomes**: approve, reject (compensate: cancel the others), **edit** (the human changes the proposal; the agent re-evaluates), and an unclear free-text reply (ask again).
5. Use a **decision model twice**: to decide whether a human is needed at all (risk score, small amounts go through) and to read a free-text reply ("sure, go ahead") into approve / reject / unclear.
6. Do **asynchronous HITL**: the client submits with `return_immediately` and a push config, then disconnects. A **human-task inbox** receives the `INPUT_REQUIRED` push, shows it to a person later, and resumes the task when they answer. A **deadline** is enforced by a reaper that answers "reject: timed out" through the normal resume path.
7. Cancel a task that is waiting for a human, and see what happens to the delegates.

## 2. Services

| Service | Port | Framework | Role |
|---|---|---|---|
| `lab11-orchestrator` | 11101 | a2a-sdk | "Trip Desk": two stages (flights ∥ hotels ∥ docs, then payments), HITL relay, ledger, reaper |
| `lab11-flights` | 11102 | a2a-sdk | asks the seat preference (`choice`) |
| `lab11-hotels` | 11103 | LangGraph | asks which hotel (`choice`), through `interrupt()` |
| `lab11-docs` | 11104 | a2a-sdk | needs the traveller's consent to read the passport: `AUTH_REQUIRED` |
| `lab11-payments` | 11105 | a2a-sdk | scores the risk of the total; small totals are paid, large ones need an `approval` (approve / reject / edit the amount) |
| `lab11-inbox` | 11106 | FastAPI | the human-task inbox: the orchestrator's push webhook, a list of waiting items, `respond` |

## 3. The convention

Agent → human, a data part on the status message:

```json
{"hitl": {"kind": "approval|choice|input|consent", "prompt": "…", "options": [{"id": "approve", "label": "Approve"}],
          "proposal": {"amount": 1280}, "deadline": "2026-11-04T10:00:00Z"}}
```

Human → agent, a data part on the resume message:

```json
{"hitl": {"answer": {"decision": "approve|reject|edit", "choice": "aisle", "edit": {"amount": 900}, "text": "…"}}}
```

Orchestrator → human lists every open question: `{"hitl": {"interrupts": [{"id", "agent", "task_id", "state", "kind", "prompt", "options", "proposal", "deadline"}]}}`.
Human → orchestrator: `{"hitl": {"answers": {"<interrupt id>": <answer>}}}`, or plain text when exactly one question is open.

## 4. Acceptance tests

- A single delegate: payments pauses for approval; approve completes, reject gives `REJECTED`, edit re-scores and may ask again; an amount under the threshold needs no human.
- Orchestrated: flights and hotels both ask at once; the orchestrator is `INPUT_REQUIRED` with two interrupts; answering one leaves one; answering the second starts payments, which asks for approval; approving completes the orchestrator. Delegate task ids are distinct and recorded in the ledger.
- `AUTH_REQUIRED` from a delegate is relayed as `AUTH_REQUIRED` (not downgraded to input).
- Reject at payments: the orchestrator ends `REJECTED` with the reason and no delegate task is left open.
- Free-text reply "yes, go ahead" is read as approve; gibberish asks again.
- Async: submit with push and `return_immediately`, no connection held; the inbox lists the question; responding through the inbox resumes and completes the task; the inbox also receives the completion push.
- Deadline: an unanswered interrupt is rejected by the reaper; delegates are cancelled; the push says why.
- Cancelling a waiting orchestrator task cancels its open delegate tasks.
