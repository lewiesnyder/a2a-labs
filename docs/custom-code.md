# Custom code register

Every `# CUSTOM:` construct in the repo is listed here: location, reason, official alternative checked.

| Location | Reason | Alternative checked |
|---|---|---|
| `libs/a2a-labs-common/src/a2a_labs_common/a2a/strands.py` (`StrandsA2AExecutor`) | `strands.multiagent.a2a.A2AServer` needs a2a-sdk 0.3 (`DataPart`) and cannot import on a2a-sdk 1.x (strands-agents 1.62.0). The executor keeps D11's goals: per-context agents via `agent_factory`, LRU eviction (`max_contexts`), chunked artifact streaming. Everything else uses official `AgentExecutor`, `TaskUpdater` and `a2a.helpers`. | `strands.multiagent.a2a.A2AServer` (broken on 1.x, see DEV-1); `StrandsA2AExecutor` upstream (does not exist for 1.x). Remove when Strands supports a2a-sdk 1.x. |
| `a2a_labs_common/telemetry/a2a_client.py` (`TelemetryInterceptor`) | SDK has no client tracing. Implements the official `ClientCallInterceptor`; `after` fires per streamed event with no end-of-stream hook, so `client.stream_text` ends the span in a `finally` | `a2a.utils.telemetry` (server-side SDK spans only) |
| `a2a_labs_common/telemetry/a2a_server.py` (`TracedExecutor`, `EventQueueTap`) | SDK emits no task-state transitions or artifact-chunk events. Wraps `AgentExecutor`; `EventQueue` is a one-method ABC in 1.x, so tapping is trivial | `a2a.utils.telemetry` spans (no A2A attributes) |
| `a2a_labs_common/a2a/client.py` (`bridge_card_urls`) | Cards advertise docker DNS names; host notebooks rewrite interface URLs to `host_url` (spec 02 §4) | none |
| `a2a_labs_common/models/settings.py` (`role_config` URL rewrite), `scripts/check_models.py` | `.env` holds the container-side `host.docker.internal` Ollama URL; host processes use `localhost` (DEV-5) | none |
