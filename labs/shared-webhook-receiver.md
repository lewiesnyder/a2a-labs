# Shared Service: `webhook-receiver`

**Package:** `services/webhook-receiver` → import `a2a_labs_webhook`
**Port:** 9100
**Profiles:** `lab00`, `lab08`, `lab10`, `all`
**Framework:** FastAPI (instrumented)

A small, inspectable push-notification receiver. It stands in for the "client-side webhook" an orchestrator would run in production.

## Endpoints

| Method/Path | Purpose |
|---|---|
| `POST /webhooks/a2a` | Receives A2A push notifications. The body is a `StreamResponse` in **HTTP+JSON** form (v1.0: always JSON, whatever the agent's binding). |
| `POST /webhooks/a2a/{channel}` | Same, namespaced per channel (Lab 08 uses one channel per orchestrator). |
| `GET /notifications?task_id=&channel=&since=` | Notebook-facing: lists received notifications with verification results, newest last. |
| `GET /notifications/stream?task_id=` | SSE feed of incoming notifications, for live notebook display. |
| `DELETE /notifications` | Clears the store (notebook reset). |
| `GET /healthz` | Health. |
| `GET /.well-known/jwks.json` | **Not** served here. JWKS comes from the agents (see below). |

## Verification pipeline (per request)

1. **Token check:** compare the `X-A2A-Notification-Token` header with the token registered for the task [VERIFY the header name the SDK sender uses]. The receiver learns expected tokens from `POST /expectations {task_id, token, agent_jwks_url}`, called by the notebook or orchestrator when creating the push config.
   - This is a **CUSTOM** convenience because the receiver is shared.
   - In Lab 08 the orchestrator embeds the receiver logic itself, so the CUSTOM registration isn't needed there.
2. **JWT check** (if an `Authorization: Bearer <jwt>` header is present):
   - fetch the agent's JWKS (`agent_jwks_url`, cached 5 min) and verify the signature with `kid`;
   - check `iat` within ±5 min;
   - check that the payload claim for request-body hash, if present, matches (`request_body_sha256` per the a2a-samples push notification auth pattern [VERIFY claim name]).
3. **Body parse:** `json_format.ParseDict(body, StreamResponse())`. Store it with `kind`, `task_id` and `state`.
4. Respond `200` quickly. Processing is synchronous but trivial.
5. Emit span `a2a.push.receive`, metric `a2a.push.received{valid}` and an INFO log.

Store: an in-memory ring buffer (10k entries) keyed by `task_id`.

## Security notes (taught in Lab 08)

- The receiver rejects unauthenticated notifications (401) when `WEBHOOK_REQUIRE_AUTH=true`. The default is `false` for Lab 00 and `true` for Labs 08 and 10.
- It never follows URLs contained in notification bodies.
- Run it only on the private compose network. The host port is exposed only for notebook convenience.
