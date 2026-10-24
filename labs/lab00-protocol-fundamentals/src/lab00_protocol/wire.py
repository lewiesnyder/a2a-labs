"""Raw JSON-RPC helpers for the notebook, so cells can show the wire without boilerplate.

Implements: spec lab00 §5 (steps 5, 16, 18). These helpers deliberately bypass the SDK client:
the point of those steps is to see the envelope and the error object exactly as sent.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from a2a_labs_common.config import endpoint

V10 = {"Content-Type": "application/json", "A2A-Version": "1.0"}


def message_params(text: str, **message: Any) -> dict[str, Any]:
    """A v1.0 ``SendMessage`` params object (ProtoJSON enums, a single ``Part`` type)."""
    return {
        "message": {
            "messageId": "wire-1",
            "role": "ROLE_USER",
            "parts": [{"text": text}],
            **message,
        }
    }


def envelope(method: str, params: Any = None, id_: int = 1) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


async def post(
    service: str,
    body: Any,
    headers: dict[str, str] | None = None,
    path: str = "/",
) -> tuple[int, Any]:
    """POST ``body`` (dict, or a raw string for malformed-JSON demos); returns (status, json)."""
    content = body if isinstance(body, str) else json.dumps(body)
    async with httpx.AsyncClient(base_url=endpoint(service), timeout=30) as http:
        resp = await http.post(path, content=content, headers={**V10, **(headers or {})})
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, resp.text


async def rpc(
    service: str, method: str, params: Any = None, headers: dict[str, str] | None = None
) -> dict[str, Any]:
    _, body = await post(service, envelope(method, params), headers)
    return body  # type: ignore[no-any-return]


def error_row(name: str, response: Any, status: int = 200) -> dict[str, Any]:
    """One row of the error catalog: code, reason (``error.data[].reason``) and message."""
    if not isinstance(response, dict) or "error" not in response:
        return {
            "trigger": name,
            "http": status,
            "code": "-",
            "reason": "-",
            "message": "(no error)",
        }
    err = response["error"]
    if not isinstance(err, dict):
        return {"trigger": name, "http": status, "code": "-", "reason": "-", "message": str(err)}
    data = err.get("data") or [{}]
    return {
        "trigger": name,
        "http": status,
        "code": err.get("code", "-"),
        "reason": data[0].get("reason", "-") if isinstance(data, list) else "-",
        "message": str(err.get("message", ""))[:70],
    }
