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
# # Lab 09 · AGP hierarchical squads (pattern 8, experimental)
#
# > ⚠️ **Experimental, not core A2A.** The Agent Gateway Protocol (AGP) is a *proposed routing extension* published as a sample in [`a2a-samples/extensions/agp`](https://github.com/a2aproject/a2a-samples/tree/main/extensions/agp) (V1). **No SDK supports it.** Everything that implements AGP in this lab is `# CUSTOM` code following that document, layered on ordinary A2A messages and the extension mechanism. It is unrelated to AGNTCY's gRPC "Agent Gateway Protocol".
#
# **Goal:** a client talks to **one gateway**; squads stay autonomous; requests are routed by *capability*, *cost* and *policy*, like BGP routes packets.
# **Time:** about 5 minutes (the failover steps wait for route expiry) · **Prerequisites:** Labs 00, 05, 06 and 10 · `make up LAB=lab09`
#
# **What you'll learn**
# - Why hierarchy helps at scale, and the BGP analogy.
# - An A2A **extension** end to end: card declaration, `A2A-Extensions` activation, payloads in data parts, extension-specific errors.
# - A gateway's **AGP table**, built from capability announcements, and lowest-cost **policy** routing.
# - A **PII check** by a decision model, **failover** and **stale** routes.
# - How a gateway serves clients that know nothing about AGP.

# %% [markdown]
# ## 1. Architecture
#
# ```mermaid
# flowchart TD
#   C[Client] -->|A2A + A2A-Extensions: agp| R[lab09-root-gateway :10901]
#   R --> F[lab09-finance-gateway :10902]
#   R --> T[lab09-travel-gateway :10903]
#   F --> P[payments · Strands :10911]
#   F --> X[fx · LangGraph :10912]
#   F --> K[compliance · ADK :10913]
#   T --> FL[flights · ADK :10914]
#   T --> H[hotels · Strands :10915]
#   T --> V[visa · LangGraph :10916]
#   P -.announce.-> F
#   X -.announce.-> F
#   H -.announce.-> T
#   F -.announce cost+1.-> R
#   T -.announce cost+1.-> R
# ```
#
# | BGP | AGP |
# |---|---|
# | Autonomous system | **Squad** (a team of agents behind a gateway) |
# | Route announcement | **Capability announcement** `{capability, version, cost, policy}` |
# | Path attributes (AS path, MED, communities) | `path`, `cost`, `policy` |
# | Routing table | **AGP table**: capability → routes |
# | Best-path selection | lowest cost among **policy-compliant** routes |
# | Route withdrawal / hold timer | route **TTL**: no re-announcement, no route |
#
# **Who owns the task?** Every hop owns its own task: a gateway mirrors the downstream task's artifacts and final state into *its* task and adds a route artifact naming the downstream task id.

# %% tags=["parameters"]
QUICK_DEMO = False

# %% [markdown]
# ## 2. Setup
# The squads announce themselves at startup and every few seconds. Give the tables a moment to converge.

# %%
# Telemetry, helpers, wait for the gateways, then wait for the root table to converge
import asyncio
import json
import shutil
import subprocess
import time

import httpx
from a2a.helpers import get_data_parts
from a2a.types import GetTaskRequest

from a2a_labs_common import display
from a2a_labs_common.a2a.client import connect, send_text, wait_for_cards
from a2a_labs_common.a2a.states import state_name
from a2a_labs_common.agp import (
    AGP_URI,
    AgpPolicyViolationError,
    AgpRouteNotFoundError,
    AgpTableStaleError,
)
from a2a_labs_common.config import endpoint, find_config_dir
from a2a_labs_common.telemetry import init_telemetry

from lab09_agp.clients import agp_client, control, route_of, send_intent, table

tel = init_telemetry("notebook-lab09", framework="notebook", lab_id="lab09")
display.show_models()
ROOT, FIN, TRAVEL = "lab09-root-gateway", "lab09-finance-gateway", "lab09-travel-gateway"
SQUADS = [f"lab09-squad-{s}" for s in ("payments", "fx", "compliance", "flights", "hotels", "visa")]
await wait_for_cards([ROOT, FIN, TRAVEL, *SQUADS], timeout=180)  # prints `make up LAB=lab09` if down


def routes(gateway=ROOT):
    return [(cap, r) for cap, rs in table(gateway)["capabilities"].items() for r in rs]


for _ in range(60):  # up to a minute
    if len(routes()) >= 7:
        break
    await asyncio.sleep(1)
print("root routes:", len(routes()), "| table ttl:", table(ROOT)["ttl_s"], "s")


def show_table(gateway=ROOT):
    display.table(
        [
            {
                "capability": cap,
                "path": " → ".join(r["path"]),
                "cost": r["cost"],
                "policy": json.dumps(r["policy"]),
                "age s": r["age_s"],
                "stale": r["stale"],
            }
            for cap, r in routes(gateway)
        ],
        title=f"AGP table of {gateway}",
    )


# %% [markdown]
# ## 3. Concept: capabilities, intents, policy
# A client does not name an agent. It states an **Intent**: *what* it wants (`target_capability`), the data (`payload`) and **constraints** (`policy_constraints`, e.g. `region: "EU"`). The gateway picks the cheapest route whose announced **policy** satisfies the constraints.
#
# AGP's wire format is a convention on top of A2A (the spec defines the data, not the transport): an ordinary `SendMessage` with `A2A-Extensions: <AGP URI>` and one **data part** `{"agp": {...}}`. Announcements and intents use the same shape.

# %% [markdown]
# ## 4. Inspect the cards
# A gateway *declares* the extension (`params.agent_role = gateway`); squads are ordinary agents and know nothing about AGP.

# %% a2a_coverage=["extensions", "op:Discovery", "pattern:8"]
# Which cards carry the AGP extension?
for name in (ROOT, FIN, TRAVEL, "lab09-squad-fx", "lab09-squad-visa"):
    card = httpx.get(f"{endpoint(name)}/.well-known/agent-card.json").json()
    exts = card["capabilities"].get("extensions", [])
    print(f"{name:24} extensions: {[(e['uri'].rsplit('/', 1)[-1], e.get('params')) for e in exts] or 'none'}")

# %% [markdown]
# ## 5. Inspect the tables
# The finance gateway learned its squads directly. The root learned the same capabilities **from the gateways**, one hop further away: every re-announcement adds `+1` to the cost and puts the gateway at the front of the path.

# %% a2a_coverage=["pattern:8"]
# The root's table and the finance gateway's table
show_table(ROOT)
show_table(FIN)

# %% [markdown]
# **What just happened**
# - `finance.fx.quote` has **two routes** at the root: through the finance gateway (the FX squad, cost 1 + 1 = **2**) and through the travel gateway (the hotels squad's deliberately worse alternative, cost 4 + 1 = **5**).
# - The finance gateway's own entries have `is_gateway = False`: its next hop is a squad (plain A2A text), not another gateway (an AGP Intent).

# %% [markdown]
# ## 6. Send an Intent
# One request at the root. The gateway checks the payload for PII, finds the route, forwards, and answers **in its own task** with the downstream artifacts plus an `agp-route` artifact.

# %% a2a_coverage=["pattern:8", "extensions", "op:SendMessage", "part:data", "decision"]
# finance.fx.quote for the EU region
gw = await agp_client(ROOT)
res = await send_intent(gw, "finance.fx.quote", {"amount": 100, "from": "USD", "to": "EUR"}, {"region": "EU"})
route = route_of(res)
print("state :", state_name(res.state), "| answer:", res.text)
print("path  :", " → ".join(route["path"]), "| cost:", route["cost"], "| pii:", route["pii_detected"], route["pii_probability"])
print("lost  :", [(a["path"][0], a["cost"], a["why"]) for a in route["alternatives"]])

# %% [markdown]
# **What just happened**
# - The second route lost on cost (`higher cost`). The loser list is in the span `agp.route` too (Grafana, step 13).
# - The client never learned a squad's address: it only knows the root.

# %% [markdown]
# ## 7. Policy routing: a violation
# `finance.compliance.check` is announced for the **EU only**. Ask for `region=US`: routes exist, none is compliant, so the gateway answers with AGP's own JSON-RPC error `-32201 AGP_POLICY_VIOLATION`, not a task.

# %% a2a_coverage=["pattern:8", "errors", "extensions"]
# EU works, US is a policy violation, an unknown capability is route-not-found
for region in ("EU", "US"):
    try:
        ok = await send_intent(gw, "finance.compliance.check", {"customer": "ACME"}, {"region": region})
        print(f"region={region}:", state_name(ok.state), "|", ok.text)
    except AgpPolicyViolationError as exc:
        print(f"region={region}:", type(exc).__name__, "|", exc)
try:
    await send_intent(gw, "hr.onboard.new_hire", {"employee": "Charlie"})
except AgpRouteNotFoundError as exc:
    print("unknown capability:", type(exc).__name__, "|", exc)

# %% [markdown]
# > 🔍 **Under the hood.** The SDK has no public way to add error codes, so `a2a_labs_common.agp` registers `-32200/-32201/-32202` in the SDK's error tables (DEV-63). The reason and the rejected candidates travel in the error's `ErrorInfo` metadata.

# %% [markdown]
# ## 8. A PII check by the decision model
# Before routing, the gateway asks Nimble a `noul` question: *"Does the payload contain personal data (PII)?"* If it does, the route must be able to take PII (`policy.pii = true`), and an Intent that asserted `pii: false` is a violation.

# %% a2a_coverage=["decision", "pattern:8", "errors"]
# A payload with a passport number
payload = {"name": "Ada Lovelace", "passport": "X1234567", "amount": 10, "from": "USD", "to": "EUR"}
for label, constraints in (("asserts pii=false", {"pii": False}), ("no assertion", None)):
    try:
        await send_intent(gw, "finance.fx.quote", payload, constraints)
    except AgpPolicyViolationError as exc:
        print(f"{label:18}: {exc}")
paid = await send_intent(gw, "finance.payments.initiate", {"amount": 10, "to": "Ada Lovelace", "passport": "X1234567"})
print("payments squad    :", state_name(paid.state), "|", paid.text)
print("pii probability   :", route_of(paid)["pii_probability"])

# %% [markdown]
# **What just happened**
# - The FX squad is announced as `pii: false`, so a payload with a passport number cannot go there. The payments squad is `pii: true`, so it can.
# - The probability comes from the decision model (`noul`), not from a regex. With the fake model the passport is scripted at 0.95.

# %% [markdown]
# ## 9. Failover
# Stop the FX squad. Two mechanisms act:
# 1. **Immediate:** the finance gateway cannot reach it, marks the route unreachable, reports `AGP_TABLE_STALE`; the root treats that as "this path is dead" and tries the **next best route**: the hotels squad's worse quote.
# 2. **Eventually:** with no re-announcements the route ages past the **TTL** and the table marks it stale.
#
# Then restart FX: it announces again and the cheap route wins back.

# %% a2a_coverage=["pattern:8", "extensions"]
# Stop the FX squad and send the same Intent
root_dir = find_config_dir().parent
can_docker = shutil.which("docker") is not None and (root_dir / "compose.yaml").exists()


def compose(*args):
    return subprocess.run(["docker", "compose", "--profile", "lab09", *args], cwd=root_dir, capture_output=True, text=True)


FX = {"amount": 100, "from": "USD", "to": "EUR"}
if can_docker:
    compose("stop", "lab09-squad-fx")
    down = await send_intent(gw, "finance.fx.quote", FX)
    r = route_of(down)
    print("answer:", down.text)
    print("path  :", " → ".join(r["path"]), "| cost", r["cost"], "(the alternative route)")
else:
    print("no docker CLI here: run `docker compose --profile lab09 stop lab09-squad-fx` on the host")

# %%
# Watch the dead route age out in the root's table
if can_docker:
    for _ in range(40):
        fx = table(ROOT)["capabilities"]["finance.fx.quote"]
        print([(r["path"][0].replace("lab09-", ""), r["age_s"], "STALE" if r["stale"] else "fresh") for r in fx])
        if any(r["stale"] for r in fx):
            break
        await asyncio.sleep(3)

# %%
# Restart FX; the cheap route returns after its next announcement
if can_docker:
    compose("start", "lab09-squad-fx")
    await wait_for_cards(["lab09-squad-fx"], timeout=120)
    for _ in range(40):
        back = await send_intent(gw, "finance.fx.quote", FX)
        if "[FX desk]" in back.text:
            break
        await asyncio.sleep(2)
    print("after restart:", back.text, "| cost", route_of(back)["cost"])

# %% [markdown]
# ## 10. A stale table
# Squads that stop announcing are not removed at once: their routes become **stale** (older than the TTL) but are kept for a while. If *every* route for a capability is stale, the honest answer is `-32202 AGP_TABLE_STALE`, not a guess. `POST /agp/pause` (lab-only) makes the payments squad go quiet.

# %% a2a_coverage=["pattern:8", "errors"]
# Pause the payments squad's announcements and poll until the root says STALE
print(control("lab09-squad-payments", "pause"))
t0 = time.monotonic()
for _ in range(40):
    try:
        await send_intent(gw, "finance.payments.initiate", {"to": "ACME"})
        await asyncio.sleep(3)
    except AgpTableStaleError as exc:
        print(f"after {time.monotonic() - t0:.0f}s:", type(exc).__name__, "|", exc)
        break
print(control("lab09-squad-payments", "resume"))

# %% [markdown]
# **What just happened**
# - The hold timer is the TTL (20 s in this profile; the spec default is 120 s). A change in a squad takes *up to* TTL per hop to be believed at the root. That is the price of a hierarchy without a central registry.
# - Compare Lab 06: there the registry **verifies** cards. AGP routes by **capability and policy** and trusts its children; combine them in production.

# %% [markdown]
# ## 11. A client that does not know AGP
# No extension header, no `agp` part: plain text. The gateway asks the decision model which **child** should handle it (root: finance or travel; domain gateway: which squad), and forwards the text unchanged.

# %% a2a_coverage=["pattern:8", "decision"]
# Plain A2A through the same root
async with await connect(ROOT, streaming=False) as plain:
    res = await send_text(plain, "What's the visa situation for a US citizen visiting France?")
print("state :", state_name(res.state), "|", res.text)
print("route :", route_of(res)["path"], "| fallback:", route_of(res)["fallback"])
print("choice probabilities:", route_of(res)["probabilities"])

# %% [markdown]
# ## 12. Task ownership across hops
# Three gateways deep, three **different tasks**. Each `agp-route` artifact names the task one level down, so you can walk the chain with `GetTask`.

# %% a2a_coverage=["pattern:8", "op:GetTask"]
# Follow downstream_task_id from the root to the squad
res = await send_intent(gw, "travel.flights.search", {"from": "Paris", "to": "Rome"})
chain = [("root gateway", ROOT, res.task_id)]
hop = route_of(res)
for label, svc in (("travel gateway", TRAVEL), ("flights squad", "lab09-squad-flights")):
    chain.append((label, svc, hop["downstream_task_id"]))
    async with await connect(svc, streaming=False) as c:
        task = await c.client.get_task(GetTaskRequest(id=hop["downstream_task_id"]))
    art = next((a for a in task.artifacts if a.artifact_id == "agp-route"), None)
    hop = get_data_parts(art.parts)[0] if art else {"downstream_task_id": None}
for label, svc, tid in chain:
    print(f"{label:15} {svc:24} task {tid}")
print(res.text)

# %% [markdown]
# ## 13. See it in Grafana

# %%
# One trace across the gateways: an intent and a fallback request
with display.cell_span("agp routes") as span:
    await send_intent(gw, "finance.fx.quote", FX, {"region": "EU"})
    async with await connect(ROOT, streaming=False) as plain:
        await send_text(plain, "Find me a hotel in Rome")
tel.flush()
display.trace_link(span)

# %% [markdown]
# Try in Grafana → Explore:
# - **TraceQL:** `{ name = "agp.route" }` shows `agp.route.path`, `agp.route.cost`, `agp.route.rejected` and `agp.pii.p` for every routed Intent: one span per gateway, so a request through two gateways has two.
# - **TraceQL:** `{ name = "agp.fallback" }` shows which child the decision model chose (`agp.fallback.choice`).
# - **LogQL:** `{service_name="lab09-root-gateway"} |= "unusable"` shows failovers.

# %% [markdown]
# ## 14. Exercises
# - 🟢 **Easy:** add a `travel.cars.rent` squad (copy `squad_flights.py`), announce it, and watch the route appear at the root.
# - 🟡 **Medium:** add latency-weighted cost: the gateway measures p50 forward latency per route and adds it to the announced cost.
# - 🔴 **Stretch:** withdraw a route after three consecutive `FAILED` results from its squad (not just transport errors).

# %% tags=["skip-ci"] jupyter={"source_hidden": true}
# Hidden solution (easy): servers/squad_cars.py like squad_flights.py; SQUADS gets a Squad(..., parent="lab09-travel-gateway",
# announcements=[ann("travel.cars.rent", 1, ["*"], False)]); add the service to config/endpoints.yaml and compose.yaml.

# %% [markdown]
# ## 15. Recap
#
# | Pattern 8 | |
# |---|---|
# | **Use it when** | Many squads behind domain boundaries; you need routing by capability, cost and policy; the client should know one address |
# | **Not when** | A handful of agents (Lab 05's supervisor is simpler) or when you mostly need trust and discovery (Lab 06's registry) |
# | Mechanics | Announcements build per-gateway tables; Intents route to the cheapest compliant path; TTL expires silent squads; each hop owns its task |
# | Caveats | Experimental spec: no transport, no refresh protocol, no auth. Everything here is our convention (DEV-63..66) |
#
# **Pitfalls:** treating AGP as an SDK feature; forgetting that an unactivated extension means *fallback*, not failure; trusting announcements without verifying cards (Lab 06) or authenticating gateways (Lab 10); a TTL shorter than twice the re-announce interval (routes flap).
#
# **Next:** M13 hardening, then the whole repo is yours.

# %% [markdown]
# ## Cleanup

# %%
# Close the client and flush telemetry
await gw.aclose()
tel.flush()
