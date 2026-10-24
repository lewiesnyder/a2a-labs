# Lab 10: Security, Extended Cards, Extensions and Versioning

**Package:** `labs/lab10-security-extensions` → import `lab10_secure`
**Profile:** `lab10`
**Models:** `quick`, `decision`
**Frameworks:** a2a-sdk, ADK, Strands

> This lab covers the "all aspects of A2A" topics the pattern labs only touch: authentication schemes, in-task authorization (`AUTH_REQUIRED`), the authenticated extended card, rejection, extensions (optional and required), version negotiation and multi-interface cards, and tenants.

## 1. Learning objectives

1. Declare **security schemes** on the card (API key, HTTP bearer, OAuth2 client-credentials and device-code flows, OIDC) plus security requirements, globally and per skill. Know that credentials travel in **HTTP headers**, never in A2A payloads.
2. Authenticate clients with `a2a.client.auth.AuthInterceptor` + `CredentialService`/`InMemoryContextCredentialStore`, and with framework equivalents:
   - ADK `A2aRemoteAgentConfig.request_interceptors`;
   - a Strands `ClientConfig` with an authenticated `httpx.AsyncClient`.
3. Distinguish **transport auth** (401/403 before any task exists) from **in-task auth** (`AUTH_REQUIRED`, when a downstream resource needs user consent).
4. Serve and consume the **extended agent card** (`GetExtendedAgentCard`), which carries extra skills for authenticated callers only.
5. Use **REJECTED** correctly: a policy refusal decided by the agent (Nimble pre-flight screen).
6. Implement **extensions**:
   - an optional data-only extension (`trace-summary/v1`);
   - a **required** extension (`cost-budget/v1`) that triggers `ExtensionSupportRequiredError` when missing;
   - activation echo; versioning by URI.
7. **Version negotiation:** a card with two interfaces (`protocol_version` 1.0 and 0.3), clients choosing by version, and `VersionNotSupportedError`.
8. **Tenants:** `AgentInterface.tenant` and a client tenant decorator [VERIFY server-side support; optional step].

## 2. Services

| Service | Port | Framework | Purpose |
|---|---|---|---|
| `lab10-mock-idp` | 11009 | `ghcr.io/navikt/mock-oauth2-server` [VERIFY image/tag and device-code support]; fallback is a tiny FastAPI IdP (`# CUSTOM`) | OAuth2/OIDC issuer: client credentials, device code, JWKS, discovery |
| `lab10-secure-agent` | 11001 | a2a-sdk | "Calendar Assistant". Security: `apiKey` (header `X-API-Key`) **or** `oauth2` (client credentials, scope `agent:invoke`). Skill `calendar.read_public` needs any auth. Skill `calendar.read_private` needs the OAuth scope `calendar:read` and triggers **AUTH_REQUIRED** for user consent (device-code flow). Extended card adds `calendar.admin_stats`. Nimble pre-flight → REJECTED for disallowed requests. |
| `lab10-ext-agent` | 11002 | a2a-sdk | Declares optional extension `https://a2a-labs.dev/ext/trace-summary/v1`. When the client activates it, the agent adds an artifact `trace-summary` (DataPart: span count, durations, models used) and echoes the activated extension in the response headers [VERIFY SDK support for the echo]. |
| `lab10-required-ext-agent` | 11003 | a2a-sdk | Declares the **required** extension `https://a2a-labs.dev/ext/cost-budget/v1` (params `{"currency": "USD"}`). Requests must activate it and include `metadata["https://a2a-labs.dev/ext/cost-budget/v1"] = {"max_usd": 0.05}`. Without it: `ExtensionSupportRequiredError`. Over budget: REJECTED. |
| `lab10-adk-secure` | 11004 | ADK `to_a2a` behind `BearerAuthMiddleware` | Validates IdP JWTs. The card declares the oauth2 scheme (passed via `to_a2a(agent_card=…)`). |
| `lab10-strands-secure` | 11005 | Strands `A2AServer` app + the same middleware | Same as above |
| `webhook-receiver` | 9100 | — | Authenticated push (`authentication` in the push config) |

**Server-side auth enforcement:**
- a2a-sdk does not ship transport authentication middleware [VERIFY]. The repo provides `a2a_labs_common.auth.BearerAuthMiddleware` and `ApiKeyMiddleware` (Starlette, `# CUSTOM`). They validate JWTs against the IdP JWKS (issuer, audience, expiry, scopes) and API keys against env.
- The card well-known route stays public.
- The authenticated principal and scopes are stored in the request state. The executor reads them via `RequestContext.call_context.user` [VERIFY how the SDK exposes `ServerCallContext.user`; use the SDK's `User` abstraction/`CallContextBuilder` if provided].
- The extended card is returned only when the principal is authenticated, through the SDK's extended-card hook [VERIFY].

## 3. Secure agent behaviors

| Request | Principal | Result |
|---|---|---|
| any, no credentials | none | HTTP 401 (transport). No task is created. |
| `What's on the public team calendar?` | API key | Task COMPLETED (public data) |
| `Show my private appointments` | OAuth token without `calendar:read` | Task → **AUTH_REQUIRED**. The status message carries a DataPart `{"auth": {"flow": "device_code", "verification_uri": ..., "user_code": ..., "device_code_ref": ...}}`. The client completes the device flow (in the notebook: simulate approval on the mock IdP), then **resumes the same task** with a message containing a DataPart `{"auth": {"access_token_ref": ...}}`. The agent never receives the raw user token in an A2A part; it fetches it from the IdP by reference (`# CUSTOM`, secure pattern). Then COMPLETED. |
| `Delete all calendars` | any | Nimble pre-flight `choice` {allow, needs_auth, reject} → **REJECTED** with a reason |
| `GetExtendedAgentCard` | none / API key / OAuth | error / basic extended / extended with admin skill (scope-dependent) |

## 4. Client-side examples

```python
# a2a-sdk
creds = InMemoryContextCredentialStore()
await creds.set_credentials(session_id, "oauth2", access_token)       # [VERIFY API]
client = await create_client(card, client_config=ClientConfig(streaming=True))
client.add_interceptor(AuthInterceptor(creds))                         # reads card.security_schemes to apply the header

# ADK
RemoteA2aAgent(name="calendar", agent_card=..., config=A2aRemoteAgentConfig(
    request_interceptors=[BearerTokenInterceptor(token_provider)]))     # before_request adds Authorization [VERIFY interceptor shape]

# Strands
A2AAgent(endpoint=url, client_config=ClientConfig(httpx_client=httpx.AsyncClient(auth=OAuthClientCredentialsAuth(...))))  # [VERIFY]
```

`OAuthClientCredentialsAuth` is an `httpx.Auth` subclass that fetches and caches the token from the IdP (`# CUSTOM` helper in common).

## 5. Version negotiation and multi-interface cards

`lab10-ext-agent` exposes two JSON-RPC interfaces:
- `http://lab10-ext-agent:11002/` with `protocol_version="1.0"`;
- `http://lab10-ext-agent:11002/v03` with `protocol_version="0.3"`, using `create_jsonrpc_routes(handler, rpc_url="/v03", enable_v0_3_compat=True)` [VERIFY that a compat-only route is possible; otherwise a single route with compat enabled, and the notebook explains it].

Notebook demos:
- a 1.0 client picks the first interface;
- a raw v0.3 request (`message/send`, no version header) goes to `/v03`;
- `A2A-Version: 2.0` → `VersionNotSupportedError`;
- the query-param form `?A2A-Version=1.0` is tried, and the SDK issue #1344 behavior is noted.

## 6. Notebook outline

1. Architecture and the threat model table: spoofed agent → Lab 06; stolen token → short TTL and scopes; over-privileged agent → per-skill scopes; leaked user token → reference-based AUTH_REQUIRED; SSRF via push → Lab 08.
2. Setup: get the client-credentials token from the mock IdP.
3. **Read security from the card**: the schemes and requirements, including per skill.
4. **Transport auth**: no creds → 401; API key → OK; OAuth → OK. Show where `AuthInterceptor` adds headers (wire view).
5. **Extended card**: three calls with different principals; diff the skills.
6. **AUTH_REQUIRED flow**: the end-to-end device code flow with task resume. A sequence diagram first, then the code. Show that the task is the same `task_id` throughout.
7. **REJECTED**: a destructive request → Nimble screen → REJECTED. Show the decision probabilities. Contrast with FAILED (an error) and CANCELED (client action).
8. **Framework clients with auth**: call `lab10-adk-secure` from an ADK `RemoteA2aAgent` with an interceptor, and `lab10-strands-secure` from a Strands `A2AAgent` with an authenticated httpx client.
9. **Optional extension**: call the ext-agent without and then with `A2A-Extensions: …trace-summary/v1`. Show the extra artifact and the echoed header.
10. **Required extension**: without it → error (show the code and reason); with it and budget metadata → OK; with too small a budget → REJECTED.
11. **Designing your own extension**: a walkthrough of the `AgentExtension` declaration, a URI versioning policy (`/v1` → `/v2` is breaking), the metadata namespace convention (keys are the extension URI), and the spec rule against silently falling back.
12. **Version negotiation demos** (§5).
13. **Authenticated push**: create a push config with `authentication={schemes:["Bearer"], credentials: <receiver token>}`. The receiver checks it.
14. **(Optional) Tenants**: call with tenant `acme` vs `globex` and observe task isolation in ListTasks [VERIFY].
15. Grafana: auth failures (401 spans), REJECTED counts, and extension-activation attributes.
16. Exercises:
    - (easy) add per-skill scopes for a new skill;
    - (medium) implement `cost-budget/v2` with a breaking param change and support both;
    - (stretch) mTLS between two agents using the `MutualTlsSecurityScheme` declaration.

## 7. Acceptance tests

- 401 without credentials, and the card is still public.
- The extended card differs by principal.
- AUTH_REQUIRED → resume → COMPLETED on the same task_id. The raw user token never appears in task history (assert by scanning the GetTask output).
- REJECTED on a destructive request (fake decision `reject`).
- Required extension missing → `ExtensionSupportRequiredError`. Present → OK.
- The optional extension adds the `trace-summary` artifact only when activated.
- `A2A-Version: 2.0` → VersionNotSupported. The v0.3 route accepts `message/send`.
