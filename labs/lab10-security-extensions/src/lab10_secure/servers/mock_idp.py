"""Mock IdP server (``lab10-mock-idp``), FastAPI. See ``lab10_secure/idp.py``."""

from __future__ import annotations

import os
from typing import Any


def build_app(ttl_s: float | None = None) -> Any:
    from a2a_labs_common.config import public_url

    from lab10_secure.idp import Idp, create_app

    ttl = ttl_s if ttl_s is not None else float(os.environ.get("IDP_TOKEN_TTL", "300"))
    return create_app(Idp(public_url("lab10-mock-idp"), ttl_s=ttl))


def main() -> None:
    import uvicorn
    from a2a_labs_common.config import port
    from a2a_labs_common.telemetry import init_telemetry, instrument_app

    init_telemetry("lab10-mock-idp", framework="fastapi", lab_id="lab10")
    app = build_app()
    instrument_app(app)
    uvicorn.run(app, host="0.0.0.0", port=port("lab10-mock-idp"), log_config=None)


if __name__ == "__main__":
    main()
