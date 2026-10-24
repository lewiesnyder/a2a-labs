# Lab 07: Sequential chaining / multi-hop (pattern 6)

Planner → writer → editor, three frameworks, kept explicit: structured Parts between hops, **one
task and one context per hop**, and `reference_task_ids` as provenance. Two topologies
(orchestrated, relay), a quality loop driven by the decision model, and the same chain built with
each framework's own constructs for comparison.

**Notebook:** [`notebooks/lab07_sequential_chain.ipynb`](notebooks/lab07_sequential_chain.ipynb)
(authored as the paired `.py`). **Spec:** [`labs/lab07-sequential-chain.md`](../lab07-sequential-chain.md).

## Run it

```bash
REASONING_THINK=false make up LAB=lab07        # needs Ollama (qwen3.8:27b, llama3.2, nimble-v3)
LLM_PROVIDER=fake DECISION_PROVIDER=fake make e2e LAB=lab07   # deterministic CI run
```

| Service | Port | Framework | Output |
|---|---|---|---|
| `lab07-adk-planner` | 10701 | ADK `to_a2a` | `outline`: DataPart `{title, audience, sections}` + text; asks "Who is the audience?" for a vague topic |
| `lab07-strands-writer` | 10702 | Strands | `draft` (markdown, streamed); reads the outline DataPart; one agent per context |
| `lab07-langgraph-editor` | 10703 | LangGraph | `final` (text) + `review` (DataPart `{issues, summary}`) |
| `lab07-chain-orchestrator` | 10704 | a2a-sdk | `final` + `provenance` (hops, task/context ids, states, scores, loops) |

## How it works

- **Orchestrated:** the orchestrator calls each hop with its own derived context, passes the
  outline as a DataPart with `reference_task_ids`, relays the writer's chunks as progress, scores the
  review with a `score` decision (levels `unusable..publishable`) and loops back to the writer
  (same context, new task, refs = previous draft + review) while the score is below 2, at most
  `MAX_LOOPS` (2) times. A hop in `INPUT_REQUIRED` pauses the orchestrator; the next client message
  goes to that hop's task. `CancelTask` on the orchestrator cancels the running hop task.
- **Relay:** `RelayExecutor` (`a2a_labs_common.a2a.relay`) wraps each hop. With
  `metadata.relay.route` set it runs the inner work, holds back the terminal status, calls the next
  hop and attaches the result as `downstream-final`. Without a route it is a pass-through.
- **Native chains** (`native.py`): ADK `SequentialAgent`, a Strands `Graph`, LangGraph
  `RemoteA2ANode`s. They hand off text (ADK: only the user's message), with no provenance links.

## Code map

| Path | Role |
|---|---|
| `executors/chain_orchestrator.py` | The orchestrator state machine |
| `executors/wrappers.py` | `OutlineExecutor` (JSON → `outline` artifact, vague-topic question), `DataAsTextExecutor` |
| `agents/{planner,writer,editor}.py` | The three framework agents |
| `outline.py` | `Outline` model, rendering, deterministic fallback |
| `native.py` | The framework-native chains |
| `../../libs/a2a-labs-common/.../a2a/relay.py` | `RelayExecutor`, `HoldingQueue` |
