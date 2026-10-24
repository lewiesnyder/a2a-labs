"""AGP gateway server: ``AGP_ROLE`` root | domain and ``OTEL_SERVICE_NAME`` select which one."""

from __future__ import annotations

import os
from typing import Any


def service_name() -> str:
    explicit = os.environ.get("OTEL_SERVICE_NAME")
    if explicit:
        return explicit
    role, domain = os.environ.get("AGP_ROLE", "root"), os.environ.get("AGP_DOMAIN", "")
    return "lab09-root-gateway" if role == "root" else f"lab09-{domain}-gateway"


def build_app(service: str | None = None, **kw: Any) -> Any:
    from lab09_agp.servers.common import gateway_app

    return gateway_app(service or service_name(), **kw)


def main() -> None:
    from lab09_agp.servers.common import run_main

    run_main(service_name(), "a2a-sdk", build_app)


if __name__ == "__main__":
    main()
