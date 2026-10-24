# Lab 05: Supervisor / Router (Pattern 4)

**Package:** `labs/lab05-supervisor-router` → import `lab05_supervisor` (depends on `lab02-agent-as-tool` for the specialist agents)
**Profile:** `lab05`
**Models:** `decision` (routing), `reasoning` (synthesis), `quick` (specialists)
**Frameworks:** LangGraph (primary), ADK, Strands

> **Pattern 4:** A supervisor discovers specialists, routes work to them, and tracks **per-remote** task and context IDs.

## 1. Learning objectives

1. Discover specialists dynamically from their Agent Cards and build routing options from **skills**. Nothing about the specialists is hard-coded.
2. Route with a **decision model** (Nimble `choice`) in a LangGraph **conditional edge**. Compare it with LLM-tool-call routing (ADK transfer) and with deterministic graph edges (Strands Graph).
3. Maintain a **Delegation Ledger**: for each supervisor context and each remote agent, keep the remote `context_id`, the active `task_id`, the last state and the task history. Reuse remote contexts and continue interrupted remote tasks correctly.
4. Fan out multi-intent requests to several specialists in parallel and merge the results.
5. Handle remote failure, rejection and input-required in the router.

## 2. Services

| Service | Port | Framework | Role |
|---|---|---|---|
| `lab05-currency` | 10502 | LangGraph (from lab02) | specialist |
| `lab05-weather` | 10503 | Strands (from lab02) | specialist |
| `lab05-facts` | 10504 | ADK (from lab02) | specialist |
| `lab05-supervisor` | 10501 | **LangGraph supervisor**, served via `LangGraphA2AExecutor` | primary router with Nimble |
| `lab05-adk-coordinator` | 10505 | ADK `Agent(model=reasoning, sub_agents=[RemoteA2aAgent×3])` | LLM transfer routing |
| `lab05-strands-graph-supervisor` | 10506 | Strands `GraphBuilder` with `A2AAgent` nodes + conditional edges | deterministic graph routing |

The specialist services run the lab02 server modules with lab05 env (service names and ports).

## 3. LangGraph supervisor (`agents/supervisor.py`)

State:

```python
class SupervisorState(TypedDict):
    messages: Annotated[list, add_messages]
    plan: list[Route]                       # [{agent, sub_request}]
    results: Annotated[dict[str, str], merge_dicts]
    ledger: Annotated[dict[str, LedgerEntry], merge_dicts]   # per-remote tracking (serializable)
    decision_trace: list[dict]              # nimble probabilities for display
    retries: int
```

`LedgerEntry = {agent, remote_context_id, active_task_id | None, last_state, task_ids: list[str]}`

Graph:

```text
START → discover (cached cards → specialists table)
      → triage            [decision: noul "is the request ambiguous?" → if p>0.6: clarify (interrupt) ]
      → route             [decision: choice over {agent_name: skill-derived description} + "none";
                           second question noul "does it need more than one specialist?"]
          ├─ single → delegate(agent)              (RemoteA2ANode-like, using ledger)
          ├─ multi  → split (quick model, structured output: list[Route]) → Send() fan-out → delegate × N (parallel)
          └─ none   → answer_directly (quick)
      → check_complete    [decision: noul "does the combined result fully answer?" ; if p<0.5 and retries<1 → route]
      → synthesize (reasoning) → END
```

- `route` builds the `choice` criteria **from discovered cards**: `{card.name: f"{card.description}. Skills: {', '.join(s.name for s in card.skills)}"}`. Adding a specialist therefore needs no code change; the notebook demonstrates this in step 9.
- `delegate` uses the ledger:
  - If the entry's `active_task_id` is in INPUT_REQUIRED, the user's new message **continues that task**.
  - Otherwise it sends a new message in the same `remote_context_id`, so the remote keeps the conversation.
  - A new remote context is created per supervisor context (uuid5 derived), and the ledger is updated on every event.
- Remote INPUT_REQUIRED becomes a local `interrupt()` (as in Lab 04). FAILED/REJECTED are recorded and synthesis mentions them.
- Each routing decision creates a span with the `decision.*` attributes and appends to `decision_trace`.
- A **fallback mode** is set by `ROUTER_MODE=llm`: routing becomes a reasoning-model structured-output call instead of Nimble. It is used to compare quality and latency in the notebook.

## 4. ADK coordinator (`agents/adk_coordinator.py`)

```python
coordinator = Agent(
  name="travel_coordinator", model=get_adk_model("reasoning"),
  instruction="Route to the right specialist. Use currency_agent for money, weather_agent for weather, facts_agent for facts.",
  sub_agents=[RemoteA2aAgent(name="currency_agent", ...), RemoteA2aAgent(name="weather_agent", ...), RemoteA2aAgent(name="facts_agent", ...)],
)
```

- Routing is LLM `transfer_to_agent`.
- ADK's internal per-remote state comes from `RemoteA2aAgent`, which tracks remote context/task in session state [VERIFY where; display it via session state inspection].
- The notebook compares this "implicit ledger" with the explicit LangGraph ledger.
- Explain ADK 2.x sub-agent modes (`chat`, `task`, `single_turn`) and why remote agents are attached as chat-mode sub-agents here. A `single_turn` local wrapper is listed as an exercise [VERIFY mode support for RemoteA2aAgent].

## 5. Strands Graph supervisor (`agents/strands_graph.py`)

```python
builder = GraphBuilder()
builder.add_node(classifier_agent, "classify")          # local quick-model Agent, structured output intent
builder.add_node(A2AAgent(endpoint=endpoint("lab05-currency"), name="currency"), "currency")
builder.add_node(A2AAgent(endpoint=endpoint("lab05-weather"), name="weather"), "weather")
builder.add_node(A2AAgent(endpoint=endpoint("lab05-facts"), name="facts"), "facts")
builder.add_node(summarizer_agent, "summarize")
builder.add_edge("classify", "currency", condition=lambda s: intent(s) == "currency")
# ... weather, facts; all → summarize
builder.set_entry_point("classify")
graph = builder.build()
```

- The `intent(state)` condition may call the decision model synchronously via a cached result. Preferred: run the classify node as a small custom node that calls `DecisionModel.choose` [VERIFY custom node support (MultiAgentBase) in GraphBuilder].
- Served via `A2AServer(agent_factory=lambda ctx: build_graph())` if `A2AServer` accepts a Graph/MultiAgentBase [VERIFY]. Otherwise wrap it in an `AgentExecutor` (custom, `# CUSTOM`).

## 6. Notebook outline

1. Architecture: three supervisors × three specialists.
2. Setup.
3. Concept: routing strategies compared (decision model, LLM tool call, deterministic graph). Explain the ledger and why per-remote IDs matter (two specialists each have their own context and tasks; never reuse one agent's `task_id` with another).
4. **Discovery**: the supervisor's `discover` output table of cards → routing criteria.
5. **Single-intent routing via Nimble**: "Convert 300 CAD to EUR". Show the decision probabilities bar (inline chart), the chosen agent and the ledger after the call.
6. **Multi-intent fan-out**: "What's the weather in Rome and convert 50 EUR to USD, plus one fun fact about Rome". Show the parallel delegations (Send API), the ledger with 3 entries, the merged answer and the trace showing the parallel spans.
7. **Continuing remote tasks**: "Convert 100 USD" → the currency specialist asks "to what?" → the supervisor interrupts → reply "JPY" → the ledger shows the same remote `task_id` continued → COMPLETED.
8. **Context reuse**: a follow-up "and in GBP?" goes to the same remote `context_id`, so the remote remembers 100 USD.
9. **Add a specialist at runtime**: start the extra service `lab05-facts`, if it was stopped, or register lab01's ADK agent. The supervisor re-discovers it and routes to it. No code change.
10. **Compare routers**: the same 6 test prompts through the LangGraph (Nimble), LangGraph (`ROUTER_MODE=llm`), ADK and Strands supervisors. Table of chosen agent, correctness vs expected, and latency. Discuss calibration: Nimble gives probabilities, so ambiguous requests can be thresholded.
11. **Failure handling**: stop `lab05-weather` (`docker compose stop`) and ask a weather question. The supervisor records a connection error as a failure and answers gracefully. Restart it afterwards.
12. Grafana: the routing decision spans and the `decision.choice` metric panel.
13. Exercises:
    - (easy) add a "none" threshold so Nimble's `none` answers directly;
    - (medium) persist the ledger in the checkpointer and show it surviving a supervisor restart;
    - (stretch) add a cost-aware route using `score`.

## 7. Acceptance tests

- Routing unit tests with FakeDecisionModel: the right agent is chosen per scripted choice; `none` → direct answer; multi → fan-out with N delegations.
- Ledger invariants: one entry per remote agent per supervisor context; the INPUT_REQUIRED continuation reuses `active_task_id`; a new task is created after a terminal state; a `task_id` is never sent to a different agent.
- Integration: the 6-prompt routing table runs for all three supervisors in fake mode.
- Failure: a specialist down → supervisor COMPLETED with a degraded answer (not FAILED).
