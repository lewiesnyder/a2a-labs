# Lab 01: Direct client → agent (pattern 1)

One client, four agents built with four stacks. Learn when a server answers with a **Message**
and when with a **Task**, how each framework turns `contextId` into memory, and how to compare
the cards each framework produces.

**Notebook:** [`notebooks/lab01_direct_client.ipynb`](notebooks/lab01_direct_client.ipynb)
(authored as the paired `.py`). **Spec:** [`labs/lab01-direct-client.md`](../lab01-direct-client.md).

## Run it

```bash
make up LAB=lab01        # four agents; needs Ollama with llama3.2 (the `quick` role)
make e2e LAB=lab01       # integration tests + the notebook
LLM_PROVIDER=fake make e2e LAB=lab01   # same, with deterministic fake models (what CI does)
```

| Service | Port | Framework and adapter | Replies with |
|---|---|---|---|
| `lab01-sdk-echo` | 10101 | `a2a-sdk` `AgentExecutor`, no LLM | Message |
| `lab01-adk-agent` | 10102 | ADK `Agent` → `to_a2a(agent_executor_factory=...)` | Task |
| `lab01-strands-agent` | 10103 | Strands `Agent` → `StrandsA2AExecutor` | Task |
| `lab01-langgraph-agent` | 10104 | LangGraph `create_agent` → `LangGraphA2AExecutor` | Task |

The three LLM agents share one skill (**Trip Tip**) and one deterministic tool (`local_time`).

## Code map

| Path | Role |
|---|---|
| `agents/common.py` | The skill text and the `local_time` data every framework shares |
| `agents/{adk,strands,langgraph}_agent.py` | The agent definition per framework (no networking) |
| `servers/*.py` | One A2A server process each, with `build_app()` (testable) and `main()` |
| `cards.py` | Hand-built cards for echo, Strands and LangGraph (ADK generates its own) |
| `../../libs/a2a-labs-common/.../a2a/langgraph.py` | The shared LangGraph ↔ A2A bridge |
| `../../libs/a2a-labs-common/.../a2a/strands.py` | The shared Strands ↔ A2A bridge |

## What to notice

- **ADK** turns every internal event into an artifact (tool call, tool result, answer), so one
  reply is three artifacts. Strands and LangGraph publish one `answer` artifact.
- **ADK's JSON metadata uses snake_case keys** (`adk_author`, ...) where A2A uses camelCase; the
  TCK flags it (`docs/conformance.md`).
- Strands and LangGraph need our small executors because the official Strands `A2AServer` does
  not import on a2a-sdk 1.x (DEV-1) and LangGraph has no SDK adapter at all.
- A 3B model sometimes skips the tool or forgets a name. The unit tests use fakes that record the
  prompt, so memory *plumbing* is asserted without an LLM.

## Timings (reference run, host notebook, warm model)

| Notebook | Time |
|---|---|
| `lab01_direct_client` | about 25 s warm; allow 30–90 s extra when Ollama loads the model |
