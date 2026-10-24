# Verification log

Resolved [VERIFY] items. Columns: item, spec location, finding, decision, date.

| Item | Spec location | Finding | Decision | Date |
|---|---|---|---|---|
| Resolved versions | 02 §3 | `a2a-sdk` 1.2.3, `google-adk` 2.13.0, `strands-agents` 1.62.0 (see `uv.lock`) | Pinned via lockfile | 2026-10-23 |
| `a2a.helpers` | AGENTS §3 | Has `new_text_message`, `new_task`, `new_text_artifact`, `get_message_text`, `get_artifact_text`, `get_stream_response_text`, `new_*_artifact_update_event`, `new_text_status_update_event` and more | Use as spec'd | 2026-10-23 |
| `TaskUpdater` | AGENTS §3 | Methods: `submit`, `start_work`, `complete`, `failed`, `reject`, `cancel`, `requires_input`, `requires_auth`, `add_artifact`, `update_status`, `new_agent_message` | Use as spec'd | 2026-10-23 |
| `DefaultRequestHandler` kwargs | 03 §3 | `agent_executor, task_store, agent_card, queue_manager, push_config_store, push_sender, request_context_builder, extended_agent_card, extended_card_modifier, push_url_validator, validate_input_modes, event_stream` | Extended card via `extended_agent_card=`; SSRF guard via `push_url_validator=` | 2026-10-23 |
| Route factories | 03 §3 | `a2a.server.routes`: `create_agent_card_routes`, `create_jsonrpc_routes`, `create_rest_routes` | Use as spec'd (REST mountability still to verify in M1) | 2026-10-23 |
| Client API | 03 §4 | `a2a.client`: `create_client`, `A2ACardResolver`, `ClientConfig(streaming, polling, httpx_client, grpc_channel_factory, supported_protocol_bindings, use_client_preference, accepted_output_modes, push_notification_config)`, `ClientCallInterceptor`, `AuthInterceptor` | Use as spec'd | 2026-10-23 |
| ADK `to_a2a` signature | 00 D10 | `to_a2a(agent, *, host, port, protocol, rpc_path, agent_card, push_config_store, task_store, runner, lifespan, agent_executor_factory, security_schemes, default_skill_security)`. `agent_executor_factory: Callable[[Runner], A2aAgentExecutor]` exists | D10 workaround is expressible; `force_new_version` still to check in M3 | 2026-10-23 |
| **Strands `A2AServer` on a2a-sdk 1.x** | 00 D11, 10 S-1/S-2 | **Incompatible.** `strands-agents[a2a]` (1.57.1 to 1.62.0) requires `a2a-sdk<0.4`, so uv cannot resolve it with `a2a-sdk` 1.2.x. Installing Strands without the extra, `import strands.multiagent.a2a` fails: `ImportError: cannot import name 'DataPart' from 'a2a.types'` | See `spec-deviations.md` DEV-1 | 2026-10-23 |
| `a2a.utils.signing` | 01, Lab 06 | Exposes `create_agent_card_signer`, `create_signature_verifier`, `canonicalize`, errors (`NoSignatureError`, `InvalidSignaturesError`, ...) | Use for Lab 06 | 2026-10-23 |
| `nimble-v3` `/v1/systemone` shape | 04 §4.2 | HTTP 200. `answers.<q>` = `{type, choice, probabilities{...}, confidence}`; top-level `model`, `usage{input_tokens,output_tokens}`, `prompt_eval_cached_count`. Extra key `confidence` not in spec | Parser keeps unknown keys in `raw`; add `confidence` handling in M1 | 2026-10-23 |
| Host models | 04 §5 | `qwen3.8:27b` (completion, vision, tools, thinking), `llama3.2:latest` (completion, tools), `nimble-v3:latest` all present; `make models` passes | none | 2026-10-23 |
| Compose file | 06 | `docker compose --profile all config -q` passes | none | 2026-10-23 |
| Strands stream events | D11 | `Agent.stream_async(prompt)` yields dicts; text deltas are under the `"data"` key. Verified only via a duck-typed fake, **not yet against a real Strands Agent** | Re-verify with a real `OllamaModel` agent in M3 | 2026-10-23 |
