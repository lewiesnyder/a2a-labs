# Lab 01: Direct Client → Agent (Pattern 1)

**Package:** `labs/lab01-direct-client` → import `lab01_direct`
**Profile:** `lab01`
**Models:** `quick`
**Frameworks:** a2a-sdk, ADK, Strands, LangGraph

> **Pattern 1:** Resolve an Agent Card, send a message, and get either a direct `Message` or a server-owned `Task`.

## 1. Learning objectives

1. The same client code talks to agents built in **four different stacks**. A2A hides the framework.
2. Understand when a server answers with a **Message** (stateless, immediate) and when it answers with a **Task** (stateful, trackable), and how each framework decides.
3. Expose an agent as an A2A server in each framework with the **official adapter**: `to_a2a`, `A2AServer` or a2a-sdk `AgentExecutor`.
4. Use `contextId` for multi-turn conversations with each framework, and see how each maps it internally.
5. Compare the auto-generated Agent Cards across frameworks.

## 2. Services

All four agents implement the same skill, **"Trip Tip"**: given a city, return one practical travel tip. Each also has one tool, `local_time(city)`, which returns deterministic fake data from a static dict, so tool calls are visible in traces.

| Service | Port | Framework / adapter | Response style |
|---|---|---|---|
| `lab01-sdk-echo` | 10101 | a2a-sdk `AgentExecutor` (no LLM) | **Message only**: returns `Message` for every request (stateless) |
| `lab01-adk-agent` | 10102 | ADK `Agent(model=get_adk_model("quick"), tools=[local_time])` → `to_a2a(...)` with `agent_executor_factory` (force new version) | Task (ADK executor always creates tasks [VERIFY]) |
| `lab01-strands-agent` | 10103 | Strands `Agent(model=get_strands_model("quick"), tools=[local_time])` via `A2AServer(agent_factory=…, enable_a2a_compliant_streaming=True)` | Task with artifact streaming |
| `lab01-langgraph-agent` | 10104 | LangGraph `create_react_agent`-style graph (or the LangGraph 1.x `create_agent` equivalent [VERIFY]) with `MemorySaver`, wrapped by `LangGraphA2AExecutor` (a2a-sdk) | Task; `context_id → thread_id` |

### 2.1 ADK server (`servers/adk_agent.py`)

```python
def main():
    init_telemetry("lab01-adk-agent", framework="adk", lab_id="lab01")
    from google.adk.agents import Agent
    from google.adk.a2a.utils.agent_to_a2a import to_a2a
    from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor   # [VERIFY import path]
    agent = Agent(name="trip_tip_adk", model=get_adk_model("quick"),
                  description="Gives one practical travel tip for a city.",
                  instruction="... Always call local_time first ...", tools=[local_time])
    app = to_a2a(agent, host=public_host("lab01-adk-agent"), port=10102, protocol="http",
                 agent_executor_factory=lambda runner: A2aAgentExecutor(runner=runner, force_new_version=True))  # [VERIFY]
    instrument_app(app); run(app, service="lab01-adk-agent")
```

- `to_a2a` advertises `host:port` on the auto-built card, so pass the **docker DNS name** (`lab01-adk-agent`).
- The notebook shows the auto-generated card and points out the ADK extension URI in `capabilities.extensions`, if present.

### 2.2 Strands server (`servers/strands_agent.py`)

```python
def build_agent(context_id: str) -> Agent:
    return Agent(name="trip_tip_strands", description="Gives one practical travel tip for a city.",
                 model=get_strands_model("quick"), tools=[local_time],
                 system_prompt="...", callback_handler=None)
server = A2AServer(agent_factory=build_agent, host="0.0.0.0", port=10103,
                   http_url=public_url("lab01-strands-agent"), enable_a2a_compliant_streaming=True,
                   version="1.0.0")
app = server.to_starlette_app(); instrument_app(app); uvicorn.run(app, ...)
```

The notebook explains `agent_factory`: one agent instance per `context_id`, with LRU eviction (`max_contexts`). Contrast it with the deprecated shared `agent=` argument, which leaks conversation between callers.

### 2.3 LangGraph server (`executors/langgraph_executor.py`, reused by Labs 02/04/05/07/08)

This is the **canonical LangGraph ↔ A2A bridge** for the repo. It lives in `a2a_labs_common.a2a.langgraph` so all labs share it.

```python
class LangGraphA2AExecutor(AgentExecutor):
    """Runs a compiled LangGraph graph per A2A request.
    A2A: context_id → LangGraph thread_id (checkpointer), so multi-turn and INPUT_REQUIRED resume work."""
    def __init__(self, graph, *, agent_name: str, output_key: str = "messages",
                 interrupt_to_input_required: bool = True, stream_mode: str = "updates"): ...
    async def execute(self, context, event_queue):
        task = context.current_task or new_task_from_user_message(context.message)
        if not context.current_task: await event_queue.enqueue_event(task)   # A2A: Task first
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        config = {"configurable": {"thread_id": task.context_id}}
        inputs = Command(resume=user_text) if self._is_resuming(config) else {"messages": [("user", user_text)]}
        await updater.start_work()
        async for chunk in graph.astream(inputs, config, stream_mode=["updates", "messages"]):   # NEVER graph.stream()
            # tool calls → WORKING status ("Calling tool X"); __interrupt__ → INPUT_REQUIRED + break
        # final → add_artifact(final AI message text, name="answer"), complete()
    async def cancel(self, context, event_queue):
        # cancel the running asyncio task for this task_id; updater.cancel()
```

Key rules:
- Use `astream`, not `stream`. A synchronous iterator blocks the event loop (a2a-samples #309).
- A `__interrupt__` in an update means INPUT_REQUIRED, with the interrupt `value` as the status message. A resume message on the same `task_id` resumes via `Command(resume=text)`. LangGraph knows it is resuming because `graph.aget_state(config).next` is non-empty.
- Partial AI tokens (`messages` stream mode) are optionally streamed as **artifact chunks** (`append=True`), with `last_chunk=True` at the end. This is behind the flag `STREAM_TOKENS_AS_ARTIFACT=true`.
- On exception: `updater.failed(message=…)` and log with the trace.

### 2.4 SDK echo (`servers/sdk_echo.py`)

The executor returns `new_text_message(f"[echo] {text}", role=ROLE_AGENT, context_id=context.context_id)`. No Task. The card declares `streaming=True`. The notebook shows that a streaming request to a message-only agent yields exactly one `Message` event, and then the stream closes.

## 3. Notebook outline

1. **Architecture**: one notebook client fanning out to 4 servers. Table of frameworks and adapters.
2. **Setup.**
3. **Concept**: Message vs Task. A decision table of when servers choose each, and the sequence diagram for both.
4. **Discover all four cards**: `show_card` for each, plus a comparison table of name, skills, streaming, push, extensions and interfaces. Discuss which fields each framework auto-fills.
5. **Same question to four agents**: `send_text(c, "Give me a tip for Lisbon")` for each, in a loop. Table of `kind`, final state, artifact count, latency and the text. Highlight that `sdk-echo` returns `Message` and the others return `Task`.
6. **Streaming from each**: `stream_text` and `show_event`. Compare the event shapes:
   - Strands compliant artifact chunks;
   - ADK status and artifact events;
   - LangGraph tool-call status updates.
7. **Multi-turn with `context_id`**: turn 1 "My name is Ada; tip for Paris". Turn 2, same `context_id`: "What's my name?" Show that each framework remembers it, and explain the internal mapping (ADK session, Strands factory key, LangGraph thread_id). Then a new `context_id` → it forgets.
8. **Inspect the task afterwards**: `GetTask` on each Task-returning agent, comparing history contents (`history_length=5`).
9. **Writing your own client loop**: a 15-line raw a2a-sdk example (no helpers), as a reference to copy.
10. **Grafana**: a TraceQL query to find the 4 parallel `a2a.client SendMessage` spans under one notebook span, and the LLM `chat llama3.2` spans under each server.
11. **Exercises**:
    - (easy) add a second skill to the Strands agent and observe the card change;
    - (medium) make sdk-echo return a Task when the text starts with `task:`;
    - (stretch) add the A2A Inspector step.

## 4. Acceptance tests

- All four cards are valid. The SDK echo agent returns a Message; the others return Tasks with ≥1 artifact.
- Multi-turn memory works per framework (fake-model script asserts the second-turn prompt contains the first-turn content).
- Streaming produces ≥2 events for the three framework agents.
- The LangGraph executor unit tests cover: astream used, interrupt → INPUT_REQUIRED, resume → COMPLETED, cancel → CANCELED.
