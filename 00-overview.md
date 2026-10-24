# 00: Overview, Goals and Key Decisions

## 1. Purpose

`a2a-labs` is a teaching and reference monorepo. A developer who works through it should be able to:

1. Explain every A2A operation, its wire shape and its effect on the task lifecycle.
2. Expose an agent built in **ADK**, **Strands** or **LangGraph** as an A2A server, and consume any A2A server from any of them.
3. Choose the right delegation pattern for a problem: direct, as tool, via MCP, sub-agent or graph node, supervisor, registry, chain, async push, or hierarchical gateway. They should also be able to justify that choice.
4. Trace one user request end to end across several agents, frameworks and processes in Grafana: spans, correlated logs and metrics.
5. Swap the model provider or model per role without touching lab code.

## 2. Scope

### In scope
- A2A Protocol **v1.0** (spec 1.0.0, 2026-03-12; 1.0.1 patch is wire-compatible) as the primary target.
- **v0.3 compatibility mode**, where the SDK supports it, so learners see the `message/send`-era names on the wire.
- A **historical crosswalk** for the legacy (v0.1-era) names the requester listed: `tasks/send`, `tasks/sendSubscribe`, `tasks/get`, `tasks/cancel`, `tasks/pushNotification/set`, `tasks/pushNotification/get` and `tasks/resubscribe`. Each legacy name is demonstrated through its current equivalent. Lab 00 also sends the raw legacy name once to show that current servers reject it with `-32601 Method not found`.
- All three transports: JSON-RPC 2.0 (primary), HTTP+JSON/REST and gRPC. Lab 00 covers all three; the other labs use JSON-RPC.
- Agent Cards: discovery, skills, capabilities, security schemes, extended card and JWS signing.
- Task lifecycle: every `TaskState`, multi-turn conversations (`contextId`), `referenceTaskIds`, artifacts with chunked streaming, and cancellation.
- Push notifications: CRUD for push configs, webhook receiver, JWT/JWKS verification and SSRF guard.
- Extensions: custom extension, required extension, ADK extension and AGP extension.
- Delegation patterns 1–8 from the brief, plus MCP-based delegation and framework-native sub-agent and graph-node delegation.
- OpenTelemetry traces, logs and metrics for every service and notebook.
- Local LLMs via Ollama; a provider-agnostic model layer.

### Out of scope
- Production hardening: HA task stores, multi-replica clustering and real OAuth IdPs. These are mentioned in notebooks as "going further" notes only.
- Non-Python SDKs.
- UI front-ends other than Jupyter, Grafana and the optional A2A Inspector.

## 3. Design principles

1. **Use A2A constructs, not custom ones.** Use `a2a-sdk` types and helpers: `AgentExecutor`, `TaskUpdater`, `DefaultRequestHandler`, `create_client`, `A2ACardResolver`, `a2a.helpers`, `a2a.utils.signing` and `push_url_validator`. Use the framework's official A2A adapters (`to_a2a`, `RemoteA2aAgent`, `A2AServer`, `A2AAgent`, `make_a2a_client`). Write custom code only where no official construct exists, and mark it in code with `# CUSTOM: <reason>`. Examples: the curated registry (Lab 06), the AGP gateway (Lab 09) and the telemetry interceptor.
2. **One concept per lab, clearly layered.** Each lab adds one idea on top of the previous ones. Notebooks never require reading source files to follow along, though they link to them.
3. **Deterministic where possible.** The protocol labs (00, 08 and 10) use a deterministic "scripted" agent so behavior is reproducible and testable. LLM-backed agents appear where the pattern needs reasoning.
4. **Configuration over code.** Model provider, model names, endpoints and telemetry come from env vars and `config/*.yaml`. No lab hard-codes a host, port or model.
5. **Observable by default.** Every process calls `init_telemetry()` first. A trace started in a notebook cell MUST appear as a single connected trace across every agent it touches.
6. **Runnable two ways.** Every notebook runs either inside the `jupyter` compose service, which reaches services by DNS name, or on the host, which reaches them by `localhost` ports. Endpoint resolution handles both automatically.
7. **Small, readable code.** Prefer under 150 lines per module. Each lab's `README.md` and notebook are its documentation.

## 4. Key decisions (ADR summary)

| # | Decision | Rationale |
|---|---|---|
| D1 | Target **A2A v1.0** and **`a2a-sdk` 1.2.x**, with `enable_v0_3_compat=True` on pure-SDK servers | v1.0 is the current spec. Compat mode lets learners see v0.3 names, and some framework clients (ADK on a2a-sdk 0.3 paths, LangGraph Agent Server) still speak 0.3 shapes. |
| D2 | Legacy names (`tasks/send` and the rest) are taught as a **crosswalk**, not served | No official SDK serves them. Building a legacy server would violate principle 1. The notebook shows the mapping and a live `-32601` rejection. |
| D3 | **uv workspace** monorepo: one shared lib, one package per lab, one webhook-receiver service | Fast, reproducible, single lockfile. Each lab can be installed and tested alone. |
| D4 | **One Docker image** (`a2a-labs:dev`) for all Python services; each compose service picks its module via `command` | Smaller build matrix, consistent dependencies, quick rebuilds. |
| D5 | **Ollama on the host by default**, with an optional `ollama` profile to run it in a container | The requester already has the models pulled locally. GPU passthrough on macOS isn't possible in Docker. |
| D6 | **Role-based model config**: `reasoning`, `quick` and `decision` | Lab code asks for a *role*, never a model name, so swapping providers is a config change. |
| D7 | **Nimble (`nimble-v3`) is used only through a `DecisionModel` interface** (`/v1/systemone`), with an LLM-prompted fallback | Nimble is a decision model, not a chat or tool-calling model. Its best uses are routing (LangGraph conditional edges), gating and scoring. |
| D8 | **`grafana/otel-lgtm`** as the single observability backend | One container gives Collector, Tempo, Loki, Prometheus and Grafana. Fine for labs (not production). |
| D9 | LangGraph is exposed via an **`a2a-sdk` `AgentExecutor`** in all core labs. The LangGraph Agent Server `/a2a/{assistant_id}` endpoint is an **optional sub-lab** in Lab 04. | The Agent Server lacks `SubscribeToTask` and push configs and returns v0.3-shaped payloads. |
| D10 | ADK servers use `to_a2a(..., agent_executor_factory=...)` with `A2aAgentExecutor(force_new_version=True)` (verify argument name) | Works around adk-python #7334, where `use_legacy=False` has no effect on a2a-sdk 1.x, and #7247 (duplicate replies). |
| D11 | Strands servers use `A2AServer(agent_factory=..., enable_a2a_compliant_streaming=True)` | Multi-tenant-safe per-context agents and spec-compliant artifact streaming. The old `agent=` argument is deprecated. |
| D12 | Python **3.12** | Supported by all frameworks. Required for consistent `asyncio` and typing features. |

## 5. Glossary

| Term | Meaning |
|---|---|
| **A2A** | Agent2Agent protocol: a JSON-RPC/gRPC/REST wire protocol for agent discovery and task delegation. |
| **Agent Card** | JSON document at `/.well-known/agent-card.json` describing an agent's identity, interfaces, capabilities, skills and security. |
| **Task** | Server-owned unit of work with an ID, status (`TaskState`), history and artifacts. |
| **Message** | One conversational turn (`ROLE_USER` or `ROLE_AGENT`) made of Parts. It can be the entire response ("direct Message"). |
| **Part** | Content unit. v1.0 uses one `Part` type holding one of `text`, `raw` (bytes), `url` or `data` (JSON). |
| **Artifact** | A task output (deliverable). It can stream in chunks (`append`, `last_chunk`). |
| **contextId** | Groups related tasks and messages into a conversation. Maps to LangGraph `thread_id`, Strands per-context agent and ADK session. |
| **Interrupted state** | `INPUT_REQUIRED` or `AUTH_REQUIRED`: the task pauses for the client. |
| **Terminal state** | `COMPLETED`, `FAILED`, `CANCELED` or `REJECTED`: no further changes. |
| **Push config** | `TaskPushNotificationConfig`: webhook URL, token and auth used to deliver task updates out of band. |
| **Extension** | Opt-in protocol add-on, declared on the card and activated per request through the `A2A-Extensions` header. |
| **Delegation pattern** | How one agent hands work to another: tool, MCP, sub-agent, graph node, supervisor, chain, push or gateway. |
| **Decision model** | A model that returns probabilities over predefined answers (`choice`, `noul`, `score`) rather than free text. In this repo: Nimble. |
| **Role** | Logical model slot: `reasoning`, `quick` or `decision`. |
| **AGP** | Agent Gateway Protocol: experimental A2A extension for hierarchical squads behind gateway agents (`a2a-samples/extensions/agp`). |

## 6. Learner journey

```
setup notebook ─► Lab 00 (protocol) ─► Lab 01 (direct) ─► Lab 02 (as tool) ─► Lab 03 (MCP)
                                                     │
                                                     ├─► Lab 04 (LangGraph node) ─► Lab 05 (supervisor)
                                                     │                                   │
                                                     │                                   ├─► Lab 06 (registry)
                                                     │                                   └─► Lab 07 (chain)
                                                     └─► Lab 08 (async push) ─► Lab 10 (security/extensions) ─► Lab 09 (AGP)
```

Labs 00 and 01 are prerequisites for everything else. All other labs can run on their own.
