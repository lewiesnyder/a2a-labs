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
# # Lab 06 · Registry and discovery (pattern 5)
#
# **Goal:** find agents you did not write, and **delegate only to the ones you can prove are who they say they are**.
# **Time:** about 4 minutes · **Prerequisites:** Labs 01 and 05 · `make up LAB=lab06` (it also generates the dev keys)
#
# **What you'll learn**
# - Three discovery strategies and what each one trusts: **well-known URI**, **curated registry**, **direct configuration**.
# - How an Agent Card is **signed** (JWS over the JCS-canonical card) and why the verifier uses a **trust store**, not the agent's own JWKS.
# - Four ways a rogue agent fails verification, and why a *validly signed* card can still be refused.
# - A delegator that re-verifies everything itself, picks among verified candidates with a decision model, and connects with the **verified card object**.
#
# Spec: [A2A specification, Agent Card signing](https://a2a-protocol.org/latest/specification/) · RFC 7515 (JWS) · RFC 8785 (JCS)

# %% [markdown]
# ## 1. Architecture and the three strategies
#
# ```mermaid
# flowchart LR
#   D[Delegator :10605] -->|"GET /agents?skill=translate"| R[Registry :10601]
#   R -->|"fetch + verify"| A[Translator A :10602<br/>Strands]
#   R --> B[Translator B :10603<br/>ADK]
#   R --> X[Rogue :10604]
#   D -->|"re-verify, then A2A"| B
#   T[(trust store)] -.-> R & D
# ```
#
# | | Well-known URI | Curated registry | Direct configuration |
# |---|---|---|---|
# | You trust | the DNS name + TLS of one host | the registry operator **and** your own re-check | the file you configured |
# | Freshness | always current | as of the last refresh | as stale as the file |
# | Scale | one fetch per agent you already know | search across many agents | a handful |
# | Fails by | a compromised or spoofed host | a registry that vouches for the wrong agent | a card that is never updated |
#
# A signature changes the first two rows: it lets you trust the **content** of a card, wherever it came from.

# %% tags=["parameters"]
QUICK_DEMO = False

# %% [markdown]
# ## 2. Setup

# %%
# Initialise telemetry, load the trust store and wait for the services
import base64
import json
import os
import shutil
import subprocess
import uuid

import httpx
from google.protobuf.json_format import MessageToDict

from a2a_labs_common import display
from a2a_labs_common.a2a.client import connect, send_text, wait_for_cards
from a2a_labs_common.a2a.states import state_name
from a2a_labs_common.a2a.trust import CardVerifier, card_from_json, fetch_raw_card, protected_header
from a2a_labs_common.config import endpoint, find_config_dir
from a2a_labs_common.telemetry import init_telemetry

from lab06_registry import keys

tel = init_telemetry("notebook-lab06", framework="notebook", lab_id="lab06")
display.show_models()
A, B, ROGUE, REG, DEL = "lab06-agent-a", "lab06-agent-b", "lab06-agent-rogue", "lab06-registry", "lab06-delegator"
await wait_for_cards([A, B, ROGUE, DEL], timeout=180)  # prints `make up LAB=lab06` if down
REG_URL, ROGUE_URL = endpoint(REG), endpoint(ROGUE)
store = keys.load_trust_store()
verifier = CardVerifier(store)
TASK = "Translate 'good morning, how are you?' to Portuguese and Japanese"


async def api(method, url, **kw):
    async with httpx.AsyncClient(timeout=30) as http:
        return (await http.request(method, url, **kw)).json()


display.table([{"kid": k.kid, "may speak for": ", ".join(k.allowed_names), "only from host": ", ".join(k.allowed_hosts)} for k in store.trusted_keys], "trust store (public keys only)")

# %% [markdown]
# ## 3. Well-known discovery: fetch a card and read its signature
# `GET /.well-known/agent-card.json` is plain HTTP. The card carries a `signatures` list; each entry is a JWS whose payload is **not** in the card, because the payload *is* the card.

# %% a2a_coverage=["pattern:5", "op:Discovery"]
# Fetch Translator A's card raw and decode the protected header of its signature
raw = httpx.get(f"{endpoint(A)}/.well-known/agent-card.json").json()
sig = raw["signatures"][0]
header = json.loads(base64.urlsafe_b64decode(sig["protected"] + "=" * (-len(sig["protected"]) % 4)))
print("card name :", raw["name"], "| version", raw["version"])
print("signature :", {k: (v[:24] + "…") if isinstance(v, str) and len(v) > 24 else v for k, v in sig.items()})
print("protected header:", header)

# %% [markdown]
# **What just happened**
# - `alg=ES256` is an ECDSA signature; `kid` names the key *the signer claims to have used*; `iat` lets a verifier reject stale cards.
# - The `kid` is a **claim**. Nothing in the card proves that key is one you trust. That is the trust store's job.

# %% [markdown]
# ## 4. Sign and verify by hand
# Canonicalise the card (RFC 8785, without `signatures`), look at the bytes that were signed, verify, then change one character.

# %% a2a_coverage=["pattern:5"]
# Verify with the trust store, then tamper with one character and verify again
from a2a.utils.signing import canonicalize

card, base = await fetch_raw_card(A)
unsigned = MessageToDict(card)
unsigned.pop("signatures")
canon = canonicalize(unsigned)
print("canonical bytes (first 150):", canon[:150], "…\n")
good = verifier.verify(card, base)
print("untouched :", "TRUSTED" if good.ok else "REFUSED", good.checks)
card.description = card.description.replace("Spanish", "Spanich")  # one character
bad = verifier.verify(card, base)
print("one char  :", "TRUSTED" if bad.ok else "REFUSED", "→", bad.reasons)

# %% [markdown]
# **What just happened**
# - JCS makes the JSON byte-for-byte deterministic (sorted keys, normalised numbers), so signer and verifier hash the same bytes.
# - The verifier took the public key from the **trust store**, selected by `kid`. It never followed a `jku` URL or used the agent's own `/.well-known/jwks.json` (which exists, for completeness): a host that controls both the card and the key proves nothing.

# %% [markdown]
# ## 5. The registry: verified by default
# The registry fetched all three seed agents at startup and verified each one on the way in.

# %% a2a_coverage=["pattern:5"]
# What a normal search returns, and what is hiding behind include_unverified
shown = await api("GET", f"{REG_URL}/agents", params={"skill": "translate"})
everything = await api("GET", f"{REG_URL}/agents", params={"include_unverified": True})
display.table([{"agent": e["card"]["name"], "kid": e["verification_report"]["kid"], "verified": e["verified"], "url": e["url"]} for e in shown], "default search")
hidden = [e for e in everything if not e["verified"]]
display.table([{"claims to be": e["card"]["name"], "verified": e["verified"], "why": "; ".join(e["verification_report"]["reasons"])[:90]} for e in hidden], "quarantined (include_unverified=true)")

# %% [markdown]
# ## 6. The rogue gallery
# The rogue imitates Translator A in four ways. Switch its mode, ask the registry to re-register it, and see which check fails first.

# %% a2a_coverage=["pattern:5", "state:FAILED"]
# Cycle through the four modes; each re-registration is verified from scratch
rows = []
for mode in ("unsigned", "untrusted_key", "tampered", "name_spoof"):
    await api("POST", f"{ROGUE_URL}/_rogue/mode", json={"mode": mode})
    e = await api("POST", f"{REG_URL}/agents", json={"url": ROGUE})
    rep = e["verification_report"]
    first = next(c for c, ok in rep["checks"].items() if not ok)
    rows.append({"mode": mode, "card name": e["card"]["name"], "kid": rep["kid"], "first failing check": first, "reason": rep["reasons"][0][:70]})
display.table(rows)
await api("POST", f"{ROGUE_URL}/_rogue/mode", json={"mode": "name_spoof"})  # the compose default

# %% [markdown]
# **What just happened**
# - `unsigned`: nothing to check. `tampered`: it copied Translator A's *real* signed card and pointed the endpoint at itself; the signature no longer matches the content.
# - `untrusted_key` and `name_spoof`: perfectly valid signatures, made with a key that is **not in your trust store**. `name_spoof` is the dangerous one: it claims the name "Translator A" and the JWS verifies mathematically. A valid signature proves *someone* signed; the trust store says *who counts*.
# - Even a trusted key is **bound**: `lab06-b` may only sign "Translator B" from `lab06-agent-b` (unit tests cover that case).

# %% [markdown]
# ## 7. The delegator: search, re-verify, select, delegate
# "Translate … to Portuguese and Japanese": only one translator knows Japanese. The delegator never contacts the rogue.

# %% a2a_coverage=["pattern:5", "decision", "op:SendMessage"]
# Ask the delegator over A2A, then read its state: candidates, verdicts and the Nimble choice
before = (await api("GET", f"{ROGUE_URL}/_rogue/requests"))["rpc_calls"]
ctx = str(uuid.uuid4())
async with await connect(DEL, streaming=False) as dc:
    answer = await send_text(dc, TASK, context_id=ctx)
print(state_name(answer.state), "|", answer.text)
v = (await api("GET", f"{endpoint(DEL)}/debug/state/{ctx}"))["values"]
display.table([{"candidate": c["name"], "re-verified": c["report"]["ok"]} for c in v["verified"] + v["rejected"]], f"source: {v['source']}")
print("\nNimble choice over the verified candidates:")
for name, p in sorted((v["decision"]["probabilities"] or {}).items(), key=lambda kv: -kv[1]):
    print(f"  {name:14} {'█' * round(p * 30):<30} {p:.2f}")
print("\nrogue was asked to do something:", (await api("GET", f"{ROGUE_URL}/_rogue/requests"))["rpc_calls"] - before, "times")

# %% [markdown]
# **What just happened**
# - The registry only returns verified cards, and the delegator **checked them again** anyway: the registry is a convenience, not an authority.
# - The `choice` criteria were the cards' own descriptions and skills ("…Japanese, Korean…"), so a capability the card does not advertise cannot win.
# - The connection was built from the *verified card object*, not by fetching the URL again: nothing can change between the check and the use.

# %% [markdown]
# ## 8. Direct configuration: an injected trusted card
# Same delegation with the registry switched off: the card comes from a file you ship with the deployment. It is still verified.

# %% a2a_coverage=["pattern:5"]
# Run the delegator in-process against a dead registry URL, with the injected card file
from langchain_core.messages import HumanMessage

from lab06_registry.agents.delegator import Delegator

injected = keys.registry_dir() / "injected" / "translator-b.json"
local = Delegator(store, registry_url="http://127.0.0.1:9", injected_cards=[str(injected)])
out = await local.build().ainvoke({"messages": [HumanMessage(TASK)]})
print("source:", out["source"], "| chosen:", out["chosen"], "| verified:", [x["name"] for x in out["verified"]])
print(out["result"])

# %% [markdown]
# **What just happened**
# - No registry, no discovery traffic: the trade is freshness. If Translator B's card changes (new skills, new URL), this file does not, until you redeploy.

# %% [markdown]
# ## 9. Handing a verified card to each framework
# Verify first, then give the framework the **card object**, so it cannot go and fetch something else.

# %% a2a_coverage=["pattern:5", "pattern:subagent"]
# a2a-sdk, ADK and Strands, in that order of how directly they accept a verified card
from a2a_labs_common.a2a.client import bridge_card_urls, connect_with_card
from a2a_labs_common.a2a.strands_tools import make_a2a_client_tool

vcard, vbase = await fetch_raw_card(B)
assert verifier.verify(vcard, vbase).ok, "never construct anything from an unverified card"
bridge_card_urls(vcard)  # after verification: host runs rewrite docker names to localhost

# 1) a2a-sdk: create the client from the verified card object
async with await connect_with_card(vcard, streaming=False) as conn:
    print("sdk   :", (await send_text(conn, "Translate 'good morning' to Japanese")).text[:70].replace("\n", " "))

# 2) ADK: RemoteA2aAgent accepts an AgentCard OBJECT (or a URL or a file path); pass the object
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent

remote = RemoteA2aAgent(name="translator_b", agent_card=vcard, use_legacy=False)
print("adk   :", type(remote).__name__, "built from a card object, nothing to fetch")

# 3) Strands: its A2AAgent fetches the card itself, so the safe order is verify, THEN allow that
#    one URL. Our stand-in tool takes an allowlist of exactly the verified endpoints.
tool = make_a2a_client_tool(name="call_translator", description="Call the verified translator.", allowed_endpoints={vcard.supported_interfaces[0].url: None})
print("strands:", tool.tool_name, "allows only", vcard.supported_interfaces[0].url)

# %% [markdown]
# **What just happened**
# - The a2a-sdk and ADK take the object directly. Strands' own `A2AAgent` re-fetches the card by URL (and cannot import on a2a-sdk 1.x anyway, DEV-19), so the safe pattern is "verify, then allowlist that URL".
# - Pin an expected card hash if you want to catch a card that changes *between* verification and the Strands fetch.

# %% [markdown]
# ## 10. Extended card (a preview of Lab 10)
# Translator B advertises an extended card. Anyone can read the public card; the private skill needs a token.

# %% a2a_coverage=["pattern:5", "op:GetExtendedAgentCard"]
# Ask for the extended card without, then with, the bearer token
from a2a.types import GetExtendedAgentCardRequest

print("public skills  :", [s["id"] for s in raw_b["skills"]] if (raw_b := httpx.get(f"{endpoint(B)}/.well-known/agent-card.json").json()) else None)
async with await connect(B, streaming=False, headers={"Authorization": "Bearer lab06-b-token"}) as authed:
    extended = await authed.client.get_extended_agent_card(GetExtendedAgentCardRequest())
print("extended skills:", [s.id for s in extended.skills])
try:
    async with await connect(B, streaming=False) as anon:
        await anon.client.get_extended_agent_card(GetExtendedAgentCardRequest())
except Exception as exc:  # no token: the guard answers 401
    print("without a token:", type(exc).__name__)

# %% [markdown]
# **What just happened**
# - Note what is *not* signed: the extended card. It is fetched over an authenticated channel, so the transport authenticates it instead of a signature. Lab 10 builds the real auth.

# %% [markdown]
# ## 11. Refresh and ETag
# Bump Translator B's card version, restart it (it re-signs at startup) and refresh it in the registry.

# %% a2a_coverage=["pattern:5"]
# Needs the docker CLI; otherwise the cell prints what to run
ROOT = find_config_dir().parent


def recreate_b(version):
    env = {**os.environ, "AGENT_B_VERSION": version}
    return subprocess.run(["docker", "compose", "--profile", "lab06", "up", "-d", "--no-deps", "--force-recreate", B], cwd=ROOT, env=env, capture_output=True, text=True)


entry_b = next(e for e in await api("GET", f"{REG_URL}/agents", params={"skill": "translate"}) if e["card"]["name"] == "Translator B")
print("registry has version", entry_b["card"]["version"], "| etag", entry_b["etag"])
if shutil.which("docker") is None:
    print("docker CLI not found. On the host: AGENT_B_VERSION=1.1.0 docker compose --profile lab06 up -d --no-deps --force-recreate lab06-agent-b")
else:
    try:
        recreate_b("1.1.0")
        await wait_for_cards([B], timeout=120)
        fresh = await api("POST", f"{REG_URL}/agents/{entry_b['id']}/refresh")
        print("after refresh  :", fresh["card"]["version"], "| etag", fresh["etag"], "| changed", fresh["changed"], "| verified", fresh["verified"])
        print("again          : changed =", (await api("POST", f"{REG_URL}/agents/{entry_b['id']}/refresh"))["changed"])
    finally:
        recreate_b("1.0.0")
        await wait_for_cards([B], timeout=120)
        await api("POST", f"{REG_URL}/agents/{entry_b['id']}/refresh")

# %% [markdown]
# **What just happened**
# - The refresh sent `If-None-Match`; the agent served a new card, the registry replaced the old one and **re-verified it** (a new version is a new signature). The second refresh found nothing changed.
# - A background loop does the same every five minutes and marks an unreachable agent unhealthy, which hides it from searches.

# %% [markdown]
# ## 12. See it in Grafana

# %%
# One cell, one trace: registry search, client-side verification and the delegation
with display.cell_span("verifying delegation") as span:
    async with await connect(DEL, streaming=False) as dc:
        await send_text(dc, TASK, context_id=str(uuid.uuid4()))
tel.flush()
display.trace_link(span)

# %% [markdown]
# Try in Grafana → Explore:
# - **TraceQL:** `{ name = "registry.verify_card" && span.a2a.card.signature.valid = false }`: the rogue and the tampered card, with `a2a.card.failed_check` telling you which check tripped.
# - **TraceQL:** `{ name = "delegator.select" }`, then its child `decision.decide` for the choice probabilities.
# - Look at the rogue service: its `a2a.server.execute` spans never appear for the delegator's trace.

# %% [markdown]
# ## 13. Exercises
# - 🟢 **Easy:** add wildcard support to `allowed_hosts` (`*.corp.example`) in `CardVerifier`, with a test.
# - 🟡 **Medium:** make the registry sign its own search responses so a delegator can detect a tampered reply.
# - 🔴 **Stretch:** expose the registry as an MCP server with a `find_agent` tool and use it from Lab 03's consumer.

# %% tags=["skip-ci"] jupyter={"source_hidden": true}
# Hidden solution (easy), in CardVerifier._verify:
#     import fnmatch
#     checks["host_bound"] = any(fnmatch.fnmatch(fetched, pat) for pat in key.allowed_hosts)
# and do the same for the interface hosts.

# %% [markdown]
# ## 14. Recap
#
# | Pattern 5 | |
# |---|---|
# | **Use it when** | You delegate to agents you did not deploy yourself, or the set of agents changes |
# | **Not when** | Every agent is yours, on your network, behind your own auth (a static list is fine) |
# | Key mechanics | Signed card → **trust store** (kid, allowed names, allowed hosts, freshness) → verify → connect with the verified object |
# | Rule | Verify before you construct a client; re-verify what a registry tells you; never take keys from the card's own host |
#
# **Pitfalls:** verifying after you already connected; trusting a valid signature without checking *whose* key it is; rewriting a card's URLs before verifying it (that breaks the signature, so verify first, bridge after); a registry that returns a URL instead of a card.
#
# **Next:** Lab 07 chains three agents from three frameworks.

# %% [markdown]
# ## Cleanup

# %%
# Flush telemetry
tel.flush()
