# 10: Pitfalls, Known Defects and Corrections

## 1. Protocol and SDK

| ID | Pitfall | Do this |
|---|---|---|
| P-1 | Using the legacy method names (`tasks/send`, `tasks/sendSubscribe`, `tasks/pushNotification/set|get`) as if they were current | They are v0.1-era. Serve and call **v1.0** names; v0.3 names work only through compat mode. Lab 00 shows `tasks/send` → `-32601`. |
| P-2 | Mixing a2a-sdk 0.3 and 1.x code from blogs and samples | 1.x is protobuf-based: `HasField`, `MessageToDict`, SCREAMING_SNAKE enums, `a2a.helpers`, `create_client`, route factories. |
| P-3 | Executor emits status updates before the Task, or emits both a Message and a Task | First event = `Task` (task pattern), or exactly one `Message` (message pattern). Otherwise `InvalidAgentResponseError`. |
| P-4 | Expecting `final=true` on status events | Removed in v1.0. Stream closure marks the end. |
| P-5 | Setting a client-generated `task_id` for a new task | Task IDs are server-generated. Unknown IDs → `TaskNotFoundError`. |
| P-6 | Treating `blocking` as v1.0 | v1.0 uses `return_immediately` (inverse semantics). Default is blocking. |
| P-7 | Putting results in status messages | Results go in **Artifacts**. Status messages are informational and not reliably delivered. |
| P-8 | Reading `card.url` / `preferredTransport` | v1.0 uses `supported_interfaces[]` with `protocol_binding` and `protocol_version`. |
| P-9 | `TextPart(...)`, `Part(root=TextPart(...))`, `kind` discriminators | v1.0: `Part(text=…)`, `Part(raw=…)`, `Part(url=…)`, `Part(data=Value)`, with `media_type` instead of `mime_type`. |
| P-10 | Indexing streaming responses like dicts (`resp['result']`) | Pydantic and protobuf objects: use attributes or `HasField`. Known in a2a-samples #292 for 0.3. |
| P-11 | `A2A-Version` via query parameter | The spec allows it, but a2a-python #1344 reports the SDK ignores it and assumes 0.3. Use the header. |
| P-12 | Streaming silently disabled | 1.x streams only if **both** `ClientConfig.streaming` and the card's `capabilities.streaming` are true. |
| P-13 | Fetching `/.well-known/agent.json` | Canonical path is `/.well-known/agent-card.json`. Only ADK's `adk api_server --a2a` folder convention uses `agent.json`. |
| P-14 | Verifying a card signature with keys fetched from the same host | Use a trust store (Lab 06). |
| P-15 | Webhook URL accepted without validation | SSRF. Validate with `push_url_validator` plus an allowlist (Lab 08). |

## 2. Google ADK

| ID | Pitfall | Do this |
|---|---|---|
| ADK-1 | `RemoteA2aAgent(use_legacy=False)` assumed to enable the new protocol path | `use_legacy` **defaults to True**. On a2a-sdk 1.x the extension header is never sent (adk-python **#7334**), so the flag is ineffective. Server side: construct `A2aAgentExecutor(..., force_new_version=True)` via `to_a2a(agent_executor_factory=...)`. This also avoids duplicate replies (**#7247**). Re-check both issues at build time; remove the workaround when fixed. |
| ADK-2 | Omitting `model=` | ADK ≥2.2 defaults to `gemini-3-flash-preview`. Always pass `get_adk_model(role)`. |
| ADK-3 | `LiteLlm(model="ollama/…")` | Use `ollama_chat/…` with `OLLAMA_API_BASE`. `ollama/` causes tool loops and lost context. |
| ADK-4 | Advertising `localhost` on the card from `to_a2a` | Pass the docker DNS host (`host=public_host(service)`). |
| ADK-5 | Expecting AgentTool to pass binary outputs to the parent | AgentTool returns a dict/text result. Binary parts are not visible to the parent LLM (per the user's research; verify for remote agents). Use sub-agents or artifacts for multimodal output. |
| ADK-6 | Task/single_turn sub-agent modes with remote agents | Modes apply to ADK 2.x sub-agents. `task` mode must be a leaf. Verify RemoteA2aAgent compatibility before using it in Lab 05 exercises. |
| ADK-7 | Pickling `RemoteA2aAgent` with an `httpx.AsyncClient` (Agent Engine deploys) | Not relevant to the local labs. Noted in the Lab 02 README as a "going further" item. |
| ADK-8 | Experimental warnings flood the logs | `ADK_SUPPRESS_A2A_EXPERIMENTAL_FEATURE_WARNINGS=true`. |

## 3. Strands

| ID | Pitfall | Do this |
|---|---|---|
| S-1 | `A2AServer(agent=my_agent)` | Deprecated and not multi-tenant safe (shared conversation). Use `agent_factory=lambda ctx: build_agent(ctx)`; `max_contexts` controls LRU eviction. |
| S-2 | Default legacy streaming | `enable_a2a_compliant_streaming=False` by default (with a deprecation warning). Set it to **True**. |
| S-3 | `OllamaModel` without `model_id` | Required (≥1.57.1). |
| S-4 | Using `strands_tools.a2a_client.A2AClientToolProvider` | Deprecated. Use `strands.vended_tools.make_a2a_client(allowed_endpoints=…)` or `A2AAgent`. |
| S-5 | Calling `A2AAgent(...)("prompt")` inside a running event loop (Jupyter) | Use `await agent.invoke_async(...)` or `stream_async`. |
| S-6 | Card URL wrong behind proxies | Set `http_url=public_url(service)`. Use `serve_at_root=True` when a path prefix is stripped. |
| S-7 | Version discrepancy between indexes (1.57.2 vs 1.59.0) | Pin from `uv.lock`; record it in the verification log. |

## 4. LangGraph

| ID | Pitfall | Do this |
|---|---|---|
| LG-1 | `graph.stream()` inside an async executor | Blocks the event loop and stalls SSE (a2a-samples #309). Use `graph.astream()`. |
| LG-2 | Interrupts without a checkpointer | `interrupt()` requires a checkpointer and a `thread_id`. Map `thread_id = context_id`. |
| LG-3 | Assuming the Agent Server `/a2a/{assistant_id}` is complete | It lacks `SubscribeToTask`, push configs, REST and gRPC; returns v0.3 shapes; requires UUID `contextId`; `historyLength` ≤ 10. Use it only in the Lab 04 sub-lab. |
| LG-4 | Resuming with a new message instead of `Command(resume=…)` | Detect pending interrupts via `aget_state(config).next`/`tasks`, then resume. |

## 5. Models

| ID | Pitfall | Do this |
|---|---|---|
| M-1 | Using `nimble-v3` as a chat model | It is a **decision model**: `/v1/systemone` with `choice`/`noul`/`score` questions. No chat, tools or images. Context 32K. Needs Ollama ≥ 0.40.4. |
| M-2 | `choice` questions with no escape option | Always include `none`/`other`. |
| M-3 | Long tool lists on `llama3.2` (3B) | ≤3 tools per quick agent. Use clear, distinct tool descriptions. |
| M-4 | `qwen3.8:27b` latency with thinking on | Thinking is on by default. Disable it per request for snappy demos (`REASONING_THINK=false`), and use `reasoning_effort` when supported. |
| M-5 | Noncommercial license of `nimble-v3` | Document it, and keep `DECISION_PROVIDER=llm_fallback` working. |

## 6. Observability

| ID | Pitfall | Do this |
|---|---|---|
| O-1 | Framework imported before `init_telemetry()` caches a no-op tracer | Init telemetry first; import frameworks inside `main()`. |
| O-2 | Two exporters set up (Strands `setup_otlp_exporter` + ours) → duplicate spans | One global provider; configure Strands telemetry to use it. |
| O-3 | Broken traces across A2A hops | Ensure httpx instrumentation is active in **every** client process, including notebooks and MCP bridges, and Starlette/FastAPI instrumentation on every server. |
| O-4 | Push deliveries in separate traces | Run the push sender within the task's span context (capture the context at task start; attach it in the sender). |
| O-5 | Prompt content captured in shared environments | Capture is ON only in the lab `.env`. Explain the trade-off. |

## 7. Corrections to the requester's Gemini research (for the record)

1. `tasks/send` and the other legacy names are not current. v0.3 uses `message/send` and the `tasks/pushNotificationConfig/*` family; v1.0 uses PascalCase names. The research also omitted `ListTasks`, `GetExtendedAgentCard`, and push-config List/Delete.
2. `use_legacy=False` exists, but the default is **True**, and on a2a-sdk 1.x it is currently ineffective (#7334).
3. `to_a2a()` internals were confirmed. It also accepts `runner`, `task_store`, `push_config_store`, `agent_executor_factory`, `rpc_path` and `lifespan`.
4. The Strands `A2AServer` parameters exist, but `enable_a2a_compliant_streaming` defaults to **False**. `make_a2a_client` lives in `strands.vended_tools`, and its parameter is `allowed_endpoints`.
5. Code using `A2AStarletteApplication`, `TextPart`, lowercase enums or `ClientFactory()` targets SDK 0.3.
6. The canonical well-known path is `agent-card.json`.
7. Model tags: `qwen3.8:27b` exists. The decision model tag is **`nimble-v3:latest`** (verified on ollama.com/library/nimble-v3; a separate `nimble` library entry also exists, but the labs use `nimble-v3`). It is a decision model, not chat.
