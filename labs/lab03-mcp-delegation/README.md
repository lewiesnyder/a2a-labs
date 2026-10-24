# Lab 03: Delegation via MCP (pattern 2b)

MCP connects an agent to **tools**; A2A connects **agents to agents**. This lab composes them
both ways: **MCP inside, A2A outside** (a specialist uses MCP tools and is served over A2A) and
**A2A behind MCP** (a bridge turns Agent Cards into MCP tools for MCP-only hosts), and measures
what the second one loses.

**Notebook:** [`notebooks/lab03_mcp_delegation.ipynb`](notebooks/lab03_mcp_delegation.ipynb)
(authored as the paired `.py`). **Spec:** [`labs/lab03-mcp-delegation.md`](../lab03-mcp-delegation.md).

## Run it

```bash
REASONING_THINK=false make up LAB=lab03      # six services; needs Ollama (quick + reasoning roles)
LLM_PROVIDER=fake make e2e LAB=lab03         # deterministic CI run
```

| Service | Port | Kind | Framework |
|---|---|---|---|
| `lab03-tools-mcp` | 10301 (`/mcp`) | MCP server | FastMCP: `convert_units`, `calculate`, `lookup_timezone` |
| `lab03-strands-specialist` | 10302 | A2A | Strands + `MCPClient` ("Engineering Helper") |
| `lab03-adk-specialist` | 10303 | A2A | ADK + `McpToolset` ("Travel Clock") |
| `lab03-mcp-a2a-bridge` | 10304 (`/mcp`) | MCP server | FastMCP + a2a-sdk client |
| `lab03-langgraph-mcp-consumer` | 10305 | A2A | LangGraph + `langchain-mcp-adapters` |
| `lab03-adk-mcp-consumer` | 10306 | A2A | ADK + `McpToolset` → bridge |

The Strands consumer runs in the notebook so the raw MCP session is visible.

## Code map

| Path | Role |
|---|---|
| `servers/tools_mcp.py`, `agents/tools_logic.py` | The MCP tools and the pure logic behind them (safe AST `calculate`) |
| `servers/mcp_a2a_bridge.py` | **The bridge**: one `ask_<agent>` tool per card, `send_message`, `get_task`, `cancel_task`, `a2a://cards/<agent>` resources |
| `agents/specialists.py` | The two MCP-using specialists |
| `agents/consumers.py` | The three MCP-only coordinators |
| `mcp_app.py` | FastMCP setup shared by both MCP servers (`/healthz`, `mcp.call_tool` spans) |
| `demo.py` | A Slow Worker plus a bridge to it, started inside the notebook for step 11 |

## What to notice

- MCP is a *session of tool calls*. The bridge maps A2A states into a result dict and always
  returns `task_id` and `context_id`, so an MCP client can continue, poll (`get_task`) and cancel.
- A blocking MCP call hides the task; status updates reach the client only as MCP log messages.
- ADK builds its own card with **one skill per tool**, which would leak the MCP tools; the Travel
  Clock passes a hand-built card to `to_a2a(agent_card=...)`.
- Trace context survives both protocols (they are HTTP): one trace spans notebook, bridge,
  specialist and tools server, asserted by `tests/e2e/test_trace_mcp.py`.
- The `mcp` SDK is pinned `<2` and `litellm` `<1.106` (DEV-24).
- 3B models are poor at arithmetic and time maths; the tool results are right, the prose may not be.

## Timings (reference run, `REASONING_THINK=false`, warm models)

| Notebook | Time |
|---|---|
| `lab03_mcp_delegation` | about 2 min (step 11 alone takes about 25 s by design) |
