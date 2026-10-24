"""Settings and endpoint resolution (spec 03 §1, 02 §4).

Implements: spec 03 §1. Ports and URLs come from ``config/endpoints.yaml``; nothing is hard-coded.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import find_dotenv, load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Host runs (notebooks, scripts) read .env from the repo; containers get it from compose env_file.
# override=False keeps real environment variables authoritative.
load_dotenv(find_dotenv(usecwd=True), override=False)


class LabSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    runtime: Literal["docker", "host"] = Field("host", alias="A2A_LABS_RUNTIME")
    config_dir: Path = Field(Path("config"), alias="A2A_LABS_CONFIG_DIR")
    lab_id: str = Field("unknown", alias="A2A_LABS_LAB_ID")
    a2a_version: str = Field("1.0", alias="A2A_LABS_A2A_VERSION")
    enable_v03_compat: bool = Field(True, alias="A2A_LABS_V03_COMPAT")
    otel_enabled: bool = Field(True, alias="A2A_LABS_OTEL_ENABLED")


@lru_cache
def settings() -> LabSettings:
    return LabSettings()


def find_config_dir() -> Path:
    """The env var wins; otherwise walk up from CWD so notebooks work from any directory."""
    configured = settings().config_dir
    if (configured / "endpoints.yaml").is_file():
        return configured.resolve()
    here = Path.cwd().resolve()
    for parent in (here, *here.parents):
        if (parent / "config" / "endpoints.yaml").is_file():
            return parent / "config"
    raise FileNotFoundError("config/endpoints.yaml not found; set A2A_LABS_CONFIG_DIR")


@lru_cache
def _endpoints() -> dict[str, dict[str, Any]]:
    data = yaml.safe_load((find_config_dir() / "endpoints.yaml").read_text())
    services: dict[str, dict[str, Any]] = data["services"]
    return services


def _entry(name: str) -> dict[str, Any]:
    try:
        return _endpoints()[name]
    except KeyError:
        raise KeyError(f"unknown service {name!r}; add it to config/endpoints.yaml") from None


def endpoint(name: str) -> str:
    """Where *this process* should call the service: env override, then docker/host URL."""
    override = os.environ.get(f"{name.upper().replace('-', '_')}_URL")
    if override:
        return override
    key = "docker_url" if settings().runtime == "docker" else "host_url"
    return str(_entry(name)[key])


def port(name: str) -> int:
    override = os.environ.get(f"{name.upper().replace('-', '_')}_PORT")
    return int(override) if override else int(_entry(name)["port"])


def public_url(name: str) -> str:
    """URL to advertise on the Agent Card: reachable from wherever this process runs.

    In compose that is the docker DNS name; a server started on the host (``make run``,
    the TCK) advertises ``localhost`` URLs so clients on the host can follow the card (DEV-9).
    """
    override = os.environ.get(f"{name.upper().replace('-', '_')}_URL")
    if override:  # e.g. tests serving on an ephemeral port: advertise where we really are
        return override
    key = "docker_url" if settings().runtime == "docker" else "host_url"
    return str(_entry(name)[key])


def public_host(name: str) -> str:
    """Bare hostname to advertise (for adapters, like ADK ``to_a2a``, that build the URL)."""
    from urllib.parse import urlparse

    url = public_url(name)
    return urlparse(url if "://" in url else f"//{url}").hostname or "localhost"


def host_url(name: str) -> str:
    return str(_entry(name)["host_url"])
