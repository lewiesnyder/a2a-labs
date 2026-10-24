# Lab 00: Protocol Fundamentals (all A2A operations)

**Package:** `labs/lab00-protocol-fundamentals` → import `lab00_protocol`
**Profile:** `lab00`
**LLM:** none (deterministic)
**Frameworks:** pure `a2a-sdk` 1.x
**Notebook:** `lab00_protocol_fundamentals.ipynb` (≤2 min runtime)

## 1. Learning objectives

1. Resolve and read an Agent Card: interfaces, capabilities, skills and extended card.
2. Exercise **all eleven v1.0 operations**, including the **seven requested legacy methods** (★) through their current names, on the JSON-RPC wire.
3. See the **legacy → v0.3 → v1.0 name crosswalk** live:
   - the v1.0 name works;
   - the v0.3 name works through compat mode;
   - the legacy `tasks/send` gets `-32601 Method not found`.
4. Observe **every `TaskState`**, chunked artifacts, multi-turn (`INPUT_REQUIRED`), `AUTH_REQUIRED`, cancellation and resubscription.
5. Use all **Part kinds**: text, raw, url and data.
6. Trigger and read **every A2A error**.
7. Use the same agent over **JSON-RPC, REST and gRPC**, and negotiate versions with `A2A-Version`.

## 2. Services

| Service | Port | Card capabilities | Purpose |
|---|---|---|---|
| `lab00-protocol-agent` | 10001 (JSON-RPC at `/`, REST at `/rest`) and 10002 (gRPC) | streaming ✔, push ✔, extended card ✔ | Main "Protocol Playground Agent" |
| `lab00-protocol-agent-nostream` | 10003 | streaming ✘, push ✔ | Shows `UnsupportedOperationError` for streaming and subscribe |
| `lab00-protocol-agent-nopush` | 10004 | streaming ✔, push ✘ | Shows `PushNotificationNotSupportedError` |
| `webhook-receiver` | 9100 | n/a | Receives push notifications (see `labs/shared-webhook-receiver.md`) |

All variants are the same module, `lab00_protocol.servers.playground`, configured by the env var `PLAYGROUND_VARIANT=full|nostream|nopush`. The gRPC server runs in the same process as `full`, on a second port, sharing one `DefaultRequestHandler` and `TaskStore` so tasks are visible across transports.

## 3. The scripted executor: `PlaygroundExecutor(AgentExecutor)`

`executors/playground.py`. It parses the first text Part of the incoming message. The command is the first token, case-insensitive. All timings are configurable via env `PLAYGROUND_STEP_DELAY` (default 0.5 s).

| Command | Response kind | State path | Artifacts | Demonstrates |
|---|---|---|---|---|
| `help` | **Message** | (no task) | — | Direct Message reply |
| `echo <text>` | **Message** | (no task) | — | Direct Message; echoes the text and the received `metadata` |
| `work [n=5]` | Task | SUBMITTED → WORKING (status msg `step i/n` each step) → COMPLETED | `progress-report`, text streamed in n chunks (`append`, `last_chunk`) | Streaming, chunked artifacts |
| `slow [seconds=30]` | Task | SUBMITTED → WORKING (status every second) → COMPLETED | `slow-result` at the end | Cancel, resubscribe, push, `return_immediately` |
| `ask` | Task | WORKING → INPUT_REQUIRED ("Which colour do you choose? red/green/blue") → *(client resumes with the same task_id)* → WORKING → COMPLETED | `answer`: "You chose <x>" | Multi-turn, task continuation |
| `auth` | Task | WORKING → AUTH_REQUIRED ("Send a data part {\"token\": …}") → resume → COMPLETED if token == `$PLAYGROUND_AUTH_TOKEN` (default `letmein`), else FAILED | `secret` | AUTH_REQUIRED (in-task auth) |
| `fail` | Task | WORKING → FAILED (status message explains) | — | FAILED |
| `reject` | Task | SUBMITTED → REJECTED ("I don't do that") | — | REJECTED |
| `parts` | Task | → COMPLETED | 4 artifacts: `text-part` (text), `image-part` (raw PNG bytes, 1×1 or a tiny generated PNG, `media_type=image/png`, `filename=dot.png`), `url-part` (url to the agent's own card, `media_type=application/json`), `data-part` (data JSON `{"temperature": 21.5, "unit": "C", "tags": ["demo"]}`) | All Part kinds |
| `multi` | Task | → COMPLETED | 3 separate artifacts streamed out of order of creation | Multiple artifacts |
| `crash` | — | executor raises `RuntimeError` | — | Internal error behavior (document the observed SDK result: FAILED task or `-32603`) |
| `refs` | Task | → COMPLETED | `refs`: lists `reference_task_ids` it received and fetches their final states from the TaskStore | `referenceTaskIds` |
| anything else | Message | — | — | Help text |

Implementation rules (A2A-correct):
- For task commands, the **first event** must be the `Task`: `new_task_from_user_message(context.message)` when `context.current_task` is None [VERIFY helper name]. Then use `TaskUpdater(event_queue, task.id, task.context_id)`, with `start_work()`, `update_status(...)`, `add_artifact(parts, artifact_id=…, name=…, append=…, last_chunk=…)`, `requires_input()`, `requires_auth()` [VERIFY], `complete()`, `failed()`, `reject()` [VERIFY method names in 1.x `TaskUpdater`]. Use these, not hand-built events.
- **Message-only** commands enqueue exactly one `Message` (`new_text_message(..., role=ROLE_AGENT)`) and nothing else. Otherwise the SDK raises `InvalidAgentResponseError`.
- Resume (`ask`/`auth`): when `context.current_task` exists and is in an INTERRUPTED state, read the new message and continue. Resume logic keys on `task.status.state` and on task metadata (`metadata.pending="ask"`).
- **Cancel:** the executor keeps `self._cancel_events: dict[task_id, asyncio.Event]`. `cancel()` sets the event and emits CANCELED via `TaskUpdater.cancel()` [VERIFY]. The `work` and `slow` loops check the event between steps.
- **Every step logs** (INFO) and is traced via `TracedExecutor`.

## 4. Server module: `servers/playground.py`

```python
def main() -> None:
    variant = os.getenv("PLAYGROUND_VARIANT", "full")
    service = {"full": "lab00-protocol-agent", "nostream": "lab00-protocol-agent-nostream",
               "nopush": "lab00-protocol-agent-nopush"}[variant]
    tel = init_telemetry(service, framework="a2a-sdk", lab_id="lab00")
    card = cards.playground_card(service, variant)                 # build_card(...)
    extended = cards.playground_extended_card(service) if variant == "full" else None
    app = build_a2a_app(card=card, executor=PlaygroundExecutor(), push=(variant != "nopush"),
                        extended_card=extended, rest=(variant == "full"), enable_v0_3_compat=True)
    if variant == "full":
        start_grpc_in_background(handler=app.state.handler, port=port("lab00-protocol-agent-grpc"))  # [VERIFY]
    run(app, service=service)
```

Card (full):
- `name="Protocol Playground Agent"`; the description lists the commands.
- Skills:
  - `playground.commands` (tags `demo`, `protocol`; examples `work 3`, `ask`, `echo hi`);
  - `playground.parts` (output modes `text/plain`, `image/png`, `application/json`).
- `supported_interfaces`: JSONRPC (`http://lab00-protocol-agent:10001/`), HTTP+JSON (`…/rest`), GRPC (`lab00-protocol-agent:10002`). All have `protocol_version="1.0"`.
- `capabilities`: streaming, push_notifications and extended_agent_card set as per variant.
- `default_output_modes`: `["text/plain", "application/json", "image/png"]`.
- **Extended card:** adds skill `playground.admin` (example `stats`). It is served only to callers presenting `Authorization: Bearer $PLAYGROUND_ADMIN_TOKEN` (default `lab00-admin`). The card declares an `HTTPAuthSecurityScheme(scheme="bearer")` named `adminBearer`, required only for the extended card [VERIFY how the SDK guards the extended card; use a Starlette middleware on that route only if needed, marked CUSTOM].
- Add the `stats` command, available only when the request is authenticated: returns a DataPart with the task counts by state from the TaskStore.

## 5. Notebook outline

Mark each step with the coverage tags from `appendices/A-coverage-matrix.md`.

0. **Title / objectives.**
1. **Architecture**: Mermaid graph (notebook → playground agent over 3 transports; agent → webhook-receiver) and a Mermaid `stateDiagram-v2` of all TaskStates, with transitions labeled by the command that triggers them.
2. **Setup**: telemetry, endpoints, `wait_for_cards`.
3. **The crosswalk**: `display.crosswalk_table()`, with prose explaining the history (v0.1 → v0.3 → v1.0) and why the labs use v1.0 names.
4. **Discover the agent**:
   - (a) raw `httpx.get(".../.well-known/agent-card.json")` and pretty JSON;
   - (b) `A2ACardResolver(httpx_client, base_url).get_agent_card()` with `show_card`;
   - (c) point out `supported_interfaces`, `capabilities` and `skills`.
5. **Raw wire, three ways** (`show_wire`): POST JSON-RPC envelopes with httpx.
   - (a) `{"method": "SendMessage", ...}` with header `A2A-Version: 1.0` → Task result.
   - (b) `{"method": "message/send", ...}` with no version header → v0.3-shaped result. Point out the `kind` fields and lowercase states.
   - (c) `{"method": "tasks/send", ...}` → `-32601`.
   - Explain that the legacy name is history and v1.0 names are what is served today.
6. **★ SendMessage (`tasks/send`)** with the SDK client (`streaming=False`):
   - (a) `echo hello` → **Message** (`SendResult.kind == "message"`); explain the direct reply;
   - (b) `work 3` → blocking → **Task** COMPLETED with artifact;
   - (c) `slow 10` with `return_immediately=True` → returns SUBMITTED/WORKING at once; keep the `task_id`.
7. **★ GetTask (`tasks/get`)**: poll the `slow` task until terminal (`wait_for_terminal`). Show `history_length=0` vs `history_length=10`. Show `TaskNotFoundError` with a random ID.
8. **ListTasks**: list all; filter by `context_id`; filter by `status=COMPLETED`; paginate with `page_size=2`, walking `next_page_token` until it is empty.
9. **★ SendStreamingMessage (`tasks/sendSubscribe`)**: `work 5`, printing each `StreamResponse` with `show_event`. Reassemble the chunked artifact (`append`/`last_chunk`) into the full text. Note that the stream closes at COMPLETED and there is no `final` flag in v1.0.
10. **★ CancelTask (`tasks/cancel`)**: start `slow 30` (`return_immediately`), sleep 2 s, cancel → CANCELED. Cancel again → `-32002 TaskNotCancelable`. Also: cancel while a stream is attached, and observe the CANCELED status event on the stream.
11. **★ SubscribeToTask (`tasks/resubscribe`)**:
    - start `slow 15` with streaming, consume 3 events, then `break` (simulated network drop);
    - call `client.subscribe(...)`: the first event is the current **Task** snapshot, then live updates continue to COMPLETED;
    - open **two concurrent subscribers** (`asyncio.gather`) and show they receive identical sequences;
    - subscribe to a completed task → error (show the code).
12. **★ Push configs (`tasks/pushNotification/set|get`)**:
    - (a) start `slow 8` with `return_immediately`;
    - (b) **CreateTaskPushNotificationConfig** (`url=http://webhook-receiver:9100/webhooks/a2a`, `token=<random>`) → show the returned config and its `id`;
    - (c) **GetTaskPushNotificationConfig** by id;
    - (d) **ListTaskPushNotificationConfigs**;
    - (e) poll `GET webhook-receiver:9100/notifications?task_id=…` and show the received StreamResponse payloads arriving: status updates, artifact, COMPLETED;
    - (f) **DeleteTaskPushNotificationConfig**, then list again (empty);
    - (g) the inline variant: `ClientConfig(push_notification_config=…)` on SendMessage;
    - (h) any push op against `nopush` → `-32003`.
    - Note: auth and JWT verification of pushes is deepened in Lab 08.
13. **Multi-turn and context**:
    - `ask` → INPUT_REQUIRED;
    - reply `green` with the same `task_id` → COMPLETED;
    - send a new message with the same `context_id` but no task_id → a new task in the same context; ListTasks by context shows both;
    - send `refs` with `reference_task_ids=[first_task_id]` and show the agent sees the referenced task;
    - mismatched `context_id`/`task_id` → error.
14. **AUTH_REQUIRED**: `auth` → AUTH_REQUIRED → resume with `data={"token": "letmein"}` → COMPLETED. Repeat with a wrong token → FAILED.
15. **Parts gallery**: `parts` → render text, show the PNG inline (`IPython.display.Image`), show the URL and pretty-print the JSON data. Also show `accepted_output_modes=["image/png"]` on a text-only skill, and observe the error or behavior [VERIFY whether the SDK enforces this; if not, explain it is the agent's responsibility per spec].
16. **Error catalog**: one cell runs every trigger and builds a table of code, reason (`error.data[].reason`) and message:
    - malformed JSON;
    - missing `jsonrpc`;
    - unknown method;
    - invalid params;
    - TaskNotFound;
    - NotCancelable;
    - PushNotSupported;
    - UnsupportedOperation (nostream);
    - ContentTypeNotSupported;
    - ExtendedCardNotConfigured (call GetExtendedAgentCard on nostream);
    - VersionNotSupported (`A2A-Version: 9.9`);
    - the `crash` command.
17. **Transports**: run `work 3` via (a) the JSON-RPC client, (b) the REST client (`transport="HTTP+JSON"`) plus one raw `curl`-equivalent `httpx.post(".../rest/message:send")`, and (c) the gRPC client. Then `GetTask` across transports (a task created over gRPC is fetched over REST). That works because they share one TaskStore.
18. **Version negotiation**: the same SendMessage with `A2A-Version: 1.0`, without the header (0.3 semantics) and with `9.9` (error). Explain spec §3.6.
19. **Extended card**: `get_extended_agent_card()` without auth → error; with the bearer token (AuthInterceptor + `InMemoryContextCredentialStore`) → card with the admin skill; then the `stats` command.
20. **See it in Grafana**: TraceQL `{ resource.service.name = "lab00-protocol-agent" && span.rpc.method = "SubscribeToTask" }`, the state-transition dashboard and a logs query.
21. **Exercises**:
    - (easy) Add a `countdown <n>` command that streams numbers as artifact chunks.
    - (medium) Make `slow` honor a `deadline` in message metadata and FAIL when exceeded.
    - (stretch) Swap `InMemoryTaskStore` for `DatabaseTaskStore` with SQLite. Restart the container mid-task and show that `GetTask` still works.
22. **Recap**: a table of the 11 operations (when to use each), plus pitfalls.

## 6. Acceptance tests

- Unit (in-process): every command's state path; first-event rule; cancel → CANCELED; resume of `ask`/`auth`; `parts` produces 4 artifacts with the correct oneof fields.
- Integration:
  - all 11 operations succeed against `full`;
  - legacy `tasks/send` → -32601;
  - v0.3 `message/send` → success;
  - nostream returns -32004 for streaming and subscribe;
  - nopush returns -32003;
  - REST and gRPC parity test (same artifacts);
  - a push notification arrives at the receiver within 10 s;
  - two concurrent subscribers receive equal event lists.
- TCK mandatory suite passes against `lab00-protocol-agent` (JSON-RPC).
- Trace check: the notebook's step 11 trace contains `a2a.client SubscribeToTask` and `a2a.server.execute` spans in one trace.
