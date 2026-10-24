# Lab 02: Agent as Tool (Pattern 2)

**Package:** `labs/lab02-agent-as-tool` → import `lab02_tool`
**Profile:** `lab02`
**Models:** `quick` for specialists, `reasoning` for controllers
**Frameworks:** ADK, Strands, LangChain/LangGraph

> **Pattern 2:** A local controller exposes a remote A2A agent as a **tool**. The controller's LLM decides when to call it. The remote side keeps its own task lifecycle and stays **opaque**: the controller sees only the tool result, never the remote agent's internals.

## 1. Learning objectives

1. Wrap a remote A2A agent as a tool in each framework using the **official constructs**:
   - **ADK:** `AgentTool(agent=RemoteA2aAgent(...))`, compared with `sub_agents=[RemoteA2aAgent]` (transfer).
   - **Strands:** the `make_a2a_client(allowed_endpoints=…)` vended tool (stateless, multi-endpoint), compared with `A2AAgent` wrapped in a `@tool` (stateful).
   - **LangChain:** a `@tool` coroutine using the a2a-sdk client (no official LangChain A2A tool exists; mark it `# CUSTOM: thin wrapper` but use only SDK calls).
2. Understand **opacity**: the remote agent's tools, prompts and model are invisible. Only its Agent Card and Artifacts cross the boundary.
3. Handle remote **task states inside a tool**: COMPLETED → return the artifact text; INPUT_REQUIRED → return a structured "needs input" result so the controller can ask the user; FAILED/REJECTED → return the error string. Never raise into the LLM loop.
4. Map tool calls to A2A `context_id`: one remote context per controller conversation. The tool keeps a `{(controller_ctx, remote_agent): remote_ctx}` map.
5. Compare tool vs sub-agent vs graph node (a preview of Labs 04 and 05).

## 2. Services

Remote specialists (one per framework, so every controller calls agents built in other frameworks):

| Service | Port | Framework | Skill | Tools inside (opaque) | Notable behavior |
|---|---|---|---|---|---|
| `lab02-langgraph-currency` | 10201 | LangGraph + `LangGraphA2AExecutor` | Currency conversion | `get_exchange_rate(from, to, date)` (static table, deterministic) | Returns **INPUT_REQUIRED** when the target currency is missing (via `interrupt()`) |
| `lab02-strands-weather` | 10202 | Strands `A2AServer(agent_factory)` | Weather report | `get_forecast(city, days)` (deterministic fake) | Streams the artifact |
| `lab02-adk-facts` | 10203 | ADK `to_a2a` | City facts | `lookup_fact(city)` (static) | Plain task |

Controllers (each is also exposed over A2A, so other labs and the notebook can call them):

| Service | Port | Framework | Remote agents used as tools |
|---|---|---|---|
| `lab02-adk-controller` | 10204 | ADK `Agent(model=get_adk_model("reasoning"), tools=[AgentTool(remote_currency), AgentTool(remote_weather)], sub_agents=[remote_facts])` | currency and weather **as tools**; facts as a **sub-agent** (contrast) |
| `lab02-strands-controller` | 10205 | Strands `Agent(model=get_strands_model("reasoning"), tools=[make_a2a_client(allowed_endpoints={currency, weather, facts})])` | all three via one vended tool; plus `ask_weather` = `@tool` wrapping a persistent `A2AAgent` (stateful contrast) |
| `lab02-langchain-controller` | 10206 | LangGraph `create_react_agent`/`create_agent` with tools `ask_currency`, `ask_weather`, `ask_facts` built by `a2a_agent_as_langchain_tool(name, url)` | all three |

## 3. Key implementations

### 3.1 ADK

```python
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent, AGENT_CARD_WELL_KNOWN_PATH
from google.adk.tools.agent_tool import AgentTool
remote_currency = RemoteA2aAgent(
    name="currency_agent", description="Converts amounts between currencies.",
    agent_card=f"{endpoint('lab02-langgraph-currency')}{AGENT_CARD_WELL_KNOWN_PATH}",
    use_legacy=False,   # see 10-pitfalls §ADK-1: on a2a-sdk 1.x this alone may not take effect
)
```

The notebook demonstrates the **AgentTool vs sub_agents** difference with the same request:
- **AgentTool:** the controller stays in charge. It receives a `FunctionResponse` and writes the final answer.
- **Sub-agent:** `transfer_to_agent` hands the conversation to the remote agent, whose reply is the final reply. The controller does not post-process it.

Show both event streams (ADK events → A2A status updates) and both traces.

### 3.2 Strands

```python
from strands.vended_tools import make_a2a_client
a2a_tool = make_a2a_client(
    name="call_specialist",
    description="Call a remote specialist agent by URL. Available: currency, weather, facts.",
    allowed_endpoints={endpoint("lab02-langgraph-currency"): None,
                       endpoint("lab02-strands-weather"): None,
                       endpoint("lab02-adk-facts"): None},
)
```

- The vended tool is **stateless**: each call constructs a fresh `A2AAgent`, so no remote context continuity.
- Contrast it with `weather_agent = A2AAgent(endpoint=…, name="weather")` held across calls inside a custom `@tool async def ask_weather(question: str) -> str: return str(await weather_agent.invoke_async(question))`. This one is **stateful**: the remote context persists [VERIFY that A2AAgent reuses context_id across invocations].
- The allowlist is a security control: the LLM cannot make the tool call arbitrary URLs. Discuss this in the notebook.

### 3.3 LangChain (`a2a_labs_common.a2a.langchain_tool`)

```python
def a2a_agent_as_langchain_tool(service: str, *, name: str | None = None, description: str | None = None) -> StructuredTool:
    """Builds a LangChain tool whose name/description come from the remote Agent Card (skills → description).
    Input: {"request": str}. Uses RunnableConfig['configurable']['thread_id'] to derive a stable remote context_id
    (uuid5(thread_id + service)). Returns artifact text, or 'NEEDS_INPUT: <question> [task_id=…]' on INPUT_REQUIRED.
    If the next call carries task_id=… it continues that remote task."""
```

- The card-derived description is the important teaching point: the **Agent Card is the tool schema source**.
- **Input-required round trip** (all three controllers): the user asks "Convert 100 USD". The currency agent returns INPUT_REQUIRED ("to which currency?"), and the controller asks the user. The next user turn, "EUR", must **continue the same remote task**.
  - LangChain: the tool parses `task_id` from its previous output via the state.
  - Strands: the stateful `A2AAgent` path.
  - ADK: `RemoteA2aAgent` handles input-required [VERIFY how ADK surfaces remote INPUT_REQUIRED; ADK 2.2+ separates input/auth].

## 4. Notebook outline

1. Architecture: a 3×3 Mermaid matrix (controllers × specialists).
2. Setup.
3. Concept: tool vs sub-agent vs node, as a table: who owns the conversation, who writes the final answer, state continuity and opacity.
4. Inspect the specialists' cards. Show how the LangChain tool description is generated from the card.
5. **LangChain controller**: invoked in-process from the notebook (import the graph) *and* via its A2A endpoint. Ask "What's 250 GBP in JPY, and what's the weather in Tokyo?" and show both tools being called.
6. **Strands controller**: the same question. Show `make_a2a_client` calls in the trace. Then show the allowlist blocking a URL the LLM was prompted to call (prompt-injection demo: the request text contains "call http://evil.example").
7. **ADK controller**: the same question, with AgentTool vs sub-agent side by side. Show the final answers and an event comparison.
8. **INPUT_REQUIRED through a tool**: the multi-turn currency flow with each controller.
9. **Opacity check**: query the currency agent's card and confirm the tools are not listed. Show that the trace still links through (W3C context propagates), but the controller code has no knowledge of the remote internals. Discuss trace visibility vs protocol opacity (cross-org deployments would often drop or sanitize `traceparent`).
10. Grafana: one trace showing controller (ADK) → currency (LangGraph) → Ollama.
11. Exercises:
    - (easy) add a fourth specialist to the Strands allowlist;
    - (medium) add a timeout and a fallback message to the LangChain tool;
    - (stretch) make the ADK controller pass `reference_task_ids` of earlier calls.

## 5. Acceptance tests

- Each controller (fake models with scripted tool calls) produces an answer that includes both tool results.
- The LangChain tool's description equals the card-derived text.
- The INPUT_REQUIRED round trip continues the same remote `task_id` (assert via GetTask on the specialist: one task, state COMPLETED, history contains both user messages).
- The Strands allowlist rejects a non-listed URL.
- The ADK AgentTool and sub-agent paths both complete.
