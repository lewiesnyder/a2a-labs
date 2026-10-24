# Lab 05: Supervisor / router (pattern 4)

One agent in front of several others. The supervisor **discovers** specialists from their Agent
Cards, **routes** work with a decision model and keeps a **delegation ledger** so every remote
agent's `context_id` and `task_id` stay separate.

**Notebook:** [`notebooks/lab05_supervisor_router.ipynb`](notebooks/lab05_supervisor_router.ipynb)
(authored as the paired `.py`). **Spec:** [`labs/lab05-supervisor-router.md`](../lab05-supervisor-router.md).

## Run it

```bash
REASONING_THINK=false make up LAB=lab05          # 3 specialists + 3 supervisors (needs Ollama incl. nimble-v3)
LLM_PROVIDER=fake DECISION_PROVIDER=fake make e2e LAB=lab05   # deterministic CI run
```

| Service | Port | What it is |
|---|---|---|
| `lab05-supervisor` | 10501 | **LangGraph** supervisor: Nimble `choice` in a conditional edge, `Send()` fan-out, ledger |
| `lab05-adk-coordinator` | 10505 | ADK `Agent(sub_agents=[RemoteA2aAgent × 3])`: the reasoning LLM transfers the conversation |
| `lab05-strands-graph-supervisor` | 10506 | Strands `GraphBuilder`: classify → one specialist (conditional edges) → summarize |
| `lab05-currency` / `-weather` / `-facts` | 10502–10504 | the Lab 02 specialists (LangGraph / Strands / ADK) run under lab05 names |

## The three routers

| | LangGraph | ADK | Strands Graph |
|---|---|---|---|
| Decides with | Nimble `choice` + `noul` | LLM `transfer_to_agent` | Python predicates after one Nimble classification |
| Multi-intent | `split` + `Send()` fan-out | one specialist | one specialist |
| Remote `INPUT_REQUIRED` | local `interrupt()` | the remote takes over | relayed as text; next message continues the paused task |
| Per-remote tracking | explicit **ledger** | implicit (`a2a:context_id` / `a2a:task_id` on session events) | `RemoteAgent` pending tasks |

## The ledger (the point of the lab)

`LedgerEntry = {agent, service, remote_context_id, active_task_id | None, last_state, task_ids}`,
one per (supervisor context, remote agent):

- the remote context is derived (uuid5) from the supervisor context, so follow-ups reuse it;
- `active_task_id` is set only while the remote task is open; a user answer continues that task,
  otherwise a **new task in the same context** starts;
- a `task_id` is never recorded for a second agent (`Ledger.record` raises);
- an unreachable specialist is recorded as `UNREACHABLE` and the supervisor still `COMPLETED`s.

## Code map

| Path | Role |
|---|---|
| `agents/supervisor.py` | The LangGraph graph: discover → triage → (clarify) → route → (split) → delegate × N → check_complete → synthesize |
| `agents/adk_coordinator.py` | ADK coordinator and `implicit_ledger()` |
| `agents/strands_graph.py` | `ClassifyNode`, `A2ANode`, `AnswerNode`, `SummarizeNode`, `GraphAgent` (CUSTOM) |
| `discovery.py` | `Directory`: cards → routing options, TTL cache, stale-on-error (CUSTOM) |
| `ledger.py` | `Ledger` and its invariants |
| `prompts.py` | The six-prompt routing table shared by tests and the notebook |
| `servers/*` | A2A servers; CUSTOM `/debug/state`, `/debug/ledger`, `/debug/history`, `/specialists` routes |

## What to notice

- Routing options are built from **card names, descriptions and skills**; `POST /specialists`
  adds one at runtime without a code change.
- `ROUTER_MODE=llm` swaps Nimble for the reasoning model answering the same questions; compare
  accuracy and latency in notebook step 10.
- Fake mode (`config/fake_responses.yaml`, `decision:` section) scripts every routing choice, so
  the six-prompt table is deterministic in CI.
- Limits kept on purpose: parallel branches that pause together resume one at a time; the ADK
  and Strands supervisors pick one specialist per request.
