"""Loads ``config/models.yaml`` with ``${VAR:-default}`` interpolation (spec 04 §2).

Implements: spec 04 §2. Precedence: env var, then YAML, then built-in default.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from a2a_labs_common.config import find_config_dir, settings

ModelRole = Literal["reasoning", "quick", "decision"]
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def interpolate(text: str) -> str:
    return _VAR.sub(lambda m: os.environ.get(m.group(1)) or (m.group(2) or ""), text)


class RoleConfig(BaseModel):
    provider: str
    model: str
    base_url: str = "http://localhost:11434"
    temperature: float | None = None
    max_tokens: int | None = None
    think: bool = False
    reasoning_effort: str | None = None
    num_ctx: int | None = None
    fallback_role: str = "quick"
    timeout_s: float = 30


@lru_cache
def _raw() -> dict[str, Any]:
    text = (find_config_dir() / "models.yaml").read_text()
    data: dict[str, Any] = yaml.safe_load(interpolate(text))
    return data


def reset_cache() -> None:
    _raw.cache_clear()


def role_config(role: ModelRole) -> RoleConfig:
    data = _raw()
    cfg = {"base_url": data["defaults"]["base_url"], **data["roles"][role]}
    # Tests and CI: one switch fakes every chat role (spec 08 §4).
    if role != "decision" and os.environ.get("LLM_PROVIDER") == "fake":
        cfg["provider"] = "fake"
    # CUSTOM: host/docker URL bridging. .env carries the container-side Ollama URL; a process
    # on the host cannot resolve host.docker.internal, so use localhost there (DEV-5).
    if settings().runtime == "host":
        cfg["base_url"] = str(cfg["base_url"]).replace("host.docker.internal", "localhost")
    return RoleConfig(**cfg)


def provider_config(name: str) -> dict[str, Any]:
    cfg: dict[str, Any] = _raw()["providers"].get(name, {})
    return cfg


def api_key(provider: str) -> str | None:
    env_name = provider_config(provider).get("api_key_env")
    return os.environ.get(env_name) if env_name else None


def describe_models() -> list[dict[str, str]]:
    rows = []
    roles: tuple[ModelRole, ...] = ("reasoning", "quick", "decision")
    for role in roles:
        c = role_config(role)
        rows.append(
            {"role": role, "provider": c.provider, "model": c.model, "base_url": c.base_url}
        )
    return rows
