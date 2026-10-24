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
