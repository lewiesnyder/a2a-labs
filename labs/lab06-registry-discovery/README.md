# Lab 06: Registry and discovery (pattern 5)

Find agents you did not write and **delegate only to the ones you can verify**: signed Agent Cards,
a trust store, a curated registry that quarantines what fails, and a delegator that re-verifies
everything itself.

**Notebook:** [`notebooks/lab06_registry_discovery.ipynb`](notebooks/lab06_registry_discovery.ipynb)
(authored as the paired `.py`). **Spec:** [`labs/lab06-registry-discovery.md`](../lab06-registry-discovery.md).

## Run it

```bash
make up LAB=lab06            # generates the dev keys first (scripts/gen_lab06_keys.py), then compose
LLM_PROVIDER=fake DECISION_PROVIDER=fake make e2e LAB=lab06   # deterministic CI run
```

| Service | Port | What it is |
|---|---|---|
| `lab06-agent-a` | 10602 | **Translator A** (Strands), card signed with `lab06-a` |
| `lab06-agent-b` | 10603 | **Translator B** (ADK), card signed with `lab06-b`; guarded extended card |
| `lab06-agent-rogue` | 10604 | impersonator; `POST /_rogue/mode` = `unsigned` / `untrusted_key` / `tampered` / `name_spoof` |
| `lab06-registry` | 10601 | curated registry (FastAPI): stores cards + a verdict, hides what fails |
| `lab06-delegator` | 10605 | LangGraph: search → re-verify → select (decision model) → delegate |

## Trust model

`config/registry/trust_store.json` (generated, git-ignored, **public keys only**) maps each `kid` to
the names and hosts it may speak for, plus `max_card_age_s`. `CardVerifier` checks, in order:
`signature_present`, `key_trusted`, `signature_valid`, `name_bound`, `host_bound`,
`interfaces_match_host`, `not_expired`, `capabilities_ok`. Keys come from the trust store only; the
`jku` header and each agent's own `/.well-known/jwks.json` are never used.

| Rogue mode | First failing check |
|---|---|
| `unsigned` | `signature_present` |
| `untrusted_key` | `key_trusted` |
| `tampered` (A's real card, endpoint swapped) | `signature_valid` |
| `name_spoof` (validly signed by the rogue key) | `key_trusted` |

## Code map

| Path | Role |
|---|---|
| `../../libs/a2a-labs-common/.../a2a/trust.py` | `TrustStore`, `sign_card`, `CardVerifier`, `VerifiedCardResolver` (CUSTOM policy over the SDK's signing) |
| `keys.py` | Dev keys, trust store and the injected card file |
| `registry.py`, `servers/registry.py` | The registry (CUSTOM API; the spec defines none) |
| `agents/delegator.py` | The verifying delegator graph |
| `servers/agent_{a,b,rogue}.py` | The three agents; B uses our server stack to carry an extended card |

## What to notice

- A valid signature is not trust: `name_spoof` verifies mathematically and is still refused.
- Verify **before** bridging card URLs for a host run; rewriting a URL breaks the signature.
- The delegator connects with the **verified card object** (`connect_with_card`), never a re-fetch.
- Fake mode scripts the choice (`best_candidate` in `fake_responses.yaml`).
