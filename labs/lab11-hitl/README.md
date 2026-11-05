# Lab 11: Human-in-the-loop (HITL) across agents

A person in the middle of a multi-agent workflow. A request goes to an **orchestrator**, which
delegates to several agents in parallel; some of them need a human. The orchestrator relays every
question, routes every answer to the right delegate task, and does the same **asynchronously**
(the client leaves; an inbox holds the question; a deadline reaper handles silence).

**Notebook:** [`notebooks/lab11_human_in_the_loop.ipynb`](notebooks/lab11_human_in_the_loop.ipynb) (authored as the paired
`.py`). **Spec:** [`labs/lab11-hitl.md`](../lab11-hitl.md). Branch: `hitl`.

## Run it

```bash
make up LAB=lab11                                             # orchestrator, 4 delegates, inbox
LLM_PROVIDER=fake DECISION_PROVIDER=fake make e2e LAB=lab11   # deterministic run
```

| Service | Port | Framework | Needs a human when |
|---|---|---|---|
| `lab11-orchestrator` | 11101 | a2a-sdk | relays; ledger; deadline reaper (`HITL_DEADLINE_S`, `HITL_REAPER_S`) |
| `lab11-flights` | 11102 | a2a-sdk | the seat is not in the request (`choice`) |
| `lab11-hotels` | 11103 | LangGraph | the hotel is not in the request (`choice`, `interrupt()`) |
| `lab11-docs` | 11104 | a2a-sdk | passport access: `AUTH_REQUIRED` (`consent`) |
| `lab11-payments` | 11105 | a2a-sdk | the decision model scores the total as risky (`approval`: approve / reject / edit) |
| `lab11-inbox` | 11106 | FastAPI | the async scenario: push webhook + `GET /items` + `POST /items/{id}/respond` |

Request keywords (the orchestrator is deterministic): `aisle`/`window`, a hotel name, `check my
documents`, `N nights`, `deadline 20s`. Leaving a preference out is what makes an agent ask.

## The convention

Data parts keyed `hitl` (spec §3): agent → human `{kind, prompt, options, proposal, deadline}`; human →
agent `{answer: {decision, choice, edit, text}}`; orchestrator → human `{interrupts: [...]}`; human →
orchestrator `{answers: {<interrupt id>: {...}}}`. `a2a_labs_common.hitl` builds and reads them
and `interpret_reply` turns free text into an answer (a decision-model `choice`).

## Code map

| Path | Role |
|---|---|
| `../../libs/a2a-labs-common/.../hitl.py` | the convention, `interpret_reply` |
| `src/lab11_hitl/orchestrator.py` | `TripDeskExecutor`: stages, ledger (`Run`), pause/resume, abort, reaper |
| `src/lab11_hitl/agents/` | flights, hotels (LangGraph), docs, payments (+ risk gate) |
| `src/lab11_hitl/inbox.py` | the human-task inbox |
| `src/lab11_hitl/servers/` | one module per service; `common.py` wires push, lifespan and `/hitl/ledger/{task}` |
| `src/lab11_hitl/clients.py` | `open_questions`, `answer`, inbox helpers |

## What to notice

- **One orchestrator task, N delegate tasks.** `INPUT_REQUIRED` is per task; the orchestrator's own
  pause lists the open questions of *all* delegates and its ledger maps each id to a delegate task.
- **Stages.** Payments only starts when stage 1 has no question left, so a second round of questions
  is normal.
- **Consent is a different state.** If every open question is consent, the orchestrator itself is
  `AUTH_REQUIRED`.
- **The clock answers through the same door.** The reaper sends a normal resume message to the
  orchestrator's own endpoint: "reject, timed out". No special path to test twice.
- **Limits (documented, DEV-70..72):** orchestrator ledger and the payment proposal are in memory;
  a restart answers "I lost track of this trip". The inbox authenticates the webhook with a token
  only. The LangGraph bridge ends `COMPLETED` even for a refusal, so the hotels agent reports it in
  its artifact (`declined`) and the orchestrator maps that to `REJECTED`.
