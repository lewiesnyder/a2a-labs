# syntax=docker/dockerfile:1.7-labs
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 A2A_LABS_RUNTIME=docker \
    A2A_LABS_CONFIG_DIR=/app/config
WORKDIR /app
# 1) deps layer (cache-friendly); --parents (dockerfile 1.7-labs) keeps workspace paths
COPY pyproject.toml uv.lock ./
COPY --parents libs/*/pyproject.toml services/*/pyproject.toml labs/*/pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --all-packages --all-extras --no-install-workspace
# 2) source
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --all-packages --all-extras
ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["/app/docker/entrypoint.sh"]
