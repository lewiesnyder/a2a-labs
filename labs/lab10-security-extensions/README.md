# Lab 10: Security, extended cards, extensions and versioning

Authenticate callers, ask a person for consent in the middle of a task, show different skills to
different principals, refuse policy violations with `REJECTED`, add optional and **required**
extensions, and negotiate protocol versions.

**Notebook:** [`notebooks/lab10_security_extensions.ipynb`](notebooks/lab10_security_extensions.ipynb)
(authored as the paired `.py`). **Spec:** [`labs/lab10-security-extensions.md`](../lab10-security-extensions.md).

## Run it

```bash
make up LAB=lab10                                             # IdP, five agents, shared webhook receiver
LLM_PROVIDER=fake DECISION_PROVIDER=fake make e2e LAB=lab10   # deterministic run
```

| Service | Port | Framework | Role |
|---|---|---|---|
| `lab10-mock-idp` | 11009 | FastAPI (`# CUSTOM`) | OIDC discovery, JWKS, `client_credentials`, device code, token references |
| `lab10-secure-agent` | 11001 | a2a-sdk | Calendar Assistant: API key **or** OAuth, extended card, `AUTH_REQUIRED`, `REJECTED`, push, tenants |
| `lab10-ext-agent` | 11002 | a2a-sdk | optional `trace-summary/v1`; two interfaces (1.0 at `/`, 0.3 at `/v03`) |
| `lab10-required-ext-agent` | 11003 | a2a-sdk | **required** `cost-budget/v1` |
| `lab10-adk-secure` | 11004 | ADK | same bearer middleware |
| `lab10-strands-secure` | 11005 | Strands | same bearer middleware |

Dev credentials (compose only): clients `lab10-client` / `lab10-secret` (scope `agent:invoke`),
`lab10-admin-client` / `lab10-admin-secret` (adds `agent:admin`), API key `lab10-dev-key`.
Override with `LAB10_CLIENT_SECRET`, `LAB10_ADMIN_SECRET`, `LAB10_AGENT_SECRET`.

## What is official and what is ours

| Concern | Official construct | Ours (`# CUSTOM`) |
|---|---|---|
| Who is the caller | `ServerCallContext.user`, `.state["auth"]` filled from Starlette's `AuthenticationMiddleware` | `BearerBackend`, `ApiKeyBackend`, `ChainBackend`, `RequireAuth` (the 401), `JwtVerifier` in `a2a_labs_common/auth.py` |
| Client credentials | `AuthInterceptor` + `CredentialService` (reads `card.security_schemes`) | `OAuthClientCredentialsAuth` (`httpx.Auth`), ADK `bearer_interceptor` |
| Extended card | `extended_card_modifier(card, ServerCallContext)` on `DefaultRequestHandler` | none |
| Extensions | `AgentExtension`, `requested_extensions`, `ExtensionSupportRequiredError` | `require_extensions` (raises it), `ExtensionEcho` (response header) in `a2a/extensions.py` |
| Versions | `A2A-Version` check (`VersionNotSupportedError`), `enable_v0_3_compat`, interface `protocol_version` | `extra_rpc_urls` on `build_a2a_app` (a second mount) |
| Tenants | `AgentInterface.tenant`, client `TenantTransportDecorator`, `owner_resolver` on the task store | `tenancy.tenant_owner` (one line) |
| IdP | none | `idp.py` (device code needs it) |

## Code map

| Path | Role |
|---|---|
| `src/lab10_secure/idp.py`, `servers/mock_idp.py` | the issuer |
| `src/lab10_secure/executors.py` | `CalendarExecutor`, `TraceSummaryExecutor`, `CostBudgetExecutor` |
| `src/lab10_secure/preflight.py` | Nimble `choice` {allow, needs_auth, reject} |
| `src/lab10_secure/cards.py` | schemes, per-skill requirements, extended card, two-interface card |
| `src/lab10_secure/idp_client.py` | the agent's side of the device flow and token reference |
| `src/lab10_secure/clients.py` | tokens, consent, raw JSON-RPC for the notebook and tests |

## What to notice

- **401 vs `AUTH_REQUIRED`:** the first happens in middleware, before any task exists; the second is a task
  state, resumed with the same `task_id`.
- **Token reference:** the user's token goes IdP -> agent. The client only ever holds an opaque,
  single-use reference, so no token appears in task history (a test scans `GetTask` for it).
- **`OAuthFlows` is a `oneof`:** one scheme can describe one flow, so the card has `oauth2`
  (client credentials) and `user_consent` (device code) (DEV-59).
- **Missing `A2A-Version` means 0.3** to this SDK, and the `?A2A-Version=` query form is ignored.
- Real Nimble classified all ten probe requests as intended on the first try (e.g. "Remove all my meetings" -> reject 0.97).
