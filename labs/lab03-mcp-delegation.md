# Lab 03: Delegation via MCP (Pattern 2b)

**Package:** `labs/lab03-mcp-delegation` → import `lab03_mcp`
**Profile:** `lab03`
**Models:** `quick` (specialists), `reasoning` (consumers)
**Frameworks:** ADK, Strands, LangGraph, MCP Python SDK (`mcp`, FastMCP)

> **Idea:** MCP connects an agent to **tools and resources**; A2A connects **agents to agents**. They compose in two directions:
> 1. **MCP inside, A2A outside:** a specialist uses MCP tools internally and is exposed over A2A. This is the canonical, officially recommended combination.
> 2. **A2A behind MCP (bridge):** an MCP server exposes remote A2A agents as MCP tools. An MCP-only host (an IDE, desktop assistant or framework with MCP support) can then delegate to A2A agents. This is useful but lossy: task states, streaming and artifacts collapse into tool results.

## 1. Learning objectives

1. Build a specialist that consumes MCP tools (Strands `MCPClient`, ADK `McpToolset`) and is served over A2A.
2. Build an **A2A→MCP bridge** that turns Agent Cards into MCP tools, using the official `mcp` SDK (FastMCP, streamable HTTP transport) and the `a2a-sdk` client. No custom protocol.
3. Consume the bridge from **LangGraph** (`langchain-mcp-adapters`), **ADK** (`McpToolset`) and **Strands** (`MCPClient`).
4. Understand what is lost through MCP (task lifecycle, streaming, artifacts and multi-turn) and how the bridge mitigates it: it exposes `get_task`/`cancel_task` tools and returns `task_id` and `context_id` in results.
5. Decide between MCP and A2A for a given integration, using a decision table.

## 2. Services

| Service | Port | Kind | Framework | Description |
|---|---|---|---|---|
| `lab03-tools-mcp` | 10301 (`/mcp`) | MCP server | FastMCP | Plain tools: `convert_units(value, from_unit, to_unit)`, `calculate(expression)` (safe AST eval), `lookup_timezone(city)`. Deterministic. |
| `lab03-strands-specialist` | 10302 | A2A server | Strands `A2AServer` | "Engineering Helper". Its agent uses `MCPClient(lambda: streamablehttp_client(tools_mcp_url))` tools. **MCP inside, A2A outside.** |
| `lab03-adk-specialist` | 10303 | A2A server | ADK `to_a2a` | "Travel Clock". Uses `McpToolset(connection_params=StreamableHTTPConnectionParams(url=tools_mcp_url))` [VERIFY class names]. |
| `lab03-mcp-a2a-bridge` | 10304 (`/mcp`) | MCP server | FastMCP + a2a-sdk client | **A2A behind MCP.** Exposes A2A agents as MCP tools. |
| `lab03-langgraph-mcp-consumer` | 10305 | A2A server | LangGraph + `langchain-mcp-adapters` `MultiServerMCPClient` | A coordinator that sees only MCP tools (from the bridge), yet delegates to A2A agents. |
| `lab03-adk-mcp-consumer` | 10306 | A2A server | ADK + `McpToolset` → bridge | The same idea in ADK. |

The Strands consumer runs **in-process in the notebook** (`MCPClient` → bridge), so the notebook shows the raw MCP session.

## 3. The bridge (`servers/mcp_a2a_bridge.py`)

Configuration: `BRIDGE_AGENTS=lab03-strands-specialist,lab03-adk-specialist` (service names resolved via endpoints).

On startup it resolves each card (`A2ACardResolver`) and registers the following MCP tools:

| MCP tool | Signature | Behavior |
|---|---|---|
| `list_agents` | `() -> list[{name, description, skills[], url}]` | From cached cards |
| `ask_<agent_slug>` (one per agent, generated) | `(request: str, context_id: str \| None = None, task_id: str \| None = None) -> {status, text, task_id, context_id, artifacts[]}` | Tool description = card description + skill examples. Calls `SendMessage` blocking. Maps states: COMPLETED → `status="completed"` + artifact text; INPUT_REQUIRED → `status="input_required"` + question; FAILED/REJECTED/CANCELED → `status=<state>` + message. |
| `send_message` | `(agent: str, request: str, context_id?, task_id?, return_immediately: bool = False)` | Generic form |
| `get_task` | `(agent: str, task_id: str)` | `GetTask` passthrough (summary) |
| `cancel_task` | `(agent: str, task_id: str)` | `CancelTask` passthrough |

Also exposed: MCP **resources** `a2a://cards/<agent>` (the card JSON), showing MCP resources as the place for discovery metadata.

Streaming: when the MCP client supports progress notifications, the bridge forwards A2A `status_update` text as MCP progress messages (`ctx.report_progress` / `ctx.info`) [VERIFY FastMCP API]. Otherwise it ignores them. This is the honest version of "lossy".

Telemetry: the MCP tool handler runs inside a `mcp.call_tool <tool>` span. The trace context arrives over HTTP via Starlette instrumentation of the FastMCP ASGI app (streamable HTTP is HTTP, so `traceparent` propagates).

Relevant official anchors to reference in the README:
- a2a-samples MCP-related samples (`a2a-mcp-without-framework`, and the `a2a_mcp` sample that uses MCP as an agent-card registry) [VERIFY current paths].
- ADK "Agent as MCP server" docs.
- Strands MCP docs.

## 4. Notebook outline

1. Architecture: two Mermaid diagrams. (A) MCP inside / A2A outside. (B) A2A behind MCP, with consumers.
2. Setup.
3. Concept: an MCP vs A2A table covering unit of interaction, statefulness, discovery, streaming, long-running work, opacity and typical host.
4. **Raw MCP session** against `lab03-tools-mcp`: `ClientSession.initialize`, `list_tools`, `call_tool("convert_units", …)`.
5. **MCP inside, A2A outside**: call `lab03-strands-specialist` over A2A with "How many feet in 3.2 km, and what is 12% of that?" Show that the trace contains A2A server → Strands → MCP `call_tool` spans. The card shows nothing about MCP (opacity again).
6. Same with `lab03-adk-specialist` ("What time is it in Tokyo when it's 9am in Chicago?").
7. **Bridge tour**: connect to the bridge with the MCP client, `list_tools` (show the generated `ask_*` tools and their descriptions built from Agent Cards), and `read_resource("a2a://cards/...")`.
8. **Strands consumer in-process**: an `Agent(tools=bridge_mcp_client.list_tools_sync())` [VERIFY sync/async] asks a composite question. The agent → MCP → bridge → A2A specialist → MCP tools. One trace spans 4 hops.
9. **LangGraph consumer** (service, called via A2A): same question. Show the `langchain-mcp-adapters` tools.
10. **ADK consumer** (service, via A2A).
11. **What's lost**: trigger a long task through the bridge and show that the MCP tool blocks. Then use `send_message(return_immediately=True)` + `get_task` polling via MCP tools, and compare with native A2A streaming from Lab 00.
12. Grafana: a 4-hop trace and the MCP spans.
13. Exercises:
    - (easy) add a `lookup_currency` tool to `tools-mcp`, used by the Strands specialist;
    - (medium) add an MCP **prompt** to the bridge that lists agents in a templated way;
    - (stretch) forward A2A artifacts as MCP embedded resources.
14. Recap: an "Use MCP when… / Use A2A when…" table.

## 5. Acceptance tests

- `lab03-tools-mcp` `list_tools` returns 3 tools. `calculate` rejects unsafe expressions.
- The bridge generates one `ask_*` tool per configured agent, with the card-derived description.
- The bridge maps INPUT_REQUIRED and FAILED correctly (in-process test against an SDK fake agent).
- All three consumers complete the composite question in fake mode.
- Trace connectivity: notebook → bridge → specialist → tools-mcp in one trace.
