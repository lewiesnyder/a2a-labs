"""The plain MCP tools server and the logic behind it."""

from __future__ import annotations

import pytest
from lab03_mcp.agents import tools_logic as logic
from lab03_mcp.servers import tools_mcp


def test_convert_units_families_and_temperature() -> None:
    assert logic.convert_units(3.2, "km", "ft")["result"] == pytest.approx(10498.6877, abs=1e-3)
    assert logic.convert_units(1, "lb", "kg")["result"] == pytest.approx(0.4536, abs=1e-4)
    assert logic.convert_units(100, "c", "f")["result"] == 212.0
    with pytest.raises(ValueError, match="unknown or mixed"):
        logic.convert_units(1, "km", "kg")


@pytest.mark.parametrize(
    "expr",
    ["__import__('os').system('x')", "9**9**9", "a + 1", "(1).real", "'a' * 3", "1; 2", "x" * 300],
)
def test_calculate_rejects_anything_that_is_not_plain_arithmetic(expr: str) -> None:
    with pytest.raises(ValueError):
        logic.calculate(expr)


def test_calculate_evaluates_arithmetic() -> None:
    assert logic.calculate("12 * 3.5 + 2 ** 3 - (4 / 2)")["result"] == 48.0
    assert logic.calculate("-3 + 10 % 4")["result"] == -1.0


def test_lookup_timezone_is_a_fixed_table() -> None:
    assert logic.lookup_timezone(" chicago ")["utc_offset_hours"] == -5
    assert logic.lookup_timezone("Tokyo")["timezone"] == "Asia/Tokyo"
    with pytest.raises(ValueError, match="unknown city"):
        logic.lookup_timezone("Atlantis")


async def test_mcp_server_lists_three_tools_and_calls_them(serve, session) -> None:  # type: ignore[no-untyped-def]
    live = await serve(tools_mcp.build_server().streamable_http_app())
    async with session(f"http://127.0.0.1:{live.port}/mcp") as s:
        tools = (await s.list_tools()).tools
        assert sorted(t.name for t in tools) == [
            "calculate",
            "convert_units",
            "lookup_timezone",
        ]
        ok = await s.call_tool("convert_units", {"value": 3.2, "from_unit": "km", "to_unit": "ft"})
        assert not ok.isError and "10498.6877" in ok.content[0].text
        bad = await s.call_tool("calculate", {"expression": "__import__('os')"})
        assert bad.isError  # unsafe input is an MCP tool error, not a crash


async def test_healthz_route_for_compose(serve) -> None:  # type: ignore[no-untyped-def]
    import httpx

    live = await serve(tools_mcp.build_server().streamable_http_app())
    async with httpx.AsyncClient() as http:
        assert (await http.get(f"http://127.0.0.1:{live.port}/healthz")).json()["status"] == "ok"
