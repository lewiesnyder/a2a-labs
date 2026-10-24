# Lab 02: Agent as tool (pattern 2)

A controller's LLM calls remote A2A agents as **tools**. The remote agent keeps its own task
lifecycle and stays opaque: only its Agent Card and Artifacts cross the boundary. Three
controllers (ADK, Strands, LangChain) reach three specialists (LangGraph, Strands, ADK).

**Notebook:** [`notebooks/lab02_agent_as_tool.ipynb`](notebooks/lab02_agent_as_tool.ipynb)
(authored as the paired `.py`). **Spec:** [`labs/lab02-agent-as-tool.md`](../lab02-agent-as-tool.md).

## Run it

```bash
REASONING_THINK=false make up LAB=lab02     # snappy: the 27B controller model without thinking
REASONING_THINK=false make notebook         # or run the .ipynb on the host with the same variable
LLM_PROVIDER=fake make e2e LAB=lab02        # deterministic CI run (scripted tool calls)
```

`REASONING_THINK` is forwarded into the containers, so setting it in your shell wins over `.env`.

| Service | Port | Framework | Role |
|---|---|---|---|
| `lab02-langgraph-currency` | 10201 | LangGraph + `LangGraphA2AExecutor` | Converts currencies; **INPUT_REQUIRED** when the target is missing |
| `lab02-strands-weather` | 10202 | Strands + `StrandsA2AExecutor` | Forecast, streams its artifact |
| `lab02-adk-facts` | 10203 | ADK `to_a2a` | City facts |
| `lab02-adk-controller` | 10204 | ADK | currency + weather as `AgentTool`, facts (and a second currency wiring) as sub-agents |
| `lab02-strands-controller` | 10205 | Strands | allowlisted `call_specialist` (stateless) + stateful `ask_weather`/`ask_currency` |
| `lab02-langchain-controller` | 10206 | LangGraph | tools **generated from the remote Agent Cards** |

## Code map

| Path | Role |
|---|---|
| `agents/currency.py` | LangGraph graph: deterministic parsing, `interrupt()` for missing fields, LLM phrasing |
| `agents/weather.py`, `agents/facts.py` | Strands and ADK specialist agents with one private tool each |
| `agents/controllers.py` | The three controllers (read this to compare the three wirings) |
| `agents/data.py` | The static tables behind the specialists' tools |
| `demo.py` | Notebook helpers (ADK event summaries, tool-call lists) |
| `../../libs/a2a-labs-common/.../a2a/remote.py` | `RemoteAgent`: never raises, stable remote context, remembers paused tasks |
| `../../libs/a2a-labs-common/.../a2a/langchain_tool.py` | `a2a_agent_as_langchain_tool` |
| `../../libs/a2a-labs-common/.../a2a/strands_tools.py` | `make_a2a_client_tool` (allowlist) and `make_stateful_a2a_tool` |

## What to notice

- The tool description comes from the **card**, so the card is the schema source.
- A tool must return text and never raise; `INPUT_REQUIRED` becomes `NEEDS_INPUT: ... [task_id=...]`
  and the next call continues **the same remote task** (asserted in the tests via `GetTask`).
- The Strands `call_specialist` tool is an **allowlist**: the LLM cannot reach arbitrary URLs.
- ADK's `AgentTool` cannot resume a paused remote agent; as a **sub-agent** it can.
- A Strands/LangGraph specialist publishes only its answer; the ADK specialist also publishes
  its tool calls as artifacts, so opacity depends on the server.
- Small models are fragile with tools. The weather tool takes a single `city` parameter because
  a second `days` parameter dropped `llama3.2` tool use from 3/3 to 1/3 in a direct test.

## Timings (reference run, `REASONING_THINK=false`, warm models)

| Notebook | Time |
|---|---|
| `lab02_agent_as_tool` | about 2 min; the first call loads the 27B model (allow a few minutes cold) |
