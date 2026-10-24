# Lab 04: LangGraph as a Framework Node (Pattern 3)

**Package:** `labs/lab04-langgraph-node` → import `lab04_langgraph`
**Profiles:** `lab04`; optional `lab04-agentserver`
**Models:** `reasoning` (planner node), `quick` (other nodes), `decision` (approval-risk gate)
**Frameworks:** LangGraph, a2a-sdk; optional LangGraph Agent Server

> **Pattern 3:** Map graph and state-machine execution to A2A tasks and states on the wire. This works in both directions: a LangGraph graph **served** as an A2A agent, and a LangGraph graph that **contains remote A2A agents as nodes**.

## 1. Learning objectives

1. Map LangGraph concepts to A2A precisely:

| LangGraph | A2A on the wire |
|---|---|
| Graph run for a `thread_id` | Task within `context_id` (`thread_id = context_id`) |
| Node start/finish (`stream_mode="updates"`) | `status_update` WORKING, `message` = "▶ node: search_flights", with `metadata.langgraph_node` |
| Tool call inside a node | `status_update` WORKING "🔧 tool: …" |
| `interrupt(value)` | `status_update` **INPUT_REQUIRED**. The message is the interrupt payload; a DataPart includes structured choices. |
| `Command(resume=…)` | Next `SendMessage` with the same `task_id`; the text or data becomes the resume value |
| Intermediate state snapshot | Optional artifact `state-snapshot` (DataPart) updated with `append=False` |
| Token stream (`stream_mode="messages"`) | Artifact chunks (`append=True`, `last_chunk` at end) |
| END reached | Artifact `itinerary` + COMPLETED |
| Exception in node | FAILED with the error message |
| Task cancel | Cancel the asyncio task running `astream`; CANCELED. Checkpoint left at the last superstep. |
| Checkpointer (`MemorySaver`; `AsyncSqliteSaver` optional) | Durability of context across tasks |

2. Build a **remote A2A node**: a LangGraph node that delegates to an A2A agent, streams its events into graph state, and propagates remote INPUT_REQUIRED into a **local** `interrupt()`.
3. Contrast this hand-built server with the **LangGraph Agent Server built-in A2A endpoint** (`/a2a/{assistant_id}`) and its limits.

## 2. Services

| Service | Port | Description |
|---|---|---|
| `lab04-langgraph-travel` | 10401 | "Trip Planner" graph served via `LangGraphA2AExecutor` (from common, extended with the node-mapping options above) |
| `lab04-langgraph-orchestrator` | 10402 | "Concierge" graph with a **remote node** calling `lab04-langgraph-travel` and a local `budget_check` node. Also served over A2A, so the nesting is A2A → LangGraph → A2A → LangGraph. |
| `lab04-langgraph-agent-server` | 10403 | *(profile `lab04-agentserver`)* The same Trip Planner graph deployed with `langgraph dev`/`langgraph-api` (`langgraph.json`), A2A at `/a2a/trip_planner` |

### 2.1 Trip Planner graph (`agents/trip_planner.py`)

State (`TypedDict`):
- `messages`
- `request: TripRequest | None` (pydantic: `origin`, `destination`, `dates`, `budget`, `travelers`)
- `flights: list`
- `hotels: list`
- `approved: bool | None`
- `itinerary: str | None`

Nodes:
1. `parse_request` (quick model, `with_structured_output(TripRequest)`). If required fields are missing, call `interrupt({"question": "...", "missing": [...]})`.
2. `search_flights` (tool `find_flights`, deterministic fake data).
3. `search_hotels` (tool `find_hotels`, deterministic).
4. `risk_gate` (**decision model**): `score` question "How risky/expensive is this plan relative to budget?" with levels ["low", "medium", "high"]. If the score is ≥1.5, route to `approval`; otherwise route to `compose`. This is a conditional edge.
5. `approval`: `interrupt({"question": "Total is $X (over budget). Approve? yes/no", "options": ["yes", "no"]})`.
   - On `no`, go back to `search_hotels` with a cheaper preference (max one loop).
6. `compose` (reasoning model): writes the itinerary markdown.

Use `MemorySaver`. The checkpointer is required for interrupts.

### 2.2 Concierge graph (`agents/concierge.py`) with `RemoteA2ANode`

`a2a_labs_common.a2a.langgraph.RemoteA2ANode`:

```python
class RemoteA2ANode:
    """A LangGraph node that delegates to an A2A agent.
    - Reads input text from state via `input_fn(state) -> str`.
    - Tracks remote ids in state['remote_tasks'][service] = {'context_id','task_id','state'}  (per-remote tracking)
    - Streams the remote task; appends status texts to state['progress'] (optional custom stream writer → get_stream_writer()).
    - On remote INPUT_REQUIRED: value = interrupt({'remote': service, 'question': text}); then sends `value`
      to the SAME remote task_id and continues streaming.
    - On COMPLETED: writes artifact text into state[output_key].
    - On FAILED/REJECTED/CANCELED: writes error and routes via `on_error` edge."""
    def __init__(self, service: str, *, input_fn, output_key: str, streaming: bool = True): ...
    async def __call__(self, state, config) -> dict: ...
```

Concierge flow: `understand` (quick) → `RemoteA2ANode("lab04-langgraph-travel")` → `budget_check` (local) → END.

The nesting demonstrates **interrupt propagation across an A2A boundary**:
1. The remote planner interrupts.
2. The remote task enters INPUT_REQUIRED.
3. The concierge's node calls a local `interrupt()`.
4. The concierge's own A2A task enters INPUT_REQUIRED for the end client.
5. The client replies, the concierge resumes, the node forwards the reply to the remote task, and both complete.

### 2.3 Agent Server variant

- `langgraph.json`: `{"graphs": {"trip_planner": "lab04_langgraph.agents.trip_planner:graph"}, "dependencies": ["."]}`.
- Run `langgraph dev --port 10403 --host 0.0.0.0` in the container [VERIFY flags; requires `langgraph-cli[inmem]`, `langgraph-api>=0.4.21`, and ≥0.18.0 for `returnImmediately`].
- The card is at `/.well-known/agent-card.json?assistant_id=trip_planner`. The endpoint is `/a2a/trip_planner`.
- Known limits, shown in the notebook as a table:
  - no `SubscribeToTask`;
  - no push configs;
  - JSON-RPC only;
  - responses use v0.3 shapes;
  - `contextId` must be a UUID;
  - `historyLength` max 10;
  - the state must have `messages`.
- Interrupt resume uses a `{"resume": …}` DataPart.

## 3. Notebook outline

1. Architecture: diagrams of the graph and the nesting (Mermaid). Render the compiled graph with `graph.get_graph().draw_mermaid()`.
2. Setup.
3. Concept: the mapping table above.
4. **Run the graph locally first**, without A2A: `graph.astream` with a complete request; print the updates. This establishes the baseline.
5. **Same graph over A2A**: stream "Plan a trip from Chicago to Lisbon, 12–16 May, budget $2000, 2 travelers". Show each status event with its `langgraph_node` metadata, and the final itinerary artifact.
6. **INPUT_REQUIRED from an interrupt**: send "Plan a trip to Lisbon" (missing fields) → INPUT_REQUIRED with structured missing fields → reply → continues. Show that `thread_id == context_id` by calling `graph.aget_state` in-process against the server's checkpointer. The `/debug/state/{context_id}` route is optional and marked CUSTOM.
7. **Approval interrupt driven by Nimble**: an over-budget request. Show the decision probabilities (span attributes and printed), the approval question, `no` → loop → `yes` → complete.
8. **Cancel mid-graph**: cancel during `search_hotels`. Show CANCELED, then `GetTask`, and note that the checkpoint keeps partial state.
9. **Nested: Concierge → Trip Planner**: an over-budget request through the concierge. Watch the two-level INPUT_REQUIRED propagation, with both `task_id`s printed (per-remote tracking in `remote_tasks`).
10. **Agent Server variant** (skipped if the profile is not running): the same request via `/a2a/trip_planner`, compared in a limits table. Try `SubscribeToTask` and observe the error.
11. Grafana: a trace of the nested run, plus a span-events filter on `a2a.task.state_change`.
12. Exercises:
    - (easy) add a `weather` node;
    - (medium) emit a `state-snapshot` DataPart artifact after each node;
    - (stretch) replace `MemorySaver` with `AsyncSqliteSaver` and resume a task after restarting the container.

## 4. Acceptance tests

- Executor mapping unit tests: node updates → WORKING with node metadata; interrupt → INPUT_REQUIRED with a DataPart; resume continues the same thread; END → artifact + COMPLETED; exception → FAILED; cancel → CANCELED.
- Nested propagation integration test (fake models, scripted decision `score=2`):
  - two INPUT_REQUIRED hops resolve with one client reply each;
  - the concierge state `remote_tasks` has the remote `task_id`;
  - the remote task is COMPLETED.
- `risk_gate` routes on the decision-model score (FakeDecisionModel).
- Agent Server smoke test (if the profile is running): `SendMessage` works and `SubscribeToTask` errors.
