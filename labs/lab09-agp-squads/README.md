# Lab 09: AGP hierarchical squads (pattern 8, EXPERIMENTAL)

> **Experimental, not core A2A.** The Agent Gateway Protocol is a *proposed* routing extension
> (`a2a-samples/extensions/agp/spec.md`, V1). No SDK supports it; everything that implements it is
> `# CUSTOM`. It is unrelated to AGNTCY's gRPC "Agent Gateway Protocol".

A client talks to one **gateway**. Squads announce `{capability, version, cost, policy}`; gateways
keep an **AGP table**, send each **Intent** down the cheapest policy-compliant route, screen the
payload for PII with a decision model, fail over, and still serve plain A2A clients.

**Notebook:** [`notebooks/lab09_agp_squads.ipynb`](notebooks/lab09_agp_squads.ipynb)
(authored as the paired `.py`). **Spec:** [`labs/lab09-agp-squads.md`](../lab09-agp-squads.md).

## Run it

```bash
make up LAB=lab09                                             # 3 gateways + 6 squads
LLM_PROVIDER=fake DECISION_PROVIDER=fake make e2e LAB=lab09   # deterministic run (~1 min: it waits for TTLs)
```

| Service | Port | Framework | Announces (cost, policy) |
|---|---|---|---|
| `lab09-root-gateway` | 10901 | a2a-sdk | table of everything below |
| `lab09-finance-gateway` / `lab09-travel-gateway` | 10902 / 10903 | a2a-sdk | their squads, `cost + 1` |
| `lab09-squad-payments` | 10911 | Strands | `finance.payments.initiate` 2 `{region US,EU; pii}` |
| `lab09-squad-fx` | 10912 | LangGraph | `finance.fx.quote` 1 `{US,EU,APAC; no pii}` |
| `lab09-squad-compliance` | 10913 | ADK | `finance.compliance.check` 3 `{EU; pii}` |
| `lab09-squad-flights` | 10914 | ADK | `travel.flights.search` 1 `{*}` |
| `lab09-squad-hotels` | 10915 | Strands | `travel.hotels.search` 1; **and** `finance.fx.quote` 4 (worse alternative) |
| `lab09-squad-visa` | 10916 | LangGraph | `travel.visa.requirements` 2 `{EU,US; pii}` |

`AGP_TTL_S` (route lifetime, 20 s in this profile; spec default 120) and `AGP_REANNOUNCE_S` (5 s; 60)
come from the environment. Lab-only endpoints: `GET /agp/table`, `POST /agp/pause`, `POST /agp/resume`.

## Wire format (our convention; the AGP spec defines the data, not the transport)

A normal `SendMessage` with `A2A-Extensions: <AGP URI>` and ONE data part:

```json
{"agp": {"type": "announce", "announcements": [{"capability": "...", "version": "1.0", "cost": 1, "policy": {}}],
         "path": ["lab09-squad-fx"], "next_hop_url": "http://...", "gateway": false}}
{"agp": {"type": "intent", "intent": {"target_capability": "...", "payload": {}, "policy_constraints": {"region": "EU"}}}}
```

An announce is answered with one `Message` (ack). An intent is answered with the gateway's own
**task**: the downstream artifacts plus an `agp-route` artifact (path, cost, losers, PII verdict,
`downstream_task_id`). Errors are JSON-RPC errors `-32200/-32201/-32202` with the reason in `ErrorInfo`.

## Code map

| Path | Role |
|---|---|
| `../../libs/a2a-labs-common/.../agp.py` | models, `AgpTable`, policy matching, errors (+ SDK registration) |
| `../../libs/a2a-labs-common/.../agp_gateway.py` | `AgpGatewayExecutor`, `Announcer`, `agp_announce` |
| `src/lab09_agp/squads.py` | the six squads: announcements and deterministic handlers |
| `src/lab09_agp/agents/squad_agents.py` | one tiny agent per squad in its framework |
| `src/lab09_agp/servers/common.py` | squad and gateway apps, lifespan announcers, `/agp/*` endpoints |
| `src/lab09_agp/clients.py` | `send_intent`, `route_of`, `table`, `control` |

## What to notice

- **Staleness has two stages.** Past the TTL a route is *stale*: ignored if a fresh alternative exists,
  `AGP_TABLE_STALE` if none does. After `10 x TTL` it is purged (then `AGP_ROUTE_NOT_FOUND`).
- **Failover is BGP-style:** a dead next hop, or a child gateway that lost its own route, makes the
  parent try the next best route. A policy violation is never retried.
- A change deep in the tree reaches the root after up to *TTL per hop*: the price of no central registry.
- PII: `pii: false` in an Intent means "I assert no PII"; the decision model can contradict it.
