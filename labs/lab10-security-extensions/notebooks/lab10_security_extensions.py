# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# %% [markdown]
# # Lab 10 · Security, extended cards, extensions and versioning
#
# **Goal:** secure an A2A agent end to end, then extend the protocol without breaking it. You will refuse strangers at the door, ask a *person* for consent in the middle of a task, hide skills behind a login, and negotiate protocol versions.
# **Time:** about 6 minutes · **Prerequisites:** Labs 00 and 01 (Lab 08 helps for push) · `make up LAB=lab10`
#
# **What you'll learn**
# - Where **credentials** live (HTTP headers, never A2A parts) and how an **Agent Card** declares them.
# - **Transport auth** (HTTP 401, no task exists) versus **in-task auth** (`AUTH_REQUIRED`, the task waits for a person).
# - The **extended card**: different skills for different callers.
# - `REJECTED` (policy), compared with `FAILED` and `CANCELED`.
# - **Extensions**: optional and *required*, activation, the echo header, and why a URI carries a version.
# - **Version negotiation**, a card with two interfaces, and **tenants**.
#
# Spec: [A2A specification, security](https://a2a-protocol.org/latest/specification/) · [extensions](https://a2a-protocol.org/latest/topics/extensions/)

# %% [markdown]
# ## 1. Architecture and threat model
#
# ```mermaid
# flowchart LR
#   NB[Notebook / client] -->|1 client-credentials| IDP[(lab10-mock-idp :11009)]
#   NB -->|Authorization: Bearer / X-API-Key| SEC[lab10-secure-agent :11001]
#   NB -->|Bearer| ADK[lab10-adk-secure :11004]
#   NB -->|Bearer| STR[lab10-strands-secure :11005]
#   NB -->|A2A-Extensions| EXT[lab10-ext-agent :11002]
#   NB -->|A2A-Extensions| REQ[lab10-required-ext-agent :11003]
#   SEC -->|JWKS, token reference| IDP
#   SEC -.->|push + credentials| WH[webhook-receiver :9100]
# ```
#
# | Service | Port | Framework | Role |
# |---|---|---|---|---|
# | `lab10-mock-idp` | 11009 | FastAPI (`# CUSTOM`) | issues tokens: client credentials, device code, JWKS |
# | `lab10-secure-agent` | 11001 | a2a-sdk | API key **or** OAuth; extended card; consent; REJECTED |
# | `lab10-ext-agent` | 11002 | a2a-sdk | optional extension + two interfaces (1.0, 0.3) |
# | `lab10-required-ext-agent` | 11003 | a2a-sdk | **required** extension |
# | `lab10-adk-secure` / `lab10-strands-secure` | 11004 / 11005 | ADK / Strands | same bearer middleware |
#
# | Threat | Defence in this lab |
# |---|---|
# | Anonymous caller | HTTP 401 before any task exists (step 4) |
# | Stolen token | short TTL, audience check, scopes (steps 2, 4) |
# | Over-privileged agent | per-skill scopes, extended card (steps 3, 5) |
# | Leaked **user** token | `AUTH_REQUIRED` with a token *reference*, never the token (step 6) |
# | Spoofed agent | signed cards, Lab 06 |
# | SSRF through push | URL guard, Lab 08 |

# %% tags=["parameters"]
QUICK_DEMO = False

# %% [markdown]
# ## 2. Setup
# The IdP is the only party that mints tokens. The agents never see a password; they fetch the IdP's public keys (JWKS) and check signature, issuer, audience and expiry.

# %%
# Telemetry, helpers, the services, and a first token from the IdP
import asyncio
import json

import httpx
import jwt
from a2a.helpers import get_data_parts
from a2a.types import (
    AgentCard,
    AuthenticationInfo,
    GetExtendedAgentCardRequest,
    GetTaskRequest,
    ListTasksRequest,
    TaskPushNotificationConfig,
)
from google.protobuf.json_format import MessageToDict, MessageToJson

from a2a_labs_common import display
from a2a_labs_common.a2a.client import connect, connect_with_card, send_text, wait_for_cards
from a2a_labs_common.a2a.states import state_name
from a2a_labs_common.auth import OAuthClientCredentialsAuth, bearer_interceptor
from a2a_labs_common.config import endpoint
from a2a_labs_common.telemetry import init_telemetry

from lab10_secure.clients import (
    StaticCredentials,
    approve,
    card_json,
    client_token,
    idp_url,
    message_params,
    rpc,
)
from lab10_secure.data import API_KEYS, BUDGET_EXT, TRACE_EXT

tel = init_telemetry("notebook-lab10", framework="notebook", lab_id="lab10")
display.show_models()
SECURE, EXT, REQUIRED = "lab10-secure-agent", "lab10-ext-agent", "lab10-required-ext-agent"
ADK, STRANDS = "lab10-adk-secure", "lab10-strands-secure"
await wait_for_cards([SECURE, EXT, REQUIRED, ADK, STRANDS], timeout=180)  # prints `make up LAB=lab10` if down
API_KEY = next(iter(API_KEYS))
PUBLIC = "What's on the public team calendar?"

token = client_token()
claims = jwt.decode(token, options={"verify_signature": False})  # decode only to LOOK; the agents verify
print({k: claims[k] for k in ("iss", "sub", "aud", "scope", "exp")})
print("the token is signed with", jwt.get_unverified_header(token))

# %% [markdown]
# > 🔍 **Under the hood.** A bearer token is a signed JWT. `iss` says who issued it, `aud` who it is *for* (an agent must refuse tokens meant for another service), `scope` what it may do, `exp` when it dies. The signature is checked against the IdP's published keys, so the agent needs no shared secret.

# %% [markdown]
# ## 3. Read the security from the card
# Discovery is public on purpose: a client has to learn *which* scheme to use before it can authenticate. The card lists **schemes** (how) and **requirements** (which are needed), at card level and per skill. A list of requirements means **OR**; several schemes inside one requirement mean **AND**.

# %% a2a_coverage=["security", "op:Discovery"]
# Show the schemes and each skill's requirements, straight from the public card
card = card_json(endpoint(SECURE))
for name, s in card["securitySchemes"].items():
    kind = next(iter(s))
    print(f"scheme {name:13} -> {kind}")
print("card-level requirements (OR):", [list(r["schemes"]) for r in card["securityRequirements"]])
for sk in card["skills"]:
    reqs = [{k: v.get("list", []) for k, v in r["schemes"].items()} for r in sk["securityRequirements"]]
    print(f"skill {sk['id']:22} needs {reqs}")

# %% [markdown]
# **What just happened**
# - `apiKey` says the key goes in the **header** `X-API-Key`; `oauth2` says get a client-credentials token from the IdP's `tokenUrl`; `user_consent` is a separate scheme because an `OAuthFlows` object holds **one** flow (a protobuf `oneof`).
# - Private calendars need `calendar:read`, granted only by a person through the device flow (step 6).
# - Nothing here is a secret. The card tells a client how to ask; it never contains credentials.

# %% [markdown]
# ## 4. Transport auth: 401 before any task exists
# Auth is the web framework's job, not the protocol's. We plug Starlette's `AuthenticationMiddleware` into the SDK server; the SDK then exposes the caller as `ServerCallContext.user`.

# %% a2a_coverage=["security", "errors", "op:SendMessage"]
# Anonymous, API key and OAuth against the same endpoint
url = endpoint(SECURE)
anon = rpc(url, "SendMessage", message_params(PUBLIC))
print("no credentials :", anon.status_code, anon.headers.get("www-authenticate"), anon.json())
for label, headers in {"API key": {"X-API-Key": API_KEY}, "OAuth token": {"Authorization": f"Bearer {token}"}}.items():
    r = rpc(url, "SendMessage", message_params(PUBLIC), headers=headers).json()["result"]["task"]
    print(f"{label:15}:", r["status"]["state"])
bad = rpc(url, "SendMessage", message_params(PUBLIC), headers={"Authorization": "Bearer not.a.token"})
print("garbage token  :", bad.status_code, bad.json()["detail"])

# %% [markdown]
# Now the same thing the SDK way. `AuthInterceptor` reads the card, finds the scheme named in the requirements, asks a `CredentialService` for that scheme's credential and writes the header. Watch the wire:

# %%
# Let the SDK's AuthInterceptor choose the header; print what actually leaves the client
sent = []


async def spy(request):
    sent.append({k: (v[:28] + "...") if k == "authorization" else v for k, v in request.headers.items() if k in ("authorization", "x-api-key", "a2a-version")})


http = httpx.AsyncClient(timeout=60, event_hooks={"request": [spy]})
oauth = await connect(SECURE, auth=StaticCredentials({"oauth2": token}), httpx_client=http, streaming=False)
res = await send_text(oauth, PUBLIC)
print("state:", state_name(res.state), "| headers sent on the SendMessage:", sent[-1])
print(res.text)
await oauth.aclose()

# %% [markdown]
# **What just happened**
# - The 401 came from middleware, so **no `Task` was created**: there is nothing to `GetTask` and no state to read. That is the difference from `AUTH_REQUIRED` (step 6).
# - `StaticCredentials({"oauth2": token})` answered only for `oauth2`, so the interceptor skipped the card's first requirement (`api_key`) and sent `Authorization: Bearer ...`.
# - Credentials never appear in a `Message` or `Part`. A payload is logged, cached and forwarded; a header is not.

# %% [markdown]
# ## 5. The extended card: more skills for authenticated callers
# `capabilities.extendedAgentCard = true` promises a second, richer card behind `GetExtendedAgentCard` (v0.3 name `agent/getAuthenticatedExtendedCard`). The SDK calls a hook with the caller's context, so the agent can answer **per caller**.

# %% a2a_coverage=["op:GetExtendedAgentCard", "security"]
# Three callers, three answers
admin_token = client_token(client_id="lab10-admin-client", scope="agent:invoke agent:admin")
callers = {
    "anonymous": None,
    "API key": {"X-API-Key": API_KEY},
    "OAuth user": {"Authorization": f"Bearer {token}"},
    "OAuth admin": {"Authorization": f"Bearer {admin_token}"},
}
for who, headers in callers.items():
    r = rpc(url, "GetExtendedAgentCard", headers=headers)
    skills = [s["id"] for s in r.json()["result"]["skills"]] if r.status_code == 200 else f"HTTP {r.status_code}"
    print(f"{who:12}: {skills}")

# %% [markdown]
# **What just happened**
# - The public card has 2 skills. The extended card adds `calendar.free_busy` for any authenticated caller and `calendar.admin_stats` only when the token carries `agent:admin`.
# - The SDK client exposes the same call as `client.get_extended_agent_card(...)`; it applies the `AuthInterceptor` like any other call.

# %% [markdown]
# ## 6. `AUTH_REQUIRED`: ask a person for consent, mid-task
# Some resources belong to a *user*, not to the calling agent. The task then enters the **interrupted** state `AUTH_REQUIRED` and waits. The agent starts an OAuth **device flow** at the IdP and tells the client what to show the user.
#
# ```mermaid
# sequenceDiagram
#   participant C as Client
#   participant A as Calendar agent
#   participant I as IdP
#   participant U as User
#   C->>A: SendMessage "Show my private appointments"
#   A->>I: device_authorization (agent's own credentials)
#   A-->>C: Task AUTH_REQUIRED {verification_uri, user_code, device_code_ref}
#   C->>U: show the code
#   U->>I: approve user_code
#   I-->>U: access_token_ref (opaque, single use)
#   C->>A: SendMessage(same task_id) {auth: {access_token_ref}}
#   A->>I: redeem ref (agent's credentials)
#   I-->>A: user's access token
#   A-->>C: COMPLETED + private calendar
# ```
# The **reference** is the trick: the user's token goes IdP → agent directly. It never passes through the client or any A2A payload, so it cannot leak into task history, logs or another agent.

# %% a2a_coverage=["state:AUTH_REQUIRED", "part:data", "security"]
# Step 1: the agent pauses the task and hands out a challenge
consent = await connect(SECURE, auth=StaticCredentials({"oauth2": token}), streaming=False)
first = await send_text(consent, "Show my private appointments")
challenge = get_data_parts(first.task.status.message.parts)[0]["auth"]
print("state     :", state_name(first.state), "| task:", first.task_id)
print("message   :", first.task.status.message.parts[0].text)
print("challenge :", challenge)

# %%
# Step 2: the PERSON approves on the IdP's page; the client receives only a reference
ref = approve(challenge["user_code"], user="alice")
print("access_token_ref:", ref, "(opaque: it is not a JWT)")

# %% a2a_coverage=["state:AUTH_REQUIRED", "op:GetTask", "part:data"]
# Step 3: resume THE SAME task with the reference, then check the history for secrets
done = await send_text(
    consent, "approved", task_id=first.task_id, context_id=first.context_id,
    data={"auth": {"access_token_ref": ref}},
)
print("state:", state_name(done.state), "| same task id:", done.task_id == first.task_id)
print(done.text)
task = await consent.client.get_task(GetTaskRequest(id=first.task_id, history_length=20))
dump = MessageToJson(task)
print("any JWT in the task history/artifacts?", "eyJ" in dump)  # every JWT starts with 'eyJ'
print("final state:", state_name(task.status.state), "| messages kept in history:", len(task.history))
print("the history holds the reference, not a token:", ref in dump)

# %% [markdown]
# **What just happened**
# - The task kept its `task_id` through `WORKING → AUTH_REQUIRED → WORKING → COMPLETED`; a *resume* is just another `SendMessage` carrying that id, exactly like `INPUT_REQUIRED`.
# - The challenge carries a `device_code_ref`, **not** the device code: the secret half of the flow stays on the agent.
# - If the reference is bogus, expired, or already used, the task stays `AUTH_REQUIRED` and asks again. Try it: send `{"auth": {"access_token_ref": "tokref_nope"}}` against a fresh challenge.
# - > ⚠️ **Pitfall.** `AUTH_REQUIRED` is *not* a way to log the client in. If the caller has no valid transport credentials they get 401 and no task at all.

# %% [markdown]
# ## 7. `REJECTED`: a policy decision, with evidence
# Before doing anything, the agent asks a **decision model** (Nimble) to classify the request as `allow`, `needs_auth` or `reject`. A decision model suits this: it returns calibrated probabilities over a closed set, so the refusal carries evidence.

# %% a2a_coverage=["state:REJECTED", "decision", "part:data"]
# A destructive request: the screen says reject, the task ends REJECTED
rej = await send_text(consent, "Delete all calendars")
evidence = get_data_parts(rej.task.status.message.parts)[0]
print("state   :", state_name(rej.state))
print("reason  :", rej.task.status.message.parts[0].text)
print("evidence:", evidence)

# %% [markdown]
# | State | Who decided | Meaning | Retry? |
# |---|---|---|---|
# | `FAILED` | the agent, after trying | something broke (a bug, a timeout, a downstream outage) | maybe, later |
# | `CANCELED` | the **client** | the caller asked to stop (`CancelTask`) | n/a |
# | `REJECTED` | the agent, **before** doing the work | policy: "I will not do this" | no, change the request |
# | HTTP 401/403 | the web layer | no or insufficient credentials; **no task** | with credentials |

# %% [markdown]
# ## 8. Framework clients with auth
# ADK and Strands agents sit behind the **same** middleware, so calling them needs the same header. Each framework has its own hook.

# %% a2a_coverage=["security"]
# ADK: a request interceptor on RemoteA2aAgent adds the bearer token
from google.adk.a2a.agent.config import A2aRemoteAgentConfig
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from a2a_labs_common.a2a.remote import fetch_card_sync
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types


async def ask_adk(interceptors):
    agent = RemoteA2aAgent(
        name="calendar",
        agent_card=fetch_card_sync(ADK),  # a card OBJECT: ADK refuses to fetch non-https, non-loopback URLs (DEV-20)
        use_legacy=False,
        config=A2aRemoteAgentConfig(request_interceptors=interceptors),
    )
    svc = InMemorySessionService()
    runner = Runner(app_name="nb", agent=agent, session_service=svc)
    session = await svc.create_session(app_name="nb", user_id="u")
    msg = types.Content(role="user", parts=[types.Part(text=PUBLIC)])
    out = ""
    async for ev in runner.run_async(user_id="u", session_id=session.id, new_message=msg):
        out += "".join(p.text or "" for p in (ev.content.parts if ev.content else []))
    return out


print("with interceptor   :", (await ask_adk([bearer_interceptor(client_token)]))[:120])
print("without interceptor:", repr(await ask_adk([])), "(ADK logs the 401 and yields nothing)")

# %% [markdown]
# Strands' own `A2AAgent` cannot be imported next to a2a-sdk 1.x (DEV-19), but it takes a `ClientConfig(httpx_client=...)`. An `httpx.Auth` object is the portable equivalent: it fetches and caches a client-credentials token and works with **any** client that accepts an httpx client.

# %%
# Strands / generic: an httpx client whose Auth fetches and caches the token
auth = OAuthClientCredentialsAuth(f"{idp_url()}/token", "lab10-client", "lab10-secret", "agent:invoke")
strands_http = httpx.AsyncClient(timeout=60, auth=auth)
async with await connect(STRANDS, httpx_client=strands_http, streaming=False) as c:
    for _ in range(2):
        res = await send_text(c, PUBLIC)
print("state:", state_name(res.state), "| answer:", res.text[:100])
print("token fetches for 2 calls:", auth.fetches, "(cached)")

# %% [markdown]
# ## 9. An optional extension: `trace-summary/v1`
# An **extension** adds behaviour on top of the base protocol. It is *declared* on the card (`capabilities.extensions`), *activated* per request by the client in the `A2A-Extensions` header, and the server *echoes* what it activated. Without activation nothing changes.

# %% a2a_coverage=["extensions", "part:data"]
# The same question twice: without, then with the extension
base = endpoint(EXT)
print("declared:", card_json(base)["capabilities"]["extensions"])
off = rpc(base, "SendMessage", message_params("What is the capital of Portugal?"))
on = rpc(base, "SendMessage", message_params("What is the capital of Portugal?"), headers={"A2A-Extensions": TRACE_EXT})
for label, r in {"off": off, "on ": on}.items():
    arts = [a["artifactId"] for a in r.json()["result"]["task"]["artifacts"]]
    print(f"{label} artifacts={arts} echo={r.headers.get('a2a-extensions')}")
summary = next(a for a in on.json()["result"]["task"]["artifacts"] if a["artifactId"] == "trace-summary")
print(json.dumps(summary["parts"][0]["data"], indent=1)[:400])

# %% [markdown]
# **What just happened**
# - The extension is **additive and data-only**: an extra artifact tagged with the extension URI.
# - The response header `A2A-Extensions` lists what the server *honoured*, so a client can tell "activated" from "ignored". Unknown URIs are silently dropped, not errors (for optional extensions).

# %% [markdown]
# ## 10. A required extension: `cost-budget/v1`
# `required: true` on the card means the agent **cannot do the job correctly without it**. A request that does not activate it is refused, never served in a degraded mode: otherwise a caller might think a spending cap was applied when it was not.

# %% a2a_coverage=["extensions", "errors", "state:REJECTED"]
# Missing -> typed error; present -> work; too expensive -> REJECTED
from a2a.utils.errors import ExtensionSupportRequiredError

async with await connect(REQUIRED, streaming=False) as c:
    try:
        await send_text(c, "Summarize: A2A lets agents talk.")
    except ExtensionSupportRequiredError as exc:
        print("missing   :", type(exc).__name__, "| JSON-RPC code -32008 |", exc)
async with await connect(REQUIRED, streaming=False, extensions=[BUDGET_EXT]) as c:
    ok = await send_text(c, "Summarize: A2A lets agents talk.", metadata={BUDGET_EXT: {"max_usd": 0.05}})
    print("present   :", state_name(ok.state), get_data_parts(ok.task.artifacts[1].parts)[0])
    big = await send_text(c, "Write a full report: A2A", metadata={BUDGET_EXT: {"max_usd": 0.05}})
    print("over cap  :", state_name(big.state), get_data_parts(big.task.status.message.parts)[0])

# %% [markdown]
# **What just happened**
# - The error is a first-class one, `ExtensionSupportRequiredError` (-32008, reason `EXTENSION_SUPPORT_REQUIRED`); the SDK client re-raises the same typed exception.
# - The extension's request data travels in `message.metadata[<extension URI>]`: the **URI is the namespace**, so two extensions can never collide.
# - Refusing to pay is `REJECTED`, not `FAILED`: the agent made a deliberate decision.

# %% [markdown]
# ## 11. Designing your own extension
# An extension is only a **contract** between two parties. Declare it like this:

# %%
# The declaration is plain data; print it as the card will show it
from a2a_labs_common.a2a.extensions import extension

mine = extension(
    "https://example.com/ext/audit-trail/v1",
    "Adds an `audit` artifact listing every tool call.",
    required=False,
    params={"detail": "summary|full"},
)
print(MessageToDict(mine))

# %% [markdown]
# | Rule | Why |
# |---|---|
# | **Version in the URI** (`/v1`) | a breaking change is a *new URI* (`/v2`); agents can support both, clients pick |
# | **URI = metadata key** | `message.metadata["…/audit-trail/v1"]` cannot collide with another extension |
# | **Optional unless the job is wrong without it** | `required` makes every old client fail; use it for things like spending caps, not for nice-to-haves |
# | **Never fall back silently** | if a required extension is missing, fail loudly (-32008); do not guess |
# | **Declare `params` on the card** | clients learn defaults and limits without a request |
#
# > 💡 **Tip.** Two versions side by side is normal: declare both URIs, activate whichever the client asks for, and share the implementation.

# %% [markdown]
# ## 12. Version negotiation and a two-interface card
# The card lists **interfaces**, each with a `protocolVersion`. A v1.0 client takes the first it understands. Every request carries `A2A-Version`; a server refuses what it does not speak.

# %% a2a_coverage=["versioning", "errors", "compat:v03"]
# What the card offers, then four requests
base = endpoint(EXT)
for i in card_json(base)["supportedInterfaces"]:
    print(f"interface {i['protocolBinding']:8} v{i['protocolVersion']:4} {i['url']}")
body = message_params("What is the capital of Portugal?")
print("1.0           :", "result" in rpc(base, "SendMessage", body).json())
print("2.0 (future)  :", rpc(base, "SendMessage", body, version="2.0").json()["error"]["message"])
print("no header     :", rpc(base, "SendMessage", body, version=None).json()["error"]["message"])
q = httpx.post(f"{base}/?A2A-Version=1.0", json={"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": body}).json()
print("?A2A-Version  :", q["error"]["message"], "(the query form is not honoured, SDK issue #1344)")

# %%
# An OLD client: v0.3 method name, lower-case roles and `kind` fields, sent to the /v03 mount
legacy = {"message": {"messageId": "m-1", "role": "user", "kind": "message", "parts": [{"kind": "text", "text": "capital of Portugal"}]}}
old = httpx.post(f"{base}/v03", json={"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": legacy}).json()["result"]
print("v0.3 reply:", old["kind"], old["status"]["state"], "|", old["artifacts"][0]["parts"][0]["text"])

# %% [markdown]
# **What just happened**
# - No `A2A-Version` header means **0.3** to this SDK, so a v1.0 method name without it is refused with `VersionNotSupportedError` (-32009). Always send the header.
# - The v0.3 shapes (`message/send`, `kind`, `completed`) are served by the SDK's compat layer, here mounted at a second URL `/v03` that the card advertises as the `0.3` interface.
# - A compat-only mount is not possible: `/v03` also answers v1.0 requests, because both mounts share one handler. The two-interface card is guidance for clients, not isolation.

# %% [markdown]
# ## 13. Authenticated push
# A push config can carry `authentication {scheme, credentials}`: the agent sends them back in the `Authorization` header of every notification, so the webhook can tell a real agent from a stranger. (Lab 08 goes further with signed JWTs.)

# %% a2a_coverage=["op:CreatePushConfig", "security"]
# Two requests, one with the right credentials and one with the wrong ones
receiver = endpoint("webhook-receiver")
hook = "http://webhook-receiver:9100/webhooks/a2a/lab10"  # the address the AGENT uses
async with httpx.AsyncClient() as h:
    await h.delete(f"{receiver}/notifications")
    for tok in ("nb-good", "nb-bad"):
        await h.post(f"{receiver}/expectations", json={"token": tok, "bearer": "s3cret"})
for tok, creds in (("nb-good", "s3cret"), ("nb-bad", "guess")):
    cfg = TaskPushNotificationConfig(url=hook, token=tok)
    cfg.authentication.CopyFrom(AuthenticationInfo(scheme="Bearer", credentials=creds))
    async with await connect(SECURE, auth=StaticCredentials({"oauth2": token}), streaming=False, push_config=cfg) as c:
        await send_text(c, PUBLIC)
await asyncio.sleep(2)
async with httpx.AsyncClient() as h:
    rows = (await h.get(f"{receiver}/notifications", params={"channel": "lab10"})).json()
print("accepted notifications:", [(r["state"], r["bearer_valid"]) for r in rows])
print("(the 'nb-bad' ones were answered 401 by the receiver and are not stored)")

# %% [markdown]
# ## 14. (Optional) Tenants
# `AgentInterface.tenant` lets one deployment serve many customers on one URL. The client wraps its transport with the tenant; the server sees `RequestContext.tenant`. The SDK **isolates nothing by itself**: tasks are scoped to an *owner* chosen by an `owner_resolver`. This agent uses `tenant/user`.

# %% a2a_coverage=["op:ListTasks", "security"]
# Same principal, two tenants: each sees only its own tasks
async def as_tenant(name):
    c = AgentCard()
    c.CopyFrom((await connect(SECURE)).card)
    c.supported_interfaces[0].tenant = name
    return await connect_with_card(c, auth=StaticCredentials({"oauth2": token}), streaming=False)


acme, globex = await as_tenant("acme"), await as_tenant("globex")
a, g = await send_text(acme, PUBLIC), await send_text(globex, PUBLIC)
acme_ids = {t.id for t in (await acme.client.list_tasks(ListTasksRequest())).tasks}
globex_ids = {t.id for t in (await globex.client.list_tasks(ListTasksRequest())).tasks}
print("acme   lists its own task:", a.task_id in acme_ids, "| lists globex's:", g.task_id in acme_ids)
print("globex lists its own task:", g.task_id in globex_ids, "| lists acme's :", a.task_id in globex_ids)
try:
    await globex.client.get_task(GetTaskRequest(id=a.task_id))
except Exception as exc:
    print("globex reads acme's task ->", type(exc).__name__)

# %% [markdown]
# ## 15. See it in Grafana

# %%
# One trace: an authenticated call, a consent flow and a rejection
with display.cell_span("secure flows") as span:
    async with await connect(SECURE, auth=StaticCredentials({"oauth2": token}), streaming=False) as c:
        await send_text(c, PUBLIC)
        await send_text(c, "Delete all calendars")
    try:
        await send_text(await connect(SECURE, streaming=False), PUBLIC)
    except Exception as exc:
        print("anonymous ->", type(exc).__name__)
tel.flush()
display.trace_link(span)

# %% [markdown]
# Try in Grafana → Explore:
# - **TraceQL:** `{ resource.service.name = "lab10-secure-agent" && span.http.status_code = 401 }` finds the refused anonymous calls.
# - **TraceQL:** `{ name = "decision.decide" && span.decision.preflight.choice = "reject" }` finds REJECTED requests and the probability that drove them.
# - **Metrics:** `a2a.task.transitions` with `to="REJECTED"` or `to="AUTH_REQUIRED"`.
# - **LogQL:** `{service_name="lab10-secure-agent"} |= "preflight"` shows every screen verdict with its probabilities.

# %% [markdown]
# ## 16. Exercises
# - 🟢 **Easy:** add a skill `calendar.export` that needs the scope `calendar:export`, and add it to the card with its own requirement.
# - 🟡 **Medium:** implement `cost-budget/v2` where `max_usd` becomes `{"amount": 0.05, "currency": "USD"}`, and support **both** URIs on one agent.
# - 🔴 **Stretch:** protect two agents with mTLS: declare a `MutualTlsSecurityScheme` on the card and require client certificates on the server.

# %% tags=["skip-ci"] jupyter={"source_hidden": true}
# Hidden solution (easy): in cards.py add skill("calendar.export", ...) with
#   security_requirements.append(requirement(oauth2=["calendar:export"]))
# and a scope `calendar:export` to the IdP client's `scopes`; check it in CalendarExecutor with `caller(context)`.

# %% [markdown]
# ## 17. Recap
#
# | | Transport auth | In-task auth | Extension | Version |
# |---|---|---|---|---|
# | Where | HTTP headers, middleware | `AUTH_REQUIRED` task state | `A2A-Extensions` header, metadata | `A2A-Version` header |
# | Failure | 401/403, **no task** | task waits for the client | -32008 if required and missing | -32009 |
# | Declared on card | `securitySchemes` + requirements | skill requirement | `capabilities.extensions` | `supportedInterfaces[].protocolVersion` |
#
# **Pitfalls:** putting a token in a message part; treating `AUTH_REQUIRED` as a login; marking an extension `required` for convenience; omitting `A2A-Version` (it means 0.3); assuming a tenant isolates tasks without an `owner_resolver`.
#
# **Next:** Lab 09 builds hierarchical squads on everything so far.

# %% [markdown]
# ## Cleanup

# %%
# Close clients and flush telemetry
await consent.aclose()
await acme.aclose()
await globex.aclose()
await strands_http.aclose()
tel.flush()
