# 09: Build Plan

Milestones are sequential unless marked ∥ (parallelizable). Each milestone ends with its acceptance checks green. Estimates assume one experienced engineer or agent.

| M | Name | Scope | Depends on | Exit criteria |
|---|---|---|---|---|
| **M0** | Verify & scaffold | uv workspace, root files, Makefile, `.env.example`, `config/*.yaml`, Dockerfiles, compose with infra only (lgtm, jupyter, models-check, optional ollama). Resolve **all [VERIFY] items** for a2a-sdk server/client/helpers/push/signing and record them in `docs/verification-log.md`. | — | `make setup`, `docker compose up` (infra) healthy; `make models` passes on the reference machine; verification log complete for the SDK. |
| **M1** | Common library | `config`, `models` (all adapters + decision + fake), `telemetry` (setup, interceptor, TracedExecutor, metrics), `a2a` (cards, server builder, client helpers, events, display, methods). Root notebook `00-setup-and-observability.ipynb`. | M0 | Unit tests ≥85% line coverage on `libs/`; root notebook runs; a test span, log and metric are visible in lgtm. |
| **M2** | Lab 00 + webhook-receiver | Playground executor, 3 variants, gRPC/REST, extended card, receiver, notebook. | M1 | Lab 00 acceptance tests; TCK mandatory green; notebook < 2 min. |
| **M3** | Lab 01 + LangGraph bridge | `LangGraphA2AExecutor` in common; ADK/Strands/LangGraph/SDK servers; resolve the ADK and Strands [VERIFY] items (`to_a2a` factory, `force_new_version`, A2AServer args). | M2 | Lab 01 acceptance; TCK capability report generated for the 3 framework servers. |
| **M4** ∥ | Lab 02 | Specialists + 3 controllers; `a2a_agent_as_langchain_tool`. | M3 | Lab 02 acceptance. |
| **M5** ∥ | Lab 03 | tools-mcp, bridge, specialists, consumers. | M3 | Lab 03 acceptance. |
| **M6** ∥ | Lab 04 | Trip Planner, `RemoteA2ANode`, Concierge, optional Agent Server profile. | M3 | Lab 04 acceptance. |
| **M7** | Lab 05 | Supervisors (LangGraph/ADK/Strands), ledger, Nimble routing. | M4, M6 | Lab 05 acceptance. |
| **M8** ∥ | Lab 06 | Keys, signing, trust store, registry, rogue, delegator. | M3 | Lab 06 acceptance. |
| **M9** ∥ | Lab 07 | Planner/writer/editor, orchestrator, relay, native chains. | M6 | Lab 07 acceptance. |
| **M10** ∥ | Lab 08 | Push sender (JWT), SSRF guard, 4 push agents, work queue, durable store. | M3 | Lab 08 acceptance. |
| **M11** | Lab 10 | Mock IdP, auth middleware, extended card, AUTH_REQUIRED, extensions, versioning. | M10 | Lab 10 acceptance. |
| **M12** | Lab 09 | Read the AGP spec.md, then AGP lib, gateways, squads. | M11 | Lab 09 acceptance. |
| **M13** | Hardening & docs | Grafana dashboards final; coverage-tag checker; CI workflows; README polish; conformance report; screenshots; `docs/` site (optional MkDocs). | all | Repo-level definition of done (`AGENTS.md` §5). |

## Work-item checklist template (copy per lab)

```markdown
### Lab NN — <name>
- [ ] Read labs/labNN spec; list [VERIFY] items; resolve and log them
- [ ] pyproject + package skeleton + README stub
- [ ] cards.py + test_cards.py
- [ ] agents/ (framework agent definitions) with fake-model unit tests
- [ ] executors/ (if any) with in-process tests
- [ ] servers/ modules + compose services + endpoints.yaml entries + ports
- [ ] clients/ (if any)
- [ ] integration tests (fake mode) green via make e2e
- [ ] notebook following 07 standards, coverage tags set
- [ ] notebook green with real models; timings noted in README
- [ ] Grafana: trace connectivity verified; screenshot added to README
- [ ] spec deviations / custom code docs updated
```

## Reference machine

The timing claims in notebooks are measured on a workstation with ≥48 GB unified memory or a 24 GB GPU, running Ollama ≥ 0.40.4. On first run, record the actual timings in each lab README.

## Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| a2a-sdk 1.x API differs from spec snippets | Rework | M0 verification gate; spec-deviations log |
| ADK `use_legacy=False` ineffective on a2a-sdk 1.x (#7334) / duplicate replies (#7247) | Wrong events, duplicated text | Server-side `force_new_version=True` via `agent_executor_factory`; integration test asserting a single final artifact |
| Strands `A2AServer` lacking a hook for executor wrapping/push sender | Telemetry/push gaps | Starlette envelope middleware; manual handler construction |
| Small local models unreliable at tool calling | Flaky notebooks | ≤3 tools per quick agent; deterministic tools; fake mode for CI; `REASONING_THINK` toggle |
| `nimble-v3` response shape undocumented | Parser errors | Tolerant parser + raw capture; `check_models.py` probe; LLM fallback |
| `nimble-v3` noncommercial license | Legal for adopters | License notice; `DECISION_PROVIDER=llm_fallback` documented |
| LangGraph Agent Server partial A2A | Learner confusion | Optional sub-lab with an explicit limits table |
| AGP spec drift | Lab 09 mismatch | Mandatory re-read at M12; experimental banner |
| Memory pressure with 27B + 9B models | Slow or OOM | Host Ollama; model swap guidance; `QUICK_DEMO` parameter |
