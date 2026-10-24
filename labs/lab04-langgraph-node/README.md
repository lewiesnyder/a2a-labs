# Lab 04: LangGraph as a framework node (pattern 3)

Map a LangGraph graph onto A2A tasks and states, in both directions: a graph **served** as an A2A
agent, and a graph that **contains a remote A2A agent as a node**, including an interrupt that
propagates across the A2A boundary.

**Notebook:** [`notebooks/lab04_langgraph_node.ipynb`](notebooks/lab04_langgraph_node.ipynb)
(authored as the paired `.py`). **Spec:** [`labs/lab04-langgraph-node.md`](../lab04-langgraph-node.md).

## Run it

```bash
REASONING_THINK=false make up LAB=lab04          # planner + concierge (needs Ollama incl. nimble-v3)
make up LAB=lab04-agentserver                    # optional, AFTER lab04: the built-in A2A endpoint
LLM_PROVIDER=fake DECISION_PROVIDER=fake make e2e LAB=lab04   # deterministic CI run
```

The Agent Server image is built `FROM a2a-labs:dev`, so build `lab04` first.

| Service | Port | What it is |
|---|---|---|
| `lab04-langgraph-travel` | 10401 | **Trip Planner** graph behind `LangGraphA2AExecutor` (node events, interrupts, `itinerary` artifact) |
| `lab04-langgraph-orchestrator` | 10402 | **Concierge**: a graph whose `plan` node is a `RemoteA2ANode` calling the planner |
| `lab04-langgraph-agent-server` | 10403 | optional: the same planner on the LangGraph Agent Server (`langgraph.json`) |

## The mapping (the point of the lab)

| LangGraph | A2A |
|---|---|
| run for a `thread_id` | Task in a `contextId` (`thread_id = context_id`) |
| node start | `WORKING` `▶ node: x`, `metadata.langgraph_node` |
| `interrupt(v)` | `INPUT_REQUIRED` (text question + DataPart payload) |
| `Command(resume=…)` | next message, same `task_id` (text, or DataPart `{"resume": …}`) |
| END | `itinerary` artifact + `COMPLETED` |
| cancel | `CANCELED`; the checkpoint stays at the last superstep |

## Code map

| Path | Role |
|---|---|
| `agents/trip_planner.py` | The planner graph: parse → flights → hotels → **risk gate (decision model)** → approval → compose |
| `agents/concierge.py` | understand → **remote plan node** → local budget check |
| `agents/travel_data.py` | Regex request parsing and the deterministic flight/hotel tables |
| `servers/trip_planner.py`, `servers/concierge.py` | A2A servers, each with a CUSTOM `/debug/state/{context_id}` route |
| `langgraph.json` | Deploys the planner on the Agent Server (`graph` is exported without a checkpointer) |
| `../../libs/a2a-labs-common/.../a2a/langgraph.py` | `LangGraphA2AExecutor` (mapping options) and `RemoteA2ANode` |

## What to notice

- **Interrupts need a checkpointer**, and a node re-runs from its first line on resume. The
  planner's parsing node is pure, so replay is harmless; the remote node is not, so it remembers
  its remote task and replays only its interrupts (see the class docstring).
- **Nimble drives a conditional edge.** Scores on this data: $2200 vs $5000 → 0.12, vs $2000 →
  1.30 (medium, no approval), vs $1500 → 1.84 (approval). The threshold is 1.5.
- The Agent Server's A2A endpoint differs from ours (no `SubscribeToTask`, no push, JSON-RPC only,
  v0.3 shapes, UUID contexts, `historyLength` ≤ 10, an `Interrupt` artifact); the notebook has the table.
- `config` parameters must be annotated exactly `RunnableConfig` or LangGraph does not inject them.

## Timings (reference run, real models, `REASONING_THINK=false`)

| Notebook | Time |
|---|---|
| `lab04_langgraph_node` | about 1.5 min (the planner sleeps 0.8 s per search on purpose) |
