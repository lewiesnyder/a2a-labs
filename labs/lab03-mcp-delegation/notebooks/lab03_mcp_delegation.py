# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# %% [markdown]
# # Lab 03 · Delegation via MCP (pattern 2b)
#
# **Goal:** see how MCP (agent ↔ tools) and A2A (agent ↔ agent) compose, in both directions.
# **Time:** about 3–5 minutes with local models · **Prerequisites:** Labs 00–02 · `REASONING_THINK=false make up LAB=lab03`
#
# **What you'll learn**
# - **MCP inside, A2A outside:** a specialist uses MCP tools internally and is served over A2A (the recommended combination).
# - **A2A behind MCP:** a bridge turns Agent Cards into MCP tools so an MCP-only host can delegate to A2A agents.
# - Exactly **what is lost** through MCP: task states, streaming, artifacts, multi-turn.
# - When to choose MCP, and when A2A.
#
# Spec: [A2A specification](https://a2a-protocol.org/latest/specification/) · [MCP](https://modelcontextprotocol.io)

# %% [markdown]
# ## 1. Architecture
#
# **(A) MCP inside, A2A outside**
#
# ```mermaid
# flowchart LR
#   C[Any A2A client] -->|A2A| S[Strands specialist :10302]
#   C -->|A2A| K[ADK specialist :10303]
#   S -->|MCP| T[(tools-mcp :10301)]
#   K -->|MCP| T
# ```
#
# **(B) A2A behind MCP (the bridge)**
#
# ```mermaid
# flowchart LR
#   LG[LangGraph consumer :10305] -->|MCP| B[A2A to MCP bridge :10304]
#   AD[ADK consumer :10306] -->|MCP| B
#   ST[Strands consumer in this notebook] -->|MCP| B
#   B -->|A2A| S[Strands specialist]
#   B -->|A2A| K[ADK specialist]
#   S & K -->|MCP| T[(tools-mcp)]
# ```
#
# | Service | Port | Kind | What it is |
# |---|---|---|---|
# | `lab03-tools-mcp` | 10301 | MCP server | `convert_units`, `calculate` (safe AST), `lookup_timezone` |
# | `lab03-strands-specialist` | 10302 | A2A | "Engineering Helper", uses MCP tools |
# | `lab03-adk-specialist` | 10303 | A2A | "Travel Clock", uses MCP tools |
# | `lab03-mcp-a2a-bridge` | 10304 | MCP server | exposes the two A2A agents as MCP tools |
# | `lab03-langgraph-mcp-consumer` | 10305 | A2A | coordinator that sees only MCP tools |
# | `lab03-adk-mcp-consumer` | 10306 | A2A | the same idea in ADK |
#
# > ⚠️ Pitfall: the `mcp` SDK 2.x renamed FastMCP and broke `langchain-mcp-adapters`, and `litellm` 1.106+ needs `mcp` 2.x symbols. This repo pins `mcp<2` and `litellm<1.106` (DEV-24).

# %% tags=["parameters"]
QUICK_DEMO = False

# %% [markdown]
# ## 2. Setup

# %%
# Initialise telemetry, show the models and wait for the A2A services
import asyncio
import json
import time

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from a2a_labs_common import display
from a2a_labs_common.a2a.client import connect, send_text, stream_text, wait_for_cards
from a2a_labs_common.a2a.states import state_name
from a2a_labs_common.config import endpoint
from a2a_labs_common.telemetry import init_telemetry

tel = init_telemetry("notebook-lab03", framework="notebook", lab_id="lab03")
display.show_models()
A2A_SERVICES = ["lab03-strands-specialist", "lab03-adk-specialist",
                "lab03-langgraph-mcp-consumer", "lab03-adk-mcp-consumer"]
await wait_for_cards(A2A_SERVICES, timeout=180)  # prints `make up LAB=lab03` if one is down
TOOLS_MCP, BRIDGE_MCP = endpoint("lab03-tools-mcp"), endpoint("lab03-mcp-a2a-bridge")
QUESTION = "How many feet in 3.2 km, and what time is it in Tokyo when it's 9am in Chicago?"
display.table([{"MCP server": "tools", "url": TOOLS_MCP}, {"MCP server": "bridge", "url": BRIDGE_MCP}])

# %% [markdown]
# ## 3. Concept: MCP vs A2A
#
# | | **MCP** | **A2A** |
# |---|---|---|
# | Unit of interaction | A **tool call** (function in, result out) | A **message** to an agent, often a **task** |
# | Statefulness | Per session; each call is independent | Server-owned tasks, `contextId` conversations |
# | Discovery | `tools/list`, resources | Agent Card (`/.well-known/agent-card.json`) |
# | Streaming | Progress and log notifications | Task event streams, artifact chunks |
# | Long-running work | Awkward: the call blocks | First-class: states, `GetTask`, cancel, push |
# | Opacity | The server *is* the implementation | The agent is a black box (card + artifacts) |
# | Typical host | IDE, desktop assistant, agent runtime | Another agent |
#
# MCP connects an agent to **capabilities**. A2A connects an agent to **another agent**. They are not rivals.

# %% [markdown]
# ## 4. A raw MCP session
# Before any framework: open a session to the plain tools server and call a tool.

# %% a2a_coverage=["pattern:2b"]
# initialize, list_tools, call_tool: the whole MCP client lifecycle
async with streamablehttp_client(TOOLS_MCP) as (read, write, _), ClientSession(read, write) as session:
    init = await session.initialize()
    print("server:", init.serverInfo.name, "| protocol", init.protocolVersion)
    tools = (await session.list_tools()).tools
    display.table([{"tool": t.name, "description": t.description} for t in tools])
    result = await session.call_tool("convert_units", {"value": 3.2, "from_unit": "km", "to_unit": "ft"})
    print("convert_units ->", result.content[0].text)

# %% a2a_coverage=["pattern:2b"]
# Unsafe input is an MCP tool error (isError), never a crash: calculate only does arithmetic
async with streamablehttp_client(TOOLS_MCP) as (read, write, _), ClientSession(read, write) as session:
    await session.initialize()
    for expr in ("12 * 3.5", "__import__('os').system('id')"):
        r = await session.call_tool("calculate", {"expression": expr})
        print(f"{expr!r:36} isError={r.isError}  {r.content[0].text[:70]}")

# %% [markdown]
# **What just happened**
# - MCP is a session: `initialize`, then `list_tools` and `call_tool`. Tool inputs are JSON Schema.
# - There is no agent here and no task: just functions. `calculate` evaluates a parsed arithmetic tree and rejects names, calls and attributes.

# %% [markdown]
# ## 5. MCP inside, A2A outside (Strands)
# The Engineering Helper is a Strands agent whose tools come from an `MCPClient`. An A2A client cannot tell.

# %% a2a_coverage=["pattern:2b", "op:SendMessage", "state:COMPLETED"]
# Ask the Strands specialist over A2A; look at its card for any trace of MCP
async with await connect("lab03-strands-specialist", streaming=False) as c:
    r = await send_text(c, "How many feet in 3.2 km, and what is 12% of that?")
    print(state_name(r.state), "|", r.text)
    print("card mentions MCP or its tools:", "mcp" in str(c.card).lower() or "convert_units" in str(c.card))
    display.show_card(c.card)

# %% [markdown]
# **What just happened**
# - The A2A call went to the specialist; *inside* it, the agent made MCP `call_tool` calls to `lab03-tools-mcp`.
# - Opacity again: the card lists one skill and says nothing about MCP or tools. Look in Grafana (step 12): the trace contains `mcp.call_tool` spans.
# - Small models are weak at arithmetic. If the percentage looks wrong, the *tools* were right; the model mis-phrased the input. That is why tool results are the source of truth.

# %% [markdown]
# ## 6. The same with ADK
# The Travel Clock uses `McpToolset` instead.

# %% a2a_coverage=["pattern:2b", "op:SendMessage"]
# Ask the ADK specialist; its hand-built card hides the MCP tools (ADK's auto card would list them)
async with await connect("lab03-adk-specialist", streaming=False) as c:
    r = await send_text(c, "What time is it in Tokyo when it is 9am in Chicago?")
    print(state_name(r.state), "|", r.text)
    print("skills on the card:", [s.id for s in c.card.skills])

# %% [markdown]
# > ⚠️ Pitfall: when ADK builds the card itself, it publishes **every tool as a skill** (here, `lookup_timezone`), leaking exactly what "MCP inside, A2A outside" is meant to hide. This lab passes a hand-built card to `to_a2a(agent_card=...)`.

# %% [markdown]
# ## 7. Bridge tour
# The bridge is an MCP server. Its tools are **generated from the Agent Cards** it fronts.

# %% a2a_coverage=["pattern:2b", "op:Discovery"]
# List the bridge's tools and read an Agent Card as an MCP resource
async with streamablehttp_client(BRIDGE_MCP) as (read, write, _), ClientSession(read, write) as session:
    await session.initialize()
    for t in (await session.list_tools()).tools:
        print(f"- {t.name}: {t.description[:110]}")
    card = json.loads((await session.read_resource("a2a://cards/lab03_adk_specialist")).contents[0].text)
    print("\nresource a2a://cards/lab03_adk_specialist ->", card["name"], "|", [s["name"] for s in card["skills"]])

# %% [markdown]
# **What just happened**
# - One `ask_<agent>` tool per card; its description is the card's description, skills and an example, plus how to continue (`context_id`, `task_id`).
# - `send_message`, `get_task` and `cancel_task` exist because MCP has no tasks: they give an MCP-only host back some of A2A.
# - The card itself is exposed as an MCP **resource**: resources are where discovery data lives in MCP.

# %% [markdown]
# ## 8. Strands consumer, in-process
# An agent that only sees MCP tools (from the bridge) and still reaches A2A agents. Here we hold the `MCPClient` ourselves, so the raw session is visible.

# %% a2a_coverage=["pattern:2b"]
# Show the raw tool list the Strands agent sees, then ask the composite question
from lab03_mcp.agents.consumers import build_strands_consumer

agent, mcp_client = build_strands_consumer()  # the Agent starts its MCPClient itself
print("tools the consumer sees:", [t.tool_name for t in mcp_client.list_tools_sync()])
t0 = time.perf_counter()
answer = await agent.invoke_async(QUESTION)
print(f"⏱ {time.perf_counter() - t0:.0f}s · role: reasoning")
print(answer)

# %% [markdown]
# **What just happened**
# - Four hops, one trace: notebook → (MCP) bridge → (A2A) specialist → (MCP) tools server.
# - The consumer has no idea A2A exists; it called plain MCP tools.

# %% [markdown]
# ## 9. LangGraph consumer (a service, called over A2A)
# Its tools come from `langchain-mcp-adapters` (`MultiServerMCPClient`).

# %% a2a_coverage=["pattern:2b", "op:SendMessage"]
# The tools langchain-mcp-adapters builds from the bridge, then the consumer answering over A2A
from langchain_mcp_adapters.client import MultiServerMCPClient

lc_tools = await MultiServerMCPClient({"bridge": {"url": BRIDGE_MCP, "transport": "streamable_http"}}).get_tools()
print("adapter tools:", [t.name for t in lc_tools])
async with await connect("lab03-langgraph-mcp-consumer", streaming=False) as c:
    r = await send_text(c, QUESTION)
print(state_name(r.state), "|", r.text)

# %% [markdown]
# ## 10. ADK consumer (a service, called over A2A)
# The same, with an `McpToolset` pointing at the bridge.

# %% a2a_coverage=["pattern:2b", "op:SendMessage"]
# Ask the ADK consumer the same question
async with await connect("lab03-adk-mcp-consumer", streaming=False) as c:
    r = await send_text(c, QUESTION)
print(state_name(r.state), "|", r.text)

# %% [markdown]
# **What just happened**
# - Three frameworks, one idea: whichever MCP client you use, the bridge makes A2A agents look like tools.
# - Each consumer is *also* an A2A server, so the notebook reached an MCP-based agent over A2A: the protocols stack.

# %% [markdown]
# ## 11. What is lost through MCP
# A native A2A client sees a task lifecycle. Through the bridge it sees one blocking tool call. To compare, we start a **Slow Worker** (an A2A agent that works for 8 seconds) and a bridge to it, inside this notebook.

# %% a2a_coverage=["pattern:2b"]
# Native A2A: stream the slow task and watch every state change as it happens
from lab03_mcp.demo import start_demo_stack

demo = start_demo_stack(seconds=8)
t0 = time.perf_counter()
async with await connect(demo.worker_url, streaming=True) as native:
    async for ev in stream_text(native, "do the slow thing"):
        display.show_event(ev)
print(f"native A2A: {time.perf_counter() - t0:.1f}s, many events")

# %% a2a_coverage=["pattern:2b", "op:SendMessage"]
# Through MCP: the tool call blocks and returns once; progress arrives only as log messages
logs = []


async def on_log(params):  # MCP log notifications are the only live signal an MCP client gets
    logs.append(str(params.data))


t0 = time.perf_counter()
async with streamablehttp_client(demo.bridge_url) as (read, write, _), ClientSession(read, write, logging_callback=on_log) as s:
    await s.initialize()
    result = json.loads((await s.call_tool("ask_slow_worker", {"request": "do the slow thing"})).content[0].text)
print(f"MCP tool call blocked for {time.perf_counter() - t0:.1f}s ->", result["status"], result["text"])
print("log messages forwarded by the bridge:", logs[:3], "...")

# %% a2a_coverage=["pattern:2b", "op:GetTask", "op:CancelTask"]
# The workaround: return_immediately + get_task polling + cancel_task, all as MCP tools
async with streamablehttp_client(demo.bridge_url) as (read, write, _), ClientSession(read, write) as s:
    await s.initialize()
    async def call(tool, args):
        return json.loads((await s.call_tool(tool, args)).content[0].text)

    started = await call("send_message", {"agent": "slow_worker", "request": "go", "return_immediately": True})
    print("started :", started["status"], started["task_id"][:8])
    await asyncio.sleep(2)
    print("polled  :", (await call("get_task", {"agent": "slow_worker", "task_id": started["task_id"]}))["status"])
    print("canceled:", (await call("cancel_task", {"agent": "slow_worker", "task_id": started["task_id"]}))["status"])
demo.stop()

# %% [markdown]
# **What just happened**
# - Native A2A gave one event per second, the final artifact and a clean `COMPLETED`.
# - Through MCP the same work was **one blocking call**; the bridge forwarded progress as MCP log messages, and only clients that listen see them.
# - The bridge returns `task_id` and `context_id` in every result, and offers `send_message(return_immediately=True)`, `get_task` and `cancel_task`, so an MCP-only host can still poll and cancel. That is the honest meaning of "lossy": states, streaming and artifacts collapse into a result, and the rest is workarounds.

# %% [markdown]
# ## 12. See it in Grafana

# %%
# Run the Strands consumer under one span and print the link
with display.cell_span("mcp four-hop question") as span:
    agent2, _ = build_strands_consumer()
    await agent2.invoke_async(QUESTION)
tel.flush()
display.trace_link(span)

# %% [markdown]
# Try in Grafana → Explore:
# - **TraceQL:** `{ name =~ "mcp.call_tool.*" }`: every MCP tool call, on the bridge and on the tools server.
# - **TraceQL:** `{ resource.service.name = "lab03-tools-mcp" } && { name = "mcp.call_tool convert_units" }`: the last hop of the four.
#
# The trace follows the request through two MCP hops and one A2A hop because both protocols here ride on HTTP, so `traceparent` travels with every call.

# %% [markdown]
# ## 13. Exercises
# - 🟢 **Easy:** add a `lookup_currency` tool to `tools-mcp` (`servers/tools_mcp.py`) and watch the Strands specialist use it.
# - 🟡 **Medium:** add an MCP **prompt** to the bridge that lists the agents in a templated way (`@mcp.prompt`).
# - 🔴 **Stretch:** forward A2A artifacts as MCP **embedded resources** instead of text.

# %% tags=["skip-ci"] jupyter={"source_hidden": true}
# Hidden solution (medium): in servers/mcp_a2a_bridge.py, inside build_server()
# @mcp.prompt(name="agents", description="Describe the available agents")
# def agents_prompt() -> str:
#     return "\n".join(f"- {a.card.name}: {a.card.description} (tool {a.tool_name})" for a in agents.values())

# %% [markdown]
# ## 14. Recap
#
# | Use **MCP** when... | Use **A2A** when... |
# |---|---|
# | You are giving an agent *tools or data* (functions, files, APIs) | You are delegating to *another agent* with its own reasoning |
# | The work is a quick, stateless call | The work is long-running, multi-turn, or needs states and cancellation |
# | The host only speaks MCP (IDE, desktop assistant) | The agent must stream, push, or negotiate input |
# | You control both sides and want a function schema | The other side is a black box you do not control |
#
# **Compose them:** MCP *inside* an agent (its tools), A2A *between* agents. Use the bridge only when an MCP-only host must reach an A2A agent, and expect the loss.
#
# **Pitfalls:** pinning `mcp` and `litellm` (DEV-24); ADK's auto card publishing MCP tools as skills; assuming a blocking MCP call can show progress; 3B models and arithmetic.
#
# **Next:** Lab 04 uses A2A agents as nodes inside a LangGraph graph.

# %% [markdown]
# ## Cleanup

# %%
# Flush telemetry
tel.flush()
