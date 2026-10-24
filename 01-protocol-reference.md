# 01: A2A Protocol Reference for the Labs

This is the protocol reference the labs implement. Where it differs from older guides, **this document and the official spec at https://a2a-protocol.org/latest/specification/ win.** Items marked **[VERIFY]** must be checked against the installed `a2a-sdk` before coding. See `AGENTS.md` §3.

## 1. Versions

| Version | Released | Status in labs |
|---|---|---|
| 0.1.x | 2025 | **Historical only.** Source of the names `tasks/send`, `tasks/sendSubscribe`, `tasks/pushNotification/set|get`. |
| 0.2.x | 2025 | Historical. Introduced `message/send` and `message/stream`. |
| 0.3.0 | 2025 | **Compatibility target.** Served via `enable_v0_3_compat=True`. Lowercase/kebab enums (`input-required`). REST paths use the `/v1/` prefix. |
| **1.0.0** | 2026-03-12 | **Primary target.** PascalCase method names, single `Part`, `supportedInterfaces[]`, ProtoJSON enums. |
| 1.0.1 | 2026-05 | Patch, wire-compatible. Prefers `application/a2a+json`. |

`a2a-sdk` **1.2.3** (2026-10-19) implements spec 1.0 on JSON-RPC, REST and gRPC, plus 0.3 compat on all three. The last 0.3-line SDK is 0.3.26. Its APIs (`A2AStarletteApplication`, `TextPart`, `ClientFactory()`, lowercase enums) **must not** be used.

## 2. Operations: the definitive crosswalk

The brief asks for "all seven JSON-RPC 2.0 task methods". In v1.0 those seven map to **eleven** operations, because v0.3/v1.0 added List Tasks, push-config List/Delete and the extended card. Lab 00 covers all eleven; the seven requested are marked ★.

| ★ | Operation | Legacy v0.1 (requested) | v0.3 JSON-RPC | **v1.0 JSON-RPC / gRPC** | v1.0 REST (HTTP+JSON) | `a2a-sdk` 1.x client method [VERIFY names] |
|---|---|---|---|---|---|---|
| ★ | Send message | `tasks/send` | `message/send` | `SendMessage` | `POST /message:send` | `client.send_message(req)` with `ClientConfig(streaming=False)` |
| ★ | Send streaming | `tasks/sendSubscribe` | `message/stream` | `SendStreamingMessage` | `POST /message:stream` (SSE) | `client.send_message(req)` with `streaming=True` |
| ★ | Get task | `tasks/get` | `tasks/get` | `GetTask` | `GET /tasks/{id}` | `client.get_task(GetTaskRequest(id=…, history_length=…))` |
|   | List tasks | — | — | `ListTasks` | `GET /tasks` | `client.list_tasks(ListTasksRequest(...))` [VERIFY] |
| ★ | Cancel task | `tasks/cancel` | `tasks/cancel` | `CancelTask` | `POST /tasks/{id}:cancel` | `client.cancel_task(CancelTaskRequest(id=…))` |
| ★ | Re-attach stream | `tasks/resubscribe` | `tasks/resubscribe` | `SubscribeToTask` | `POST /tasks/{id}:subscribe` (SSE) | `client.subscribe(SubscribeToTaskRequest(id=…))` |
| ★ | Set push config | `tasks/pushNotification/set` | `tasks/pushNotificationConfig/set` | `CreateTaskPushNotificationConfig` | `POST /tasks/{id}/pushNotificationConfigs` | `client.create_task_push_notification_config(...)` |
| ★ | Get push config | `tasks/pushNotification/get` | `tasks/pushNotificationConfig/get` | `GetTaskPushNotificationConfig` | `GET /tasks/{id}/pushNotificationConfigs/{configId}` | `client.get_task_push_notification_config(...)` |
|   | List push configs | — | `tasks/pushNotificationConfig/list` | `ListTaskPushNotificationConfigs` | `GET /tasks/{id}/pushNotificationConfigs` | `client.list_task_push_notification_configs(...)` [VERIFY plural] |
|   | Delete push config | — | `tasks/pushNotificationConfig/delete` | `DeleteTaskPushNotificationConfig` | `DELETE /tasks/{id}/pushNotificationConfigs/{configId}` | `client.delete_task_push_notification_config(...)` |
|   | Extended card | — | `agent/getAuthenticatedExtendedCard` | `GetExtendedAgentCard` | extended-card GET endpoint (spec §11.3.4) [VERIFY path] | `client.get_extended_agent_card()` |

Card discovery is not an RPC: `GET /.well-known/agent-card.json` via `A2ACardResolver`.

### 2.1 Behavioral rules the labs MUST demonstrate

**SendMessage**
- Default is blocking (`SendMessageConfiguration.return_immediately=false`): the call returns once the task reaches a terminal or interrupted state. v0.3 used the inverse flag, `blocking`.
- With `return_immediately=true`, the call returns right after task creation. The client then follows up with `GetTask`, `SubscribeToTask` or push.
- The response is either a `Message` (direct reply, no task) or a `Task`.
- To continue a task, send a Message carrying its `task_id` (and `context_id`). The server infers `context_id` from `task_id` when the client omits it. A mismatched pair MUST be rejected.
- Task IDs are always server-generated. A client-supplied unknown `task_id` gets `TaskNotFoundError`.
- `history_length` bounds the history returned. `accepted_output_modes` lists the media types the client accepts.

**SendStreamingMessage** has two stream shapes:
- **Message-only:** exactly one `Message`, then the stream closes.
- **Task:** first the `Task`, then any mix of `status_update` and `artifact_update` events. The stream closes once the task reaches a terminal state. It also ends at an interrupted state, where the client continues with a new SendMessage.
- v1.0 removed `final` from `TaskStatusUpdateEvent`; stream closure marks the end.

**SubscribeToTask**
- Sends the current `Task` first, then live events.
- Fails on terminal tasks (`UnsupportedOperationError` [VERIFY]).
- Several concurrent subscribers get identical, ordered events.

**GetTask** returns a point-in-time snapshot. `history_length=0` means no history.

**ListTasks**
- Cursor pagination: `page_size` defaults to 50 (max 100). `next_page_token` is always present and empty on the last page.
- Sorted newest status timestamp first.
- Filters: `context_id`, `status`, `status_timestamp_after`, `include_artifacts`.
- Returns only tasks the caller may see.

**CancelTask**
- Allowed in any non-terminal state. Terminal tasks return `TaskNotCancelableError` (-32002).
- The executor's `cancel()` decides how to stop cooperatively.

**Push config operations**
- If `capabilities.push_notifications` is false, they MUST return `PushNotificationNotSupportedError` (-32003).
- A push config can be created in two ways:
  - inline, through `SendMessageConfiguration.push_notification_config` (or `ClientConfig.push_notification_config` in the SDK);
  - later, with `CreateTaskPushNotificationConfig`.

**Capability gating:** if `capabilities.streaming` is false, `SendStreamingMessage` and `SubscribeToTask` MUST return `UnsupportedOperationError` (-32004).

## 3. Transports and endpoints

| Binding | Card `protocol_binding` | Lab 00 endpoint | Notes |
|---|---|---|---|
| JSON-RPC 2.0 over HTTP | `JSONRPC` | `POST http://<host>:10001/` | SSE for streaming. Content type `application/json`; `application/a2a+json` is also accepted. |
| HTTP+JSON (REST) | `HTTP+JSON` | `http://<host>:10001/rest/...` [VERIFY mount prefix support in `create_rest_routes`] | Errors as `google.rpc.Status` JSON. |
| gRPC | `GRPC` | `<host>:10002` | Uses the SDK gRPC handler. Metadata carries `a2a-version`. |

Each Agent Card lists every binding it serves in `supported_interfaces[]`. Clients pick the first one they support.

## 4. Service parameters (headers)

| Header | Purpose | Lab |
|---|---|---|
| `A2A-Version: 1.0` | Version negotiation. A missing header is treated as 0.3. An unsupported version returns `VersionNotSupportedError`. | 00, 10 |
| `A2A-Extensions: <uri>[,<uri>]` | Activate extensions for this request. Legacy `X-A2A-Extensions` is still sent by ADK and accepted by LangGraph. | 10, 09 |
| `Authorization: Bearer …` / API key header | Security schemes declared on the card | 10 |
| `traceparent`, `tracestate`, `baggage` | W3C trace context (not A2A, but required in this repo) | all |

## 5. Data model (v1.0, Python `a2a.types` protobuf messages)

`a2a-sdk` 1.x types are **protobuf** messages:
- Use `HasField("x")` for oneofs.
- Use `MessageToDict`/`MessageToJson` to print (`google.protobuf.json_format`).
- Enums use SCREAMING_SNAKE_CASE (`TaskState.TASK_STATE_WORKING`, `Role.ROLE_USER`).

### 5.1 AgentCard

```text
AgentCard
  name, description, version
  provider: AgentProvider{organization, url}
  documentation_url, icon_url
  supported_interfaces[]: AgentInterface{url, protocol_binding, protocol_version, tenant}
  capabilities: AgentCapabilities{streaming, push_notifications, extended_agent_card,
                                  extensions[]: AgentExtension{uri, description, required, params}}
  default_input_modes[], default_output_modes[]       # media types
  skills[]: AgentSkill{id, name, description, tags[], examples[], input_modes[], output_modes[], security_requirements?}
  security_schemes{name: SecurityScheme}  # APIKey | HTTPAuth | OAuth2 | OpenIdConnect | MutualTls
  security_requirements[] [VERIFY field name: `security` vs `security_requirements`]
  signatures[]: AgentCardSignature{protected, signature, header}   # JWS over JCS-canonical card
```

Removed in v1.0 (do not use): `url`, `protocolVersion`, `preferredTransport`, `additionalInterfaces`, `supportsAuthenticatedExtendedCard`, and capabilities input/output modes. OAuth implicit and password flows were also removed. `DeviceCodeOAuthFlow` and `pkce_required` were added.

**Well-known path:** `/.well-known/agent-card.json`. The legacy `agent.json` appears only in ADK's `adk api_server --a2a` folder convention; the labs don't use it.

### 5.2 Task, status and states

```text
Task{id, context_id, status: TaskStatus{state, message?, timestamp}, history[]: Message, artifacts[]: Artifact, metadata}
```

| `TaskState` (v1.0) | v0.3 string | Kind | Lab 00 trigger command |
|---|---|---|---|
| `TASK_STATE_SUBMITTED` | `submitted` | active | (any task start) |
| `TASK_STATE_WORKING` | `working` | active | `work <n>` |
| `TASK_STATE_INPUT_REQUIRED` | `input-required` | interrupted | `ask` |
| `TASK_STATE_AUTH_REQUIRED` | `auth-required` | interrupted | `auth` |
| `TASK_STATE_COMPLETED` | `completed` | terminal | `work <n>` finishing |
| `TASK_STATE_FAILED` | `failed` | terminal | `fail` |
| `TASK_STATE_CANCELED` | `canceled` | terminal | CancelTask during `work` |
| `TASK_STATE_REJECTED` | `rejected` | terminal | `reject` |
| `TASK_STATE_UNSPECIFIED` | `unknown` | — | never emitted |

Lab 00 MUST include a Mermaid state diagram of these transitions in its notebook.

### 5.3 Message and Part

```text
Message{message_id (required, client-generated UUID), context_id?, task_id?, role, parts[], metadata, extensions[], reference_task_ids[]}
Part{ oneof content { text | raw (bytes) | url | data (google.protobuf.Value) }, media_type, filename, metadata }
```

Python construction [VERIFY]:

```python
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Value
from a2a.types import Part
Part(text="hello")
Part(raw=png_bytes, media_type="image/png", filename="chart.png")
Part(url="https://example.org/report.pdf", media_type="application/pdf")
Part(data=ParseDict({"amount": 10, "currency": "USD"}, Value()), media_type="application/json")
```

- Messages SHOULD NOT carry task outputs; outputs go in **Artifacts**.
- Status messages are informational and not reliable delivery.

### 5.4 Artifact and stream events

```text
Artifact{artifact_id, name, description, parts[], metadata, extensions[]}
StreamResponse{ oneof { task | message | status_update | artifact_update } }
TaskStatusUpdateEvent{task_id, context_id, status, metadata}
TaskArtifactUpdateEvent{task_id, context_id, artifact, append, last_chunk, index?, metadata}  [VERIFY index]
```

Chunked artifacts:
- The first chunk has `append=false`. Later chunks have `append=true` with the same `artifact_id`.
- The final chunk has `last_chunk=true`.
- Lab 00 `work <n>` streams one artifact in n chunks.

### 5.5 Push notification config (v1.0, flattened)

```text
TaskPushNotificationConfig{tenant?, id, task_id, url, token?, authentication?: AuthenticationInfo{schemes[], credentials?}}
```

- The server POSTs `StreamResponse` objects as HTTP+JSON to `url`, whatever binding the agent uses.
- The `token` is echoed back so the receiver can correlate and validate the call.
- The labs authenticate pushes with an agent-signed JWT verified against the agent's JWKS (Lab 08).

### 5.6 Errors

JSON-RPC errors keep their numeric codes. v1.0 adds `error.data` containing `google.rpc.ErrorInfo{reason, domain: "a2a-protocol.org"}` objects.

| Code | Name (`reason`) | Typical trigger in labs |
|---|---|---|
| -32700 | Parse error | Lab 00 sends malformed JSON |
| -32600 | Invalid request | Missing `jsonrpc` field |
| -32601 | Method not found | Lab 00 sends legacy `tasks/send` |
| -32602 | Invalid params | Message without parts |
| -32603 | Internal error | Executor raises |
| -32001 | `TASK_NOT_FOUND` | GetTask with random ID |
| -32002 | `TASK_NOT_CANCELABLE` | CancelTask on a completed task |
| -32003 | `PUSH_NOTIFICATION_NOT_SUPPORTED` | Push op on the `noPush` agent variant |
| -32004 | `UNSUPPORTED_OPERATION` | Streaming on the `noStream` variant; subscribe to a terminal task |
| -32005 | `CONTENT_TYPE_NOT_SUPPORTED` | `accepted_output_modes=["image/png"]` on a text-only agent |
| -32006 | `INVALID_AGENT_RESPONSE` | (explained only; the SDK raises it when an executor misbehaves) |
| -32007 | `EXTENDED_AGENT_CARD_NOT_CONFIGURED` | GetExtendedAgentCard on an agent without one |
| -32009 [VERIFY] | `VERSION_NOT_SUPPORTED` | `A2A-Version: 9.9` |
| [VERIFY] | `EXTENSION_SUPPORT_REQUIRED` | Lab 10: call a required-extension agent without the header |

REST errors use `google.rpc.Status` JSON; gRPC errors use status codes.

## 6. Extensions (summary; details in Lab 10)

- **Declare** an extension on the card: `capabilities.extensions[]{uri, description, required, params}`.
- **Activate** it per request with `A2A-Extensions: <uri>`. The server echoes the extensions it activated in the response header [VERIFY].
- A breaking change needs a **new URI**. Agents must not silently downgrade; if the extension is required, they return `ExtensionSupportRequiredError`.
- Extensions used in the labs:

| URI | Source | Lab |
|---|---|---|
| `https://google.github.io/adk-docs/a2a/a2a-extension/` | ADK (new executor) | 01, 02, 05 |
| `https://github.com/a2aproject/a2a-samples/tree/main/extensions/agp` | AGP | 09 |
| `https://a2a-labs.dev/ext/trace-summary/v1` | **Custom (this repo)**: agent returns a compact span summary as an artifact | 10 |
| `https://a2a-labs.dev/ext/cost-budget/v1` | **Custom, required**: client must send a budget in message metadata | 10 |

## 7. Security model summary

- Card `security_schemes` and requirements declare what the server needs. Credentials travel out of band in HTTP headers, never in the payload.
- `AUTH_REQUIRED` is for *in-task* secondary authentication, for example when a downstream API needs a user's token.
- Push security:
  - Validate webhook URLs: no private ranges unless allow-listed (`a2a.utils.push_url_validator` [VERIFY]).
  - Sign notifications (JWT via the agent's JWKS).
  - The receiver verifies the signature, `iat` freshness and `token` equality.
- Card integrity: JWS signatures over the RFC 8785 JCS-canonicalized card, excluding the `signatures` field. Verify against a trusted key before delegating (Lab 06), using `a2a.utils.signing` (`signing` extra) [VERIFY API].

## 8. Mapping to framework concepts

| A2A concept | LangGraph | ADK | Strands |
|---|---|---|---|
| `context_id` | `thread_id` in checkpointer config | Session ID (`A2aAgentExecutor` maps it) | `agent_factory(context_id)` key |
| INPUT_REQUIRED | `interrupt()` / structured `require_user_input` | Long-running tool / ask user; ADK 2.2+ separates input from auth | Interrupt (`tool_context.interrupt`) / A2A input-required |
| Artifact | Final state field / AI message | Event content, `output_key` | `AgentResult` (compliant streaming → artifact updates) |
| Streaming | `graph.astream(stream_mode=…)` | Runner events | `stream_async` |
| Cancel | Task cancellation + checkpoint | Runner cancellation [VERIFY executor `cancel`] | Server-handled [VERIFY] |
| Remote agent as client | `a2a.client` in node or `@tool` | `RemoteA2aAgent` | `A2AAgent`, `make_a2a_client` |
