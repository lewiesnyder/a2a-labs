# Custom code register

Every `# CUSTOM:` construct in the repo is listed here: location, reason, official alternative checked.

| Location | Reason | Alternative checked |
|---|---|---|
| `libs/a2a-labs-common/src/a2a_labs_common/a2a/strands.py` (`StrandsA2AExecutor`) | `strands.multiagent.a2a.A2AServer` needs a2a-sdk 0.3 (`DataPart`) and cannot import on a2a-sdk 1.x (strands-agents 1.62.0). The executor keeps D11's goals: per-context agents via `agent_factory`, LRU eviction (`max_contexts`), chunked artifact streaming. Everything else uses official `AgentExecutor`, `TaskUpdater` and `a2a.helpers`. | `strands.multiagent.a2a.A2AServer` (broken on 1.x, see DEV-1); `StrandsA2AExecutor` upstream (does not exist for 1.x). Remove when Strands supports a2a-sdk 1.x. |
| `a2a_labs_common/telemetry/a2a_client.py` (`TelemetryInterceptor`) | SDK has no client tracing. Implements the official `ClientCallInterceptor`; `after` fires per streamed event with no end-of-stream hook, so `client.stream_text` ends the span in a `finally` | `a2a.utils.telemetry` (server-side SDK spans only) |
| `a2a_labs_common/telemetry/a2a_server.py` (`TracedExecutor`, `EventQueueTap`) | SDK emits no task-state transitions or artifact-chunk events. Wraps `AgentExecutor`; `EventQueue` is a one-method ABC in 1.x, so tapping is trivial | `a2a.utils.telemetry` spans (no A2A attributes) |
| `a2a_labs_common/a2a/client.py` (`bridge_card_urls`) | Cards advertise docker DNS names; host notebooks rewrite interface URLs to `host_url` (spec 02 §4) | none |
| `a2a_labs_common/models/settings.py` (`role_config` URL rewrite), `scripts/check_models.py` | `.env` holds the container-side `host.docker.internal` Ollama URL; host processes use `localhost` (DEV-5) | none |
| `lab00_protocol/executors/tck.py` (`TckExecutor`) | The A2A TCK drives the agent through in-band `messageId` prefixes defined by its own Gherkin scenarios. No SDK construct provides that contract | TCK's generated reference SUT (`sut/a2a-python`) is a separate project; we keep our own so the lab agent stays the system under test |
| `lab00_protocol/auth.py` (`AdminBearerBackend`, `ExtendedCardGuard`) | SDK has no extended-card guard or "unauthenticated" error (DEV-8). Uses the official Starlette `AuthenticationBackend` for identity | `ExtendedCardNotConfiguredError` (wrong meaning); Lab 10 builds `a2a_labs_common.auth` |
| `a2a_labs_webhook/app.py` (`POST /expectations`) | The receiver is shared by several labs, so it must be told which token/JWKS to expect per task. A real orchestrator embeds this knowledge | none |
| `a2a_labs_common/a2a/langgraph.py` (`LangGraphA2AExecutor`) | The SDK has no LangGraph adapter, and the LangGraph Agent Server's A2A endpoint lacks `SubscribeToTask` and push configs (D9, LG-3). Keeps a2a-sdk in charge of the protocol; maps `context_id`→`thread_id`, `interrupt`→INPUT_REQUIRED, resume→`Command(resume=...)`, uses `astream` | LangGraph Agent Server `/a2a/{assistant_id}` (Lab 04 sub-lab) |
| `a2a_labs_common/a2a/remote.py` (`RemoteAgent`) | Calling a remote agent from a tool safely (never raise, stable remote context per conversation, continue a paused task) has no SDK equivalent; uses only a2a-sdk client calls | none |
| `a2a_labs_common/a2a/langchain_tool.py` (`a2a_agent_as_langchain_tool`) | No official LangChain A2A tool exists; thin wrapper over `RemoteAgent` whose description comes from the Agent Card | none |
| `a2a_labs_common/a2a/strands_tools.py` (`make_a2a_client_tool`, `make_stateful_a2a_tool`) | The official `strands.vended_tools.make_a2a_client` and `A2AAgent` cannot import on a2a-sdk 1.x (DEV-19); same behaviour: stateless allowlisted multi-endpoint tool and stateful single-agent tool | `make_a2a_client`, `A2AAgent` (broken on 1.x) |
| `lab02_tool/agents/controllers.py` (`remote_agent`) | ADK refuses to fetch a card from a non-https, non-loopback URL; the card is fetched by us and passed as an object (DEV-20) | `RemoteA2aAgent(agent_card="http://...")` (blocked in compose) |
| `lab03_mcp/servers/mcp_a2a_bridge.py` | No official A2A-to-MCP bridge exists. Built only from official parts: FastMCP (streamable HTTP) and the a2a-sdk client. States are mapped into tool results; `task_id`/`context_id` returned; `get_task`/`cancel_task` added to compensate for MCP having no tasks | a2a-samples MCP samples (not a bridge) |
| `a2a_labs_common/a2a/langgraph.py` (`graph_factory`) | Lets a graph whose tools load asynchronously (MCP) be built on first use | none |
| `a2a_labs_common/a2a/langgraph.py` (`RemoteA2ANode`) | No SDK or LangGraph construct calls an A2A agent from a node. Uses only a2a-sdk client calls; carries a remote INPUT_REQUIRED into a local `interrupt()` and forwards the answer to the SAME remote task. Memory of (thread, remote) -> task and forwarded-answer count makes the node replay-safe | none |
| `lab04_langgraph/servers/{trip_planner,concierge}.py` (`/debug/state/{context_id}`) | Optional route showing that LangGraph's `thread_id` is the A2A `context_id` and exposing `remote_tasks`. Not part of A2A | `graph.aget_state` in-process |
| `lab05_supervisor/agents/supervisor.py` (`Supervisor`, `delegate`, `split`) | Pattern 4 is not provided by any SDK. Uses LangGraph's official `Send`/conditional edges and the shared `RemoteA2ANode` for every A2A call | none |
| `lab05_supervisor/discovery.py` (`Directory`) | Card discovery with a TTL and stale-on-error. Uses `fetch_card_sync` (DEV-20) | `A2ACardResolver` (one fetch, no cache) |
| `lab05_supervisor/ledger.py` | The Delegation Ledger is this lab's teaching object | none |
| `lab05_supervisor/agents/strands_graph.py` (`A2ANode`, `ClassifyNode`, `GraphAgent`) | Strands' `A2AAgent` and `A2AServer` cannot import on a2a-sdk 1.x (DEV-38); built from `MultiAgentBase`, `GraphBuilder` and `StrandsA2AExecutor` | `A2AAgent` nodes |
| `lab05_supervisor/servers/*` (`/debug/*`, `/specialists`) | Debug and runtime-registration routes. Not part of A2A | `graph.aget_state` in-process |
| `a2a_labs_common/a2a/trust.py` | The SDK provides sign/verify primitives (`a2a.utils.signing`), not a trust policy. Adds the trust store, name/host binding, freshness and the report; keys never come from the card's host | `create_signature_verifier` alone (checks the signature, not who signed) |
| `a2a_labs_common/a2a/client.py` (`connect_with_card`) | Connect from a card you already verified, without re-fetching | `create_client(card)` (this wraps it with our interceptors) |
| `lab06_registry/registry.py` | The A2A spec describes curated registries but defines no API; stores cards plus a verdict | none |
| `lab06_registry/servers/agent_rogue.py` (`/_rogue/*`) | Lab-only control and counter endpoints | none |
| `a2a_labs_common/a2a/relay.py` | A2A defines no relay protocol; a convention in message metadata, built from `TaskUpdater` and client calls | none |
| `lab07_chain/executors/chain_orchestrator.py` | a2a-sdk has no workflow engine; client calls only | `SequentialAgent` / Strands Graph (implicit hand-off, no provenance) |
| `lab07_chain/executors/wrappers.py` | Adapt ADK/Strands executors to DataPart in/out | none |
| `lab07_chain/native.py` (Strands chain) | `A2AAgent` unusable on a2a-sdk 1.x (DEV-19) | `A2AAgent` nodes |
| `a2a_labs_common/a2a/push.py` | The SDK sender does not sign; `PushSigner`, `JwtPushNotificationSender`, `make_push_url_validator`, JWKS route. Follows the a2a-samples push-auth pattern | `BasePushNotificationSender` (token header only) |
| `lab08_push/work_queue.py`, `receivers.py` | A client-side queue over push + `GetTask`; an embedded receiver | none |
| `lab08_push/servers/adk_research_agent.py` | `to_a2a` cannot wire a push sender (DEV-52) | `to_a2a(push_config_store=)` |
