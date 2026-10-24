# 06: Docker Compose

One `compose.yaml` at the repo root. A single command starts any lab:

```bash
docker compose --profile lab05 up -d --build
# or: make up LAB=lab05
```

The full service list is in `appendices/C-compose-skeleton.md`. It MUST stay consistent with each lab spec's "Services" table.

## 1. Service classes

| Class | Services | Profiles | Notes |
|---|---|---|---|
| **Always-on infrastructure** | `lgtm`, `jupyter`, `models-check` | *(none: always start)* | `models-check` is one-shot (`restart: "no"`). It runs `scripts/check_models.py` and other services `depends_on` it with `condition: service_completed_successfully`. |
| **Optional Ollama container** | `ollama`, `ollama-pull` | `ollama` | Use when `OLLAMA_MODE=container`. `ollama-pull` is one-shot: `ollama pull` for all three role models. GPU reservation block included (commented for macOS). |
| **Shared services** | `webhook-receiver` | `lab00`, `lab08`, `lab10`, `all` | |
| **Lab services** | `labNN-*` | `labNN`, `all` | One per A2A/MCP server process. |
| **Tools** | `a2a-inspector` | `tools` | Built from `github.com/a2aproject/a2a-inspector` [VERIFY build instructions]. |
| **Production-shaped telemetry** | `otel-collector` | `collector` | Optional; see 05 §1. |

Compose profiles rule: a service with no `profiles:` key always starts. Lab services list both their lab profile and `all`.

## 2. Ollama: host (default) vs container

**Host mode** (default, `OLLAMA_MODE=host`): the user's existing Ollama (with `qwen3.8:27b`, `llama3.2:latest` and `nimble-v3:latest` already pulled) is used.

```yaml
x-ollama-env: &ollama-env
  OLLAMA_BASE_URL: ${OLLAMA_BASE_URL:-http://host.docker.internal:11434}
  OLLAMA_API_BASE: ${OLLAMA_BASE_URL:-http://host.docker.internal:11434}   # LiteLLM (ADK)
x-host-gateway: &host-gateway
  extra_hosts: ["host.docker.internal:host-gateway"]   # required on Linux
```

On Linux the host Ollama must listen on all interfaces (`OLLAMA_HOST=0.0.0.0`), which the README documents. On macOS and Windows, Docker Desktop resolves `host.docker.internal` natively.

**Container mode** (`OLLAMA_MODE=container`, `--profile ollama`):

```yaml
  ollama:
    image: ollama/ollama:${OLLAMA_VERSION:-latest}      # must be >= 0.40.4 for nimble-v3
    profiles: [ollama]
    ports: ["11434:11434"]
    volumes: ["ollama-models:/root/.ollama"]
    healthcheck: {test: ["CMD", "ollama", "list"], interval: 10s, retries: 30}
    # deploy: {resources: {reservations: {devices: [{driver: nvidia, count: all, capabilities: [gpu]}]}}}
  ollama-pull:
    image: ollama/ollama:${OLLAMA_VERSION:-latest}
    profiles: [ollama]
    depends_on: {ollama: {condition: service_healthy}}
    environment: {OLLAMA_HOST: "http://ollama:11434"}
    entrypoint: ["/bin/sh", "-c"]
    command: ["ollama pull ${REASONING_MODEL:-qwen3.8:27b} && ollama pull ${QUICK_MODEL:-llama3.2:latest} && ollama pull ${DECISION_MODEL:-nimble-v3:latest}"]
    restart: "no"
```

With container mode, set `OLLAMA_BASE_URL=http://ollama:11434` in `.env`. The Makefile adds `--profile ollama` automatically when `OLLAMA_MODE=container`.

## 3. Image: `docker/Dockerfile`

```dockerfile
# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 A2A_LABS_RUNTIME=docker \
    A2A_LABS_CONFIG_DIR=/app/config
WORKDIR /app
# 1) deps layer (cache-friendly)
COPY pyproject.toml uv.lock ./
COPY libs/a2a-labs-common/pyproject.toml libs/a2a-labs-common/
COPY services/webhook-receiver/pyproject.toml services/webhook-receiver/
COPY labs/*/pyproject.toml ./_lab_pyprojects/        # helper script re-places them (see note)
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --all-packages --no-install-workspace
# 2) source
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --all-packages
ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["/app/docker/entrypoint.sh"]
```

Note: copying lab `pyproject.toml` files with globs flattens their paths. Use a small `scripts/copy_manifests.sh` or BuildKit `--parents` (`COPY --parents labs/*/pyproject.toml ./`; requires dockerfile:1.7-labs) [builder chooses; document it].

`docker/jupyter.Dockerfile`: `FROM a2a-labs:dev`, then `CMD jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --IdentityProvider.token=${JUPYTER_TOKEN:-a2a-labs} --ServerApp.root_dir=/app`. The repo is bind-mounted at `/app` so notebook edits persist.

`docker/entrypoint.sh`: optionally waits for `$WAIT_FOR` URLs (comma-separated), then runs `exec "$@"`.

## 4. Lab service template

```yaml
x-lab-service: &lab-service
  image: a2a-labs:dev
  build: {context: ., dockerfile: docker/Dockerfile}
  <<: *host-gateway
  env_file: [.env]
  environment:
    <<: *ollama-env
    OTEL_EXPORTER_OTLP_ENDPOINT: http://lgtm:4317
    OTEL_EXPORTER_OTLP_PROTOCOL: grpc
    OTEL_SEMCONV_STABILITY_OPT_IN: gen_ai_latest_experimental
    ADK_SUPPRESS_A2A_EXPERIMENTAL_FEATURE_WARNINGS: "true"
  depends_on:
    lgtm: {condition: service_started}
    models-check: {condition: service_completed_successfully}
  restart: unless-stopped
  healthcheck:
    test: ["CMD", "python", "-c", "import os,urllib.request;urllib.request.urlopen(f'http://localhost:{os.environ[\"PORT\"]}/.well-known/agent-card.json',timeout=3)"]
    interval: 10s
    timeout: 5s
    retries: 12

services:
  lab02-strands-weather:
    <<: *lab-service
    profiles: [lab02, all]
    command: ["python", "-m", "lab02_agent_as_tool.servers.strands_weather"]
    environment:
      <<: [*ollama-env]
      A2A_LABS_LAB_ID: lab02
      OTEL_SERVICE_NAME: lab02-strands-weather
      PORT: "10202"
      # (repeat OTEL vars or use a YAML anchor merge list)
    ports: ["10202:10202"]
```

- Every lab service exposes its port on the host so notebooks can run outside Docker.
- Services that call other agents use `depends_on: {<other>: {condition: service_healthy}}`.
- MCP servers in Lab 03 use a TCP or HTTP healthcheck on their own path instead of the agent-card check.

## 5. Base services (always on)

```yaml
  lgtm:
    image: grafana/otel-lgtm:${LGTM_VERSION:-latest}
    ports: ["3000:3000", "4317:4317", "4318:4318", "3200:3200", "3100:3100", "9090:9090"]
    volumes:
      - ./observability/grafana/dashboards:/otel-lgtm/a2a-dashboards:ro
      - ./observability/grafana/provisioning/dashboards.yaml:/otel-lgtm/grafana/conf/provisioning/dashboards/a2a.yaml:ro   # [VERIFY path]
      - lgtm-data:/data
    environment: {GF_AUTH_ANONYMOUS_ENABLED: "true", GF_AUTH_ANONYMOUS_ORG_ROLE: Admin}
  jupyter:
    build: {context: ., dockerfile: docker/jupyter.Dockerfile}
    <<: *host-gateway
    ports: ["8888:8888"]
    volumes: [".:/app"]
    env_file: [.env]
    environment: {<<: *ollama-env, OTEL_EXPORTER_OTLP_ENDPOINT: http://lgtm:4317, A2A_LABS_RUNTIME: docker}
  models-check:
    image: a2a-labs:dev
    <<: *host-gateway
    environment: {<<: *ollama-env}
    command: ["python", "scripts/check_models.py", "--require-all"]
    restart: "no"
volumes: {lgtm-data: {}, ollama-models: {}}
```

`models-check` honors `SKIP_MODEL_CHECK=true`, which exits 0 immediately. Use it for non-LLM labs (00, 08, 10) or other providers.

## 6. Profiles summary

| Profile | Services started (besides infra) |
|---|---|
| `lab00` | `lab00-protocol-agent` (JSON-RPC+REST :10001, gRPC :10002), `lab00-protocol-agent-nostream` (:10003), `lab00-protocol-agent-nopush` (:10004), `webhook-receiver` |
| `lab01` | `lab01-sdk-echo` (:10101), `lab01-adk-agent` (:10102), `lab01-strands-agent` (:10103), `lab01-langgraph-agent` (:10104) |
| `lab02` | `lab02-langgraph-currency` (:10201), `lab02-strands-weather` (:10202), `lab02-adk-facts` (:10203), `lab02-adk-controller` (:10204), `lab02-strands-controller` (:10205), `lab02-langchain-controller` (:10206) |
| `lab03` | `lab03-tools-mcp` (:10301 MCP), `lab03-strands-specialist` (:10302), `lab03-adk-specialist` (:10303), `lab03-mcp-a2a-bridge` (:10304 MCP), `lab03-langgraph-mcp-consumer` (:10305), `lab03-adk-mcp-consumer` (:10306) |
| `lab04` | `lab04-langgraph-travel` (:10401), `lab04-langgraph-orchestrator` (:10402), optional `lab04-langgraph-agent-server` (:10403, profile `lab04-agentserver`) |
| `lab05` | `lab05-supervisor` (:10501), `lab05-currency` (:10502), `lab05-weather` (:10503), `lab05-facts` (:10504), `lab05-adk-coordinator` (:10505), `lab05-strands-graph-supervisor` (:10506) |
| `lab06` | `lab06-registry` (:10601), `lab06-agent-a` (:10602), `lab06-agent-b` (:10603), `lab06-agent-rogue` (:10604), `lab06-delegator` (:10605) |
| `lab07` | `lab07-adk-planner` (:10701), `lab07-strands-writer` (:10702), `lab07-langgraph-editor` (:10703), `lab07-chain-orchestrator` (:10704) |
| `lab08` | `lab08-sdk-report-agent` (:10801), `lab08-strands-batch-agent` (:10802), `lab08-adk-research-agent` (:10803), `lab08-langgraph-pipeline-agent` (:10804), `webhook-receiver` |
| `lab09` | `lab09-root-gateway` (:10901), `lab09-finance-gateway` (:10902), `lab09-travel-gateway` (:10903), squad agents (:10911–10916) |
| `lab10` | `lab10-secure-agent` (:11001), `lab10-ext-agent` (:11002), `lab10-required-ext-agent` (:11003), `lab10-adk-secure` (:11004), `lab10-strands-secure` (:11005), `lab10-mock-idp` (:11009), `webhook-receiver` |
| `all` | everything above (expect high memory use with the 27B model) |
| `tools` | `a2a-inspector` (:5001) |

## 7. Resource guidance (README)

- `qwen3.8:27b` needs about 18 GB of VRAM or unified memory, plus KV cache. `llama3.2` needs about 2–3 GB and `nimble-v3:9b-q8_0` about 9.5 GB. Ollama swaps models as needed (`OLLAMA_MAX_LOADED_MODELS`), so the first calls are slow.
- To use less memory, set `REASONING_MODEL=qwen3.8:27b` → `llama3.2:latest` and `DECISION_MODEL=nimble-v3:9b-q4_K_M`. Every lab MUST still run, with lower quality.
- Profile `all` is not recommended on machines with <48 GB of memory.
