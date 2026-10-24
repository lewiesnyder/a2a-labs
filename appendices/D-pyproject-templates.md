# Appendix D: `pyproject.toml` Templates

Versions are the research baseline (October 2026). The build agent MUST resolve them at M0, then lock with `uv lock` and record the actual versions in `docs/verification-log.md`.

## D.1 `libs/a2a-labs-common/pyproject.toml`

```toml
[project]
name = "a2a-labs-common"
version = "0.1.0"
description = "Shared helpers for the A2A labs: config, models, telemetry, A2A client/server helpers."
requires-python = ">=3.12,<3.13"
dependencies = [
  "a2a-sdk[http-server,fastapi,grpc,telemetry,sqlite,signing]>=1.2.3,<1.3",
  "httpx>=0.27",
  "uvicorn[standard]>=0.30",
  "starlette>=0.40",
  "fastapi>=0.115",
  "pydantic>=2.8",
  "pydantic-settings>=2.4",
  "pyyaml>=6",
  "rich>=13",
  "pyjwt[crypto]>=2.9",
  "jwcrypto>=1.5",
  # telemetry
  "opentelemetry-api>=1.27",
  "opentelemetry-sdk>=1.27",
  "opentelemetry-exporter-otlp>=1.27",
  "opentelemetry-instrumentation-httpx",
  "opentelemetry-instrumentation-starlette",
  "opentelemetry-instrumentation-fastapi",
  "opentelemetry-instrumentation-grpc",
  "opentelemetry-instrumentation-logging",
]

[project.optional-dependencies]
langchain = [
  "langgraph>=1.0",           # [VERIFY latest major]
  "langchain-core>=1.0",
  "langchain>=1.0",
  "langchain-ollama",
  "langchain-openai",
  "langchain-anthropic",
  "langchain-google-genai",
  "langchain-aws",
  "openinference-instrumentation-langchain",
]
adk = ["google-adk[a2a]>=2.12,<3", "litellm"]
strands = ["strands-agents[a2a,ollama,otel,litellm]>=1.57.1", "strands-agents-tools"]   # [VERIFY extras names]
mcp = ["mcp[cli]>=1.2", "langchain-mcp-adapters"]
all = ["a2a-labs-common[langchain,adk,strands,mcp]"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/a2a_labs_common"]
```

## D.2 Lab package template (`labs/lab02-agent-as-tool/pyproject.toml`)

```toml
[project]
name = "lab02-agent-as-tool"
version = "0.1.0"
description = "Lab 02 — Agent as Tool (A2A pattern 2) with ADK, Strands and LangChain."
requires-python = ">=3.12,<3.13"
dependencies = ["a2a-labs-common[langchain,adk,strands]"]

[project.scripts]
lab02-langgraph-currency = "lab02_tool.servers.langgraph_currency:main"
lab02-strands-weather    = "lab02_tool.servers.strands_weather:main"
lab02-adk-facts          = "lab02_tool.servers.adk_facts:main"
lab02-adk-controller     = "lab02_tool.servers.adk_controller:main"
lab02-strands-controller = "lab02_tool.servers.strands_controller:main"
lab02-langchain-controller = "lab02_tool.servers.langchain_controller:main"

[tool.uv.sources]
a2a-labs-common = { workspace = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/lab02_tool"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = ["compose: needs running compose profile", "llm: needs a real model", "slow: long"]
```

Per-lab extras:

| Lab | Common extras | Additional |
|---|---|---|
| 00 | (none) | — |
| 01, 02, 05, 07 | `langchain,adk,strands` | 05 also depends on `lab02-agent-as-tool` (workspace) |
| 03 | `langchain,adk,strands,mcp` | — |
| 04 | `langchain` | optional group `agentserver = ["langgraph-cli[inmem]", "langgraph-api>=0.18.0"]` |
| 06 | `langchain,adk,strands` | — |
| 08 | `langchain,adk,strands` | `aiosqlite` (via the a2a-sdk sqlite extra) |
| 09 | `langchain,adk,strands` | — |
| 10 | `adk,strands` | — |

## D.3 `services/webhook-receiver/pyproject.toml`

```toml
[project]
name = "a2a-labs-webhook-receiver"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = ["a2a-labs-common"]
[project.scripts]
webhook-receiver = "a2a_labs_webhook.app:main"
[tool.uv.sources]
a2a-labs-common = { workspace = true }
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[tool.hatch.build.targets.wheel]
packages = ["src/a2a_labs_webhook"]
```

## D.4 Tooling config (root `pyproject.toml` additions)

```toml
[tool.ruff]
line-length = 100
target-version = "py312"
[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "ASYNC", "RUF", "SIM"]
[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]
[[tool.mypy.overrides]]
module = ["strands.*", "google.adk.*", "litellm.*", "a2a.*"]
ignore_missing_imports = true
```
