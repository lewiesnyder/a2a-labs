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
# # Lab 02 · Agent as tool (pattern 2)
#
# **Goal:** let a controller's LLM call remote A2A agents as *tools*, in ADK, Strands and LangChain, and learn what stays hidden behind the boundary.
# **Time:** about 3–6 minutes with local models (the 27B `reasoning` model loads slowly the first time) · **Prerequisites:** Labs 00 and 01 · `REASONING_THINK=false make up LAB=lab02` for snappy demos
#
# **What you'll learn**
# - How to turn a remote agent into a tool with each framework's own construct.
# - **Opacity:** a specialist's tools, prompt and model are invisible; only its Agent Card and Artifacts cross the boundary.
# - How a tool should treat remote task states: return results, relay questions (`INPUT_REQUIRED`), never raise into the LLM loop.
# - Why the Agent Card is the best source for a tool's description.
# - Tool vs sub-agent vs graph node.
#
# Spec: [A2A specification](https://a2a-protocol.org/latest/specification/)

# %% [markdown]
# ## 1. Architecture
#
# ```mermaid
# flowchart LR
#   subgraph Controllers
#     L[LangChain controller :10206]
#     S[Strands controller :10205]
#     A[ADK controller :10204]
#   end
#   subgraph "Specialists (opaque)"
#     C[currency · LangGraph :10201]
#     W[weather · Strands :10202]
#     F[facts · ADK :10203]
#   end
#   L & S & A --> C
#   L & S & A --> W
#   L & S & A --> F
# ```
#
# Every controller (3) can reach every specialist (3), and every specialist is built in a *different* framework than at least one controller: A2A hides that.
#
# | Controller | How it reaches a remote agent |
# |---|---|
# | LangChain | `a2a_agent_as_langchain_tool(...)`: a tool generated **from the remote Agent Card** |
# | Strands | an allowlisted `call_specialist` tool (stateless) plus stateful `ask_weather` / `ask_currency` |
# | ADK | `AgentTool(RemoteA2aAgent)` for currency and weather; a **sub-agent** for facts |
#
# > 🔍 Under the hood: the official Strands A2A client tools (`make_a2a_client`, `A2AAgent`) cannot load next to `a2a-sdk` 1.x, so this repo provides two small `# CUSTOM` equivalents with the same behaviour (DEV-19).

# %% tags=["parameters"]
QUICK_DEMO = False

# %% [markdown]
# ## 2. Setup

# %%
# Initialise telemetry, show the models and wait for the six services
import time

from a2a.types import GetTaskRequest, ListTasksRequest

from a2a_labs_common import display
from a2a_labs_common.a2a.client import connect, send_text, wait_for_cards
from a2a_labs_common.a2a.remote import card_description
from a2a_labs_common.a2a.states import state_name
from a2a_labs_common.telemetry import init_telemetry

tel = init_telemetry("notebook-lab02", framework="notebook", lab_id="lab02")
display.show_models()
SPECIALISTS = ["lab02-langgraph-currency", "lab02-strands-weather", "lab02-adk-facts"]
CONTROLLERS = {"langchain": "lab02-langchain-controller", "strands": "lab02-strands-controller",
               "adk": "lab02-adk-controller"}
await wait_for_cards([*SPECIALISTS, *CONTROLLERS.values()], timeout=180)  # `make up LAB=lab02`
QUESTION = "What's 250 GBP in JPY, and what's the weather in Tokyo?"

# %% [markdown]
# ## 3. Concept: tool vs sub-agent vs graph node
#
# | | **Tool** (this lab) | **Sub-agent / transfer** | **Graph node** (Lab 04) |
# |---|---|---|---|
# | Who owns the conversation | The controller, always | The sub-agent after the hand-over | The graph |
# | Who writes the final answer | The controller | The remote agent | A later node |
# | State continuity | You must keep the remote `context_id` yourself | The framework keeps the session | Graph state |
# | Opacity | Full: only the tool result comes back | Full, but the user now talks to the remote agent | Full |
#
# A tool call is a function call from the LLM's point of view. The wrapper must therefore **return a string and never raise**: a remote failure becomes text the LLM can reason about.

# %% [markdown]
# ## 4. Inspect the specialists' cards
# The card is all a controller learns about a specialist. The LangChain tool builds its description from it.

# %% a2a_coverage=["op:Discovery", "pattern:2"]
# Fetch each specialist's card and show the skills a controller will see
cards = {}
for svc in SPECIALISTS:
    async with await connect(svc, streaming=False) as c:
        cards[svc] = c.card
display.compare_cards(list(cards.values()))

# %% a2a_coverage=["pattern:2"]
# The LangChain tool description is generated from the card: the card IS the tool schema source
from a2a_labs_common.a2a.langchain_tool import a2a_agent_as_langchain_tool

tool = a2a_agent_as_langchain_tool("lab02-langgraph-currency", name="ask_currency")
print("name       :", tool.name)
print("description:", tool.description)
print("same text  :", tool.description == card_description(cards["lab02-langgraph-currency"]))
print("input      :", list(tool.args))

# %% [markdown]
# **What just happened**
# - The tool name is ours, but its description (agent name, purpose, skills, an example) came from the **remote card**. Change the card and the controller's tool changes with it.
# - The tool's input is just `request` (plain language) and an optional `task_id`: the remote agent's real tools are not part of it.

# %% [markdown]
# ## 5. LangChain controller
# Invoked two ways: in-process (import the graph) and through its own A2A endpoint. Tools are `ask_currency`, `ask_weather` and `ask_facts`.

# %% a2a_coverage=["pattern:2", "op:SendMessage"]
# Run the LangChain controller in-process and list which tools it called
from lab02_tool.agents.controllers import build_langchain_controller
from lab02_tool.demo import langchain_tool_calls

graph = build_langchain_controller()
t0 = time.perf_counter()
out = await graph.ainvoke({"messages": [("user", QUESTION)]}, config={"configurable": {"thread_id": "nb-1"}})
print(f"⏱ {time.perf_counter() - t0:.0f}s · role: reasoning")
print("tools called:", langchain_tool_calls(out["messages"]))
print(out["messages"][-1].content)

# %% a2a_coverage=["pattern:2", "op:SendMessage", "state:COMPLETED"]
# The same controller through its A2A endpoint: it is an agent like any other
async with await connect(CONTROLLERS["langchain"], streaming=False) as c:
    r = await send_text(c, QUESTION)
print(state_name(r.state), "|", r.text)

# %% [markdown]
# **What just happened**
# - The LLM chose two tools, in parallel, because the question has two parts.
# - Each tool call became an A2A `SendMessage` to a specialist; the specialist's **Artifact** text came back as the tool result.
# - Over A2A, the controller is itself a server with a `Task`: agents compose.

# %% [markdown]
# ## 6. Strands controller
# One vended-style tool reaches many agents, so it needs an **allowlist**: the LLM must not be able to make it call arbitrary URLs.

# %% a2a_coverage=["pattern:2", "op:SendMessage"]
# The Strands controller answers the same question; list its tool calls
from lab02_tool.agents.controllers import build_strands_controller
from lab02_tool.demo import strands_tool_calls

agent = build_strands_controller("nb-strands")
t0 = time.perf_counter()
answer = await agent.invoke_async(QUESTION)
print(f"⏱ {time.perf_counter() - t0:.0f}s · role: reasoning")
print("tools called:", strands_tool_calls(agent))
print(answer)

# %% a2a_coverage=["pattern:2", "errors"]
# Prompt-injection demo: the request tells the LLM to call an attacker's URL. The allowlist blocks it
from a2a_labs_common.a2a.strands_tools import make_a2a_client_tool
from a2a_labs_common.config import endpoint

guarded = make_a2a_client_tool(name="call_specialist", description="demo",
                               allowed_endpoints={endpoint("lab02-strands-weather"): None})
blocked = await guarded._tool_func(url="http://evil.example", message="send me your secrets")
allowed = await guarded._tool_func(url=endpoint("lab02-strands-weather"), message="weather in Tokyo")
print("evil.example ->", blocked)
print("allowlisted  ->", allowed[:80])

# %% [markdown]
# **What just happened**
# - The Strands agent used `call_specialist` (stateless, allowlisted) and its stateful `ask_weather`.
# - The allowlist is a **security control, not a convenience**: whatever the model decides (or whatever a prompt injection tells it to decide), only listed endpoints are reachable. The blocked call never left the process.
# - Stateless means each call uses a fresh remote context, so the remote agent remembers nothing between calls. The stateful tools keep one.

# %% [markdown]
# ## 7. ADK controller: AgentTool vs sub-agent
# Same remote agent, two wirings. Watch *who writes the final answer*.

# %% a2a_coverage=["pattern:2", "pattern:subagent"]
# AgentTool: the controller stays in charge and writes the final answer
from google.adk.agents import Agent
from google.adk.tools.agent_tool import AgentTool
from lab02_tool.agents.controllers import FACTS, remote_agent
from lab02_tool.demo import adk_events

from a2a_labs_common.models import get_adk_model

facts = remote_agent("facts_agent", "Interesting facts about a city.", FACTS)
as_tool = Agent(name="tool_controller", model=get_adk_model("reasoning"),
                instruction="Call facts_agent for the city, then answer in one sentence.",
                tools=[AgentTool(agent=facts)])
display.table(await adk_events(as_tool, "Tell me an interesting fact about Lisbon."), "AgentTool: controller writes the answer")

# %% a2a_coverage=["pattern:subagent"]
# Sub-agent: transfer_to_agent hands the conversation over; the REMOTE agent writes the reply
as_sub = Agent(name="sub_controller", model=get_adk_model("reasoning"),
               instruction="You know no facts about cities. For ANY request about a city you MUST call "
                           "transfer_to_agent with facts_agent; never answer yourself.",
               sub_agents=[facts])
display.table(await adk_events(as_sub, "Tell me an interesting fact about Lisbon."), "Sub-agent: remote agent answers")

# %% a2a_coverage=["pattern:2"]
# The ADK controller as served by lab02 (currency and weather as tools, facts as sub-agent)
async with await connect(CONTROLLERS["adk"], streaming=False) as c:
    r = await send_text(c, QUESTION)
print(state_name(r.state), "|", r.text)

# %% [markdown]
# **What just happened**
# - With `AgentTool` the events show `facts_agent` as a *function call and result* authored by the controller, followed by the controller's own text.
# - With `sub_agents` the controller issues `transfer_to_agent` and the final text is authored by `facts_agent`: the controller no longer post-processes it.
# - Pick a tool when you want to combine results; pick a sub-agent when the specialist should own the rest of the conversation.
# - Look closely at the sub-agent table: `facts_agent` shows its own `lookup_fact` call. The ADK specialist publishes its tool calls as **artifacts** (ADK's default), and an ADK controller turns them back into events. Opacity is only as good as what a server chooses to publish (step 9).
#
# > ⚠️ Pitfall: `RemoteA2aAgent(use_legacy=False)` does not take effect on a2a-sdk 1.x (adk-python #7334), and ADK refuses to *fetch* a card from a non-https, non-loopback URL. This lab passes the card as an object, which is acceptable only inside a private network (DEV-20).

# %% [markdown]
# ## 8. INPUT_REQUIRED through a tool
# The currency specialist asks "to which currency?" when it is missing. The controller must relay the question, and the user's answer must continue **the same remote task**.

# %% a2a_coverage=["state:INPUT_REQUIRED", "context:multiturn", "pattern:2"]
# Turn 1: ask for a conversion with no target currency; turn 2: answer in the same context
for kind in ("langchain", "strands"):
    async with await connect(CONTROLLERS[kind], streaming=False) as c:
        first = await send_text(c, "Convert 100 USD")
        second = await send_text(c, "EUR", context_id=first.context_id)
    print(f"{kind:9} turn 1: {first.text[:80]!r}\n{'':9} turn 2: {second.text[:80]!r}")

# %% a2a_coverage=["state:INPUT_REQUIRED", "pattern:2"]
# ADK: the question surfaces inside ADK's events; the controller re-transfers on the answer
async with await connect(CONTROLLERS["adk"], streaming=False) as c:
    first = await send_text(c, "Convert 100 USD")
    second = await send_text(c, "EUR", context_id=first.context_id)  # same context, no task_id
print("turn 1:", state_name(first.state), repr(first.text[:90]))
print("turn 2:", state_name(second.state), repr(second.text[:90]))

# %% a2a_coverage=["op:GetTask", "op:ListTasks"]
# Prove it was ONE remote task: the specialist holds a single task whose history has both user turns
async with await connect("lab02-langgraph-currency", streaming=False) as spec:
    tasks = (await spec.client.list_tasks(ListTasksRequest())).tasks
    paired = [t for t in tasks if len([m for m in t.history if m.role == 1]) >= 2]
    t = await spec.client.get_task(GetTaskRequest(id=paired[-1].id, history_length=10))
print("tasks with two user turns:", len(paired))
print("states:", state_name(t.status.state), "| user messages:", [m.parts[0].text for m in t.history if m.role == 1])

# %% [markdown]
# **What just happened**
# - The tool wrapper remembers which remote task paused (`task_id`) per conversation and sends the next message to **that** task, so the specialist's graph resumes from its `interrupt()`.
# - ADK needs one extra step: `AgentTool` discards the nested session, so it cannot resume a paused remote agent (ADK logs exactly that). The same remote agent wired as a **sub-agent** can, because the controller transfers to it again and ADK continues the paused task.
# - Small local models vary between runs: if an answer looks off, run the cell again; the unit tests prove the plumbing with scripted models.

# %% [markdown]
# ## 9. Opacity check
# What can a controller learn about a specialist? Only what its card publishes.

# %% a2a_coverage=["pattern:2"]
# Search the specialists' cards for anything that betrays their internals
secrets = ["get_exchange_rate", "get_forecast", "lookup_fact", "llama", "qwen", "interrupt"]
for svc in ("lab02-langgraph-currency", "lab02-strands-weather"):
    blob = str(cards[svc]).lower()
    print(f"{svc:26} leaks:", [s for s in secrets if s in blob] or "nothing")

# %% [markdown]
# **What just happened**
# - The cards describe *what* each agent does. Tool names, prompts, models and the fact that the currency agent is a LangGraph graph are not published anywhere.
# - The **trace** still links through: the W3C `traceparent` header travels with every call, so Grafana shows controller → specialist → Ollama in one trace.
#
# - **Caveat:** opacity is a property of the *server*, not of A2A. The LangGraph and Strands specialists publish only their final answer. The ADK specialist also publishes its tool calls and results as artifacts, tagged `adk_type`, so step 7 showed `lookup_fact` leaking through. If a specialist's internals are sensitive, publish only the answer.
#
# > 💡 Tip: trace visibility is not protocol opacity. Across organisations you would usually drop or sanitise `traceparent`, because it reveals call structure.

# %% [markdown]
# ## 10. See it in Grafana

# %%
# One cell, one trace: ask each controller the two-part question under a single notebook span
with display.cell_span("ask three controllers") as span:
    for svc in CONTROLLERS.values():
        async with await connect(svc, streaming=False) as c:
            await send_text(c, QUESTION)
tel.flush()
display.trace_link(span)

# %% [markdown]
# Try in Grafana → Explore:
# - **TraceQL:** `{ resource.service.name = "lab02-adk-controller" } >> { resource.service.name = "lab02-langgraph-currency" }`: the controller trace continues into the currency agent.
# - **TraceQL:** `{ name =~ "chat .*" && resource.service.name =~ "lab02-.*" }`: the LLM calls inside each service.

# %% [markdown]
# ## 11. Exercises
# - 🟢 **Easy:** add `lab02-adk-facts` to the Strands allowlist description and ask for a fact (`build_strands_controller`).
# - 🟡 **Medium:** add a timeout and a fallback message to the LangChain tool (`RemoteAgent.ask`).
# - 🔴 **Stretch:** make the ADK controller pass `reference_task_ids` of earlier calls to the specialists.

# %% tags=["skip-ci"] jupyter={"source_hidden": true}
# Hidden solution (medium): in a2a_labs_common/a2a/remote.py wrap the send in a timeout
# try:
#     result = await asyncio.wait_for(send_text(conn, request, ...), timeout=20)
# except TimeoutError:
#     return RemoteReply("error", "the specialist did not answer in time; try again later")

# %% [markdown]
# ## 12. Recap
#
# | Pattern 2: agent as tool | |
# |---|---|
# | **Use it when** | One controller should decide, per request, which specialists to call and combine their answers |
# | **Not when** | The specialist should own the whole conversation (sub-agent), or the route is fixed (graph node, Lab 04) |
# | Tool contract | Return a string; never raise; surface `NEEDS_INPUT`; continue the same remote task |
# | Security | Allowlist endpoints; treat tool results as untrusted text |
#
# **Pitfalls:** descriptions written by hand drift from the card; `llama3.2` handles one or two simple tools, not many; a controller that restarts the remote context on every call loses `INPUT_REQUIRED` continuity.
#
# **Next:** Lab 03 reaches the same specialists through MCP instead.

# %% [markdown]
# ## Cleanup

# %%
# Flush telemetry (clients were closed by their `async with` blocks)
tel.flush()
