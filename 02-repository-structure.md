# 02: Repository Structure and Conventions

## 1. Top-level layout

```text
a2a-labs/
├── AGENTS.md                          # copy of spec AGENTS.md, adapted for contributors
├── README.md                          # quickstart: prerequisites, `make up LAB=lab01`, notebook links
├── LICENSE                            # Apache-2.0
├── Makefile                           # thin wrappers over uv/docker compose (see §6)
├── pyproject.toml                     # uv workspace root (virtual, no code)
├── uv.lock
├── .python-version                    # 3.12
├── .env.example                       # every env var with defaults (appendices/B-env-example.md)
├── compose.yaml                       # single compose file, profiles per lab
├── docker/
│   ├── Dockerfile                     # a2a-labs:dev, all workspace packages installed
│   ├── jupyter.Dockerfile             # FROM a2a-labs:dev + jupyterlab + kernels
│   └── entrypoint.sh                  # waits for ollama/lgtm health, then exec "$@"
├── config/
│   ├── models.yaml                    # role → provider/model/params (04-model-providers.md)
│   ├── endpoints.yaml                 # service name → docker DNS URL + host URL
│   └── registry/                      # Lab 06 curated registry seed + trusted keys
├── observability/
│   ├── grafana/
│   │   ├── dashboards/a2a-labs-overview.json
│   │   ├── dashboards/a2a-task-lifecycle.json
│   │   └── provisioning/dashboards.yaml
│   └── collector/otelcol-config.yaml  # only for the optional `collector` profile
├── libs/
│   └── a2a-labs-common/               # shared library (03-common-library.md)
├── services/
│   └── webhook-receiver/              # shared push-notification receiver (Labs 00, 08, 10)
├── labs/
│   ├── lab00-protocol-fundamentals/
│   ├── lab01-direct-client/
│   ├── lab02-agent-as-tool/
│   ├── lab03-mcp-delegation/
│   ├── lab04-langgraph-node/
│   ├── lab05-supervisor-router/
│   ├── lab06-registry-discovery/
│   ├── lab07-sequential-chain/
│   ├── lab08-async-push/
│   ├── lab09-agp-squads/
│   └── lab10-security-extensions/
├── notebooks/
│   └── 00-setup-and-observability.ipynb   # environment check + telemetry tour
├── scripts/
│   ├── check_models.py                # verifies Ollama models + systemone endpoint
│   ├── wait_for.py                    # waits for agent cards to be reachable
│   ├── run_tck.sh                     # runs a2a-tck against a given SUT
│   ├── execute_notebooks.sh           # nbmake/papermill runner
│   ├── gen_endpoints.py               # regenerates config/endpoints.yaml from compose.yaml
│   ├── gen_lab06_keys.py              # dev signing keys for Lab 06 (git-ignored private JWKs)
│   └── check_coverage_tags.py         # notebook a2a_coverage tags vs appendices/A matrix
├── tests/
│   └── e2e/                           # cross-lab smoke tests (pytest, compose required)
└── .github/workflows/
    ├── ci.yaml                        # lint, type-check, unit tests
    └── e2e.yaml                       # compose up per profile, notebooks, TCK
```

## 2. Lab package layout (identical for every lab)

```text
labs/labNN-<slug>/
├── pyproject.toml                     # name = "labNN-<slug>", depends on a2a-labs-common
├── README.md                          # what you'll learn, architecture diagram, how to run, exercises
├── notebooks/
│   └── labNN_<slug>.ipynb             # THE lab (07-notebook-standards.md)
├── src/labNN_<slug_underscored>/
│   ├── __init__.py
│   ├── cards.py                       # Agent Card builders for this lab's servers
│   ├── servers/                       # one module per A2A server process
│   │   ├── __init__.py
│   │   └── <framework>_<role>.py      # each has `def main()` and `if __name__ == "__main__": main()`
│   ├── agents/                        # framework agent definitions (no networking)
│   ├── clients/                       # orchestrators / controllers that consume A2A
│   └── executors/                     # a2a-sdk AgentExecutor subclasses (when used)
└── tests/
    ├── test_cards.py                  # card validity (always)
    ├── test_executors.py              # in-process tests via the SDK test client (no LLM)
    └── test_integration.py            # @pytest.mark.compose: talks to running services
```

Rules:
- A server module MUST run in two ways: `python -m labNN_x.servers.<module>`, and as a console script `labNN-<module>` declared in `[project.scripts]`.
- Server modules MUST NOT read model names directly. They call `a2a_labs_common.models.*` with a role.
- Each server listens on `0.0.0.0:<port>`. Ports come from `config/endpoints.yaml` (see §4) and can be overridden by `<SERVICE>_PORT`.

## 3. uv workspace

Root `pyproject.toml`:

```toml
[project]
name = "a2a-labs"
version = "0.1.0"
requires-python = ">=3.12,<3.13"

[tool.uv]
package = false

[tool.uv.workspace]
members = ["libs/*", "services/*", "labs/*"]

[tool.uv.sources]
a2a-labs-common = { workspace = true }
a2a-labs-webhook-receiver = { workspace = true }

[dependency-groups]
dev = [
  "pytest>=8", "pytest-asyncio>=0.24", "pytest-timeout", "respx", "nbmake", "papermill",
  "ruff>=0.7", "mypy>=1.13", "pre-commit", "jupyterlab>=4.3", "ipykernel", "rich",
]
```

Pinned dependency baseline (the build agent MUST resolve these, then lock them; see `appendices/D-pyproject-templates.md`):

| Package | Pin | Used by |
|---|---|---|
| `a2a-sdk[http-server,fastapi,grpc,telemetry,sqlite,signing]` | `==1.2.3` (or latest 1.2.x) | everything |
| `google-adk[a2a]` | `==2.12.0` (or latest 2.x) | ADK labs |
| `litellm` | latest compatible with ADK | ADK model adapter |
| `strands-agents[a2a,ollama,otel]` | `>=1.57.1` (lock the exact version) | Strands labs |
| `strands-agents-tools` | latest | optional tools |
| `langgraph`, `langchain-core`, `langchain` | latest 1.x [VERIFY at scaffold] | LangGraph labs |
| `langchain-ollama`, `langchain-openai`, `langchain-anthropic` | latest | model adapters |
| `langchain-mcp-adapters` | latest | Lab 03 |
| `mcp[cli]` | latest (FastMCP included) | Lab 03 |
| `openinference-instrumentation-langchain` | latest | LangChain tracing |
| `opentelemetry-sdk`, `-api`, `-exporter-otlp` | latest 1.x | telemetry |
| `opentelemetry-instrumentation-httpx`, `-starlette`, `-fastapi`, `-grpc`, `-logging` | matching contrib version | telemetry |
| `httpx`, `uvicorn[standard]`, `starlette`, `fastapi`, `pydantic>=2`, `pydantic-settings`, `pyyaml` | latest | common |
| `pyjwt[crypto]`, `jwcrypto` | latest | push JWT, card JWS |
| `langgraph-cli[inmem]`, `langgraph-api>=0.18.0` | optional | Lab 04 Agent Server sub-lab |

## 4. Port map (canonical; mirrored in `config/endpoints.yaml`)

| Range / port | Owner |
|---|---|
| 3000 | Grafana (lgtm) |
| 4317 / 4318 | OTLP gRPC / HTTP (lgtm) |
| 3200 / 3100 / 9090 | Tempo / Loki / Prometheus query APIs (lgtm; used by tests and the setup notebook) |
| 8888 | JupyterLab |
| 5001 | A2A Inspector (profile `tools`) |
| 9100 | webhook-receiver |
| 11434 | Ollama |
| `10000 + NN*100 + i` | Lab NN, service i (i = 1..99) |

Each lab spec lists its exact services and ports. Example: Lab 02 uses 10201–10206.

`config/endpoints.yaml` format:

```yaml
services:
  lab00-protocol-agent:
    port: 10001
    docker_url: http://lab00-protocol-agent:10001
    host_url: http://localhost:10001
  lab00-protocol-agent-grpc:
    port: 10002
    docker_url: lab00-protocol-agent:10002
    host_url: localhost:10002
  # ...
```

Resolution: when `A2A_LABS_RUNTIME=docker` (set in every container), use `docker_url`; otherwise use `host_url`. An env var `<SERVICE_NAME_UPPER_SNAKE>_URL` overrides both. The resolution logic lives in `a2a_labs_common.config.endpoint(name)`.

Each server must advertise a reachable URL on its card: `docker_url` inside compose. Notebooks running on the host must rewrite card URLs. `a2a_labs_common.a2a.client.connect(name)` resolves the card and then **overrides the interface URL** with the resolved endpoint when running on the host. This is marked `# CUSTOM: host/docker URL bridging` and documented in each notebook's setup cell.

## 5. Code conventions

- Python 3.12, `from __future__ import annotations`, full type hints, `mypy --strict` on `libs/` and best effort on labs.
- `ruff` (lint and format), line length 100.
- Async everywhere on the network path. No `asyncio.run` inside library code.
- Logging: `logger = logging.getLogger(__name__)`. Never `print` in services. Notebooks use `rich` display helpers.
- Every module starts with a docstring: what it is, which pattern it demonstrates, and which spec section it implements (e.g., `Implements: spec labs/lab02 §4.2`).
- Comments explain *why*. A2A-specific lines get a short `# A2A:` comment explaining the protocol significance, for example `# A2A: first event must be the Task so clients can track it`.
- Custom (non-A2A-SDK) constructs carry `# CUSTOM: <reason>`.
- No secrets in code. Read them from env vars and `.env`; `.env` is git-ignored.

## 6. Makefile targets

| Target | Action |
|---|---|
| `make setup` | `uv sync --all-packages`; install pre-commit |
| `make models` | `uv run scripts/check_models.py` (pull/verify via Ollama API) |
| `make up LAB=lab02` | `docker compose --profile lab02 up -d --build` (adds the `ollama` profile if `OLLAMA_MODE=container`) |
| `make down` | `docker compose --profile "*" down` |
| `make logs LAB=lab02` | follow logs of that profile |
| `make notebook` | start the `jupyter` service and print its URL |
| `make test` | unit tests (no compose) |
| `make e2e LAB=lab02` | up → wait_for → pytest `-m compose` → nbmake on that lab's notebook |
| `make tck SUT=lab00-protocol-agent` | run a2a-tck |
| `make lint` | ruff + mypy |
