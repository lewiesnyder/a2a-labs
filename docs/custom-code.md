# Custom code register

Every `# CUSTOM:` construct in the repo is listed here: location, reason, official alternative checked.

| Location | Reason | Alternative checked |
|---|---|---|
| `libs/a2a-labs-common/src/a2a_labs_common/a2a/strands.py` (`StrandsA2AExecutor`) | `strands.multiagent.a2a.A2AServer` needs a2a-sdk 0.3 (`DataPart`) and cannot import on a2a-sdk 1.x (strands-agents 1.62.0). The executor keeps D11's goals: per-context agents via `agent_factory`, LRU eviction (`max_contexts`), chunked artifact streaming. Everything else uses official `AgentExecutor`, `TaskUpdater` and `a2a.helpers`. | `strands.multiagent.a2a.A2AServer` (broken on 1.x, see DEV-1); `StrandsA2AExecutor` upstream (does not exist for 1.x). Remove when Strands supports a2a-sdk 1.x. |
