"""The pure logic behind the MCP tools (deterministic, no I/O), unit-tested on its own.

Implements: spec lab03 §2 (``lab03-tools-mcp``).
"""

from __future__ import annotations

import ast
import operator
from typing import Any

# ── convert_units ───────────────────────────────────────────────────────────────────────────
# Each family maps a unit to its size in the family's base unit.
_FAMILIES: dict[str, dict[str, float]] = {
    "length": {
        "m": 1.0,
        "km": 1000.0,
        "cm": 0.01,
        "mm": 0.001,
        "mi": 1609.344,
        "ft": 0.3048,
        "in": 0.0254,
        "yd": 0.9144,
    },
    "mass": {"kg": 1.0, "g": 0.001, "lb": 0.45359237, "oz": 0.028349523125},
    "volume": {"l": 1.0, "ml": 0.001, "gal": 3.785411784, "cup": 0.2365882365},
}
_TEMPERATURE = {"c", "f", "k"}


def convert_units(value: float, from_unit: str, to_unit: str) -> dict[str, Any]:
    src, dst = from_unit.strip().lower(), to_unit.strip().lower()
    if src in _TEMPERATURE and dst in _TEMPERATURE:
        kelvin = {"c": value + 273.15, "f": (value - 32) * 5 / 9 + 273.15, "k": value}[src]
        out = {"c": kelvin - 273.15, "f": (kelvin - 273.15) * 9 / 5 + 32, "k": kelvin}[dst]
        return {"value": value, "from": src, "to": dst, "result": round(out, 4)}
    for table in _FAMILIES.values():
        if src in table and dst in table:
            return {
                "value": value,
                "from": src,
                "to": dst,
                "result": round(value * table[src] / table[dst], 4),
            }
    raise ValueError(f"cannot convert {from_unit!r} to {to_unit!r}: unknown or mixed unit families")


# ── calculate (safe AST evaluation) ─────────────────────────────────────────────────────────
_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_MAX_POWER = 100  # keep `9**9**9` from burning CPU


def _eval(node: ast.AST) -> float:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    ):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POWER:
            raise ValueError("exponent too large")
        return _BINARY[type(node.op)](left, right)  # type: ignore[operator]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))  # type: ignore[operator]
    raise ValueError(f"unsupported expression element: {type(node).__name__}")


def calculate(expression: str) -> dict[str, Any]:
    """Evaluate plain arithmetic. Names, calls, attributes and strings are rejected."""
    if len(expression) > 200:
        raise ValueError("expression too long")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"not an arithmetic expression: {exc.msg}") from exc
    return {"expression": expression, "result": round(float(_eval(tree.body)), 6)}


# ── lookup_timezone ─────────────────────────────────────────────────────────────────────────
# Fixed table "as of 2026-10-24" so answers never depend on the machine's clock or tzdata.
TIMEZONES: dict[str, tuple[str, int]] = {
    "chicago": ("America/Chicago", -5),
    "new york": ("America/New_York", -4),
    "london": ("Europe/London", 1),
    "paris": ("Europe/Paris", 2),
    "lisbon": ("Europe/Lisbon", 1),
    "tokyo": ("Asia/Tokyo", 9),
    "sydney": ("Australia/Sydney", 11),
}


def lookup_timezone(city: str) -> dict[str, Any]:
    key = city.strip().lower()
    if key not in TIMEZONES:
        raise ValueError(f"unknown city {city!r}; known: {', '.join(sorted(TIMEZONES))}")
    name, offset = TIMEZONES[key]
    return {
        "city": city.strip().title(),
        "timezone": name,
        "utc_offset_hours": offset,
        "as_of": "2026-10-24",
    }
