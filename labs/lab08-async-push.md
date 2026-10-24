# Lab 08: Async Queue and Push Notifications (Pattern 7)

**Package:** `labs/lab08-async-push` → import `lab08_push`
**Profile:** `lab08`
**Models:** `quick` (LLM steps are short; the long duration is simulated)
**Frameworks:** a2a-sdk, Strands, ADK, LangGraph

> **Pattern 7:** Long-running work uses webhooks or push config instead of holding the client on the wire.

## 1. Learning objectives

1. Start work with `return_immediately=true` and receive results by **push** rather than holding a connection.
2. Manage push configs over their full lifecycle:
   - inline creation in `SendMessage` (★ legacy `tasks/send` with pushNotification);
   - `CreateTaskPushNotificationConfig` (★ `tasks/pushNotification/set`);
   - `GetTaskPushNotificationConfig` (★ `tasks/pushNotification/get`);
   - `ListTaskPushNotificationConfigs`;
   - `DeleteTaskPushNotificationConfig`;
   - **multiple configs per task**.
3. **Secure** push in both directions:
   - Server side: an SSRF guard on webhook URLs (`push_url_validator`) and **JWT-signed** notifications, with a JWKS published by the agent.
   - Receiver side: verify the signature against the agent's JWKS, check `iat` freshness and a body hash, compare the `token`, and dedupe replays.
4. Combine push with **polling** (`GetTask`) and **re-attachment** (`SubscribeToTask`) for robustness. Push is a hint; `GetTask` is the source of truth.
5. Build a client-side **async work queue**: fan out N tasks, consume completions from a queue fed by the webhook, and process them as they arrive.
6. Use a **durable task store** (`DatabaseTaskStore` with SQLite) so task state survives agent restarts.
7. Enable push in each framework's server.

## 2. Services

| Service | Port | Framework | Long-running behavior | Push wiring |
|---|---|---|---|---|
| `lab08-sdk-report-agent` | 10801 | a2a-sdk `AgentExecutor` | "Quarterly report": 6 phases × `PHASE_SECONDS` (default 5 s). Each phase emits a status update and an artifact chunk. The final phase calls the quick model to write a summary. | `DefaultRequestHandler(push_config_store=DatabasePushNotificationConfigStore(sqlite), push_sender=JwtPushNotificationSender(...))`, task store `DatabaseTaskStore(sqlite)` [VERIFY constructor args] |
| `lab08-strands-batch-agent` | 10802 | Strands `A2AServer` | Summarizes a batch of N documents (bundled sample texts) one by one | `A2AServer(..., push_config_store=InMemoryPushNotificationConfigStore(), push_sender=JwtPushNotificationSender(...))` |
| `lab08-adk-research-agent` | 10803 | ADK | A "research" agent with a long-running tool (`LongRunningFunctionTool` or a slow tool) | `to_a2a(agent, push_config_store=…)`. **If `to_a2a` does not wire a push sender [VERIFY], build `DefaultRequestHandler(agent_executor=A2aAgentExecutor(runner=…), task_store=…, agent_card=…, push_config_store=…, push_sender=…)` manually with the SDK route factories.** Document which path was needed. |
| `lab08-langgraph-pipeline-agent` | 10804 | LangGraph executor | 4-node data pipeline graph with `asyncio.sleep` per node | the same `build_a2a_app(push=True)` as the SDK agent |
| `webhook-receiver` | 9100 | FastAPI | — | `WEBHOOK_REQUIRE_AUTH=true` |

All four cards declare `capabilities.push_notifications=true` and `streaming=true`.

### 2.1 `JwtPushNotificationSender` (`a2a_labs_common.a2a.push`)

- Subclass `BasePushNotificationSender` [VERIFY 1.x base class and override point]. Before each POST, it adds `Authorization: Bearer <JWT>`.
- The JWT is ES256, signed by the agent's key, with header `kid`. Claims:
  - `iss` = agent card URL;
  - `aud` = the webhook URL;
  - `iat`;
  - `jti` (UUID, for replay protection);
  - `task_id`;
  - `request_body_sha256` (hex SHA-256 of the exact body bytes).
- It sends the config `token` as the `X-A2A-Notification-Token` header [VERIFY the SDK's default header name and keep it].
- Agents serve their public key at `/.well-known/jwks.json` via a small route added to the app (`# CUSTOM`, following the a2a-samples push-auth pattern).
- If the 1.x SDK ships an equivalent authenticated sender, use it and delete this class. Check this at scaffold time.
- Delivery: retry 3× with exponential backoff on 5xx or connection errors. Span `a2a.push.deliver`, metric `a2a.push.deliveries`.

### 2.2 SSRF guard

Push config creation validates the URL with `a2a.utils.push_url_validator` [VERIFY name/API]:
- reject non-http(s) schemes;
- reject private, loopback and link-local ranges (e.g. `169.254.169.254`) unless the host is in `PUSH_ALLOWED_HOSTS` (default `webhook-receiver`, `jupyter`, `host.docker.internal`).

If the SDK validator is not applied automatically by `DefaultRequestHandler`, wrap the push config store's `set` method (`ValidatingPushConfigStore`, `# CUSTOM`) and raise `InvalidParamsError`.

## 3. Client-side async queue (`clients/work_queue.py`)

```python
class PushWorkQueue:
    """Fan out tasks with push configs, consume completions from the webhook-receiver SSE feed
    (or an embedded receiver when running in-notebook), and expose an asyncio.Queue of terminal Tasks."""
    async def submit(self, service: str, text: str) -> str            # returns task_id; return_immediately + inline push config
    async def completions(self) -> AsyncIterator[Task]                 # yields when a terminal push arrives, after confirming with GetTask
    async def reconcile(self) -> None                                  # periodic GetTask for tasks with no push in > N s (lost-push safety net)
```

Inside the Docker network, the notebook can also run an **embedded receiver**: a uvicorn server on port 9200 in a background task, registered as `http://jupyter:9200/...`. This shows the "client owns the webhook" production shape. The shared `webhook-receiver` is the fallback for host-run notebooks.

## 4. Notebook outline

1. Architecture: a sequence diagram with three phases: submit (return immediately), push notifications, and confirmation by GetTask.
2. Setup: start the embedded receiver if `A2A_LABS_RUNTIME=docker`.
3. Concept: "don't hold the wire". A comparison of polling, streaming, resubscribe and push (latency, connection cost, reliability, firewall needs).
4. **Inline push config on SendMessage** with the SDK report agent: show the immediate return (SUBMITTED/WORKING) and the notifications arriving live (SSE feed rendering).
5. **CRUD on push configs**: create a second config (a second channel) for the same task, then Get, List (2 configs), Delete one, and show that only one channel keeps receiving.
6. **Verify a notification by hand**: take one received notification and decode the JWT header and claims. Fetch the JWKS, verify the signature, recompute the body hash and compare the token.
7. **Attacks**:
   - (a) register `http://169.254.169.254/latest/meta-data` → rejected (`InvalidParams`);
   - (b) POST a forged notification to the receiver with a self-signed JWT → 401;
   - (c) replay a captured notification → rejected as a duplicate `jti`;
   - (d) a stale `iat` → rejected;
   - (e) a body modified after signing → hash mismatch.
8. **Push in every framework**: submit one job to each of the four agents with push. Show a table of agent, framework, notifications received, final state and time. Note any framework differences, such as which events are pushed.
9. **Async work queue**: submit 8 jobs across the agents, consume completions as they arrive (out of order), and process each with a tiny local step. Show a timeline chart (start/finish per task).
10. **Lost push safety net**: stop the receiver (or delete the config) mid-task; `reconcile()` finds the task via `GetTask`.
11. **Re-attach with SubscribeToTask**: for a running task, switch from push to a live stream (as a UI would when a user opens a page), then switch back.
12. **Durability**: let a task complete, then restart `lab08-sdk-report-agent` (`docker compose restart`). `GetTask` still returns it from SQLite. Discuss in-flight tasks on restart: they are not resumed automatically, and the notebook shows what state they are in.
13. **Cancel an async task** and observe the CANCELED push.
14. Grafana: push delivery spans linked to the original request trace (the push sender runs inside the task's trace context). Also a push-deliveries panel.
15. Exercises:
    - (easy) add a `channel` filter to the work queue;
    - (medium) add HMAC-signed pushes as an alternative scheme and compare with JWT;
    - (stretch) back the receiver queue with Redis Streams (add a `redis` service to the profile).

## 5. Acceptance tests

- Each of the 4 agents delivers ≥1 push with a valid JWT for a task, and delivers a final-state notification.
- CRUD: List shows 2, Delete leaves 1, and the deleted channel receives no further notifications.
- SSRF: the metadata IP is rejected. A host outside the allowlist with a private IP is rejected.
- The receiver rejects a forged JWT, a replayed `jti`, a stale `iat` and a body tampered with after signing.
- Durable store: GetTask after restart returns COMPLETED with artifacts.
- Work queue: 8 tasks all reach terminal states and every completion is confirmed via GetTask.
