"""Registry server (``lab06-registry``), FastAPI. See ``lab06_registry/registry.py``."""

from __future__ import annotations

import os
from typing import Any


def build_app() -> Any:
    from lab06_registry.keys import load_trust_store, registry_dir
    from lab06_registry.registry import Registry, create_app, load_seed

    root = registry_dir()
    return create_app(
        Registry(load_trust_store(root)),
        load_seed(root / "seed.yaml"),
        reverify_s=float(os.environ.get("REGISTRY_REVERIFY_S", "300")),
    )


def main() -> None:
    import uvicorn
    from a2a_labs_common.config import port
    from a2a_labs_common.telemetry import init_telemetry, instrument_app

    init_telemetry("lab06-registry", framework="fastapi", lab_id="lab06")
    app = build_app()
    instrument_app(app)
    uvicorn.run(app, host="0.0.0.0", port=port("lab06-registry"), log_config=None)


if __name__ == "__main__":
    main()
