"""Plain MCP tools server: ``convert_units``, ``calculate``, ``lookup_timezone``.

An MCP server exposes *tools* (functions) and *resources*; it is not an agent. The two specialists
use these tools internally ("MCP inside, A2A outside"). Implements: spec lab03 §2.
"""

from __future__ import annotations

from typing import Any

from lab03_mcp import mcp_app
from lab03_mcp.agents import tools_logic


def build_server():  # type: ignore[no-untyped-def]
    mcp = mcp_app.new_server("lab03-tools-mcp", "Deterministic unit, math and timezone tools.")

    @mcp.tool(
        name="convert_units",
        description="Convert a value between units (length, mass, volume, temperature).",
    )
    @mcp_app.traced("convert_units")
    async def convert_units(value: float, from_unit: str, to_unit: str) -> dict[str, Any]:
        return tools_logic.convert_units(value, from_unit, to_unit)

    @mcp.tool(
        name="calculate",
        description="Evaluate a plain arithmetic expression, for example '12 * 3.5'.",
    )
    @mcp_app.traced("calculate")
    async def calculate(expression: str) -> dict[str, Any]:
        return tools_logic.calculate(expression)

    @mcp.tool(
        name="lookup_timezone", description="Return the timezone name and UTC offset for a city."
    )
    @mcp_app.traced("lookup_timezone")
    async def lookup_timezone(city: str) -> dict[str, Any]:
        return tools_logic.lookup_timezone(city)

    return mcp


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab03-tools-mcp", framework="mcp", lab_id="lab03")
    mcp_app.serve(build_server(), service="lab03-tools-mcp")


if __name__ == "__main__":
    main()
