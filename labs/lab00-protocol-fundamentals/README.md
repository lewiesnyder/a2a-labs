# Lab 00: Protocol fundamentals

Use every A2A operation, task state, part kind and error against one scripted agent, over
JSON-RPC, REST and gRPC. No LLM is involved, so every run is identical.

**Notebook:** [`notebooks/lab00_protocol_fundamentals.ipynb`](notebooks/lab00_protocol_fundamentals.ipynb)
(authored as the paired `.py`; edit that, then `uv run jupytext --sync`).
**Spec:** [`labs/lab00-protocol-fundamentals.md`](../lab00-protocol-fundamentals.md) at the repo root.

## Run it

```bash
SKIP_MODEL_CHECK=true make up LAB=lab00      # agents + webhook receiver + Grafana stack
make notebook                                # JupyterLab, or open the .ipynb on the host
make e2e LAB=lab00                           # integration tests + the notebook, headless
make tck SUT=lab00-protocol-agent            # official A2A TCK (see docs/conformance.md)
```

| Service | Port | What it shows |
|---|---|---|
| `lab00-protocol-agent` | 10001 (JSON-RPC at `/`, REST at `/rest`), 10002 (gRPC) | Everything |
| `lab00-protocol-agent-nostream` | 10003 | `UnsupportedOperationError` for streaming and subscribe; strict media types |
| `lab00-protocol-agent-nopush` | 10004 | `PushNotificationNotSupportedError` |
| `webhook-receiver` | 9100 | Receives push notifications |

## The agent's commands

`help`, `echo <text>`, `work [n]`, `slow [seconds]`, `ask`, `auth`, `fail`, `reject`, `parts`,
`multi`, `crash`, `refs`, and `stats` (admin token only). See
[`executors/playground.py`](src/lab00_protocol/executors/playground.py); each command's comment
says which protocol construct it demonstrates.

## Code map

| Path | Role |
|---|---|
| `executors/playground.py` | The scripted `AgentExecutor` (read this first) |
| `executors/tck.py` | `# CUSTOM` shim answering the A2A TCK's `messageId`-prefix scenarios |
| `cards.py` | Agent Cards for the three variants and the extended card |
| `auth.py` | `# CUSTOM` bearer backend and extended-card guard |
| `servers/playground.py` | One module, three variants (`PLAYGROUND_VARIANT`), gRPC in the same process |
| `wire.py` | Raw JSON-RPC helpers used by the notebook |

## Things worth knowing

- JSON-RPC, REST and gRPC share **one handler and one `TaskStore`**, so a task made over gRPC is
  readable over REST.
- Tasks are scoped to the authenticated caller. An admin identity cannot see anonymous tasks.
- The agent runs `ExtendedCardGuard` only on `full`; on `nostream`/`nopush` the SDK answers
  `-32004` because the capability is off.

## Timings (reference run, host notebook, no LLM)

| Notebook | Time |
|---|---|
| `lab00_protocol_fundamentals` | about 40 s |

## Going further

Exercises are in the notebook. A durable store is the stretch exercise: `DatabaseTaskStore`
with SQLite (`a2a-sdk[sqlite]` is already installed), then restart the container mid-task.
