# Spec deviations

| ID | Spec section | What I found | What I did | Evidence |
|---|---|---|---|---|
| DEV-1 | 00 D11, 02 §3, D.1 | The `strands-agents[a2a]` extra pins `a2a-sdk>=0.3,<0.4`, and `strands.multiagent.a2a` imports 0.3-only types (`DataPart`). It cannot coexist with `a2a-sdk` 1.2.x. | Dropped the `a2a` extra from the `strands` extra in `libs/a2a-labs-common/pyproject.toml`. Strands agents are served by our own `StrandsA2AExecutor` (`a2a_labs_common/a2a/strands.py`, `# CUSTOM`, listed in `custom-code.md`) behind the standard SDK server builder. `A2AServer` and `enable_a2a_compliant_streaming` are not used, so pitfalls S-1/S-2 are handled by the executor itself. Lab specs that say `A2AServer(agent_factory=...)` mean `StrandsA2AExecutor(agent_factory=...)`. | `uv sync` resolver error; `ImportError` on `strands.multiagent.a2a` with strands-agents 1.62.0 + a2a-sdk 1.2.3 |
