"""Squad `lab09-squad-hotels` (strands). A plain A2A agent that announces itself to its gateway."""

from __future__ import annotations

from typing import Any

SERVICE = "lab09-squad-hotels"


def build_app(**kw: Any) -> Any:
    from a2a_labs_common.a2a.strands import StrandsA2AExecutor

    from lab09_agp.agents.squad_agents import build_strands

    executor = StrandsA2AExecutor(build_strands(SERVICE))

    from lab09_agp.servers.common import squad_app

    return squad_app(SERVICE, executor, **kw)


def main() -> None:
    from lab09_agp.servers.common import run_main

    run_main(SERVICE, "strands", build_app)


if __name__ == "__main__":
    main()
