# Lab 08: Async work and push notifications (pattern 7)

Long-running work without holding the connection: `return_immediately` + a push config, signed
notifications to a webhook the **client** owns, an SSRF guard on the agent, a client work queue
that confirms every push with `GetTask`, and a durable task store.

**Notebook:** [`notebooks/lab08_async_push.ipynb`](notebooks/lab08_async_push.ipynb)
(authored as the paired `.py`). **Spec:** [`labs/lab08-async-push.md`](../lab08-async-push.md).

## Run it

```bash
REASONING_THINK=false make up LAB=lab08        # four agents + the shared webhook receiver
PHASE_SECONDS=0.3 DOC_SECONDS=0.2 TOOL_SECONDS=0.3 NODE_SECONDS=0.2 \
  LLM_PROVIDER=fake DECISION_PROVIDER=fake make e2e LAB=lab08   # fast deterministic run
```

| Service | Port | Framework | Long-running part |
|---|---|---|---|
| `lab08-sdk-report-agent` | 10801 | a2a-sdk | 6 phases × `PHASE_SECONDS` (5 s); SQLite task and push-config stores |
| `lab08-strands-batch-agent` | 10802 | Strands | 5 documents × `DOC_SECONDS` |
| `lab08-adk-research-agent` | 10803 | ADK | a slow tool, `TOOL_SECONDS` |
| `lab08-langgraph-pipeline-agent` | 10804 | LangGraph | 4 nodes × `NODE_SECONDS` |

## Security, both directions

- **Agent:** `make_push_url_validator` = the SDK's SSRF screen (scheme, private / loopback /
  link-local / metadata ranges) plus `PUSH_ALLOWED_HOSTS` (default `webhook-receiver,jupyter,host.docker.internal`).
  The check runs when a config is created AND again at delivery.
- **Notification:** `Authorization: Bearer <ES256 JWT>` with `iss`, `aud` (the webhook), `iat`,
  `jti`, `task_id`, `request_body_sha256`; the config `token` in `X-A2A-Notification-Token`.
  The agent publishes its key at `/.well-known/jwks.json` (a restart rotates it).
- **Receiver** (`services/webhook-receiver`, `require_auth=True`): verifies the signature against
  the agent's JWKS (refetching once on an unknown `kid`), `iat` ±5 min, body hash, token, and
  rejects a repeated `jti`.

## Code map

| Path | Role |
|---|---|
| `../../libs/a2a-labs-common/.../a2a/push.py` | `PushSigner`, `JwtPushNotificationSender`, `make_push_url_validator`, JWKS route (CUSTOM) |
| `servers/common.py` | `push_app()`: `build_a2a_app(push=True)` + sender + validator + JWKS + optional SQLite stores |
| `receivers.py` | `EmbeddedReceiver` (client-owned webhook), `SharedReceiver` |
| `work_queue.py` | `PushWorkQueue`: `submit`, `completions`, `reconcile` |
| `agents/*` | the four agents |

## What to notice

- Register the receiver's expectation by **token before** submitting; the first pushes otherwise
  race it and are refused.
- Push is a hint: the queue yields a task only after `GetTask` confirms a terminal state, and
  `reconcile()` finds tasks whose push never arrived.
- A task that was in flight when an agent restarted is not resumed.
