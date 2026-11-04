"""Human-task inbox server (``lab11-inbox``), FastAPI. See ``lab11_hitl/inbox.py``."""

from __future__ import annotations

from typing import Any


def build_app() -> Any:
    from lab11_hitl.inbox import create_app

    return create_app()


def main() -> None:
    import uvicorn
    from a2a_labs_common.config import port
    from a2a_labs_common.telemetry import init_telemetry, instrument_app

    init_telemetry("lab11-inbox", framework="fastapi", lab_id="lab11")
    app = build_app()
    instrument_app(app)
    uvicorn.run(app, host="0.0.0.0", port=port("lab11-inbox"), log_config=None)


if __name__ == "__main__":
    main()
