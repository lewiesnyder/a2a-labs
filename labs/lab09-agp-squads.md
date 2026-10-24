# Lab 09: AGP Hierarchical Squads (Pattern 8, EXPERIMENTAL)

**Package:** `labs/lab09-agp-squads` → import `lab09_agp`
**Profile:** `lab09`
**Models:** `quick` (squad agents), `decision` (policy checks, non-AGP fallback routing)
**Frameworks:** a2a-sdk (gateways), ADK, Strands, LangGraph (squad members)

> ⚠️ **Experimental. Not core A2A.** The Agent Gateway Protocol (AGP) is a **proposed routing extension** published as a sample in `a2aproject/a2a-samples/extensions/agp/spec.md` (V1, "Core Protocol Layer / Routing Extension"). No SDK supports it. Everything in this lab that implements AGP is `# CUSTOM` code that follows that document, layered on standard A2A messages and the **extension mechanism**. Do not confuse it with AGNTCY's unrelated gRPC "Agent Gateway Protocol".

> **Pattern 8:** Domain gateways and squad routing.

## 0. Pre-build verification (mandatory)

Before coding, the build agent MUST read `extensions/agp/spec.md` (and any code in that folder) at the current a2a-samples `main`. Then it updates this lab spec's §3 if names, fields, error codes or the announcement transport differ. The research basis for this spec records:
- extension URI `https://github.com/a2aproject/a2a-samples/tree/main/extensions/agp`;
- card params `{"agent_role": "gateway", "supported_agp_versions": ["1.0"]}`;
- Capability Announcements `{capability, version, cost?, policy}`;
- Intent `{target_capability, payload, policy_constraints?}`;
- AGP Table: `capability → [RouteEntry{path, cost, policy}]`, with lowest-cost, policy-compliant selection;
- errors -32200 `AGP_ROUTE_NOT_FOUND`, -32201 `AGP_POLICY_VIOLATION`, -32202 `AGP_TABLE_STALE`.

## 1. Learning objectives

1. Understand why hierarchy helps at scale: a client talks to **one gateway**; squads are autonomous; routing is policy- and cost-aware (a BGP analogy).
2. Declare and activate an A2A **extension** end to end:
   - declare it in the card's `capabilities.extensions`;
   - activate it with the `A2A-Extensions` header;
   - carry its payloads in DataParts;
   - use extension-specific errors.
3. Build gateways that maintain an **AGP Table** from **capability announcements** and route **Intents** to the lowest-cost compliant path.
4. Enforce **policy constraints**, such as region, PII and max cost, with a decision-model PII check.
5. Gracefully handle non-AGP clients by routing via Nimble over squad descriptions.

## 2. Topology and services

```text
                         client (notebook)
                               │  A2A + A2A-Extensions: agp
                    ┌──────────▼──────────┐
                    │ lab09-root-gateway   │ 10901  (AGP table: finance.*, travel.*)
                    └───────┬───────┬─────┘
             ┌──────────────┘       └──────────────┐
   ┌─────────▼─────────┐                 ┌─────────▼─────────┐
   │ lab09-finance-gw   │ 10902           │ lab09-travel-gw    │ 10903
   └─┬────────┬───────┬┘                 └─┬────────┬───────┬┘
     │        │       │                    │        │       │
 payments   fx     compliance           flights   hotels   visa
 (Strands) (LangGraph) (ADK)            (ADK)   (Strands) (LangGraph)
  10911     10912   10913               10914    10915    10916
```

| Service | Port | Framework | Capabilities announced (capability@version, cost, policy) |
|---|---|---|---|
| `lab09-squad-payments` | 10911 | Strands | `finance.payments.initiate@1.0` cost 2 `{region:["US","EU"], pii:true}` |
| `lab09-squad-fx` | 10912 | LangGraph | `finance.fx.quote@1.0` cost 1 `{region:["US","EU","APAC"], pii:false}` |
| `lab09-squad-compliance` | 10913 | ADK | `finance.compliance.check@1.0` cost 3 `{region:["EU"], pii:true}` |
| `lab09-squad-flights` | 10914 | ADK | `travel.flights.search@1.0` cost 1 `{region:["*"], pii:false}` |
| `lab09-squad-hotels` | 10915 | Strands | `travel.hotels.search@1.0` cost 1 `{region:["*"], pii:false}`; **also** `finance.fx.quote@1.0` cost 4 (a deliberately worse alternative route) |
| `lab09-squad-visa` | 10916 | LangGraph | `travel.visa.requirements@1.0` cost 2 `{region:["EU","US"], pii:true}` |
| `lab09-finance-gateway` | 10902 | a2a-sdk `AgpGatewayExecutor` | aggregates its squad; announces upward with cost +1 |
| `lab09-travel-gateway` | 10903 | a2a-sdk `AgpGatewayExecutor` | aggregates its squad; announces upward with cost +1 |
| `lab09-root-gateway` | 10901 | a2a-sdk `AgpGatewayExecutor` | top-level table |

The squad agents are simple quick-model agents with deterministic tools. They need not know AGP, apart from announcing at startup through the `agp_announce()` helper.

## 3. AGP implementation (`a2a_labs_common.agp`, all `# CUSTOM`)

```python
AGP_URI = "https://github.com/a2aproject/a2a-samples/tree/main/extensions/agp"

class CapabilityAnnouncement(BaseModel): capability: str; version: str; cost: float | None = None; policy: dict = {}
class Intent(BaseModel): target_capability: str; payload: dict; policy_constraints: dict | None = None
class RouteEntry(BaseModel): path: list[str]; cost: float; policy: dict; announced_at: datetime; next_hop_url: str
class AgpTable:
    def upsert(self, ann: CapabilityAnnouncement, path: list[str], next_hop_url: str) -> None
    def lookup(self, capability: str, constraints: dict | None) -> RouteEntry  # lowest cost satisfying policy
    def withdraw(self, path_prefix: str) -> None
    def is_stale(self, entry, ttl_s) -> bool
class AgpError(Exception): code: int; reason: str   # -32200/-32201/-32202
```

**Transport** (unless spec.md says otherwise; see §0). AGP messages are ordinary A2A `SendMessage` calls with `A2A-Extensions: <AGP_URI>`, carrying one DataPart:
- **Announce:** `{"agp": {"type": "announce", "announcements": [...], "path": ["lab09-squad-fx"], "next_hop_url": "..."}}` → the gateway replies with a direct Message ack.
- **Intent:** `{"agp": {"type": "intent", "intent": {...}}}` → the gateway routes it and returns the downstream **Task** result. It wraps the downstream task: the gateway owns its own task and mirrors downstream status and artifacts, which preserves A2A task ownership per hop.

**Gateway behavior** (`AgpGatewayExecutor(AgentExecutor)`):
1. If the extension is not activated for the request and the message has no `agp` DataPart, use the **non-AGP fallback**: Nimble `choice` over child descriptions, then forward.
2. **Announce:** validate, upsert into the table, then re-announce upward (if it has a parent) with `cost + 1` and `path = [self] + path`. Announcements expire after `AGP_TTL_S` (default 120 s); children re-announce every `AGP_REANNOUNCE_S` (60 s).
3. **Intent:**
   1. Check the payload against policy constraints with the **decision model**: `noul` "Does the payload contain personal data (PII)?". The result becomes `constraints.pii_detected`.
   2. Look up the route. If none exists → `AGP_ROUTE_NOT_FOUND`. If routes exist but all violate policy → `AGP_POLICY_VIOLATION`. If the best entry is stale → `AGP_TABLE_STALE`.
   3. Forward to the next hop with `create_client`.
   4. Record span `agp.route` with the chosen path, cost and rejected alternatives.
4. Errors are returned as JSON-RPC errors with AGP codes [VERIFY how to raise custom error codes through the SDK. If impossible, return a REJECTED task with a DataPart `{"agp_error": {code, reason}}` and document the deviation].
5. Debug endpoint `GET /agp/table` (`# CUSTOM`, lab-only) dumps the table for the notebook.

Gateway card:
- `capabilities.extensions=[AgentExtension(uri=AGP_URI, required=False, params={"agent_role": "gateway", "supported_agp_versions": ["1.0"]})]`.
- Skills summarize the domains it routes.

## 4. Notebook outline

1. ⚠️ Experimental banner, plus a short history: where AGP comes from and its status.
2. Architecture (the tree above) and the BGP analogy table: AS ↔ squad, route announcement ↔ capability announcement, path attributes ↔ cost/policy.
3. Setup: wait for the gateways and give the announcements 5 s to converge.
4. **Inspect cards**: the AGP extension declaration on the gateways; squad agents lack it.
5. **Inspect tables**: `GET /agp/table` on the root and domain gateways. Show `finance.fx.quote` with two routes (fx via lab09-finance-gateway, cost 2; hotels via lab09-travel-gateway, cost 5).
6. **Send an Intent**: `finance.fx.quote` with `{amount: 100, from: "USD", to: "EUR"}` and constraint `region=EU`. Show the routed path, the cost and the result task.
7. **Policy routing**: `finance.compliance.check` with `region=US` → `AGP_POLICY_VIOLATION`, since compliance is EU-only.
8. **PII check with Nimble**: an Intent whose payload contains a passport number with constraint `pii=false` → the decision model flags it → violation. Show the probabilities.
9. **Failover**: stop `lab09-squad-fx` and wait for the TTL (set `AGP_TTL_S=20` in the profile for the demo). The route is withdrawn, so the fallback route via hotels (higher cost) is chosen. Restart fx → it re-announces → the cheaper route wins again.
10. **Stale table**: pause re-announcements (`POST /agp/pause` on a child, `# CUSTOM`) → `AGP_TABLE_STALE`.
11. **Non-AGP client**: send plain text "What's the visa situation for a US citizen visiting France?" without the extension header → Nimble fallback routing → travel → visa.
12. **Task ownership across hops**: GetTask at the root, the domain gateway and the squad agent. Three distinct tasks, linked by `reference_task_ids` or metadata.
13. Grafana: a 3-hop trace with `agp.route` spans and a route-cost attribute table.
14. Exercises:
    - (easy) add a `travel.cars.rent` squad agent;
    - (medium) add latency-weighted cost (the gateway measures p50 latency and adds it to the cost);
    - (stretch) add route withdrawal on repeated FAILED results.
15. Recap: when hierarchical gateways are worth the complexity, and when a supervisor (Lab 05) or registry (Lab 06) is enough.

## 5. Acceptance tests

- Table convergence: within 10 s of startup, the root has routes for all 7 capability entries (6 + the alternative fx).
- Lowest-cost selection, policy rejection (-32201 or the documented deviation), route-not-found (-32200), and stale (-32202).
- The PII constraint is enforced using the FakeDecisionModel `noul=0.95`.
- Failover to the alternative route after the TTL, and failback after re-announcement.
- The non-AGP fallback routes plain-text requests.
